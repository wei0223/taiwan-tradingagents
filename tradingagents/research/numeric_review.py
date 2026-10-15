"""Controlled counterfactuals: agent-proposed inputs, program-calculated EPS and prices."""

import copy

from .bundle import fingerprint
from .scenarios import finite, forecast

ALLOWED = {"revenue_twd", "operating_margin", "weighted_diluted_shares", "valuation_pe"}


def validate_opening(result, role, bundle):
    required = {"role", "stage", "bundle_sha256", "summary", "claims", "challenges", "limitations"}
    if (
        set(result) != required
        or result["role"] != role
        or result["stage"] != "opening"
        or result["bundle_sha256"] != fingerprint(bundle)
    ):
        raise ValueError("Opening context/schema mismatch")
    if not 2 <= len(result["claims"]) <= 3 or not 1 <= len(result["challenges"]) <= 2:
        raise ValueError("Bounded claims and numeric challenges required")
    seen = set()
    for row in result["claims"] + result["challenges"]:
        if row["id"] in seen or not row["id"].startswith(role + "_"):
            raise ValueError("Duplicate or incorrect role claim ID")
        seen.add(row["id"])
        refs = row["evidence_ids"]
        if not refs or len(refs) != len(set(refs)):
            raise ValueError("Distinct evidence IDs required")
        for key in refs:
            e = bundle["evidence"][key]
            if e["availability"] != "available" or e["value"] is None:
                raise ValueError("Unavailable review evidence")
    for row in result["challenges"]:
        if (
            row["target_scenario"] != "base"
            or row["field"] not in ALLOWED
            or not row.get("rationale")
        ):
            raise ValueError("Counterfactual must target shared base assumptions")
        finite(row["proposed_value"])
    return True


def recalculate(bundle, specification, changes, coupled_minority=True):
    model = forecast(bundle, specification)
    case = copy.deepcopy(specification["scenarios"]["base"])
    seen = set()
    for change in changes:
        field = change["field"]
        period = change.get("period")
        if field not in ALLOWED or (period, field) in seen:
            raise ValueError("Unsupported/duplicate counterfactual input")
        seen.add((period, field))
        target = (
            case
            if field == "valuation_pe"
            else next(r for r in case["quarters"] if r["period"] == period)
        )
        target[field] = {
            "kind": "assumption",
            "value": finite(change["proposed_value"]),
            "rationale": change["rationale"],
            "evidence_ids": change["evidence_ids"],
        }
    if coupled_minority:
        for old, new in zip(
            specification["scenarios"]["base"]["quarters"], case["quarters"], strict=True
        ):
            old_after_tax = (
                old["revenue_twd"]["value"] * old["operating_margin"]["value"]
                + old["nonoperating_twd"]["value"]
            ) * (1 - old["tax_rate"]["value"])
            if old_after_tax <= 0:
                raise ValueError("Minority ratio sensitivity needs positive baseline group profit")
            ratio = old["noncontrolling_profit_twd"]["value"] / old_after_tax
            new_after_tax = (
                new["revenue_twd"]["value"] * new["operating_margin"]["value"]
                + new["nonoperating_twd"]["value"]
            ) * (1 - new["tax_rate"]["value"])
            new["noncontrolling_profit_twd"]["value"] = new_after_tax * ratio
            new["noncontrolling_profit_twd"]["rationale"] += (
                " Counterfactual preserves original group minority profit ratio."
            )
    counter = copy.deepcopy(specification)
    counter["scenarios"] = {name: copy.deepcopy(case) for name in ("bear", "base", "bull")}
    revised = forecast(bundle, counter)["scenarios"]["base"]
    original = model["scenarios"]["base"]
    return {
        "baseline_eps": original["next_four_quarter_eps_sum"],
        "revised_eps": revised["next_four_quarter_eps_sum"],
        "eps_delta": revised["next_four_quarter_eps_sum"] - original["next_four_quarter_eps_sum"],
        "baseline_price": original["scenario_price_twd"],
        "revised_price": revised["scenario_price_twd"],
        "price_delta": None
        if revised["scenario_price_twd"] is None or original["scenario_price_twd"] is None
        else revised["scenario_price_twd"] - original["scenario_price_twd"],
        "minority_policy": "constant_original_profit_ratio"
        if coupled_minority
        else "isolated_absolute_minority_held_fixed",
        "changes": changes,
        "revised_case": revised,
        "calculation": "same forecast engine; no agent-calculated EPS",
    }
