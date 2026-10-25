"""Graded earnings outputs, per-share references and explicit sensitivity sets."""

from copy import deepcopy

from .financial_model import waterfall
from .scenarios import finite

EXPECTED = {
    "revenue_twd": "monthly revenue, orders or company guidance",
    "variable_cogs_ratio": "product mix, gross margin and cost evidence",
    "variable_opex_ratio": "quarterly operating expense notes",
    "nonoperating_twd": "investment, FX and non-recurring profit notes",
    "tax_rate": "quarterly tax note and geographic profit mix",
    "noncontrolling_profit_twd": "subsidiary profit allocation note",
    "noncontrolling_profit_fraction": "subsidiary profit allocation note",
    "diluted_weighted_shares": "EPS note, recognition dates and daily weighted share bridge",
    "diluted_adjustment_twd": "if-converted numerator and anti-dilution note",
}


def describe(forward, reference, evidence):
    ref = None
    if reference:
        key = reference["evidence_id"]
        if key not in evidence or not reference.get("source_id"):
            raise ValueError("Issued-share reference needs bound evidence and source")
        shares = finite(evidence[key])
        if shares <= 0:
            raise ValueError("Positive issued share reference required")
        ref = {
            **reference,
            "issued_shares": shares,
            "basis": "parent_profit_divided_by_current_issued_shares_not_basic_or_diluted_eps",
        }
    rows = []
    for scenario, model in forward.get("scenarios", {}).items():
        for case in model["quarters"]:
            calc = case["calculation"]
            missing = [
                {
                    "driver": key,
                    "reason": item.get("rationale"),
                    "required_evidence": item.get(
                        "expected_evidence",
                        EXPECTED.get(key, "source-reviewed quarterly assumption"),
                    ),
                    "availability_date": item.get("expected_at"),
                    "date_status": "not_announced"
                    if not item.get("expected_at")
                    else "analyst_expectation_not_official",
                }
                for key, item in case["inputs"].items()
                if item.get("kind") == "unknown"
            ]
            parent = calc.get("parent_profit_twd")
            quotient = parent / ref["issued_shares"] if ref and parent is not None else None
            rows.append(
                {
                    "scenario": scenario,
                    "period": case["period"],
                    "output_tier": "conditional_scenario"
                    if parent is not None
                    else "unavailable_incomplete_profit_bridge",
                    "is_official_actual": False,
                    "calculation": calc,
                    "inputs": case["inputs"],
                    "blockers": missing,
                    "parent_profit_per_current_issued_share_twd": quotient,
                    "per_share_basis": ref,
                    "official_diluted_eps": None,
                    "conditional_eps_twd": calc.get("eps_twd"),
                    "conditional_eps_basis": "explicit_weighted_share_assumption_not_official"
                    if calc.get("eps_twd") is not None
                    else None,
                    "share_reference_sensitivity": [
                        {
                            "assumed_share_multiplier": factor,
                            "assumed_shares": ref["issued_shares"] * factor,
                            "parent_profit_quotient_twd": parent / (ref["issued_shares"] * factor),
                            "label": "illustrative_denominator_axis_not_dilution_bound_or_probability",
                        }
                        for factor in (0.95, 1.0, 1.05)
                    ]
                    if quotient is not None
                    else [],
                }
            )
    totals = {}
    for name in forward.get("scenarios", {}):
        selected = [r for r in rows if r["scenario"] == name]
        profits = [r["calculation"].get("parent_profit_twd") for r in selected]
        total = sum(profits) if len(profits) == 4 and all(p is not None for p in profits) else None
        totals[name] = {
            "four_quarter_parent_profit_twd": total,
            "four_quarter_parent_profit_per_current_issued_share_twd": total / ref["issued_shares"]
            if total is not None and ref
            else None,
            "label": "four_quarter_parent_profit_divided_by_fixed_current_shares_not_ntm_diluted_eps",
        }
    return {
        "official_actuals_location": "reported_actuals_separate_from_scenarios",
        "rows": rows,
        "four_quarter_reference_totals": totals,
        "current_share_reference": ref,
        "ntm_official_eps_established": False,
        "note": "Never sum issued-share quotients and label them NTM diluted EPS or Forward PE",
    }


def sensitivity(case, spec):
    """One-factor endpoints and explicitly joint scenarios; no independence assumption."""
    missing = [key for key, item in case["inputs"].items() if item.get("value") is None]
    if missing and set(missing) != {"diluted_weighted_shares"}:
        return {
            "status": "unavailable_incomplete_inputs",
            "missing": missing,
            "ranking": [],
            "joint": [],
        }
    baseline = {key: item["value"] for key, item in case["inputs"].items()}
    if missing:
        baseline["diluted_weighted_shares"] = 1
    original = waterfall(baseline)

    def public(result):
        if missing:
            result.update(eps_twd=None, diluted_weighted_shares=None)
        return result

    def changed(changes):
        values = deepcopy(baseline)
        for key, item in changes.items():
            if not item.get("rationale"):
                raise ValueError("Sensitivity assumptions need a rationale")
            value = finite(item["value"])
            if key == "gross_margin_change_pp":
                values["variable_cogs_ratio"] -= value / 100
            elif key == "revenue_multiplier":
                values["revenue_twd"] *= value
            elif key == "fx_multiplier":
                fraction = finite(item["revenue_exposure_fraction"])
                if not 0 <= fraction <= 1:
                    raise ValueError("Explicit FX revenue exposure fraction required")
                values["revenue_twd"] *= 1 + (value - 1) * fraction
            elif key in values:
                values[key] = value
            else:
                raise ValueError("Unknown sensitivity driver")
        return waterfall(values)

    ranking = []
    for axis in spec.get("axes", []):
        if not axis.get("name") or len(axis["endpoints"]) != 2:
            raise ValueError("Named low/high endpoints required")
        results = [changed(endpoint) for endpoint in axis["endpoints"]]
        ranking.append(
            {
                "driver": axis["name"],
                "endpoints": axis["endpoints"],
                "parent_profit_twd": [r["parent_profit_twd"] for r in results],
                "conditional_eps_twd": [r["eps_twd"] if not missing else None for r in results],
                "eps_span": abs(results[1]["eps_twd"] - results[0]["eps_twd"])
                if not missing
                else None,
                "parent_profit_span_twd": abs(
                    results[1]["parent_profit_twd"] - results[0]["parent_profit_twd"]
                ),
            }
        )
    ranking.sort(key=lambda r: (-r["parent_profit_span_twd"], r["driver"]))
    joint = [
        {
            "name": j["name"],
            "changes": j["changes"],
            "calculation": public(changed(j["changes"])),
            "dependence_assumption": j["dependence_assumption"],
        }
        for j in spec.get("joint", [])
        if j.get("dependence_assumption")
    ]
    if len(joint) != len(spec.get("joint", [])):
        raise ValueError("Joint scenario dependence must be explicit")
    return {
        "status": "conditional_sensitivity_not_probability",
        "baseline": public(original),
        "ranking_metric": "parent_profit_span_twd",
        "ranking": ranking,
        "joint": joint,
        "probabilities": None,
        "limitation": "Ranking depends on chosen endpoint widths, not measured variance or causal importance",
    }


def parameters(kind, supplied, documents, eligible, required):
    rows = []
    for key in sorted(required):
        item = supplied.get(key)
        if not item:
            rows.append(
                {
                    "parameter": key,
                    "value": None,
                    "status": "unavailable",
                    "frequency": None,
                    "measurement_error": None,
                    "required_evidence": "company-specific original or explicit scenario anchor",
                }
            )
            continue
        if item.get("source_id") not in eligible or not item.get("locator"):
            raise ValueError("Industry parameter needs an eligible source and locator")
        if item.get("kind") not in {"fact", "assumption", "unobservable"} or not item.get(
            "frequency"
        ):
            raise ValueError("Parameter observation status and frequency required")
        if item.get("kind") == "fact" and not item.get("numeric_evidence_id"):
            raise ValueError("Observed parameter requires numeric evidence binding")
        source = documents[item["source_id"]]
        rows.append(
            {
                "parameter": key,
                **item,
                "source_sha256": source["sha256"],
                "captured_at": source["captured_at"],
                "published_at": source.get("published_at"),
                "measurement_error": item.get("measurement_error"),
                "status": item["kind"],
            }
        )
    return {
        "profile": kind,
        "parameters": rows,
        "statistically_calibrated": False,
        "missing_count": sum(r["status"] == "unavailable" for r in rows),
    }
