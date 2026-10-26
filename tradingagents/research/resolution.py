"""Actionable official-source work queue; acquisition never certifies numeric impact."""

import json
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from tradingagents.dataflows.taiwan.originals import review_original, validate_url

from .announcement_coverage import canonical as canonical_url
from .bundle import canonical, fingerprint
from .scenarios import timestamp


def index_rows(documents):
    import base64
    import hashlib

    from tradingagents.dataflows.taiwan.publications import MopsAnnouncementClient

    rows, seen = [], set()
    for doc in documents.values():
        if doc.get("source_url") == MopsAnnouncementClient.endpoint:
            raw = base64.b64decode(doc["content_base64"], validate=True)
            if hashlib.sha256(raw).hexdigest() != doc["sha256"]:
                raise ValueError("Official index original changed")
            for row in json.loads(raw.decode("utf-8-sig")).get("data", []):
                identity = fingerprint(row)
                if identity not in seen:
                    rows.append(row)
                    seen.add(identity)
    return rows


def queue(stock, events, plan):
    source = plan.get("completeness", {}).get("official_index_reconciliation", {})
    details = {r["id"]: r for r in [*source.get("matched_events", []), *source.get("unprocessed_events", [])]}
    grouped = {}
    for event in events:
        key = event["source_id"]
        info = details.get(key, {})
        kind = "historical_gap" if event.get("historical_backtest_block") else (
            "scope_gap" if key.startswith(("scope:", "registry:")) else "announcement")
        categories = sorted(event.get("categories", []))
        parts = key.split("|", 4)
        url = info.get("original_url") or (parts[-1] if len(parts) == 5 else None)
        subject = info.get("subject") or event.get("subject")
        identity = {"company": stock, "type": categories, "published_at": event.get("published_at"),
                    "source_url": url, "subject": subject, "record_kind": kind,
                    "announcement_code": parts[3] if len(parts) == 5 else None}
        # A shared listing URL alone cannot merge distinct uploads/events.
        if not url or not identity["published_at"] or not identity["announcement_code"]:
            identity["unresolved_identity"] = key
        event_id = fingerprint(identity)
        if event_id in grouped:
            grouped[event_id]["aliases"].append(key)
            continue
        critical = bool(set(categories) & {"capital", "earnings", "corrections", "monthly_revenue",
                                            "financial_reports_notes", "fundraising", "capital_registration"})
        unknown_impact = event.get("model_impact", "").startswith("unknown")
        severity = "P0" if kind == "historical_gap" or critical or unknown_impact else "P1"
        if event.get("impact_review_verified") and not event.get("material"):
            severity = "P2"
        status = event["status"]
        issue = ("INDEX_SCAN_INCOMPLETE" if kind == "scope_gap" else
                 "HISTORICAL_VERSION_LINK" if kind == "historical_gap" else
                 "MANUAL_REVIEW_REQUIRED" if info.get("original_content_review", {}).get("content_verified") else
                 "SOURCE_TEMPORARILY_UNAVAILABLE" if status == "SOURCE_UNAVAILABLE" else
                 "REVISION_LINK_REQUIRED" if status == "REVISION_UNRESOLVED" else
                 "PUBLISHED_NOT_CAPTURED" if status == "MISSING_PUBLISHED" else status)
        action = ("query_official_family_and_prove_scope" if kind == "scope_gap" else
                  "bind_official_event_to_filename_and_revision" if kind == "historical_gap" else
                  "review_original_numeric_impact_and_recompute_dependencies" if issue == "MANUAL_REVIEW_REQUIRED" else
                  "fetch_indexed_official_original_and_trace_revision" if url else "recover_official_index_identity")
        grouped[event_id] = {"event_id": event_id, **identity, "aliases": [key],
            "revision_of": info.get("supersedes"), "observed_at": info.get("first_observed_at") or plan.get("context", {}).get("cutoff_at"),
            "impact_tag": "historical_PIT" if kind == "historical_gap" else (
                "financial_or_share_denominator" if critical else "operating_or_unclassified"),
            "severity": severity, "reason": event["reason"], "issue_kind": issue,
            "resolution_action": action, "resolution_state": "RESOLVED" if status == "VERIFIED" else "PENDING",
            "original_status": status, "affects_current_denominator": severity == "P0" and kind != "historical_gap",
            "attempts": []}
    rows = sorted(grouped.values(), key=lambda r: (r["severity"], r["impact_tag"],
                  -timestamp(r["published_at"]).timestamp() if r["published_at"] else 0, r["event_id"]))
    return {"stock_id": stock, "items": rows, "counts": {
        "announcements": sum(r["record_kind"] == "announcement" for r in rows),
        "scope_gaps": sum(r["record_kind"] == "scope_gap" for r in rows),
        "historical_gap_groups": sum(r["record_kind"] == "historical_gap" for r in rows),
        "input_status_items": len(events), "deduplicated_items": len(rows)},
        "counts_are_not_independent_material_news": True}


def gates(work, market, cutoff, waivers=(), user_authorizations=()):
    accepted = set()
    for waiver in waivers:
        key = waiver.get("event_id")
        if (key in user_authorizations and waiver.get("approved_by") and waiver.get("reason")
                and timestamp(waiver["approved_at"]) <= timestamp(cutoff) < timestamp(waiver["expires_at"])):
            accepted.add(key)
    blocked = [r["event_id"] for r in work["items"] if r["affects_current_denominator"]
               and r["resolution_state"] not in {"RESOLVED", "RESOLVED_SELECTED_CURRENT_FACTS"} and r["event_id"] not in accepted]
    return {"fact_summary": "PARTIAL" if blocked else "PASS_selected_facts",
            "trade_market_inputs": market.get("status", "FAIL"),
            "formal_valuation_inputs": "BLOCKED" if blocked else "PASS_selected_scope",
            "unresolved_P0": blocked, "explicit_user_waivers": sorted(accepted),
            "fair_exit_multiple": "UNVERIFIED", "fair_value": None,
            "trade_statistics": "SAMPLE_INSUFFICIENT", "orders": False}


def execute(work, index_rows, documents, output, client=None, now=None, budget=1):
    """Bounded read-only capture, cache first, one-day failure cooldown, no redirects.

    Client is injected for mocks. Every item, including deferred P0 items, gets an
    immutable executable disposition. Captured bytes are retained separately.
    """
    if now is None or budget < 0:
        raise ValueError("Exact execution time and nonnegative budget required")
    current = timestamp(now)
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    receipts, blocked_hosts = {}, set()
    for doc in documents.values():
        if ((doc.get("http_status") in {403, 429} or doc.get("status") in {"source_rejected", "source_restricted"}) and doc.get("captured_at")
                and timestamp(doc["captured_at"]) + timedelta(days=1) > current):
            blocked_hosts.add(urlsplit(doc.get("source_url", "")).netloc)
    prior = []
    for path in root.glob("*.json"):
        data = json.loads(path.read_text("utf8"))
        if data["attempt_sha256"] != fingerprint({k: v for k, v in data.items() if k != "attempt_sha256"}):
            raise ValueError("Resolution history changed")
        prior.append(data)
        if data.get("http_status") in {403, 429} and timestamp(data["observed_at"]) + timedelta(days=1) > current:
            blocked_hosts.add(urlsplit(data.get("source_url", "")).netloc)
    result = []
    for item in work["items"]:
        row = next((r for r in index_rows if canonical_url(r.get("HYPERLINK")) == canonical_url(item["source_url"])
                    and str(r.get("COMPANY_ID")) == item["company"]
                    and (not item.get("subject") or r.get("SUBJECT") == item["subject"])), None)
        host = urlsplit(item["source_url"] or "").netloc
        record = {"event_id": item["event_id"], "company": item["company"],
                  "observed_at": now, "source_url": item["source_url"],
                  "action": item["resolution_action"], "resolution_state": item["resolution_state"],
                  "model_recomputed": False, "numeric_impact_verified": False}
        cached = [d for d in documents.values() if canonical_url(d.get("source_url")) == canonical_url(item["source_url"]) and d.get("content_base64")
                  and d.get("captured_at") and timestamp(d["captured_at"]) <= current]
        cached_verified = False
        if row:
            for doc in cached:
                try:
                    cached_verified = review_original(row, doc)["content_verified"]
                except (KeyError, TypeError, ValueError):
                    continue
                if cached_verified:
                    record.update(source_sha256=doc["sha256"], resolution_state="ACQUIRED_PENDING_REVIEW",
                                  reason="cached_original_found_numeric_impact_and_revision_review_required")
                    break
        recent = [p for p in prior if p["event_id"] == item["event_id"] and p.get("receipt_sha256")
                  and timestamp(p["observed_at"]) + timedelta(days=1) > current]
        if item["resolution_state"] in {"RESOLVED", "RESOLVED_SELECTED_CURRENT_FACTS"}:
            record["resolution_state"] = item["resolution_state"]
            record["reason"] = "already_source_and_impact_reviewed"
        elif item["resolution_state"] == "NUMBERS_VERIFIED_SCOPE_PENDING":
            record.update(resolution_state=item["resolution_state"], numeric_transcription_verified=True,
                          reason="numbers_verified_remaining_scope_not_closed")
        elif not cached_verified:
            if not row:
                record["reason"] = "official_identity_or_scope_query_required_before_fetch"
            elif host in blocked_hosts or recent:
                record.update(reason="source_restriction_or_retry_cooldown", next_retry_at=(current+timedelta(days=1)).isoformat())
            elif client is None or budget == 0:
                record["reason"] = "bounded_capture_budget_deferred_not_resolved"
            else:
                try:
                    validate_url(row)
                except ValueError:
                    record["reason"] = "indexed_URL_not_eligible_no_fallback_or_bypass"
                else:
                    budget -= 1
                    receipt = client.capture(row)
                    receipts[fingerprint(receipt)] = receipt
                    captured = root/"captured"
                    captured.mkdir(exist_ok=True)
                    saved = captured/(fingerprint(receipt)+".json")
                    if not saved.exists():
                        with saved.open("x", encoding="utf8") as stream:
                            stream.write(canonical(receipt)+"\n")
                    verified = receipt.get("original_content_review", {}).get("content_verified", False)
                    record.update(http_status=receipt.get("http_status"), receipt_sha256=fingerprint(receipt),
                        resolution_state="ACQUIRED_PENDING_REVIEW" if verified else "SOURCE_UNAVAILABLE",
                        reason="capture_requires_impact_and_revision_review" if verified else receipt.get("status", "unavailable"))
                    if receipt.get("http_status") in {403, 429} or receipt.get("status") in {"source_rejected", "source_restricted"}:
                        blocked_hosts.add(host)
        record["attempt_sha256"] = fingerprint(record)
        path = root/(record["attempt_sha256"]+".json")
        if not path.exists():
            with path.open("x", encoding="utf8") as stream:
                stream.write(canonical(record)+"\n")
        result.append(record)
    return {"items": result, "captured_receipts": receipts,
            "P0_warnings_removed": False, "network_calls_remaining": budget}
