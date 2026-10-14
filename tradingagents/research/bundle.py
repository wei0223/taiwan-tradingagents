"""Prepare bounded, date-checked evidence; raw excluded rows never enter agent context."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone

from tradingagents.dataflows.taiwan.calculations import (
    financial_metrics,
    institutional_metrics,
    revenue_metrics,
    technical,
)
from tradingagents.dataflows.taiwan.provider import (
    DATASETS,
    FINANCIAL,
    INSTITUTIONS,
    PRICE,
    REVENUE,
    day,
)

TAIPEI = timezone(timedelta(hours=8))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def check_dates(start, as_of):
    if day(start) > day(as_of) or day(as_of) > datetime.now(TAIPEI).date():
        raise ValueError("Invalid date window or future analysis date")


def reject_secret(value, secret):
    if secret and secret in canonical(value):
        raise ValueError("Credential detected in artifact; export refused")


def build_bundle(snapshot):
    """Recompute metrics from eligible records, never trust incoming derived numbers."""
    as_of = snapshot["as_of"]
    if day(as_of) > datetime.now(TAIPEI).date():
        raise ValueError("Future analysis date")
    instrument = snapshot["instrument"]
    stock_id = instrument["stock_id"]
    if instrument["exchange"] not in {"twse", "tpex"}:
        raise ValueError("Unsupported exchange")
    records = {}
    coverage = {}
    for dataset in DATASETS:
        result = snapshot["datasets"][dataset]
        if result["status"] not in {"ok", "partial", "unavailable"}:
            raise ValueError("Invalid dataset status")
        selected = result["records"]
        if result["status"] == "unavailable" and selected:
            raise ValueError("Unavailable dataset contains eligible records")
        for record in selected:
            if record["stock_id"] != stock_id or record["dataset"] != dataset:
                raise ValueError("Instrument/dataset mismatch")
            observed = day(record["data_date"])
            publication = record["published_at"]
            if observed > day(as_of):
                raise ValueError("Future observation in eligible data")
            if publication:
                if not observed <= day(publication) <= day(as_of):
                    raise ValueError("Invalid publication date")
            elif dataset in {FINANCIAL, REVENUE} or observed >= day(as_of):
                raise ValueError("Unverified availability in eligible data")
            if not record.get("source") or not record.get("retrieved_at"):
                raise ValueError("Missing source or retrieval timestamp")
            for number in record["values"].values():
                if isinstance(number, (float, int)) and not isinstance(number, bool) and not math.isfinite(number):
                    raise ValueError("Non-finite source value")
        records[dataset] = sorted(selected, key=lambda r: r["data_date"])
        coverage[dataset] = {
            "status": result["status"], "eligible_rows": len(selected),
            "warnings": sorted(set(result.get("warnings", []))),
            "sources": sorted({r["source"] for r in selected}),
            "retrieved_at": sorted({r["retrieved_at"] for r in selected}),
        }
    evidence = {}

    def add(path, value, unit, dataset, subset):
        key = f"E{len(evidence) + 1:04d}"
        dates = sorted({r["data_date"] for r in subset})
        evidence[key] = {
            "path": path, "value": value, "unit": unit, "dataset": dataset,
            "data_start": dates[0] if dates else None, "data_end": dates[-1] if dates else None,
            "input_rows": len(subset),
            "publication_dates": sorted({r["published_at"] for r in subset if r["published_at"]}),
            "sources": sorted({r["source"] for r in subset}),
            "retrieved_at": sorted({r["retrieved_at"] for r in subset}),
            "availability": "available" if value is not None else "unavailable",
        }

    prices = records[PRICE]
    if prices and (day(as_of) - day(prices[-1]["data_date"])).days > 7:
        coverage[PRICE]["status"] = "partial"
        coverage[PRICE]["warnings"].append("Latest price is more than a week old; freshness unverified")
    # Small context: recent rows plus full-window calculated indicators, not the raw archive.
    for record in prices[-10:]:
        for field, value in record["values"].items():
            add(f"prices.{record['data_date']}.{field}", value,
                "shares" if field == "volume" else "TWD unadjusted", PRICE, [record])
    tech = technical(prices)
    for period, value in tech["ma"].items():
        add(f"technical.ma.{period}", value, "TWD unadjusted", PRICE, prices[-int(period):])
    for key in ("rsi_14", "macd", "macd_signal", "macd_histogram", "volume_ma20"):
        add(f"technical.{key}", tech[key], "shares" if key == "volume_ma20" else "indicator", PRICE, prices)
    for metric in revenue_metrics(records[REVENUE])[-24:]:
        subset = [r for r in records[REVENUE] if r["data_date"] in metric["input_dates"]]
        for key in ("revenue_twd", "yoy_pct"):
            add(f"revenue.{metric['period']}.{key}", metric[key], "percent" if key == "yoy_pct" else "TWD",
                REVENUE, subset)
    for metric in financial_metrics(records[FINANCIAL])[-12:]:
        subset = [r for r in records[FINANCIAL] if r["period"] in metric["input_periods"]]
        for key in ("eps_twd", "eps_qoq_pct", "eps_yoy_pct", "gross_margin_pct",
                    "operating_margin_pct", "net_margin_pct"):
            add(f"financial.{metric['period']}.{key}", metric[key],
                "TWD/share" if key == "eps_twd" else "percent", FINANCIAL, subset)
    institutions = institutional_metrics(records[INSTITUTIONS], prices, instrument["exchange"])
    for group, metric in institutions["groups"].items():
        for key in ("latest_net_shares", "signed_days", "streak_net_shares", "window_net_shares"):
            add(f"institutions.{group}.{key}", metric[key],
                "observed days" if key == "signed_days" else "shares", INSTITUTIONS, records[INSTITUTIONS])
    missing = [name for name, result in coverage.items() if result["status"] != "ok"]
    return {
        "schema_version": 1, "stock_id": stock_id, "exchange": instrument["exchange"], "as_of": as_of,
        "data_mode": snapshot.get("data_mode", "provider_snapshot"),
        "snapshot_sha256": fingerprint(snapshot), "evidence": evidence, "coverage": coverage,
        "requires_insufficient_data": not prices or PRICE in missing or FINANCIAL in missing or REVENUE in missing,
        "limitations": [*[item for item in snapshot.get("limitations", []) if not item.startswith("No LLM analysis")],
                        *institutions["warnings"],
                        "No broker orders or portfolio simulation",
                        "Current market metadata is not verified historical membership",
                        "Numeric citations are checked; qualitative interpretations are not proven"],
        "policy": {"llm_auth": "chatgpt_only", "api_fallback": False, "orders": False,
                   "opening_rounds": 1, "reply_rounds": 1},
    }
