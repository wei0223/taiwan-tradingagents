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
