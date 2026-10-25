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
            if record.get("publication_basis", "").startswith("MOPS ") and not record.get("publication_evidence"):
                raise ValueError("Missing MOPS publication evidence")
            if "publication_evidence" in record:
                from tradingagents.dataflows.taiwan.publications import validate_publication

                proof = record["publication_evidence"]
                available = validate_publication(dataset, record["raw"], proof, record["retrieved_at"])
                if (record.get("available_at") != available or day(available) > day(as_of)
                        or publication != proof["published_at"]):
                    raise ValueError("Invalid or future value-vintage availability")
                from tradingagents.dataflows.taiwan.provider import TaiwanMarketDataProvider

                period, values, units = TaiwanMarketDataProvider._normalize(dataset, record["raw"])
                if ((record["period"], record["values"], record["units"]) != (period, values, units)
                        or record["data_date"] != record["raw"]["date"]
                        or record["source"] != f"FinMind:{dataset}"):
                    raise ValueError("Normalized values disagree with archived source")
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
            "publication_sources": sorted({r["publication_evidence"]["source"] for r in subset
                                            if r.get("publication_evidence")}),
            "publication_evidence_sha256": sorted({fingerprint(r["publication_evidence"]) for r in subset
                                                    if r.get("publication_evidence")}),
            "value_available_dates": sorted({r["available_at"] for r in subset if r.get("available_at")}),
            "availability": "available" if value is not None else "unavailable",
        }
        if any(r["values"].get("url") for r in subset):
            evidence[key]["article_urls"] = sorted({r["values"]["url"] for r in subset if r["values"].get("url")})
            evidence[key]["attributed_publishers"] = sorted({r["values"]["publisher"] for r in subset if r["values"].get("publisher")})
        if "extensions" in snapshot and any(r.get("publication_evidence") for r in subset):
            evidence[key]["publication_document_urls"] = sorted({r["publication_evidence"]["document_url"]
                                                                 for r in subset if r.get("publication_evidence")})

    prices = records[PRICE]
    if prices and (day(as_of) - day(prices[-1]["data_date"])).days > 7:
        coverage[PRICE]["status"] = "partial"
        coverage[PRICE]["warnings"].append("Latest price is more than a week old; freshness unverified")
    # Small context: recent rows plus full-window calculated indicators, not the raw archive.
    for record in prices[-10:]:
        for field, value in sorted(record["values"].items()):
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
    period_metrics = financial_metrics(records[FINANCIAL], snapshot.get("financial_period_review"),
                                       as_of, stock_id)
    latest_financial = period_metrics[-1] if period_metrics else {}
    coverage[FINANCIAL]["calculation_status"] = (
        "reviewed_single_quarter" if latest_financial.get("eps_twd") is not None
        and latest_financial.get("revenue_twd") is not None
        else "unavailable_unverified_field_periods")
    coverage[FINANCIAL]["latest_calculation_period"] = latest_financial.get("period")
    for metric in period_metrics[-12:]:
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
    from tradingagents.dataflows.taiwan.extensions import add_extended_evidence

    extended_missing = add_extended_evidence(snapshot, add, coverage)
    # Corrected amounts originate in reviewed official observations, not the vendor
    # records that merely supplied a quarter-end label. Keep that lineage explicit.
    if snapshot.get("financial_period_review"):
        from tradingagents.dataflows.taiwan.periods import resolve

        approved = resolve(snapshot["financial_period_review"], stock_id, as_of)["rows"]
        docs = snapshot["financial_period_review"]["documents"]
        refs = {key for row in approved for key in row["document_ids"]}
        lineage = {
            "sources": sorted({docs[k]["source_url"] for k in refs}),
            "publication_dates": sorted({docs[k]["published_at"] for k in refs if docs[k].get("published_at")}),
            "retrieved_at": sorted({docs[k]["captured_at"] for k in refs}),
            "publication_sources": ["source_reviewed_financial_originals"],
            "publication_evidence_sha256": sorted({docs[k]["sha256"] for k in refs}),
            "publication_document_urls": sorted({docs[k]["source_url"] for k in refs}),
            "calculation_policy": "reviewed_single_quarter_only_v1",
            "financial_period_observations": approved,
        }
        for item in evidence.values():
            if item["path"].startswith(("financial.", "cashflow.")) and item["value"] is not None:
                item.update(lineage)
                item["data_start"] = min((r["period_start"] for r in approved), default=None)
                item["data_end"] = max((r["period_end"] for r in approved), default=None)
                item["input_rows"] = len(approved)
                item["value_available_dates"] = sorted({docs[k]["captured_at"][:10] for k in refs})
    missing = [name for name, result in coverage.items() if result["status"] != "ok"]
    bundle = {
        "schema_version": 1, "stock_id": stock_id, "exchange": instrument["exchange"], "as_of": as_of,
        "data_mode": snapshot.get("data_mode", "provider_snapshot"),
        "snapshot_sha256": fingerprint(snapshot), "evidence": evidence, "coverage": coverage,
        "requires_insufficient_data": bool(not prices or PRICE in missing or FINANCIAL in missing
                                            or REVENUE in missing or extended_missing
                                            or coverage[FINANCIAL]["calculation_status"] != "reviewed_single_quarter"),
        "limitations": [*[item for item in snapshot.get("limitations", []) if not item.startswith("No LLM analysis")],
                        *institutions["warnings"],
                        "No broker orders or portfolio simulation",
                        "Current market metadata is not verified historical membership",
                        "Numeric citations are checked; qualitative interpretations are not proven"],
        "policy": {"llm_auth": "chatgpt_only", "api_fallback": False, "orders": False,
                   "opening_rounds": 1, "reply_rounds": 1},
    }
    if "extensions" in snapshot:
        # Four analyst views share validated evidence; they are not extra LLM calls.
        groups = {"market": ("prices.", "technical.", "adjusted_technical.", "reference_adjusted_technical.",
                              "institutions.", "calendar_verified_institutions.", "actions."),
                  "fundamentals": ("financial.", "revenue.", "cashflow.", "balance.", "valuation."),
                  "news": ("news.",), "social": ("social.",)}
        bundle["analyst_views"] = {name: [key for key, value in evidence.items()
                                            if value["path"].startswith(prefixes)]
                                   for name, prefixes in groups.items()}
        calendar = snapshot.get("market_calendar", {})
        bundle["calendar_coverage"] = {key: calendar.get(key) for key in
                                       ("status", "start", "end", "basis", "warnings")}
    return bundle
