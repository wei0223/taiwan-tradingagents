"""Separate scoped current facts from exhaustive research and historical PIT evidence."""

import base64
import copy
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from tradingagents.dataflows.taiwan.official_sources import FAMILIES, OfficialSources
from tradingagents.dataflows.taiwan.originals import compact, html_data, review_original
from tradingagents.dataflows.taiwan.source_store import SourceStore

from .bundle import fingerprint
from .review import review
from .scenarios import TAIPEI, timestamp


def rolling_plan(plan, as_of):
    """Roll source scopes only; never change model values or sealed predictions."""
    result = copy.deepcopy(plan)
    year, month = as_of.year, as_of.month
    months = []
    for offset in range(3):
        absolute = year * 12 + month - 2 - offset
        months.append(f"{absolute // 12}-{absolute % 12 + 1:02d}")
    quarter = (month - 1) // 3
    fiscal_year = year if quarter else year - 1
    result.update(
        monthly_periods=sorted(months),
        financial_periods=[f"{fiscal_year}-Q{q}" for q in range(1, (quarter or 4) + 1)],
        correction_periods=[f"{fiscal_year}-Q{q}" for q in range(1, (quarter or 4) + 1)],
        fundraising_year=year,
        scope_generated_for=as_of.isoformat(),
    )
    return result


def assess(store, snapshot_id, review_input=None):
    snapshot = store.read_snapshot(snapshot_id)
    stock, resources = snapshot["stock_id"], snapshot["resources"]
    verified = {r["key"]: r for r in resources if r["status"] == "verified_current"}
    families = {}
    for family in FAMILIES:
        found = [r for r in resources if r["family"] == family]
        families[family] = {
            "verified_current": sum(r["status"] == "verified_current" for r in found),
            "pending_keys": [r["key"] for r in found if r["status"] != "verified_current"],
            "scope": "explicit_plan_queries_and_observed_current_versions_only",
            "all_historical_events_proven": False,
        }
    # Parse current share number from bound original again, never trust a cached number.
    issued = None
    basic = verified.get("capital:basic")
    if basic:
        receipt = store.receipt(basic["receipt_ids"][0])
        parser, _ = html_data(base64.b64decode(receipt["content_base64"], validate=True))
        text = compact(" ".join(parser.parts))
        match = re.search(r"已發行普通股數或TDR原股發行股數([\d,]+)股", text)
        if not match or f"股票代號{stock}" not in text:
            raise ValueError("Current capital original identity unavailable")
        issued = {
            "shares": int(match[1].replace(",", "")),
            "source_sha256": receipt["sha256"],
            "captured_at": receipt["captured_at"],
            "receipt_id": basic["receipt_ids"][0],
            "basis": "observed_current_issued_common_shares_not_diluted_weights_or_float",
        }
        if issued["shares"] != basic["data"]["issued_common_shares"]:
            raise ValueError("Cached issued share count differs from original")
    financial_hashes = {
        r["content_sha256"] for r in verified.values() if r["key"].startswith("filing:")
    }
    reviewed_hashes = {d["sha256"] for d in (review_input or {}).get("documents", {}).values()}
    reused = sorted(financial_hashes & reviewed_hashes)
    financial_review = None
    if review_input:
        data = copy.deepcopy(review_input)
        data["cutoff_at"] = snapshot["captured_at"]
        as_of = snapshot["captured_at"][:10]

        as_of = timestamp(snapshot["captured_at"]).astimezone(TAIPEI).date().isoformat()
        financial_review = review(stock, as_of, data)

    def matched_packets(keys):
        data = review_input or {}
        ids = {i for key in keys for i in (data.get(key) or {}).get("document_ids", [])}
        return bool(ids) and all(
            data.get("documents", {}).get(i, {}).get("sha256") in financial_hashes for i in ids
        )

    approved = bool(
        matched_packets(["earnings", "prior_earnings"])
        and financial_review
        and financial_review["axes"]["accounting_earnings"]["status"] != "unavailable"
    )
    cash_ok = bool(
        matched_packets(["cash_ytd", "previous_cash_ytd"])
        and financial_review
        and financial_review["axes"]["cash_sustainability"]["status"] != "unavailable"
    )
    months = snapshot["plan"]["monthly_periods"]
    company = next(c for c in snapshot["plan"]["companies"] if c["stock_id"] == stock)
    for period in months:
        resource = verified.get("revenue:" + period)
        if not resource:
            continue
        receipt = store.receipt(resource["receipt_ids"][0])
        year, month = map(int, period.split("-"))
        row = {
            "COMPANY_ID": stock,
            "COMPANY_NAME": company["name"],
            "AN_CODE": "F22",
            "SUBJECT": f"{year - 1911}年{month}月營業收入",
            "HYPERLINK": receipt["source_url"],
        }
        parsed = review_original(row, receipt)
        if (
            not parsed["content_verified"]
            or parsed["numeric_observations"] != resource["data"]["observations"]
        ):
            raise ValueError("Cached monthly values differ from official original")
    month_ok = all("revenue:" + p in verified for p in months)
    claims = {
        "source_matched_previously_reviewed_financial_numbers": approved,
        "source_matched_reviewed_single_quarter_cash": cash_ok,
        "specified_monthly_revenue_originals_available": month_ok and bool(months),
        "current_issued_share_reference_available": issued is not None,
        "fundraising_completion_and_all_events_verified": False,
    }
    claims["previously_reviewed_accounting_facts_available"] = bool(
        financial_review
        and financial_review["axes"]["accounting_earnings"]["status"] != "unavailable"
    )
    result = {
        "schema_version": 1,
        "stock_id": stock,
        "snapshot_sha256": snapshot_id,
        "source_checked_at": max(
            (store.receipt(i)["captured_at"] for r in resources for i in r.get("receipt_ids", [])),
            key=timestamp,
            default=snapshot["captured_at"],
        ),
        "registry_snapshot_created_at": snapshot["captured_at"],
        "registry_event_kind": snapshot.get("event_kind", "source_capture"),
        "plan_sha256": snapshot["plan_sha256"],
        "families": families,
        "current_facts": claims,
        "current_research": {
            "status": "usable_for_scoped_facts_with_gaps"
            if any(claims.values())
            else "unavailable",
            "full_current_research_ready": False,
            "reason": "Required originals can support specified facts; capital-event coverage, numerical review and latest market inputs are separate requirements",
        },
        "historical_backtest": {
            "status": "not_ready",
            "strict_pit_ready": False,
            "requested_start": snapshot["plan"].get("historical_start"),
            "first_registry_observation": min(
                (r["first_seen_at"] for r in resources if r.get("first_seen_at")), default=None
            ),
            "reason": "Current query versions and new captures do not supply contemporaneous versions for each historical decision cutoff",
        },
        "current_issued_share_reference": issued,
        "matched_reviewed_document_hashes": reused,
        "financial_review": financial_review,
        "version_changes": snapshot["changes"],
        "historical_event_links_pending": True,
        "all_mops_announcements_proven": False,
        "forecasts_modified": False,
        "llm_api_calls": False,
        "orders": False,
    }
    result["assessment_sha256"] = fingerprint(result)
    return result


def monthly_vintages(store, snapshot_id):
    """Export official observed values for later replays without backdating them."""
    snapshot = store.read_snapshot(snapshot_id)
    company = next(c for c in snapshot["plan"]["companies"] if c["stock_id"] == snapshot["stock_id"])
    rows = []
    for resource in snapshot["resources"]:
        if resource["family"] != "monthly_revenue" or resource["status"] != "verified_current":
            continue
        period = resource["key"].removeprefix("revenue:")
        year, month = map(int, period.split("-"))
        receipt_id = resource["receipt_ids"][0]
        doc = store.receipt(receipt_id)
        row = {"COMPANY_ID": snapshot["stock_id"], "COMPANY_NAME": company["name"],
               "AN_CODE": "F22", "SUBJECT": f"{year-1911}年{month}月營業收入", "HYPERLINK": doc["source_url"]}
        verified = review_original(row, doc)
        if not verified["content_verified"] or verified["numeric_observations"] != resource["data"]["observations"]:
            raise ValueError("Monthly vintage differs from archived original")
        observation = verified["numeric_observations"][0]
        raw = {"stock_id": snapshot["stock_id"], "revenue_year": year,
               "revenue_month": month, "revenue": observation["value"]}
        rows.append({"stock_id": snapshot["stock_id"], "period": period, "value": raw["revenue"],
            "raw": raw, "raw_kind": "normalized_from_verified_original_not_HTTP_body",
            "source_sha256": fingerprint(raw), "source_document_sha256": doc["sha256"],
            "source_url": doc["source_url"], "source_locator": observation["locator"],
            "revision_id": "observed_current:"+doc["sha256"], "original_receipt_id": receipt_id,
            "source_snapshot_id": snapshot_id, "published_at": None,
            "captured_at": doc["captured_at"], "public_capture_verified": True,
            "publication_time_status": "unknown_current_revision", "initial_publication_verified": False})
    return {"stock_id": snapshot["stock_id"], "rows": rows,
            "known_from_capture_only": True, "historic_initial_version_claimed": False}


def refresh(plan, archive_dir, output_dir, transport=None, clock=None, reviews=None):
    today = (clock or (lambda: datetime.now(UTC)))().astimezone(TAIPEI).date()
    if plan.get("rolling_scope"):
        plan = rolling_plan(plan, today)
    validate_plan(plan, today)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=False)
    store = SourceStore(archive_dir)
    client = OfficialSources(
        store,
        transport=transport,
        clock=clock,
        max_requests=plan.get("max_requests", 90),
        max_pdfs=plan.get("max_pdfs", 24),
    )
    results = {}
    for company in plan["companies"]:
        stock = company["stock_id"]
        resources = client.collect(company, plan)
        key = store.append(stock, plan, resources, client.clock().isoformat())
        result = assess(store, key, (reviews or {}).get(stock))
        (target / (stock + "-monthly-vintages.json")).write_text(
            json.dumps(monthly_vintages(store, key), ensure_ascii=False, indent=2), encoding="utf8")
        (target / (stock + ".json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
        )
        results[stock] = {
            "snapshot_sha256": key,
            "assessment_sha256": result["assessment_sha256"],
            "current_research": result["current_research"],
            "historical_backtest": result["historical_backtest"],
        }
    manifest = {
        "generated_at": (clock or (lambda: datetime.now(UTC)))().isoformat(),
        "plan": plan,
        "companies": results,
        "request_count": client.requests,
        "pdf_count": client.pdfs,
        "refused_hosts": sorted(client.refused),
        "inherited_refusals_until": client.inherited_refusals,
        "llm_api_calls": False,
    }
    (target / "registry-run.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8"
    )
    return manifest


def validate_plan(plan, today):
    """Reject ambiguous or future query scopes before any HTTP call or output."""
    from datetime import date

    companies = plan["companies"]
    ids = [c["stock_id"] for c in companies]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Unique explicit companies required")
    for company in companies:
        if (
            not re.fullmatch(r"\d{4,6}", company["stock_id"])
            or company["exchange"] not in {"twse", "tpex"}
            or not company["name"]
        ):
            raise ValueError("Explicit company name and exchange required")
    for field in ("financial_periods", "correction_periods", "monthly_periods"):
        periods = plan[field]
        if len(periods) != len(set(periods)):
            raise ValueError("Duplicate query period")
        for period in periods:
            if field == "monthly_periods":
                if not re.fullmatch(r"\d{4}-\d{2}", period):
                    raise ValueError("Invalid monthly query period")
                year, month = map(int, period.split("-"))
                date(year, month, 1)
                end = date(year + (month == 12), month % 12 + 1, 1)
            else:
                if not re.fullmatch(r"\d{4}-Q[1-4]", period):
                    raise ValueError("Invalid financial query period")
                year, quarter = int(period[:4]), int(period[-1])
                end = date(year + (quarter == 4), quarter * 3 % 12 + 1, 1)
            if end > today:
                raise ValueError("Query period has not completed")
    if not 1912 <= int(plan["fundraising_year"]) <= today.year:
        raise ValueError("Invalid fundraising year")
    if not plan["financial_editions"] or set(plan["financial_editions"]) - {"AI1", "AIA"}:
        raise ValueError("Unsupported financial edition")
