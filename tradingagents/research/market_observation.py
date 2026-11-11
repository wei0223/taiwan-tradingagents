"""Opt-in same-day public capture supplement; preserve the original next-day snapshot."""

from zoneinfo import ZoneInfo

from tradingagents.dataflows.taiwan.calculations import technical
from tradingagents.dataflows.taiwan.provider import PRICE, TaiwanMarketDataProvider

from .bundle import fingerprint
from .knowledge import exact


def latest(snapshot, ctx):
    result = snapshot["datasets"].get(PRICE, {})
    capture = exact(result["retrieved_at"])
    cutoff = exact(ctx["cutoff_at"])
    local = capture.astimezone(ZoneInfo("Asia/Taipei"))
    if (
        ctx["mode"] != "latest_research"
        or capture > cutoff
        or local.date().isoformat() != snapshot["as_of"]
        or (local.hour, local.minute) < (16, 30)
    ):
        return {"status": "unavailable_same_day_capture_not_verified"}
    stock = snapshot["instrument"]["stock_id"]
    records = []
    seen = set()
    for row in result.get("raw_rows", []):
        if row["date"] > snapshot["as_of"]:
            continue
        if str(row.get("stock_id")) != stock or row["date"] in seen:
            raise ValueError("Same-day quote source identity/uniqueness mismatch")
        seen.add(row["date"])
        period, values, units = TaiwanMarketDataProvider._normalize(PRICE, row)
        records.append(
            {"data_date": row["date"], "period": period, "values": values, "units": units}
        )
    records.sort(key=lambda r: r["data_date"])
    if not records or records[-1]["data_date"] != snapshot["as_of"]:
        return {"status": "unavailable_no_same_day_row"}
    return {
        "status": "known_from_verified_public_capture_only",
        "stock_id": stock,
        "source_url": "https://api.finmindtrade.com/api/v4/data",
        "captured_at": capture.isoformat(),
        "raw_rows_sha256": fingerprint(result["raw_rows"]),
        "price_basis": "unadjusted_not_total_return",
        "latest": records[-1],
        "technical": technical(records),
        "original_normalized_last_date": max(
            (r["data_date"] for r in result.get("records", [])), default=None
        ),
        "publication_timestamp": None,
        "initial_vintage_proven": False,
        "trade_signal_issued": False,
        "limitation": "Separate current-capture observation; never backdate it or relabel original adjusted next-day signals",
    }


def post_close_inputs(snapshot):
    """Build a calculation-only view from the three aligned source captures."""
    import copy
    import json
    from datetime import date

    from tradingagents.dataflows.taiwan.calendar import month_sessions, verify_calendar
    from tradingagents.dataflows.taiwan.extensions import ADJUSTED, normalize
    from tradingagents.dataflows.taiwan.provider import INSTITUTIONS

    from .publishing import market_gate
    from .review import _end_of_day

    day = snapshot["as_of"]
    calendar = snapshot.get("market_calendar", {})
    if calendar.get("status") != "ok" or calendar.get("end", "")[:7] != day[:7]:
        return snapshot, None
    inputs = {
        "ohlcv": ("datasets", PRICE),
        "adjusted": ("extensions", ADJUSTED),
        "institutions": ("datasets", INSTITUTIONS),
    }
    if any(not snapshot.get(group, {}).get(dataset, {}).get("raw_rows")
           for group, dataset in inputs.values()):
        return snapshot, None
    gate = market_gate(snapshot, _end_of_day(day).isoformat())
    captures = gate["same_day_captures_outside_signal_inputs"]
    if captures.keys() != inputs.keys() or any(
        reason.endswith("_captured_after_cutoff") for reason in gate["reasons"]
    ):
        return snapshot, None
    view = copy.deepcopy(snapshot)
    exchange = snapshot["instrument"]["exchange"]
    month = date.fromisoformat(day).replace(day=1)
    query = month.strftime("%Y%m%d" if exchange == "twse" else "%Y/%m/%d")
    original = next(a for a in calendar["archives"] if a["query"]["date"] == query)
    dates = month_sessions(json.loads(original["raw_response_utf8"].lstrip("\ufeff")), exchange, month)
    view["market_calendar"]["end"] = day
    view["market_calendar"]["sessions"] = sorted(
        set(calendar["sessions"]) | {d.isoformat() for d in dates if d.isoformat() <= day}
    )
    verify_calendar(view["market_calendar"], exchange)
    for key, (group, dataset) in inputs.items():
        payload = view[group][dataset]
        for raw in payload["raw_rows"]:
            if raw["date"] != day:
                continue
            period, values, units = (
                normalize(dataset, raw) if dataset == ADJUSTED
                else TaiwanMarketDataProvider._normalize(dataset, raw)
            )
            payload["records"].append({
                "stock_id": raw["stock_id"], "dataset": dataset, "data_date": day,
                "period": period, "values": values, "units": units, "raw": raw,
                "retrieved_at": captures[key]["captured_at"], "available_at": captures[key]["captured_at"],
                "published_at": None, "source": f"FinMind:{dataset}",
            })
    return view, captures
