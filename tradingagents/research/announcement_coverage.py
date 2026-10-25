"""Reconcile reviewed originals with captured official index mother sets.

A response with no server total/exhaustion evidence is an observed index, not
proof that the source is exhausted. Never promote caller ``reviewed`` flags.
"""

import base64
import hashlib
import json
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from tradingagents.dataflows.taiwan.originals import review_original
from tradingagents.dataflows.taiwan.publications import MopsAnnouncementClient

from .scenarios import timestamp

CATEGORIES = ("capital", "corrections", "earnings", "monthly_revenue")


def canonical(url):
    parts = urlsplit(url or "")
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            urlencode(sorted(parse_qsl(parts.query))),
            "",
        )
    )


def classify(row):
    code, subject = row.get("AN_CODE", ""), row.get("SUBJECT", "")
    kinds = set()
    if code in {"M11", "M36", "F03", "F07", "E01"} or (
        code == "M99"
        and any(x in subject for x in ("增資", "GDR", "海外存託", "可轉債", "股數", "資本"))
    ):
        kinds.add("capital")
    if code in {"M31", "E02", "F26", "F27", "F28", "F29"} or (
        code == "M99" and any(x in subject for x in ("每股盈餘", "財務報告", "財務資訊"))
    ):
        kinds.add("earnings")
    if code == "F22":
        kinds.add("monthly_revenue")
    if any(x in subject for x in ("更正", "重編", "補正", "修正")):
        kinds.add("corrections")
    if code in {"M99", "M20", "M22", "M19"} and not kinds:
        kinds.add("unclassified_material")
    return kinds


def identity(row):
    return "|".join(
        str(row.get(k, "")) for k in ("COMPANY_ID", "CDATE", "CTIME", "AN_CODE", "HYPERLINK")
    )


def published(row):
    year, month, day = map(int, row["CDATE"].split("/"))
    return timestamp(f"{year + 1911}-{month:02d}-{day:02d}T{row['CTIME']}+08:00")


def _query_day(value):
    year, month, day = map(int, value.split("/"))
    return f"{year + 1911}-{month:02d}-{day:02d}"


def reconcile(ctx, documents, inventory, coverage, eligible):
    cutoff = timestamp(ctx["cutoff_at"])
    sources, events, issues = [], {}, []
    for key, doc in documents.items():
        if doc.get("source_url") != MopsAnnouncementClient.endpoint or not doc.get("query"):
            continue
        if key not in eligible:
            issues.append({"source_id": key, "reason": "index_unavailable_at_data_as_of"})
            continue
        raw = base64.b64decode(doc["content_base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != doc["sha256"]:
            raise ValueError("Official index bytes changed")
        payload = json.loads(raw.decode("utf-8-sig"))
        rows, query = payload.get("data"), doc["query"]
        if payload.get("status") != "success" or not isinstance(rows, list):
            issues.append({"source_id": key, "reason": "invalid_official_index_response"})
            continue
        if str(query.get("CO_ID")) != ctx["stock_id"]:
            raise ValueError("Official index query company mismatch")
        if query.get("AN") or query.get("PRO_ITEM") or query.get("SUBJECT"):
            issues.append({"source_id": key, "reason": "filtered_index_not_all_categories"})
            continue
        first, last = _query_day(query["SDATE"]), _query_day(query["EDATE"])
        seen = {}
        duplicate_rows = 0
        for row in rows:
            if str(row.get("COMPANY_ID")) != ctx["stock_id"]:
                raise ValueError("Official index row company mismatch")
            when = published(row)
            if not first <= when.date().isoformat() <= last:
                raise ValueError("Index row outside query window")
            event_id = identity(row)
            if event_id in seen:
                if seen[event_id] != row:
                    raise ValueError("Conflicting rows share official index identity")
                duplicate_rows += 1
                continue
            seen[event_id] = row
            if when <= cutoff:
                events[event_id] = {
                    "id": event_id,
                    "row": row,
                    "source_id": key,
                    "published_at": when.isoformat(),
                    "categories": sorted(classify(row)),
                    "first_observed_at": doc["captured_at"],
                    "first_publication_vintage_proven": False,
                }
        # Server counts must be inside hashed response, never a caller attestation.
        total = payload.get("total_count", payload.get("recordsTotal"))
        exhausted = type(total) is int and total == len(rows) and not payload.get("has_more", False)
        sources.append(
            {
                "source_id": key,
                "from_date": first,
                "through_date": last,
                "response_sha256": doc["sha256"],
                "row_count": len(rows),
                "identical_duplicate_rows": duplicate_rows,
                "captured_at": doc["captured_at"],
                "server_total": total,
                "exhaustion_verified": exhausted,
            }
        )

    matched, unresolved, revisions = [], [], []
    inventory_by_url = {}
    for item in inventory:
        link = item.get("original_url")
        if link:
            inventory_by_url.setdefault(canonical(link), []).append(item)
    for event in events.values():
        if not event["categories"]:
            continue
        link = canonical(event["row"].get("HYPERLINK"))
        candidates = inventory_by_url.get(link, [])
        exact = [x for x in candidates if x.get("id") == event["id"]]
        # Shared year-list URLs do not identify an individual upload. Never let
        # insertion order silently choose a different document for an event.
        choices = exact or candidates
        entry = choices[0] if len({x.get("document_id") for x in choices}) == 1 else {}
        doc_id = entry.get("document_id")
        doc = documents.get(doc_id, {})
        # URL match to the original is required, not just an inventory checkbox.
        valid = doc_id in eligible and canonical(doc.get("source_url")) == link
        content = {"status": "missing_document", "content_verified": False}
        if valid:
            try:
                content = review_original(event["row"], doc)
            except (ValueError, KeyError, TypeError):
                content = {"status": "original_content_unverified", "content_verified": False}
            valid = content["content_verified"]
        item = {k: v for k, v in event.items() if k != "row"}
        item.update(
            subject=event["row"].get("SUBJECT"),
            original_url=link,
            document_id=doc_id,
            status="content_verified_original" if valid else content["status"],
            original_content_review=content,
            numeric_impact_reviewed=False,
            first_publication_vintage_proven=False,
        )
        if content["status"] == "listing_only" and entry.get("filing_documents"):
            from tradingagents.dataflows.taiwan.filing_links import candidates, verify_chain

            chains, excluded_chains = [], []
            for document in entry["filing_documents"]:
                try:
                    proof = verify_chain(event["row"], doc, document)
                    if timestamp(proof["known_since_capture"]) > cutoff:
                        excluded_chains.append(
                            {"filename": document["filename"], "reason": "captured_after_cutoff"}
                        )
                    else:
                        chains.append(proof)
                except (ValueError, KeyError, TypeError):
                    excluded_chains.append(
                        {
                            "filename": document.get("filename"),
                            "reason": "unverified_document_chain",
                        }
                    )
            expected = {d["filename"] for d in candidates(event["row"], doc)}
            item["current_period_document_chains"] = chains
            item["excluded_filing_chains"] = excluded_chains
            item["current_period_document_set_captured"] = bool(expected) and expected == {
                d["filename"] for d in chains
            }
            item["event_version_link_verified"] = False
        if valid:
            item.update(
                original_sha256=doc["sha256"],
                revision_id=doc.get("revision_id"),
                supersedes=doc.get("supersedes"),
            )
        (matched if valid else unresolved).append(item)
        if "corrections" in item["categories"]:
            parent_id = doc.get("supersedes")
            parent = documents.get(parent_id, {})
            linked = bool(
                valid
                and parent_id in eligible
                and parent.get("stock_id") == doc.get("stock_id")
                and parent.get("sha256") != doc.get("sha256")
                and timestamp(parent.get("published_at") or parent["captured_at"])
                < timestamp(doc.get("published_at") or doc["captured_at"])
            )
            revisions.append({**item, "revision_link_verified": linked})
            if valid and not linked:
                unresolved.append({**item, "status": "revision_link_pending"})

    categories = {}
    for category in CATEGORIES:
        windows = [x for x in coverage if x.get("category") == category]
        verified = []
        for window in windows:
            start, end = timestamp(window["from_at"]), timestamp(window["through_at"])
            if end != cutoff or start > end:
                continue
            days = (end.date() - start.date()).days + 1
            complete = all(
                any(
                    s["exhaustion_verified"]
                    and timestamp(s["captured_at"]) >= end
                    and s["from_date"]
                    <= (start + timedelta(days=i)).date().isoformat()
                    <= s["through_date"]
                    for s in sources
                )
                for i in range(days)
            )
            if complete:
                verified.append(window)
        missing = [x["id"] for x in unresolved if category in x["categories"]]
        observed = sum(category in x["categories"] for x in events.values())
        state = (
            "missing_document"
            if missing
            else "source_uncovered"
            if not sources or not windows
            else "index_exhaustion_unverified"
            if not verified
            else "verified_no_updates"
            if observed == 0
            else "reviewed_events"
        )
        categories[category] = {
            "state": state,
            "observed_index_events": observed,
            "missing_event_ids": missing,
            "declared_windows": windows,
        }
    passed = not any("unclassified_material" in e["categories"] for e in unresolved) and all(
        v["state"] in {"verified_no_updates", "reviewed_events"} for v in categories.values()
    )
    # TWSE describes ezSearch as selected disclosure items. Exhausting a search
    # response, even with a bound total, cannot prove every MOPS source family.
    all_mops_announcements_proven = False
    return {
        "status": "passed_within_declared_window" if passed else "blocked",
        "data_as_of": ctx["cutoff_at"],
        "categories": categories,
        "index_sources": sources,
        "mother_set_count": len(events),
        "unprocessed_events": unresolved,
        "matched_events": matched,
        "outside_declared_categories": [
            {
                **{k: v for k, v in event.items() if k != "row"},
                "subject": event["row"].get("SUBJECT"),
                "reason": "outside_declared_category_rules_not_content_reviewed",
            }
            for event in events.values()
            if not event["categories"]
        ],
        "revision_links": revisions,
        "issues": issues,
        "source_coverage_verified": passed,
        "source_coverage_scope": "selected_ezsearch_items_within_declared_query_only",
        "all_mops_announcements_proven": all_mops_announcements_proven,
        "source_scope_reference": "https://www.twse.com.tw/zh/about/company/guide.html",
        "latest_complete_research_allowed": passed
        and all_mops_announcements_proven
        and ctx["mode"] == "latest_research",
        "formal_valuation_allowed": passed
        and all_mops_announcements_proven
        and ctx["mode"] != "retrospective_diagnostic",
        "limitation": "Selected ezSearch items are not the complete MOPS disclosure universe; bound totals certify only selected return-set exhaustion. Current capture is not an initial historical vintage",
    }
