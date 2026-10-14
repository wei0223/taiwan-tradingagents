"""Repeatable, offline source-quality audits. A single audit is not a long-term soak test."""

from datetime import UTC, datetime

from .bundle import build_bundle, fingerprint


def audit_snapshots(snapshots):
    cells = []
    for snapshot in snapshots:
        bundle = build_bundle(snapshot)
        missing = [name for name, value in bundle["coverage"].items() if value["status"] != "ok"]
        calendar = bundle.get("calendar_coverage", {})
        cells.append({"stock_id": bundle["stock_id"], "as_of": bundle["as_of"],
                      "snapshot_sha256": fingerprint(snapshot), "bundle_sha256": fingerprint(bundle),
                      "status": "incomplete" if bundle["requires_insufficient_data"] else "ready",
                      "critical_data_missing": bundle["requires_insufficient_data"],
                      "missing_or_partial_datasets": missing, "calendar": calendar,
                      "coverage": bundle["coverage"],
                      "analyst_evidence_counts": {name: sum(bundle["evidence"][key]["availability"] == "available"
                                                            for key in keys)
                                                   for name, keys in bundle.get("analyst_views", {}).items()}})
    if not cells:
        raise ValueError("Audit requires prepared snapshots")
    result = {"audited_at": datetime.now(UTC).isoformat(), "cells": cells,
              "ready": sum(c["status"] == "ready" for c in cells),
              "incomplete": sum(c["status"] == "incomplete" for c in cells),
              "limitations": ["Capture integrity and availability checks; not proof of model accuracy or long-term uptime"],
              "llm_api_calls": False, "orders": False}
    result["audit_sha256"] = fingerprint(result)
    return result
