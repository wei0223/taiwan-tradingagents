"""Explicit knowledge boundaries; publication dates never substitute capture receipts."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .scenarios import timestamp

MODES = {"historical_replay", "retrospective_diagnostic", "latest_research"}


def exact(value):
    if len(value) <= 10:
        raise ValueError("Exact timezone-aware timestamp required")
    return timestamp(value)


def context(spec, stock, as_of, now=None):
    now = now or datetime.now(UTC)
    mode = spec["mode"]
    cutoff = exact(spec["cutoff_at"])
    if mode not in MODES or cutoff > now:
        raise ValueError("Unknown mode or future knowledge cutoff")
    if cutoff.astimezone(ZoneInfo("Asia/Taipei")).date().isoformat() != as_of:
        raise ValueError("Snapshot day and exact cutoff disagree")
    if spec.get("stock_id") != stock:
        raise ValueError("Research company mismatch")
    # Latest means close to the execution time, not an old snapshot relabelled today.
    if mode == "latest_research" and now - cutoff > timedelta(hours=24):
        raise ValueError("Latest research cutoff is stale")
    return {
        "mode": mode,
        "stock_id": stock,
        "cutoff_at": cutoff.isoformat(),
        "evaluated_at": now.isoformat(),
        "prospective_performance": False,
    }


def source_eligibility(source, ctx):
    """Unknown publication time is usable from verified capture only, never backdated."""
    cutoff = exact(ctx["cutoff_at"])
    capture = exact(source["captured_at"])
    if not source.get("sha256") or not source.get("source_url"):
        raise ValueError("Source identity required")
    if (
        source.get("status") in {"source_rejected", "unavailable", "failed"}
        or source.get("public_capture_verified") is False
        or (source.get("http_status") is not None and not 200 <= source["http_status"] < 300)
    ):
        return {"eligible": False, "reason": "source_rejected_or_unverified_response"}
    published = source.get("published_at")
    # A date-only announcement is conservatively known at the end of its day.
    publication = (
        timestamp(published) + timedelta(days=1) - timedelta(microseconds=1)
        if published and len(published) == 10
        else exact(published)
        if published
        else capture
    )
    if publication > cutoff:
        return {"eligible": False, "reason": "published_after_cutoff"}
    if capture > cutoff and ctx["mode"] != "retrospective_diagnostic":
        return {"eligible": False, "reason": "captured_after_cutoff_lookahead"}
    if not published and not source.get("public_capture_verified"):
        return {"eligible": False, "reason": "publication_and_public_capture_unverified"}
    return {
        "eligible": True,
        "reason": "current_revision_retrospective_only" if capture > cutoff else "known_by_cutoff",
        "historical_vintage_proven": capture <= cutoff,
    }


def completeness(ctx, documents, inventory, coverage):
    """A caller must inventory material events and document the official search coverage.

    Passing is restricted to the declared search window; it is not a claim that an
    issuer has never published any other document. Required categories are explicit.
    """
    eligible, excluded = {}, {}
    for key, source in documents.items():
        check = source_eligibility(source, ctx)
        (eligible if check["eligible"] else excluded)[key] = check
    missing, announced = [], set()
    seen = set()
    for event in inventory:
        if event["id"] in seen:
            raise ValueError("Duplicate material event")
        seen.add(event["id"])
        pub = event.get("published_at")
        if not pub:
            missing.append(event["id"] + ":unknown_publication")
            continue
        available = timestamp(pub) + (
            timedelta(days=1) - timedelta(microseconds=1) if len(pub) == 10 else timedelta()
        )
        if available > exact(ctx["cutoff_at"]):
            continue
        if event.get("kind") == "earnings" and event.get("period"):
            announced.add(event["period"])
        if event.get("material", True) and event.get("document_id") not in eligible:
            missing.append(event["id"])
    required = {"earnings", "capital", "monthly_revenue", "corrections"}
    covered = set()
    for item in coverage:
        key = item.get("document_id")
        if (
            key in eligible
            and item.get("reviewed")
            and item.get("category") in required
            and exact(item["through_at"]) >= exact(ctx["cutoff_at"])
            and exact(item["from_at"]) <= exact(ctx["cutoff_at"])
            and exact(item["through_at"]) <= exact(documents[key]["captured_at"])
        ):
            covered.add(item["category"])
    gaps = sorted(required - covered)
    return {
        "status": "complete_within_declared_search" if not missing and not gaps else "incomplete",
        "missing_material_events": missing,
        "coverage_gaps": gaps,
        "eligible_sources": eligible,
        "excluded_sources": excluded,
        "announced_earnings_periods": sorted(announced),
        "ex_ante_performance_eligible": False,
    }
