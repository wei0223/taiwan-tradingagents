"""Deterministic ratios from selected, source-reviewed facts; no period or EPS guessing."""

from decimal import Decimal

from .bundle import fingerprint

RATIOS = {
    "gross_margin_pct": ("gross_profit", "revenue", 100),
    "operating_margin_pct": ("operating_profit", "revenue", 100),
    "parent_net_margin_pct": ("parent_profit", "revenue", 100),
    "effective_tax_rate_pct": ("tax", "pretax_profit", 100),
    "parent_profit_share_pct": ("parent_profit", "consolidated_profit", 100),
    "cfo_revenue_pct": ("cfo", "revenue", 100),
    "cfo_parent_profit_ratio": ("cfo", "parent_profit", 1),
}


def build(company):
    groups = {}
    for fact in company["facts"]:
        if fact["kind"] != "numeric" or fact["basis"] == "balance":
            continue
        key = tuple(fact[k] for k in ("period_start", "period_end", "basis", "scope"))
        group = groups.setdefault(key, {})
        if fact["metric"] in group:
            raise ValueError("Select one fact version before calculating")
        group[fact["metric"]] = fact
    rows = []
    for period, facts in sorted(groups.items()):
        metrics = {}
        for name, (numerator, denominator, scale) in RATIOS.items():
            pair = [facts.get(numerator), facts.get(denominator)]
            reason = "missing_same_period_amounts"
            value = None
            if all(pair):
                a, b = pair
                if a["unit"] != "TWD" or b["unit"] != "TWD":
                    reason = "unit_mismatch"
                elif a["source_sha256"] != b["source_sha256"]:
                    reason = "source_versions_not_proven_compatible"
                elif Decimal(b["normalized_value"]) == 0:
                    reason = "zero_denominator"
                else:
                    value = float(
                        Decimal(a["normalized_value"]) / Decimal(b["normalized_value"]) * scale
                    )
                    reason = None
            metrics[name] = {
                "value": value,
                "status": "available" if value is not None else "unavailable",
                "reason": reason,
                "source_fact_ids": [f["id"] for f in pair if f],
                "definition": f"{numerator}/{denominator}*{scale}",
            }
        rows.append(
            {
                **dict(zip(("period_start", "period_end", "basis", "scope"), period, strict=True)),
                "metrics": metrics,
            }
        )
    return {
        "schema_version": 1,
        "stock_id": company["stock_id"],
        "as_of": company["as_of"],
        "company_snapshot_sha256": fingerprint(company),
        "periods": rows,
        "policy": "selected direct facts; exact period/scope/TWD/same-source version only; no EPS or share differencing",
        "eps_forecast_calculated": False,
        "paid_llm_api": False,
    }
