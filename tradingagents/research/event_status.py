"""Observed official events and missing source scope are different questions."""

from .scenarios import timestamp


def inventory(plan, documents, spec):
    complete = plan.get("completeness", {})
    index = complete.get("official_index_reconciliation", {})
    cutoff = (plan.get("context") or {}).get("cutoff_at")
    if not cutoff:
        return {"status": "SOURCE_UNAVAILABLE", "events": [], "hard_blockers": []}
    entries = {}
    revisions = {r["id"]: r for r in index.get("revision_links", [])}
    for row in [*index.get("matched_events", []), *index.get("unprocessed_events", [])]:
        if row["id"] not in entries or not row.get("original_content_review", {}).get("content_verified"):
            entries[row["id"]] = row
    for row in spec.get("inventory", []):
        entries.setdefault(row["id"], row)
    official_ids = {r["id"] for r in [*index.get("matched_events", []), *index.get("unprocessed_events", [])]}
    out = []
    eligible = complete.get("eligible_sources", {})
    impacts = spec.get("event_impact_reviews", {})
    for key, row in sorted(entries.items()):
        doc_id = row.get("document_id")
        doc = documents.get(doc_id, {})
        published = row.get("published_at")
        categories = row.get("categories", [row.get("kind", "unclassified_material")])
        content_verified = row.get("original_content_review", {}).get("content_verified", False)
        impact = impacts.get(key, {})
        # A downgrade to nonmaterial requires an eligible ORIGINAL, a bound hash,
        # a named numeric-impact review and an explicit model dependency list.
        impact_verified = bool(content_verified and doc_id in eligible
                               and impact.get("original_sha256") == doc.get("sha256")
                               and impact.get("reviewed_by") and impact.get("locator")
                               and impact.get("rationale")
                               and isinstance(impact.get("affected_parameters"), list))
        material = not (impact_verified and impact.get("material") is False)
        if published and timestamp(published) > timestamp(cutoff):
            state, reason = (("NOT_YET_PUBLISHED", "official_event_timestamp_after_cutoff")
                             if key in official_ids else ("SOURCE_UNAVAILABLE", "future_analyst_date_not_official_schedule_proof"))
        elif "corrections" in categories and not revisions.get(key, {}).get("revision_link_verified"):
            state, reason = "REVISION_UNRESOLVED", "correction_predecessor_or_replacement_not_verified"
        elif doc_id in eligible and content_verified and impact_verified:
            state, reason = "VERIFIED", "original_and_numeric_impact_reviewed"
        elif not published:
            state, reason = "SOURCE_UNAVAILABLE", "publication_time_not_established"
        elif doc.get("status") in {"unavailable", "failed", "source_rejected"}:
            state, reason = "SOURCE_UNAVAILABLE", "known_announcement_original_unavailable"
        else:
            state, reason = "MISSING_PUBLISHED", "announced_original_or_numeric_impact_missing"
        out.append({"source_id": key, "document_id": doc_id, "published_at": published,
                    "categories": categories, "subject": row.get("subject"), "status": state,
                    "reason": reason, "material": material, "impact_review_verified": impact_verified,
                    "affected_parameters": impact.get("affected_parameters", []),
                    "model_impact": impact.get("rationale") if impact_verified else "unknown_assume_material_until_review",
                    "hard_block": material and state != "VERIFIED" and state != "NOT_YET_PUBLISHED"})
    for category, details in index.get("categories", {}).items():
        if details["state"] not in {"reviewed_events", "verified_no_updates"}:
            out.append({"source_id": "scope:" + category, "status": "SOURCE_UNAVAILABLE",
                        "published_at": None, "categories": [category], "material": True,
                        "reason": details["state"], "affected_parameters": [],
                        "model_impact": "cannot_certify_absence_of_other_announcements",
                        "hard_block": True})
    return {"status": "PARTIAL" if any(r["hard_block"] for r in out) else "VERIFIED",
            "events": out, "hard_blockers": [r["source_id"] for r in out if r["hard_block"]],
            "known_fact_research_allowed": True,
            "complete_current_research_allowed": bool(out) and not any(r["hard_block"] for r in out)
            and index.get("all_mops_announcements_proven", False),
            "absence_is_not_nonpublication": True,
            "scope": index.get("source_coverage_scope", "official_index_unavailable")}
