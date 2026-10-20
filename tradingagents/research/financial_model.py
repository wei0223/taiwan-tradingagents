"""Company-specific research questions and an explicit, auditable earnings waterfall."""

import re
from copy import deepcopy

from .scenarios import finite

PROFILES = {
    "foundry": {
        "label": "晶圓代工",
        "drivers": [
            "先進製程組合",
            "產能利用率",
            "先進封裝",
            "資本支出及折舊",
            "法說指引與客戶需求",
        ],
    },
    "thermal": {
        "label": "散熱與液冷",
        "drivers": [
            "液冷滲透與產品組合",
            "單機散熱價值",
            "客戶導入及認證",
            "良率",
            "擴產及固定成本",
        ],
    },
    "odm": {
        "label": "伺服器 ODM",
        "drivers": [
            "AI 伺服器出貨",
            "ODM 利潤率",
            "供應鏈付款條件",
            "營運資金及回款",
            "產能融資與稀釋",
        ],
    },
    "generic": {"label": "未分類", "drivers": ["量價組合", "成本結構", "營運資金", "股數"]},
}
DEFAULT_PROFILES = {"2330": "foundry", "3017": "thermal", "3231": "odm"}


def profile(stock, selected=None):
    key = selected or DEFAULT_PROFILES.get(stock, "generic")
    if key not in PROFILES:
        raise ValueError("Unknown company research profile")
    return {"key": key, **deepcopy(PROFILES[key]), "numeric_parameters_inherited": False}


def waterfall(inputs):
    """Depreciation is explicit; all other cost fields exclude depreciation.

    This is arithmetic, not evidence of causality. Monetary values are TWD, shares
    are weighted diluted shares, and tax is a rate applied to pretax income.
    """
    fields = (
        "revenue_twd",
        "variable_cogs_ratio",
        "fixed_cogs_ex_depreciation_twd",
        "cogs_depreciation_twd",
        "variable_opex_ratio",
        "fixed_opex_ex_depreciation_twd",
        "opex_depreciation_twd",
        "other_operating_twd",
        "nonoperating_twd",
        "tax_rate",
        "noncontrolling_profit_twd",
        "diluted_adjustment_twd",
        "diluted_weighted_shares",
    )
    if "noncontrolling_profit_fraction" in inputs:
        fields = tuple(
            "noncontrolling_profit_fraction" if k == "noncontrolling_profit_twd" else k
            for k in fields
        )
    if set(inputs) != set(fields):
        raise ValueError("Complete earnings waterfall inputs required")
    v = {k: finite(inputs[k]) for k in fields}
    nonnegative = fields[:7]
    if any(v[k] < 0 for k in nonnegative) or v["diluted_weighted_shares"] <= 0:
        raise ValueError("Invalid cost, revenue or weighted shares")
    if any(not 0 <= v[k] <= 1 for k in ("variable_cogs_ratio", "variable_opex_ratio", "tax_rate")):
        raise ValueError("Cost and tax ratios must be fractions")
    revenue = v["revenue_twd"]
    cogs = (
        revenue * v["variable_cogs_ratio"]
        + v["fixed_cogs_ex_depreciation_twd"]
        + v["cogs_depreciation_twd"]
    )
    gross = revenue - cogs
    opex = (
        revenue * v["variable_opex_ratio"]
        + v["fixed_opex_ex_depreciation_twd"]
        + v["opex_depreciation_twd"]
    )
    operating = gross - opex + v["other_operating_twd"]
    pretax = operating + v["nonoperating_twd"]
    tax = pretax * v["tax_rate"]
    net = pretax - tax
    if "noncontrolling_profit_fraction" in v:
        if not 0 <= v["noncontrolling_profit_fraction"] <= 1:
            raise ValueError("Invalid noncontrolling profit fraction")
        noncontrolling = net * v["noncontrolling_profit_fraction"]
    else:
        noncontrolling = v["noncontrolling_profit_twd"]
    parent = net - noncontrolling
    numerator = parent + v["diluted_adjustment_twd"]
    return {
        "revenue_twd": revenue,
        "cogs_twd": cogs,
        "gross_profit_twd": gross,
        "opex_twd": opex,
        "operating_profit_twd": operating,
        "pretax_profit_twd": pretax,
        "tax_twd": tax,
        "consolidated_profit_twd": net,
        "noncontrolling_profit_twd": noncontrolling,
        "parent_profit_twd": parent,
        "diluted_numerator_twd": numerator,
        "diluted_weighted_shares": v["diluted_weighted_shares"],
        "eps_twd": numerator / v["diluted_weighted_shares"],
        "gross_margin": gross / revenue if revenue else None,
        "operating_margin": operating / revenue if revenue else None,
        "limitation": "Conditional arithmetic; tax losses, production lags and cost classification require separate evidence",
    }


def conditional_case(case, evidence_values):
    """Facts equal source-bound values. Assumptions keep their basis and rationale."""
    if not re.fullmatch(r"\d{4}-Q[1-4]", case["period"]):
        raise ValueError("Explicit target quarter required")
    inputs = {}
    for key, item in case["inputs"].items():
        refs = item.get("evidence_ids", [])
        if not refs or any(ref not in evidence_values for ref in refs):
            raise ValueError("Each driver needs available evidence")
        value = finite(item["value"])
        if item.get("kind") == "fact":
            if len(refs) != 1 or value != evidence_values[refs[0]]:
                raise ValueError("Fact must equal its referenced numeric evidence")
        elif item.get("kind") != "assumption" or not item.get("rationale"):
            raise ValueError("Assumptions must be explicit and justified")
        inputs[key] = value
    return {
        "label": "conditional_not_official_forecast",
        "name": case.get("name", case["period"]),
        "cost_classification": case.get("cost_classification", "explicit_cost_split_assumptions"),
        "period": case["period"],
        "inputs": deepcopy(case["inputs"]),
        "calculation": waterfall(inputs),
        "confidence_interval": None,
        "probability_calibrated": False,
    }


def stress(case, changes):
    """Change cost, tax or share assumptions and recompute every downstream line."""
    base = waterfall(case)
    if not set(changes) <= set(case):
        raise ValueError("Unknown stress driver")
    revised = waterfall({**case, **changes})
    return {
        "baseline": base,
        "revised": revised,
        "eps_delta": revised["eps_twd"] - base["eps_twd"],
        "causal_effect_established": False,
    }


def break_even_gross_margin(inputs, target_eps):
    """Solve the same model for gross margin; all other assumptions stay frozen."""
    target = finite(target_eps)
    zero = waterfall({**inputs, "variable_cogs_ratio": 1})
    one = waterfall({**inputs, "variable_cogs_ratio": 0})
    sensitivity = one["eps_twd"] - zero["eps_twd"]
    if sensitivity <= 0 or inputs["revenue_twd"] <= 0:
        return {"status": "unavailable", "reason": "No positive gross-margin EPS sensitivity"}
    variable_gross = (target - zero["eps_twd"]) / sensitivity
    gross = (
        variable_gross
        - (inputs["fixed_cogs_ex_depreciation_twd"] + inputs["cogs_depreciation_twd"])
        / inputs["revenue_twd"]
    )
    return {
        "status": "conditional_sensitivity",
        "required_gross_margin": gross,
        "target_eps_twd": target,
        "eps_per_one_pp_gross_margin": sensitivity / 100,
        "within_variable_cost_ratio_bounds": 0 <= variable_gross <= 1,
        "limitation": "Other operating, tax, ownership and weighted-share assumptions held fixed; not a forecast threshold",
    }
