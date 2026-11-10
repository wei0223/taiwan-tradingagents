"""Observable work accounting and conservative optimization policy."""

import json
from pathlib import Path

from .bundle import canonical
from .delivery import read_bytes
from .scenarios import timestamp

MAX_SUPPLEMENT_QUERIES = 3
MAX_SUPPLEMENT_HTTP_REQUESTS = 9
MAX_SUPPLEMENT_PDFS = 3


def _elapsed(receipt):
    started, finished = receipt.get("started_at"), receipt.get("finished_at")
    if not started or not finished or len(started) <= 10 or len(finished) <= 10:
        return None
    seconds = (timestamp(finished) - timestamp(started)).total_seconds()
    if seconds < 0:
        raise ValueError("Agent receipt finish precedes start")
    return seconds


def observations(root, assembled, impact):
    from .eight_stage import artifact

    audit = assembled["workflow"]["agent_execution_audit"]
    core_ids = {f["claim_id"] for f in assembled["workflow"]["core"]["faces"]}
    changed_ids = {r["claim_id"] for r in impact["decision"]["declared_changed_claims"]}
    jobs = []
    for row in audit["jobs"]:
        job = row["job"]
        result = assembled["results"][job]
        execution = json.loads(read_bytes(artifact(root, row["receipt"])))
        claims = {r["id"] for r in result["findings"]}
        jobs.append({
            "job": job, "agent_id": row["agent_id"], "kind": row["execution_kind"],
            "status": result["status"], "elapsed_seconds": _elapsed(execution),
            "retry_count": None, "token_usage": None, "api_cost": None,
            "selected_core_claim_ids": sorted(claims & core_ids),
            "declared_decision_change_claim_ids": sorted(claims & changed_ids),
            "answered_opposing_claims": len(result["payload"].get("responses", [])),
            "receipt_sha256": row["receipt"]["sha256"],
        })
    return {
        "schema_version": 1, "stock_id": assembled["stock_id"], "as_of": assembled["as_of"],
        "mode": assembled["mode"], "jobs": jobs, "accepted_jobs": len(jobs),
        "recorded_dispatch_calls": sum(j["kind"] in {"spawn_agent", "followup_task"} for j in jobs),
        "unavailable_jobs": sum(j["kind"] == "unavailable" for j in jobs),
        "elapsed_seconds": None, "retry_count": None, "failure_rate": None,
        "token_usage": None, "api_cost": None,
        "timing_basis": "explicit_receipt_times_only_missing_remains_null",
        "policy": {"complete_eight_stage_preserved": True, "fresh_openings_preserved": True,
                   "immutable_inputs_reused": True, "supplement_max_queries": MAX_SUPPLEMENT_QUERIES,
                   "supplement_max_http_requests": MAX_SUPPLEMENT_HTTP_REQUESTS,
                   "supplement_max_pdfs": MAX_SUPPLEMENT_PDFS, "supplement_retries": 0},
    }


def compare_costs(comparison_path, output):
    comparison = json.loads(read_bytes(comparison_path))
    pairs = comparison["paired_holdout_cells"]
    means = comparison["paired_holdout_means"]
    result = {
        "comparison_plan_sha256": comparison["plan_sha256"],
        "paired_holdout_cells": len(pairs), "paired_holdout_means": means if pairs else None,
        "token_usage": None, "api_cost": None,
        "status": "descriptive_only" if pairs else "insufficient_comparable_holdout",
        "quality_tolerance_points": 0,
        "policy": "retain_complete_eight_stage",
        "quality_noninferiority_proven": False,
        "model_cost_reduction_proven": False,
    }
    target = Path(output)
    target.mkdir(parents=True)
    with (target / "cost-comparison.json").open("x", encoding="utf8") as handle:
        handle.write(canonical(result) + "\n")
    return result
