"""Four explicit quarterly waterfalls, not one quarter multiplied by four."""

from .financial_model import conditional_case, waterfall
from .scenarios import finite, next_quarters


def partial_case(case, evidence):
    unknown = [k for k, v in case["inputs"].items() if v.get("kind") == "unknown"]
    if not unknown:
        return conditional_case(case, evidence)
    # Validate the *whole* schema and each available number with the regular path.
    substituted = {**case, "inputs": {k: dict(v) for k, v in case["inputs"].items()}}
    for key in unknown:
        item = substituted["inputs"][key]
        if item.get("value") is not None or not item.get("rationale"):
            raise ValueError("Unknown driver must be null with a reason")
        item.update(kind="assumption", value=1 if key == "diluted_weighted_shares" else 0)
    checked = conditional_case(substituted, evidence)
    # Substitution is only a schema check, never a published EPS calculation.
    v = {k: (None if k in unknown else x["value"]) for k, x in case["inputs"].items()}

    def combine(keys, fn):
        return fn() if all(v.get(k) is not None for k in keys) else None

    revenue = v.get("revenue_twd")
    cogs = combine(
        (
            "revenue_twd",
            "variable_cogs_ratio",
            "fixed_cogs_ex_depreciation_twd",
            "cogs_depreciation_twd",
        ),
        lambda: (
            revenue * v["variable_cogs_ratio"]
            + v["fixed_cogs_ex_depreciation_twd"]
            + v["cogs_depreciation_twd"]
        ),
    )
    gross = revenue - cogs if revenue is not None and cogs is not None else None
    opex = combine(
        (
            "revenue_twd",
            "variable_opex_ratio",
            "fixed_opex_ex_depreciation_twd",
            "opex_depreciation_twd",
        ),
        lambda: (
            revenue * v["variable_opex_ratio"]
            + v["fixed_opex_ex_depreciation_twd"]
            + v["opex_depreciation_twd"]
        ),
    )
    operating = (
        gross - opex + v["other_operating_twd"]
        if gross is not None and opex is not None and v.get("other_operating_twd") is not None
        else None
    )
    pretax = (
        operating + v["nonoperating_twd"]
        if operating is not None and v.get("nonoperating_twd") is not None
        else None
    )
    net = (
        pretax * (1 - v["tax_rate"])
        if pretax is not None and v.get("tax_rate") is not None
        else None
    )
    nci = (
        v.get("noncontrolling_profit_twd")
        if "noncontrolling_profit_twd" in v
        else (
            net * v["noncontrolling_profit_fraction"]
            if net is not None and v.get("noncontrolling_profit_fraction") is not None
            else None
        )
    )
    parent = net - nci if net is not None and nci is not None else None
    checked.update(
        inputs=case["inputs"],
        label="incomplete_conditional_bridge_not_eps_forecast",
        missing_drivers=unknown,
        calculation={
            "revenue_twd": revenue,
            "cogs_twd": cogs,
            "gross_profit_twd": gross,
            "opex_twd": opex,
            "operating_profit_twd": operating,
            "pretax_profit_twd": pretax,
            "tax_rate": v.get("tax_rate"),
            "tax_twd": pretax - net if pretax is not None and net is not None else None,
            "consolidated_profit_twd": net,
            "noncontrolling_profit_twd": nci,
            "parent_profit_twd": parent,
            "diluted_numerator_twd": parent + v["diluted_adjustment_twd"]
            if parent is not None and v.get("diluted_adjustment_twd") is not None
            else None,
            "diluted_weighted_shares": None,
            "eps_twd": None,
        },
    )
    return checked


def build(as_of, scenarios, evidence, announced=()):
    targets = next_quarters(as_of)
    result = {
        "target_quarters": targets,
        "scenarios": {},
        "status": "unavailable_missing_quarterly_inputs",
        "label": "conditional_four_quarter_eps_sum_not_reported_annual_diluted_eps",
        "statistically_calibrated": False,
        "fair_value_established": False,
    }
    if not scenarios:
        return result
    if set(scenarios) != {"bear", "base", "bull"}:
        raise ValueError("Three explicit forward scenarios required")
    for name, cases in scenarios.items():
        if [c["period"] for c in cases] != targets or set(targets) & set(announced):
            raise ValueError("Exactly four unannounced target quarters in order required")
        rows = [partial_case(c, evidence) for c in cases]
        result["scenarios"][name] = {
            "quarters": rows,
            "revenue_twd": sum(c["calculation"]["revenue_twd"] for c in rows)
            if all(c["calculation"]["revenue_twd"] is not None for c in rows)
            else None,
            "eps_sum_twd": sum(c["calculation"]["eps_twd"] for c in rows)
            if all(c["calculation"]["eps_twd"] is not None for c in rows)
            else None,
            "annual_weighted_eps": None,
        }
    result["status"] = "conditional_four_quarter_models"
    if any(s["eps_sum_twd"] is None for s in result["scenarios"].values()):
        result["status"] = "partial_four_quarter_bridges_missing_verified_drivers"
    return result


def reverse(price, pe, quarters):
    """Solve a common revenue multiplier and a common gross-margin increment.

    All other quarterly assumptions are held fixed. This is an equation, not
    observed consensus or a probability of attaining the implied requirements.
    """
    price, pe = finite(price), finite(pe)
    if price <= 0 or pe <= 0 or len(quarters) != 4:
        raise ValueError("Positive price/PE and four quarters required")
    required = price / pe
    original = [{k: x["value"] for k, x in q["inputs"].items()} for q in quarters]

    def total(multiplier=1, gm_add=0):
        values = []
        for source in original:
            v = dict(source)
            v["revenue_twd"] *= multiplier
            v["variable_cogs_ratio"] -= gm_add
            values.append(waterfall(v)["eps_twd"])
        return sum(values)

    base, zero, double = total(), total(0), total(2)
    slope = (double - zero) / 2
    mult = (required - zero) / slope if slope > 0 else None
    # Choose a feasible finite difference; a zero cost ratio permits downward GM only.
    step = 0.001 if min(v["variable_cogs_ratio"] for v in original) >= 0.001 else -0.001
    if step < 0 and max(v["variable_cogs_ratio"] for v in original) > 0.999:
        gm = None
    else:
        gm_slope = (total(gm_add=step) - base) / step
        gm = (required - base) / gm_slope if gm_slope > 0 else None
    feasible_gm = gm is not None and all(0 <= v["variable_cogs_ratio"] - gm <= 1 for v in original)
    return {
        "assumed_pe": pe,
        "required_eps_sum_twd": required,
        "base_eps_sum_twd": base,
        "required_change_vs_base_pct": (required / base - 1) * 100 if base > 0 else None,
        "required_revenue_multiplier": mult if mult is not None and mult >= 0 else None,
        "required_revenue_growth_vs_base_pct": (mult - 1) * 100
        if mult is not None and mult >= 0
        else None,
        "required_common_gross_margin_change_pp": gm * 100 if feasible_gm else None,
        "label": "conditional_equations_other_assumptions_fixed_not_market_consensus",
    }


def expectations(model, packets, stock=None):
    """Compare only identical company, metric, currency, period and diluted basis."""
    comparisons = []
    for p in packets:
        if p["kind"] not in {"management_guidance", "consensus"} or not p.get("source_id"):
            raise ValueError("Expectation provenance required")
        if (
            not stock
            or p.get("stock_id") != stock
            or p.get("currency") != "TWD"
            or not p.get("values_reviewed")
            or not p.get("reviewed_by")
            or not p.get("locator")
            or not p.get("basis_reviewed")
            or p.get("metric") != "eps_twd"
            or p.get("periods") != model["target_quarters"]
            or p.get("basis") != "sum_quarterly_diluted_eps"
        ):
            comparisons.append({"status": "not_comparable", "source": p})
            continue
        low, high = finite(p["low"]), finite(p["high"])
        if low > high:
            raise ValueError("Expectation interval reversed")
        base = model.get("scenarios", {}).get("base", {}).get("eps_sum_twd")
        comparisons.append(
            {
                "status": "comparable" if base is not None else "model_unavailable",
                "source": p,
                "model_minus_midpoint": base - (low + high) / 2 if base is not None else None,
            }
        )
    return {
        "comparisons": comparisons,
        "consensus_verified": any(
            x["status"] == "comparable" and x["source"]["kind"] == "consensus" for x in comparisons
        ),
    }
