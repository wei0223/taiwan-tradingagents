"""Fixed-rule calendar folds with knowledge and sample-size disclosure."""

from statistics import mean

from .bundle import fingerprint
from .historical_validation import short_horizon
from .knowledge import exact
from .market_expectations import quantile


def sample_summary(events, minimum=30):
    if minimum < 30:
        raise ValueError("Reporting threshold cannot be reduced below 30")
    n = len(events)
    return {
        "count": n,
        "positive_count": sum(x["net_return"] > 0 for x in events),
        "net_win_rate": mean(x["net_return"] > 0 for x in events) if n >= minimum else None,
        "aggregate_statistics_suppressed": n < minimum,
        "display_policy": "individual_events_only"
        if n < minimum
        else "descriptive_statistics_not_edge_proof",
        "empirical_edge_proven": False,
    }


def run(bars, sessions, folds, config, data_receipt, company):
    """No tuning within folds. Captures today can only produce diagnostics of past folds.

    True OOS requires both an immutable plan and the *inputs* known before the
    fold; future outcomes are deliberately separate price observations.
    """
    if config.get("rule_version") != "breakout-v1":
        raise ValueError("Unsupported frozen trading rule")
    exact(config["selected_at"])
    supplied = config.get("plan_sha256")
    plan = {k: v for k, v in config.items() if k != "plan_sha256"}
    if not supplied or fingerprint(plan) != supplied:
        raise ValueError("Frozen plan identity required")
    if plan.get("folds") != folds or company not in plan.get("universe", []):
        raise ValueError("Fold/universe differs from frozen plan")
    if not data_receipt.get("sha256"):
        raise ValueError("Captured price vintage hash required")
    exact(data_receipt["captured_at"])
    if data_receipt.get("bars_sha256") != fingerprint(bars) or data_receipt.get(
        "sessions_sha256"
    ) != fingerprint(sessions):
        raise ValueError("Price/calendar receipt does not bind supplied inputs")
    rows, previous_end = [], None
    for fold in folds:
        start, end = fold["test_start"], fold["test_end"]
        if start > end or (previous_end and start <= previous_end):
            raise ValueError("Overlapping or reversed test folds")
        previous_end = end
        prior = [x for x in sessions if x < start]
        if len(prior) < 60:
            rows.append({"fold": fold, "status": "insufficient_warmup"})
            continue
        # Current full-history adjusted prices never prove PIT adjustment vintages.
        mode = "retrospective_diagnostic"
        # A boolean attestation cannot prove daily PIT adjustment vintages. This
        # batch endpoint never upgrades current full-history bars to strict OOS.
        horizons = {}
        for horizon in (5, 10, 20):
            result = short_horizon(bars, sessions, start, end, horizon, **config.get("costs", {}))
            result.update(sample_summary(result["events"]))
            returns = [e["net_return"] for e in result["events"]]
            result["return_distribution"] = {
                f"p{int(p * 100)}": quantile(returns, p) if len(returns) >= 30 else None
                for p in (0.1, 0.25, 0.5, 0.75, 0.9)
            }
            result["market_regimes"] = {}
            for event in result["events"]:
                i = sessions.index(event["signal_date"])
                history = bars[max(0, i - 59) : i + 1]
                regime = (
                    "insufficient_ma60"
                    if len(history) < 60
                    else "above_ma60"
                    if bars[i]["close"] > mean(b["close"] for b in history)
                    else "below_ma60"
                )
                result["market_regimes"].setdefault(regime, []).append(
                    {"signal_date": event["signal_date"], "net_return": event["net_return"]}
                )
            horizons[str(horizon)] = result
        ids = sorted(
            {company + ":" + e["signal_date"] for r in horizons.values() for e in r["events"]}
        )
        rows.append(
            {
                "fold": fold,
                "status": "completed",
                "mode": mode,
                "unique_signal_ids": ids,
                "horizons": horizons,
            }
        )
    return {
        "stock_id": company,
        "plan": config,
        "data_receipt": data_receipt,
        "folds": rows,
        "unique_signal_count": len({k for r in rows for k in r.get("unique_signal_ids", [])}),
        "horizons_are_independent": False,
        "industry_peers_are_independent": False,
        "empirical_edge_proven": False,
        "orders": False,
        "oos_unavailable_reason": "Daily immutable input vintages and outcome separation require the release/vintage evaluation path; a caller flag is insufficient",
    }
