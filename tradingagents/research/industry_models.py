"""Different operating equations feeding the common accounting waterfall.

Volume and price inputs are conditional assumptions unless source-bound facts.
A missing product, yield or capacity observation is never filled by narrative.
"""

from .scenarios import finite

FIELDS = {
    "foundry": (
        "capacity_wafers",
        "utilization",
        "advanced_mix",
        "advanced_asp_twd",
        "mature_asp_twd",
        "wafer_cash_cost_twd",
        "packaging_capacity_units",
        "packaging_utilization",
        "packaging_asp_twd",
        "packaging_cash_cost_twd",
        "depreciation_twd",
        "overseas_incremental_cost_twd",
    ),
    "thermal": (
        "server_units",
        "liquid_penetration",
        "supplier_share",
        "liquid_asp_twd",
        "air_asp_twd",
        "liquid_material_cost_twd",
        "air_material_cost_twd",
        "yield",
        "capacity_units",
        "fixed_cost_twd",
        "depreciation_twd",
        "other_revenue_twd",
        "other_cogs_twd",
    ),
    "odm": (
        "ai_units",
        "other_units",
        "ai_asp_twd",
        "other_asp_twd",
        "ai_unit_cost_twd",
        "other_unit_cost_twd",
        "depreciation_twd",
        "dso_days",
        "dio_days",
        "dpo_days",
        "opening_operating_working_capital_twd",
        "cash_available_twd",
        "annual_financing_rate",
        "period_days",
    ),
}


def calculate(kind, values):
    if kind not in FIELDS:
        raise ValueError("Unsupported operating model")
    missing = [k for k in FIELDS[kind] if values.get(k) is None]
    if missing:
        return {"status": "unavailable_missing_drivers", "missing": missing, "profile": kind}
    if set(values) != set(FIELDS[kind]):
        raise ValueError("Unexpected industry driver")
    v = {k: finite(values[k]) for k in FIELDS[kind]}
    if any(x < 0 for x in v.values()):
        raise ValueError("Industry inputs must be nonnegative")
    for key in (
        "utilization",
        "advanced_mix",
        "packaging_utilization",
        "liquid_penetration",
        "supplier_share",
        "yield",
    ):
        if key in v and not 0 <= v[key] <= 1:
            raise ValueError("Invalid operating fraction")
    detail = {}
    if kind == "foundry":
        wafers = v["capacity_wafers"] * v["utilization"]
        wafer_revenue = wafers * (
            v["advanced_mix"] * v["advanced_asp_twd"]
            + (1 - v["advanced_mix"]) * v["mature_asp_twd"]
        )
        packaging_units = v["packaging_capacity_units"] * v["packaging_utilization"]
        revenue = wafer_revenue + packaging_units * v["packaging_asp_twd"]
        cogs = (
            wafers * v["wafer_cash_cost_twd"]
            + packaging_units * v["packaging_cash_cost_twd"]
            + v["depreciation_twd"]
            + v["overseas_incremental_cost_twd"]
        )
        detail = {"wafer_revenue_twd": wafer_revenue, "packaging_units": packaging_units}
    elif kind == "thermal":
        if v["yield"] == 0:
            raise ValueError("Positive production yield required")
        units = v["server_units"] * v["supplier_share"]
        good_capacity = v["capacity_units"] * v["yield"]
        if units > good_capacity:
            return {
                "status": "infeasible_capacity",
                "required_good_units": units,
                "good_capacity_units": good_capacity,
            }
        mix = v["liquid_penetration"]
        revenue = (
            units * (mix * v["liquid_asp_twd"] + (1 - mix) * v["air_asp_twd"])
            + v["other_revenue_twd"]
        )
        cogs = (
            units
            / v["yield"]
            * (mix * v["liquid_material_cost_twd"] + (1 - mix) * v["air_material_cost_twd"])
            + v["fixed_cost_twd"]
            + v["depreciation_twd"]
            + v["other_cogs_twd"]
        )
        detail = {"supplier_good_units": units, "good_capacity_units": good_capacity}
    else:
        if v["period_days"] <= 0 or v["annual_financing_rate"] > 1:
            raise ValueError("Invalid financing period/rate")
        revenue = v["ai_units"] * v["ai_asp_twd"] + v["other_units"] * v["other_asp_twd"]
        cash_cogs = (
            v["ai_units"] * v["ai_unit_cost_twd"] + v["other_units"] * v["other_unit_cost_twd"]
        )
        cogs = cash_cogs + v["depreciation_twd"]
        ar = revenue / v["period_days"] * v["dso_days"]
        inventory = cash_cogs / v["period_days"] * v["dio_days"]
        payable = cash_cogs / v["period_days"] * v["dpo_days"]
        wc = ar + inventory - payable
        funding = max(0, wc - v["opening_operating_working_capital_twd"] - v["cash_available_twd"])
        detail = {
            "receivables_twd": ar,
            "inventory_twd": inventory,
            "payables_twd": payable,
            "operating_working_capital_twd": wc,
            "incremental_financing_twd": funding,
            "financing_cost_twd": funding * v["annual_financing_rate"] * v["period_days"] / 365,
            "collection_verified": False,
        }
    return {
        "status": "conditional_operating_equation",
        "profile": kind,
        "revenue_twd": revenue,
        "cogs_twd": cogs,
        "gross_profit_twd": revenue - cogs,
        "detail": detail,
        "causality_empirically_verified": False,
    }


def reviewed(kind, inputs, evidence):
    if kind == "generic" and not inputs:
        return {
            "status": "unavailable_unclassified_company",
            "profile": kind,
            "missing": ["company_specific_operating_classification"],
        }
    values = {}
    for key, item in inputs.items():
        if key not in FIELDS.get(kind, ()):
            raise ValueError("Unknown industry input")
        if item is None:
            values[key] = None
            continue
        refs = item.get("evidence_ids", [])
        if not refs or any(x not in evidence for x in refs):
            raise ValueError("Industry driver needs eligible evidence")
        value = finite(item["value"])
        if item.get("kind") == "fact":
            if len(refs) != 1 or value != evidence[refs[0]]:
                raise ValueError("Industry fact differs from source")
        elif item.get("kind") != "assumption" or not item.get("rationale"):
            raise ValueError("Industry assumption basis required")
        values[key] = value
    return calculate(kind, values)
