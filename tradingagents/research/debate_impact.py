"""Trace observable debate changes from accepted work, without inferring causal benefit."""

import json
from pathlib import Path

from .bundle import canonical, fingerprint
from .delivery import read_bytes
from .eight_stage import DIMENSIONS, assemble
from .forecast_journal import read_research


def _positions(result):
    return {"job": result["job"], "agent_id": result["agent_id"],
            "stance": result["stance"], "summary": result["summary"],
            "prompt_sha256": result["prompt_sha256"]}


def _trace(opening, opponent, reply):
    # Only compare identical claim wording and horizon. No semantic matching of prose.
    before = {(f["claim"], f["horizon"]): f for f in opening["findings"]}
    confidence = []
    for finding in reply["findings"]:
        original = before.get((finding["claim"], finding["horizon"]))
        if original and original["confidence"] != finding["confidence"]:
            confidence.append({"opening_claim_id": original["id"], "reply_claim_id": finding["id"],
                               "before": original["confidence"], "after": finding["confidence"]})
    responses = reply["payload"]["responses"]
    prior_refs = {fingerprint(ref) for result in (opening, opponent) for f in result["findings"]
                  for ref in f["evidence"]}
    additional_refs = {
        fingerprint(ref): ref for row in [*responses, *reply["findings"]]
        for ref in row["evidence"] if fingerprint(ref) not in prior_refs
    }
    changed = opening["stance"] != reply["stance"]
    linked = [r["claim_id"] for r in responses if r["changed_my_position"] and r["evidence"]]
    unavailable = any(row["status"] == "unavailable" for row in (opening, opponent, reply))
    flags = []
    if changed and linked:
        flags.append("stance_changed")
    elif changed:
        flags.append("unverified_stance_change")
    if confidence:
        flags.append("confidence_changed")
    return {"before": _positions(opening), "after": _positions(reply),
            "opening_claims": opening["findings"], "rebuttals_to": opponent["job"],
            "rebuttals": responses, "linked_stance_change_claim_ids": linked,
            "additional_citations": list(additional_refs.values()),
            "disputed_claim_ids": [r["claim_id"] for r in responses if r["assessment"] == "dispute"],
            "confidence_changes": confidence, "confidence_comparison": "matched_exact_claim_and_horizon"
            if any((f["claim"], f["horizon"]) in before for f in reply["findings"]) else "unavailable",
            "change_types": ["unavailable"] if unavailable else flags or ["no_material_change"]}


def build(assembled, validation):
    results = assembled["results"]
    rounds = {}
    for dimension in (*DIMENSIONS, "overall"):
        bull, bear = (("bull_opening", "bear_opening") if dimension == "overall"
                      else (dimension + "_bull", dimension + "_bear"))
        prefix = "" if dimension == "overall" else dimension + "_"
        rounds[dimension] = {
            "bull": _trace(results[bull], results[bear], results[prefix + "bull_reply"]),
            "bear": _trace(results[bear], results[bull], results[prefix + "bear_reply"]),
        }
    decision = results["decision"]
    payload = decision["payload"]
    findings = {f["id"]: {"job": result["job"], **f}
                for result in results.values() for f in result["findings"]}
    reviewed = validation["claims"]
    declared = []
    for key in payload["changed_claim_ids"]:
        finding = findings[key]
        row = reviewed.get(key, {})
        declared.append({"claim_id": key, "job": finding["job"], "claim": finding["claim"],
                         "evidence": finding["evidence"], "reasoning_status": row.get("status", "insufficient"),
                         "reviewed": row.get("status") == "supported"})
    # A new calculation or changed wording alone does not establish evidence improvement.
    cross = assembled["workflow"].get("cross_review", {})
    return {"schema_version": 1, "stock_id": assembled["stock_id"], "as_of": assembled["as_of"],
            "mode": assembled["mode"], "rounds": rounds,
            "decision": {"before": payload["position_before"], "after": _positions(decision),
                         "reason": payload["evidence_change"], "declared_changed_claims": declared,
                         "change_types": ["declared_change_requires_comparative_review"] if declared else
                                         ["no_material_change_reported"],
                         "evidence_improvement_verified": False},
            "cross_evidence_reactions": {job: item["responses"] for job, item in cross.items()},
            "supplement_review": {"active_revision": assembled["workflow"]["active_revision"],
                                  "decisions": assembled["workflow"]["supplement"]["decisions"]},
            "outcome_link": {"status": "unlinked"},
            "semantic_and_causal_benefit_proven": False}


def link(report_dir, evaluation_path, output_dir):
    report = Path(report_dir).resolve()
    output = Path(output_dir).resolve()
    if output.is_relative_to(report):
        raise ValueError("Save debate linkage outside the immutable research archive")
    record, manifest = read_research(report)
    if "debate-impact.json" in manifest["files"]:
        impact = json.loads(read_bytes(report / "debate-impact.json"))
    else:
        impact = build(assemble(report / "agent-run"),
                       json.loads(read_bytes(report / "claim-validation.json")))
    evaluations = json.loads(read_bytes(evaluation_path))
    matches = [r for r in evaluations if r["research_id"] == record["research_id"]]
    if len(matches) != 1:
        raise ValueError("Select exactly one outcome for this research ID")
    evaluation = matches[0]
    if (evaluation["research_manifest_sha256"] != manifest["manifest_sha256"]
            or evaluation["stock_id"] != record["stock_id"]
            or evaluation["evaluation_sha256"] != fingerprint({k: v for k, v in evaluation.items()
                                                                 if k != "evaluation_sha256"})):
        raise ValueError("Outcome identity or manifest binding changed")
    impact["outcome_link"] = {"status": "linked", "research_id": record["research_id"],
                              "evaluation_sha256": evaluation["evaluation_sha256"],
                              "market_statuses": {str(r["sessions"]): r["status"] for r in evaluation["market"]},
                              "financial_status": evaluation["fundamentals"]["status"]}
    output.mkdir(parents=True)
    with (output / "debate-impact.json").open("x", encoding="utf8") as handle:
        handle.write(canonical(impact) + "\n")
    return impact["outcome_link"]


def summary_line(impact):
    changes = [flag for row in impact["rounds"].values() for role in row.values()
               for flag in role["change_types"]]
    return (f"已連結證據的立場變動 {changes.count('stance_changed')}；"
            f"可比主張的信心變動 {changes.count('confidence_changed')}。"
            + ("最終裁決未列實質改判。" if not impact["decision"]["declared_changed_claims"]
               else "最終裁決有列改判，證據改善仍待比較審查。"))
