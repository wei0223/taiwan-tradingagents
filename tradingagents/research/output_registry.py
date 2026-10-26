"""One versioned table for current conditional outputs and their qualifications."""

import copy
import math

from .bundle import fingerprint

FIELDS = ("revenue_twd", "gross_profit_twd", "opex_twd", "operating_profit_twd",
          "pretax_profit_twd", "tax_twd", "noncontrolling_profit_twd", "parent_profit_twd",
          "diluted_numerator_twd", "diluted_weighted_shares", "eps_twd")


def build(snapshot, estimate, generated_at):
    version = estimate.get("model_version") or estimate.get("model_sha256") or "unavailable_model"
    snapshot_id = fingerprint(snapshot)
    run = fingerprint({"input_snapshot_id": snapshot_id, "model_version": version,
                       "generated_at": generated_at})
    common = {"model_version": version, "calculation_run_id": run,
              "input_snapshot_id": snapshot_id, "source_as_of": estimate.get("created_at", snapshot["as_of"])}
    rows = []

    def add(scenario, period, metric, value, qualification="conditional", unit=None):
        allowed = estimate.get("status") != "BLOCKED" or qualification != "conditional"
        if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError("Nonfinite versioned output")
        row = {**common, "scenario": scenario, "period": period, "metric": metric,
               "qualification": qualification, "value": value if allowed else None,
               "status": "AVAILABLE" if allowed and value is not None else "UNAVAILABLE",
               "unit": unit or ("shares" if metric == "diluted_weighted_shares" else
                                  "TWD/share" if metric == "eps_twd" else "multiple" if metric.endswith("pe") else "TWD")}
        row["output_id"] = fingerprint(row)
        rows.append(row)
    for scenario, model in sorted(estimate.get("scenarios", {}).items()):
        for case in model["quarters"]:
            for key in FIELDS:
                add(scenario, case["period"], key, case.get(key))
        add(scenario, "next_four_quarters", "four_quarter_eps_sum_twd", model["four_quarter_eps_sum_twd"], unit="TWD/share")
        add(scenario, "next_four_quarters", "forward_pe", model["forward_pe"])
        prices = model.get("price_sensitivity_twd", [])
        axes = estimate.get("pe_sensitivity", []) if prices else []
        for pe, price in zip(axes, prices, strict=True):
            add(scenario, "assumed_pe:"+str(pe), "conditional_price_twd", price, "conditional_price_not_fair_value")
    trailing = estimate.get("ttm", {})
    add("reported", "trailing_four_quarters", "eps_twd", trailing.get("eps_twd"), "reported_quarter_sum")
    add("reported", "trailing_four_quarters", "ttm_pe", estimate.get("ttm_pe"), "current_data_TTM_price_reference_not_historical_PIT")
    if snapshot.get("datasets"):
        from .bundle import build_bundle

        for key, evidence in build_bundle(snapshot)["evidence"].items():
            if isinstance(evidence["value"], (int, float)) and not isinstance(evidence["value"], bool):
                add("observed", evidence.get("data_end") or snapshot["as_of"], evidence["path"],
                    evidence["value"], "source_observation_not_forecast_or_valuation_validation", evidence["unit"])
                rows[-1]["source_lineage"] = {"evidence_id": key, "sources": evidence["sources"],
                                             "retrieved_at": evidence["retrieved_at"],
                                             "publication_dates": evidence["publication_dates"]}
                rows[-1]["output_id"] = fingerprint({k: v for k, v in rows[-1].items() if k != "output_id"})
    result = {"schema_version": 6, **common, "rows": rows, "model_calculation_sha256": fingerprint(estimate),
              "current_model_kind": "conditional_not_formal_validated_forecast",
              "legacy_sections": "historical_revision_record_not_current_prediction_state"}
    validate(result)
    return result


def validate(registry):
    seen = {}
    for row in registry["rows"]:
        for key in ("model_version", "calculation_run_id", "input_snapshot_id", "source_as_of", "status"):
            if not row.get(key):
                raise ValueError("Output lineage missing")
            if key != "status" and row[key] != registry[key]:
                raise ValueError("Mixed current output lineage")
        if row.get("output_id") != fingerprint({k: v for k, v in row.items() if k != "output_id"}):
            raise ValueError("Output value or status modified")
        key = tuple(row[k] for k in ("model_version", "scenario", "period", "metric", "qualification"))
        if key in seen:
            raise ValueError("Duplicate or contradictory same-version output state")
        seen[key] = row
        if (row["status"] == "AVAILABLE") != (row["value"] is not None):
            raise ValueError("Output value and status disagree")
    return True


def materialize(estimate, registry):
    """Renderer reads the accepted table, never an unrelated legacy quotient."""
    validate(registry)
    if fingerprint(estimate) != registry["model_calculation_sha256"]:
        raise ValueError("Current model differs from accepted output registry")
    result = copy.deepcopy(estimate)
    index = {(r["scenario"], r["period"], r["metric"]): r["value"]
             for r in registry["rows"] if r["qualification"] in {"conditional", "conditional_price_not_fair_value"}}
    for scenario, model in result["scenarios"].items():
        for case in model["quarters"]:
            for key in FIELDS:
                case[key] = index[(scenario, case["period"], key)]
        for key in ("four_quarter_eps_sum_twd", "forward_pe"):
            model[key] = index[(scenario, "next_four_quarters", key)]
        if model.get("price_sensitivity_twd"):
            model["price_sensitivity_twd"] = [index[(scenario, "assumed_pe:"+str(pe), "conditional_price_twd")]
                                               for pe in result["pe_sensitivity"]]
    return result


def audit_presentation(text, registry):
    import re

    validate(registry)
    current = re.sub(r'<section[^>]*data-scope="historical"[^>]*>.*?</section>', '', text, flags=re.S)
    if re.search(r"待補[：:]\s*</(?:td|p|div)>", current):
        raise ValueError("Empty pending item in report")
    available = any(r["metric"] == "eps_twd" and r["qualification"] == "conditional" and r["status"] == "AVAILABLE" for r in registry["rows"])
    if available and any(s in current for s in ("歸母橋不足，商數不可用", "當前條件EPS不可用", "當前條件 EPS 不可用")):
        raise ValueError("Unqualified legacy contradiction in current model presentation")
    return {"status": "passed", "model_version": registry["model_version"],
            "registered_outputs": len(registry["rows"]), "empty_pending_items": 0,
            "scope": "versioned_state_and_known_template_contradictions_not_general_NLP_verification"}
