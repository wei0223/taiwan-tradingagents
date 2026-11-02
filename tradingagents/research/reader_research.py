"""Reader-facing conditional economics and aligned market observations, never signals."""

import base64
import copy
import hashlib
import math

from .financial_model import waterfall


def reverse_conditions(cases, price, multiples):
    """Solve the same four-quarter waterfall, changing ONE driver at a time."""
    if len(cases) != 4:
        return {"status": "unavailable_four_distinct_quarters_required", "rows": []}
    periods = [c["period"] for c in cases]
    indices = [int(p[:4]) * 4 + int(p[-1]) - 1 for p in periods]
    if indices != list(range(indices[0], indices[0] + 4)):
        return {"status": "unavailable_four_distinct_quarters_required", "rows": []}
    base = [{k: v["value"] for k, v in c["inputs"].items()} for c in cases]

    def earnings(inputs):
        return sum(waterfall(v)["eps_twd"] for v in inputs)

    eps = earnings(base)
    if not math.isfinite(price) or eps <= 0 or price <= 0:
        return {"status": "unavailable_positive_earnings_required", "rows": []}
    revenue = sum(v["revenue_twd"] for v in base)
    gm = sum(waterfall(v)["gross_profit_twd"] for v in base) / revenue
    doubled = [{**v, "revenue_twd": v["revenue_twd"] * 2} for v in base]
    revenue_slope = earnings(doubled) - eps
    # Lower the gross margin one point; remains valid even if the base margin is zero.
    shifted = [
        {**v, "other_operating_twd": v["other_operating_twd"] + v["revenue_twd"] * 0.01}
        for v in base
    ]
    margin_point_eps = earnings(shifted) - eps
    rows = []
    for pe in multiples:
        if not math.isfinite(pe) or pe <= 0:
            raise ValueError("Positive finite PE axis required")
        required = price / pe
        scale = 1 + (required - eps) / revenue_slope if revenue_slope > 0 else None
        shift = (required - eps) / margin_point_eps if margin_point_eps > 0 else None
        if shift is not None and any(
            not 0 <= v["variable_cogs_ratio"] - shift / 100 <= 1 for v in base
        ):
            shift = None
        rows.append(
            {
                "assumed_pe": pe,
                "required_eps": required,
                "required_revenue_twd": revenue * scale
                if scale is not None and scale > 0
                else None,
                "revenue_change_pct": (scale - 1) * 100
                if scale is not None and scale > 0
                else None,
                "gross_margin_shift_pp": shift,
                "required_weighted_gross_margin_pct": gm * 100 + shift
                if shift is not None
                else None,
                "share_scale": eps / required,
                "share_change_pct": (eps / required - 1) * 100,
                "share_note": "Pure denominator sensitivity; not a feasible issuance/buyback recommendation",
                "conditional_price": eps * pe,
            }
        )
    shocks = []
    for name, changes in [
        ("毛利率下降1個百分點", {"variable_cogs_ratio_delta": 0.01}),
        ("四季新增折舊合計10億元", {"cogs_depreciation_twd_delta": 250000000}),
        ("稀釋加權股數增加3%", {"shares_factor": 1.03}),
    ]:
        revised = copy.deepcopy(base)
        for v in revised:
            if "variable_cogs_ratio_delta" in changes:
                v["variable_cogs_ratio"] += changes["variable_cogs_ratio_delta"]
            if "cogs_depreciation_twd_delta" in changes:
                v["cogs_depreciation_twd"] += changes["cogs_depreciation_twd_delta"]
            if "shares_factor" in changes:
                v["diluted_weighted_shares"] *= changes["shares_factor"]
        value = earnings(revised)
        shocks.append({"name": name, "eps": value, "delta_eps": value - eps})
    return {
        "status": "conditional_equations_only",
        "base_eps": eps,
        "base_revenue_twd": revenue,
        "base_gross_margin_pct": gm * 100,
        "eps_per_margin_point": margin_point_eps,
        "rows": rows,
        "shocks": shocks,
        "periods": periods,
        "joint_scenarios": False,
        "limitation": "Each inverse changes one driver, with tax/ownership/other assumptions fixed; neither fair PE nor market consensus is established",
    }


def market_alignment(snapshot, window=20):
    from .dynamics import rolling

    prices = sorted(
        snapshot["datasets"].get("TaiwanStockPrice", {}).get("records", []),
        key=lambda r: r["data_date"],
    )
    if len({r["data_date"] for r in prices}) != len(prices):
        raise ValueError("Duplicate stock sessions")
    days = [r for r in prices if r["data_date"] < snapshot["as_of"]][-window:]
    calendar_verified = False
    calendar = snapshot.get("market_calendar")
    if calendar and calendar.get("status") == "ok":
        from tradingagents.dataflows.taiwan.calendar import verify_calendar

        sessions = [
            d
            for d in verify_calendar(calendar, snapshot["instrument"]["exchange"])
            if d < snapshot["as_of"]
        ][-window:]
        if [r["data_date"] for r in days] != sessions:
            return {"status": "unavailable_missing_price_sessions", "series": []}
        calendar_verified = True
    if not days:
        return {"status": "unavailable", "series": []}
    institutions = (
        snapshot["datasets"].get("TaiwanStockInstitutionalInvestorsBuySell", {}).get("records", [])
    )
    identities = [(r["data_date"], r["values"]["name"]) for r in institutions]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate institutional date/category")
    # Reuse the provider's reviewed grouping and coverage rules for each session.
    from tradingagents.dataflows.taiwan.calculations import institutional_metrics

    series = []
    prior = {
        prices[i]["data_date"]: prices[i - 1]["values"]["close"] for i in range(1, len(prices))
    }
    for row in days:
        date = row["data_date"]
        subset = [r for r in institutions if r["data_date"] == date]
        groups = institutional_metrics(subset, [row], snapshot["instrument"]["exchange"])["groups"]
        close, previous = row["values"]["close"], prior.get(date)
        series.append(
            {
                "date": date,
                "close": close,
                "high": row["values"]["high"],
                "low": row["values"]["low"],
                "volume": row["values"]["volume"],
                "return_pct": (close / previous - 1) * 100 if previous else None,
                **{
                    k: groups.get(k, {}).get("latest_net_shares")
                    for k in ["foreign", "investment_trust", "dealer"]
                },
            }
        )
    complete = all(
        r[k] is not None for r in series for k in ["foreign", "investment_trust", "dealer"]
    )
    bins = []
    closes = sorted(r["close"] for r in series)
    cuts = [closes[len(closes) // 3], closes[2 * len(closes) // 3]]
    for i, label in enumerate(["低收盤區", "中收盤區", "高收盤區"]):
        selected = [
            r
            for r in series
            if (0 if r["close"] < cuts[0] else 1 if r["close"] < cuts[1] else 2) == i
        ]
        bins.append(
            {
                "label": label,
                "days": len(selected),
                "low": min((r["close"] for r in selected), default=None),
                "high": max((r["close"] for r in selected), default=None),
                **{
                    k: sum(r[k] for r in selected)
                    if all(r[k] is not None for r in selected) and selected
                    else None
                    for k in ["foreign", "investment_trust"]
                },
            }
        )
    trust_buy = [
        r
        for r in series
        if r["investment_trust"] is not None
        and r["investment_trust"] > 0
        and r["return_pct"] is not None
    ]
    updown = []
    for key, label in [(True, "上漲日"), (False, "下跌日")]:
        chosen = [
            r
            for r in series
            if r["return_pct"] is not None and (r["return_pct"] > 0 if key else r["return_pct"] < 0)
        ]
        updown.append(
            {
                "label": label,
                "days": len(chosen),
                **{
                    k: sum(r[k] for r in chosen)
                    if chosen and all(r[k] is not None for r in chosen)
                    else None
                    for k in ["foreign", "investment_trust"]
                },
            }
        )
    return {
        "status": "available"
        if complete and calendar_verified
        else "partial_unverified_calendar_or_categories",
        "series": series,
        "start": series[0]["date"],
        "end": series[-1]["date"],
        "sessions": len(series),
        "price_bins": bins,
        "up_down": updown,
        "trust_buy_days": len(trust_buy)
        if all(r["investment_trust"] is not None for r in series)
        else None,
        "trust_buy_down_days": sum(r["return_pct"] < 0 for r in trust_buy)
        if all(r["investment_trust"] is not None for r in series)
        else None,
        "price_change_pct": (series[-1]["close"] / prior[series[0]["date"]] - 1) * 100
        if series[0]["date"] in prior
        else None,
        "close_bins_are_not_cost_basis": True,
        "causality_established": False,
        "rolling": rolling(snapshot).get("windows", {}).get(str(window), {})
        if calendar_verified
        else {},
    }


def build(snapshot, extension, spec=None):
    spec = spec or {}
    if spec and (
        spec.get("stock_id") != snapshot["instrument"]["stock_id"]
        or spec.get("as_of") != snapshot["as_of"]
    ):
        raise ValueError("Reader research company/date mismatch")
    from .review import _end_of_day
    from .scenarios import timestamp

    for doc in spec.get("sources", {}).values():
        raw = base64.b64decode(doc["content_base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != doc["sha256"] or not raw.startswith(b"%PDF"):
            raise ValueError("Reader source PDF/hash mismatch")
        published = doc.get("published_at")
        instant = (
            _end_of_day(published)
            if published and len(published) == 10
            else timestamp(published or doc["captured_at"])
        )
        if max(instant, timestamp(doc["captured_at"])) > _end_of_day(snapshot["as_of"]):
            raise ValueError("Reader source after cutoff")
        if not doc.get("source_url", "").startswith("https://") or not doc.get("pages"):
            raise ValueError("Reader source URL/page required")
    for card in spec.get("industry_cards", []):
        if not card.get("source_ids") or any(
            k not in spec.get("sources", {}) for k in card["source_ids"]
        ):
            raise ValueError("Industry claim without packaged source")
    peer_periods = set()
    for peer in spec.get("peers", []):
        doc = spec.get("sources", {}).get(peer["source_id"], {})
        if (
            doc.get("stock_id") != peer["stock_id"]
            or peer.get("scope") != "consolidated"
            or peer.get("unit") != "TWD"
        ):
            raise ValueError("Peer identity/scope/unit mismatch")
        if peer.get("period_basis") != "single_quarter":
            raise ValueError("Peer comparison requires single quarters")
        peer_periods.add((peer["period_start"], peer["period_end"]))
    if len(peer_periods) > 1:
        raise ValueError("Peer financial periods differ")
    rows = snapshot["datasets"].get("TaiwanStockPrice", {}).get("records", [])
    rows = [r for r in rows if r["data_date"] < snapshot["as_of"]]
    price = max(rows, key=lambda r: r["data_date"])["values"]["close"] if rows else None
    reverse = (
        reverse_conditions(extension["financial_cases"], price, spec.get("pe_axes", [24, 30, 36]))
        if price
        else {"status": "unavailable", "rows": []}
    )
    editorial = copy.deepcopy(spec)
    for doc in editorial.get("sources", {}).values():
        doc.pop("content_base64", None)
        doc.pop("text_pages", None)
    return {
        "stock_id": snapshot["instrument"]["stock_id"],
        "as_of": snapshot["as_of"],
        "reverse": reverse,
        "market": market_alignment(snapshot),
        "editorial": editorial,
        "financial_qualification_changed": False,
    }
