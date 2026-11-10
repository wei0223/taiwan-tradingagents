"""Present existing source-reviewed valuations on separate research horizons."""

from .bundle import fingerprint
from .output_registry import materialize
from .scenarios import finite, next_quarters

SCENARIOS = ("bear", "base", "bull")
LABELS = {"bear": "悲觀", "base": "基準", "bull": "樂觀"}


def latest_eps(company):
    rows = [
        f for f in (company or {}).get("facts", [])
        if f["metric"] == "diluted_eps" and f["kind"] == "numeric"
        and f.get("derivation") == "direct" and f["basis"] == "single_quarter"
        and f["scope"] == "consolidated" and f["unit"] == "TWD/share"
    ]
    if not rows:
        return None
    fact = max(rows, key=lambda f: f["period_end"])
    return {k: fact[k] for k in ("id", "period_start", "period_end", "unit", "normalized_value", "source_sha256")}


def build(data, company):
    as_of = data["as_of"]
    raw_model = data.get("forward_estimate") or {}
    registry = data.get("v6_review", {}).get("current_output_registry")
    model = materialize(raw_model, registry) if raw_model and registry else raw_model
    periods = model.get("target_quarters", [])
    accepted = (
        model.get("status") == "conditional_estimate_reviewed_assumptions"
        and model["stock_id"] == data["stock_id"] and periods == next_quarters(as_of)
        and set(model["scenarios"]) == set(SCENARIOS)
        and all([q["period"] for q in model["scenarios"][name]["quarters"]] == periods
                for name in SCENARIOS)
    )
    axes = model.get("pe_sensitivity", []) if accepted else []
    price = model.get("price_twd")
    usable_price = bool(
        accepted and model.get("price_fresh") and model.get("price_date")
        and model["price_date"] <= as_of
        and model.get("price_basis") == "unadjusted_TWD_per_common_share"
        and price is not None and finite(price) > 0
    )
    blockers = []
    if not accepted:
        blockers.append("same_issuer_reviewed_four_quarter_model_unavailable")
    elif not usable_price:
        blockers.append("qualified_unadjusted_market_price_unavailable")
    if accepted and not usable_price:
        price = None
    ttm = model.get("ttm", {})
    trailing_eps = ttm.get("eps_twd") if ttm.get("status") == "available_reported_quarterly_sum" else None
    rows = {}
    for name in SCENARIOS:
        item = model["scenarios"][name] if accepted else {}
        eps = item.get("four_quarter_eps_sum_twd")
        conditional_prices = item.get("price_sensitivity_twd", []) if usable_price else []
        rows[name] = {
            "status": "conditional" if eps is not None else "unavailable",
            "eps_sum_twd": eps,
            "price_sensitivity": [{"assumed_pe": pe, "conditional_price_twd": value}
                                  for pe, value in zip(axes, conditional_prices, strict=True)
                                  if value is not None] if conditional_prices else [],
        }
    history = data.get("extension", {}).get("valuation", {}).get("reported_history", {})
    base_eps = rows["base"]["eps_sum_twd"]
    implied = []
    if usable_price:
        for pe in axes:
            required = price / pe
            implied.append({
                "assumed_pe": pe, "required_eps_twd": required,
                "required_change_vs_reported_four_quarters_pct": (
                    (required / trailing_eps - 1) * 100 if trailing_eps and trailing_eps > 0 else None
                ),
                "base_case_minus_required_eps_twd": base_eps - required if base_eps is not None else None,
            })
    actual_eps = latest_eps(company)
    public_eps = data.get("public_eps", {})
    valuation = {
        "stock_id": data["stock_id"], "as_of": as_of,
        "status": "conditional" if implied and all(row["price_sensitivity"] for row in rows.values())
                  else "limited" if accepted or actual_eps or public_eps.get("estimates") else "unavailable",
        "reported_quarterly_eps": actual_eps,
        "reported_four_quarter_eps_sum_twd": trailing_eps,
        "public_annual_consensus": public_eps.get("estimates", []),
        "blockers": blockers,
        "forecast_basis": "four_consecutive_quarter_conditional_diluted_eps_sum" if accepted else None,
        "periods": periods if accepted else [],
        "scenarios": rows,
        "model_sha256": raw_model.get("model_sha256"),
        "parameter_sources_ref": "forward-estimate.json#/parameter_register" if accepted else None,
        "price_reference": {"price_twd": price, "date": model.get("price_date"),
                            "basis": model.get("price_basis"), "eligible": usable_price},
        "multiple_basis": {"assumption_rationale": model.get("pe_rationale") if accepted else None,
                           "model_risks": model.get("risks", []) if accepted else [],
                           "reported_history_context": history,
                           "fair_value_verified": False},
        "implied_growth_conditions": implied,
    }
    claims = data.get("claim_validation", {}).get("claims", {})
    core = next((row for row in claims.values() if row["use"] == "core" and row["job"].startswith("fundamental")), {})
    short = claims.get("decision.ruling_short_term", {})
    horizons = {
        "stock_id": data["stock_id"], "as_of": as_of,
        "enterprise": {"status": core.get("status", "unavailable"), "claim": core.get("claim"),
                       "claim_id": core.get("id"), "scope": "reported_business_and_medium_long_term_conditions"},
        "valuation": {"status": valuation["status"], "scope": "next_four_quarters_conditional",
                      "valuation_sha256": fingerprint(valuation)},
        "short_term": {"status": short.get("status", "unavailable"), "claim": short.get("claim"),
                       "claim_id": short.get("id"), "market_session": data["dynamics"].get("latest_session"),
                       "scope": "short_term_price_observation"},
    }
    return valuation, horizons


def display_rows(valuation, horizons):
    def value(x):
        return f"{x:.2f}" if x is not None else "不可用"

    rows = [[LABELS[name], value(row["eps_sum_twd"]),
             "、".join(f'{axis["assumed_pe"]:g} 倍 → {value(axis["conditional_price_twd"])} 元'
                      for axis in row["price_sensitivity"]) or "不可用"]
            for name, row in valuation["scenarios"].items()]
    implied = [[f'{r["assumed_pe"]:g}', value(r["required_eps_twd"]),
                value(r["required_change_vs_reported_four_quarters_pct"]),
                value(r["base_case_minus_required_eps_twd"])] for r in valuation["implied_growth_conditions"]]
    times = [["企業中長期", horizons["enterprise"]["status"], horizons["enterprise"]["claim"] or "未取得已審基本面主張"],
             ["四季估值", valuation["status"], valuation["multiple_basis"]["assumption_rationale"] or "缺少合格四季模型及倍數依據"],
             ["短期價格", horizons["short_term"]["status"], horizons["short_term"]["claim"] or "短期價格主張待核"]]
    return rows, implied, times
