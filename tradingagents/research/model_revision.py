"""Deterministic version-to-version bridges; attribution order is explicit."""

import math

from .bundle import fingerprint
from .financial_model import waterfall

OUTPUTS = ("revenue_twd", "gross_profit_twd", "opex_twd", "operating_profit_twd",
           "pretax_profit_twd", "tax_twd", "consolidated_profit_twd",
           "noncontrolling_profit_twd", "parent_profit_twd", "diluted_numerator_twd",
           "diluted_weighted_shares", "eps_twd")


def calculate(inputs):
    missing = [k for k, v in inputs.items() if v is None]
    if set(missing) - {"diluted_weighted_shares", "diluted_adjustment_twd"}:
        raise ValueError("Unknown monetary driver prevents a revision bridge")
    # The two EPS-only unknowns do not affect operating/parent profit. Dummy values
    # validate arithmetic only; all dependent published outputs stay explicitly null.
    result = waterfall({k: (1 if k == "diluted_weighted_shares" else 0) if v is None else v
                        for k, v in inputs.items()})
    if "diluted_adjustment_twd" in missing:
        result["diluted_numerator_twd"] = None
    if "diluted_weighted_shares" in missing:
        result["diluted_weighted_shares"] = None
    if missing:
        result["eps_twd"] = None
    return result


def bridge(previous, current):
    if set(previous) != set(current):
        raise ValueError("Changing NCI formula/schema requires an explicit separate structural bridge")
    before, after = calculate(previous), calculate(current)
    state, last, steps = dict(previous), before, []
    # Dict insertion order is not a model choice: fix alphabetical order for replay.
    for key in sorted(k for k in previous if previous[k] != current[k]):
        state[key] = current[key]
        now = calculate(state)
        delta = {k: now[k] - last[k] if now[k] is not None and last[k] is not None else None
                 for k in OUTPUTS}
        steps.append({"driver": key, "old": previous[key], "new": current[key],
                      "delta": delta, "after": {k: now[k] for k in OUTPUTS}})
        last = now
    totals = {k: after[k] - before[k] if after[k] is not None and before[k] is not None else None
              for k in OUTPUTS}
    for key, delta in totals.items():
        if delta is not None:
            assert math.isclose(sum(s["delta"][key] or 0 for s in steps), delta, rel_tol=1e-10, abs_tol=1e-5)
    return {"previous_inputs": previous, "current_inputs": current,
            "previous_sha256": fingerprint(previous), "current_sha256": fingerprint(current),
            "before": {k: before[k] for k in OUTPUTS}, "after": {k: after[k] for k in OUTPUTS},
            "steps": steps, "total_delta": totals, "reconciled": True,
            "method": "alphabetical_order_telescoping_not_unique_causal_attribution",
            "forecast_accuracy_improved": None}


def compare(previous, current):
    rows = []
    for scenario, model in current.get("scenarios", {}).items():
        old = {c["period"]: c for c in previous.get("scenarios", {}).get(scenario, {}).get("quarters", [])}
        for case in model["quarters"]:
            if case["period"] not in old:
                continue
            a = {k: v["value"] for k, v in old[case["period"]]["inputs"].items()}
            b = {k: v["value"] for k, v in case["inputs"].items()}
            try:
                change = bridge(a, b)
            except ValueError as exc:
                change = {"status": "unavailable_prior_structure_or_monetary_driver",
                          "reason": str(exc), "previous_inputs": a, "current_inputs": b,
                          "previous_sha256": fingerprint(a), "current_sha256": fingerprint(b),
                          "reconciled": False, "steps": []}
            rows.append({"scenario": scenario, "period": case["period"], **change})
    return {"status": "compared" if rows else "no_matching_prior_periods", "rows": rows}
