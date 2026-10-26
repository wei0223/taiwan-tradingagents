"""Time-isolated fixed-method financial replay. Current backfills cannot pass PIT."""

from statistics import mean

from .bundle import fingerprint
from .market_expectations import quantile
from .predictive import month_index
from .revenue_projection import METHODS, predict
from .scenarios import finite, timestamp


def metrics(pairs):
    errors = [finite(p["prediction"]) - finite(p["actual"]) for p in pairs]
    ape = [abs(e / p["actual"]) for e, p in zip(errors, pairs, strict=True) if p["actual"] != 0]
    smape = [2 * abs(e) / (abs(p["prediction"]) + abs(p["actual"]))
             for e, p in zip(errors, pairs, strict=True) if abs(p["prediction"]) + abs(p["actual"]) > 0]
    return {"n": len(errors), "mae": mean(map(abs, errors)) if errors else None,
            "bias": mean(errors) if errors else None, "mape": mean(ape) if ape else None,
            "mape_n": len(ape), "smape": mean(smape) if smape else None,
            "absolute_error_p50": quantile(list(map(abs, errors)), .5),
            "absolute_error_p90": quantile(list(map(abs, errors)), .9),
            "forecast_hit_rate": None, "independent_samples_proven": False}


def availability(row):
    captured = timestamp(row["captured_at"])
    published = row.get("published_at")
    if published:
        return max(timestamp(published), captured)
    if (row.get("public_capture_verified") is True
            and row.get("publication_time_status") == "unknown_current_revision"):
        return captured
    raise ValueError("Unknown publication time requires verified public capture; never invent publication dates")


def known(rows, cutoff):
    groups = {}
    for row in rows:
        if not row.get("source_sha256") or not row.get("revision_id"):
            raise ValueError("PIT requires original numeric-vintage identity")
        if (not row.get("raw") or fingerprint(row["raw"]) != row["source_sha256"]
                or row["raw"].get("stock_id") != row.get("stock_id")
                or finite(row["raw"].get("revenue")) != finite(row["value"])
                or f"{int(row['raw']['revenue_year']):04d}-{int(row['raw']['revenue_month']):02d}" != row["period"]):
            raise ValueError("PIT numeric value does not match its archived raw row")
        if availability(row) > timestamp(cutoff):
            continue
        period = month_index(row["period"])
        if period >= month_index(cutoff[:7]):
            continue
        groups.setdefault(period, []).append(row)
    selected = {}
    for period, candidates in groups.items():
        last = max(availability(r) for r in candidates)
        latest = [r for r in candidates if availability(r) == last]
        if len({fingerprint(r) for r in latest}) != 1:
            raise ValueError("Ambiguous selected numeric version")
        selected[period] = latest[0]
    return selected


def replay(rows, outcomes, folds, plan, evaluated_at):
    """Weights freeze at train_end; test outcomes never tune them.

    Each fold supplies exact origin timestamps and explicit target months. Outcomes
    are separate and require first-official numeric-version proof for scoring.
    A model selected today does not become a historically released prediction.
    """
    if plan.get("plan_sha256") != fingerprint({k: v for k, v in plan.items() if k != "plan_sha256"}):
        raise ValueError("Frozen method/universe/fold plan changed")
    if plan.get("folds") != folds or not plan.get("registered_at") or not plan.get("model_version"):
        raise ValueError("Frozen model/fold identity required")
    if (timestamp(plan["registered_at"]) > timestamp(evaluated_at)
            or not plan.get("stock_id") or any(r.get("stock_id") != plan["stock_id"] for r in [*rows, *outcomes])):
        raise ValueError("Future plan or mixed company histories")
    result, previous_end = [], None
    for fold in folds:
        if (timestamp(fold["train_end"]) >= timestamp(fold["test_start"]) or timestamp(fold["test_start"]) > timestamp(fold["test_end"])
                or (previous_end and timestamp(fold["test_start"]) <= timestamp(previous_end))):
            raise ValueError("Training/test overlap or unordered folds")
        previous_end = fold["test_end"]
        training = known(rows, fold["train_end"])
        train_values = {k: r["value"] for k, r in training.items()}
        train_errors = {m: [] for m in METHODS}
        for origin in sorted(training):
            if origin + 1 not in training:
                continue
            past = {k: v for k, v in train_values.items() if k <= origin}
            try:
                candidates = {m: predict(past, origin, 1, m) for m in METHODS}
            except (KeyError, ValueError, OverflowError, ZeroDivisionError):
                continue
            for method in METHODS:
                train_errors[method].append(abs(candidates[method] - train_values[origin+1]))
        inv = {m: 1 / max(mean(v), 1) if v else 1 for m, v in train_errors.items()}
        weights = {m: v / sum(inv.values()) for m, v in inv.items()}
        predictions, pending, scored = [], [], {m: [] for m in (*METHODS, "frozen_blend")}
        for origin in fold["origins"]:
            cutoff, target = origin["as_of"], origin["target"]
            if not timestamp(fold["test_start"]) <= timestamp(cutoff) <= timestamp(fold["test_end"]):
                raise ValueError("Origin outside fold")
            available = known(rows, cutoff)
            if len(available) < 24:
                pending.append({"origin": cutoff, "target": target,
                                "reason": "historical_input_vintages_missing", "known_months": len(available)})
                continue
            end = max(available)
            horizon = month_index(target) - end
            if horizon < 1:
                raise ValueError("Forecast target is already observed")
            history = {k: r["value"] for k, r in available.items()}
            try:
                estimates = {m: predict(history, end, horizon, m) for m in METHODS}
            except (KeyError, ValueError, OverflowError, ZeroDivisionError):
                pending.append({"origin": cutoff, "target": target, "reason": "consecutive_history_missing"})
                continue
            estimates["frozen_blend"] = sum(weights[m] * estimates[m] for m in METHODS)
            record = {"as_of": cutoff, "target": target, "model_version": plan["model_version"],
                      "plan_sha256": plan["plan_sha256"], "weights": weights,
                      "input_snapshot_id": fingerprint(available), "input_vintages": list(available.values()),
                      "forecasts": estimates, "historically_issued_forecast": False}
            actuals = [r for r in outcomes if r["period"] == target
                       and r.get("vintage_kind") == "initial" and r.get("initial_publication_verified") is True
                       and r.get("official_source_reviewed") is True and r.get("source_locator")
                       and r.get("initial_publication_history_verified") is True
                       and r.get("published_at")
                       and r.get("source_sha256") and r.get("revision_id")
                       and max(timestamp(r["published_at"]), timestamp(r["captured_at"])) <= timestamp(evaluated_at)
                       and timestamp(r["published_at"]) > timestamp(cutoff)]
            if len(actuals) == 1:
                known(actuals, evaluated_at)  # Bind outcome values to their original numeric rows too.
                record["actual"] = actuals[0]
                for method, value in estimates.items():
                    scored[method].append({"prediction": value, "actual": actuals[0]["value"]})
            else:
                record["actual_status"] = "pending_unique_initial_actual"
            predictions.append(record)
        result.append({"fold": fold, "weights_frozen_at": fold["train_end"], "weights": weights,
                       "input_training_months": len(training), "predictions": predictions, "pending": pending,
                       "metrics": {m: metrics(v) for m, v in scored.items()},
                       "plan_predates_test": timestamp(plan["registered_at"]) <= timestamp(fold["test_start"]),
                       "status": "INPUTS_UNAVAILABLE" if not predictions else "PIT_input_replay_not_ex_ante_release"})
    return {"folds": result, "model_version": plan["model_version"],
            "strict_prospective_performance": False, "forecast_hit_rate": None,
            "completed_input_isolated_folds": sum(bool(r["predictions"]) for r in result),
            "no_test_outcome_weight_feedback": True}


def attribution(prediction, actual, issued_at, actual_published_at):
    if timestamp(actual_published_at) <= timestamp(issued_at):
        return {"status": "ineligible_already_public", "errors": {}}
    keys = ("revenue_twd", "gross_profit_twd", "opex_twd", "tax_twd", "noncontrolling_profit_twd",
            "parent_profit_twd", "diluted_numerator_twd", "diluted_weighted_shares", "eps_twd")
    errors = {k: prediction[k] - actual[k] for k in keys if prediction.get(k) is not None and actual.get(k) is not None}
    n, d, an, ad = (prediction.get("diluted_numerator_twd"), prediction.get("diluted_weighted_shares"),
                    actual.get("diluted_numerator_twd"), actual.get("diluted_weighted_shares"))
    bridge = None
    if None not in (n, d, an, ad) and min(d, ad) > 0:
        bridge = {"numerator_effect_twd_per_share": (n-an)/ad,
                  "denominator_effect_twd_per_share": n*(1/d-1/ad),
                  "exact_quotient_error": n/d-an/ad,
                  "reported_rounding_residual": errors.get("eps_twd", n/d-an/ad)-(n/d-an/ad)}
    return {"status": "selected_actual_stage_differences_not_causal_proof", "errors": errors,
            "eps_bridge": bridge, "forecast_hit_rate": None}
