"""Credential-free integration acceptance for captured, source-bound research inputs."""

import hashlib
import json
from pathlib import Path

from tradingagents.dataflows.taiwan.calendar import verify_calendar
from tradingagents.dataflows.taiwan.publications import MopsAnnouncementClient

from .bundle import fingerprint
from .debate import write_json
from .health import audit_snapshots
from .reporting import package


def accept(snapshot, output_dir):
    """Exercise the daily report path without manufacturing EPS assumptions or PIT history."""
    output = Path(output_dir)
    health = audit_snapshots([snapshot])
    report = package(snapshot, output)
    calendar = snapshot.get("market_calendar", {})
    sessions = verify_calendar(calendar, snapshot["instrument"]["exchange"])
    queries = []
    for archive in snapshot.get("publication_audit", []):
        query = archive.get("query", {})
        start, end = query.get("SDATE", ""), query.get("EDATE", "")
        raw = archive.get("raw_response_utf8", "")
        verified = bool(raw) and (
            hashlib.sha256(raw.encode("utf-8")).hexdigest() == archive.get("response_sha256")
            and json.loads(raw.lstrip("\ufeff"))["data"] == archive["raw_rows"]
            and archive.get("source_url") == MopsAnnouncementClient.endpoint
            and query.get("CO_ID") == snapshot["instrument"]["stock_id"]
            and archive["stock_id"] == snapshot["instrument"]["stock_id"]
        )
        queries.append({
            "dataset": archive["dataset"], "start": start, "end": end,
            "annual_window": bool(start and end and start[:3] == end[:3]),
            "raw_response_verified": verified,
            "warnings": archive.get("warnings", []),
            "raw_rows": len(archive.get("raw_rows", [])),
            "archive_sha256": fingerprint(archive),
        })
    annual = bool(queries) and all(
        q["annual_window"] and q["raw_response_verified"] and not q["warnings"] for q in queries
    )
    ready = (health["incomplete"] == 0 and calendar.get("status") == "ok"
             and bool(sessions) and annual and report["html_audit"]["status"] == "passed")
    result = {
        "stock_id": snapshot["instrument"]["stock_id"], "as_of": snapshot["as_of"],
        "status": "passed" if ready else "incomplete", "snapshot_sha256": fingerprint(snapshot),
        "health": health, "source_audit": report["source_audit"],
        "mops_queries": queries, "annual_mops_verified": annual,
        "calendar_sessions": len(sessions), "calendar_archives": len(calendar.get("archives", [])),
        "dataset_counts": {k: len(v["records"]) for k, v in snapshot["datasets"].items()},
        "forecast_status": report["forecast"]["status"],
        "limitations": [
            "Operational acceptance is not forecast accuracy or profitable strategy validation",
            "Current captures cannot stand in for original historical numeric versions",
            "EPS and industry assumptions require separate reviewed input; none invented here",
            "Original PDF completeness and future quarterly actuals remain separate tracking tasks",
        ],
        "orders": False, "llm_api_calls": False,
    }
    write_json(output / "acceptance.json", result)
    return result
