"""Offline current-document evidence, with historical event ambiguity preserved."""

import json
from pathlib import Path

from tradingagents.dataflows.taiwan.filing_links import candidates, verify_chain

from .announcement_coverage import identity


def audit(spec, output_dir):
    events, unique_files = [], set()
    for event in spec["events"]:
        row = event["row"]
        listing = spec["listings"][event["listing_id"]]
        expected = {d["filename"] for d in candidates(row, listing)}
        supplied = event["document_ids"]
        if len(set(supplied)) != len(supplied):
            raise ValueError("Duplicate current document references")
        documents = [verify_chain(row, listing, spec["documents"][key]) for key in supplied]
        actual = {d["filename"] for d in documents}
        unique_files.update(actual)
        events.append(
            {
                "id": identity(row),
                "stock_id": str(row["COMPANY_ID"]),
                "subject": row["SUBJECT"],
                "documents": documents,
                "current_period_document_set_captured": bool(expected) and expected == actual,
                "event_version_link_verified": False,
                "status": "pending_official_event_filename_crosswalk",
                "initial_vintage_verified": False,
            }
        )
    result = {
        "events": events,
        "unique_current_documents": len(unique_files),
        "verified_event_version_links": 0,
        "forecast_modified": False,
        "limitation": "Current file chains cannot establish which historical event published each version; temporal ordering or matching counts is not proof",
    }
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=False)
    (target / "filing-link-input.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf8"
    )
    (target / "filing-link-audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
    )
    return result
