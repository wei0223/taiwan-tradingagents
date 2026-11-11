"""Audit the archived dispatch envelope, within the shared-workspace Codex model."""

import hashlib
import json
from pathlib import PurePosixPath, PureWindowsPath

from . import eight_stage as workflow

PROMPT_INPUT_MARKER = workflow.PROMPT_INPUT_MARKER


def request_fields(state, job):
    fields = {"protocol", "job", "revision", "input_hashes", "visible_inputs", "dependencies",
              "dependency_outputs", "execution", "receipt_limitation"}
    kind = workflow.role(job)
    if kind in {"cross", "supplement"} or job in workflow.FINAL:
        fields.add("core_evidence")
    if kind == "cross":
        fields.add("market_comparison_preflight")
    if job == "supplement" or job in workflow.FINAL:
        fields.add("research_questions")
    if job in workflow.FINAL:
        fields.add("supplement_resolution")
    if state.get("cross_review_required") and job in workflow.CROSS_REVIEW_JOBS:
        fields.add("cross_review_ids")
    if workflow.dimension(job) == "technical" or job in {"risk", "decision"}:
        fields.add("short_term_check")
    if job == "planner":
        fields.add("required_research")
    return fields


def audit(root, state):
    templates_match = all(
        (workflow.TEMPLATES / name).is_file()
        and hashlib.sha256((workflow.TEMPLATES / name).read_bytes()).hexdigest() == digest
        for name, digest in state["templates"].items()
    )
    jobs = []
    seen = set()
    receipt_ids = set()
    unavailable = []
    expected_jobs = set(workflow.JOBS) | (set(workflow.UPDATES) if state["active_revision"] else set())
    for sequence, (job, entry) in enumerate(state["completed"].items(), 1):
        files = state["prompts"][job]
        request = workflow.load_json(workflow.artifact(root, files["request"]))
        prompt = workflow.artifact(root, files["prompt"]).read_text(encoding="utf8")
        header, marker, payload = prompt.rpartition(PROMPT_INPUT_MARKER)
        if not marker or json.loads(payload) != request:
            raise ValueError("Rendered prompt and dispatch request differ")
        if templates_match and header != workflow.prompt_header(job, request):
            raise ValueError("Unexpected text in rendered dispatch prompt")
        if set(request) != request_fields(state, job):
            raise ValueError("Unexpected content in dispatch request")
        revision = {**state, "active_revision": request["revision"]}
        inputs = state["revisions"][request["revision"]]["inputs"]
        input_names = {"metadata", "company_plan", "technical", "source_snapshot"} if job == "planner" else inputs
        if request["input_hashes"] != {name: inputs[name]["sha256"] for name in input_names}:
            raise ValueError("Dispatch input revision mismatch")
        visible = request["visible_inputs"]
        if set(visible) != workflow.allowed_inputs(job):
            raise ValueError("Dispatch input allowlist mismatch")
        for name, item in visible.items():
            # Portable archives retain the absolute path in the original prompt.
            path_type = PurePosixPath if item["path"].startswith("/") else PureWindowsPath
            original = path_type(item["path"])
            relative = path_type(inputs[name]["path"])
            if (set(item) != {"path", "sha256"} or not original.is_absolute() or ".." in original.parts
                    or original.parts[-len(relative.parts):] != relative.parts
                    or item["sha256"] != inputs[name]["sha256"]):
                raise ValueError("Dispatch input path or digest mismatch")
        deps = workflow.dependencies(revision, job)
        if (set(request["dependencies"]) != set(deps)
                or set(request["dependency_outputs"]) != set(deps)
                or not set(deps) <= seen):
            raise ValueError("Unapproved or unpublished dispatch dependency")
        if any(request["dependency_outputs"][d] != workflow.result(root, state, d) for d in deps):
            raise ValueError("Dispatch dependency content differs from accepted output")
        if "core_evidence" in request and request["core_evidence"] != workflow.core_packet(root, revision):
            raise ValueError("Dispatch core evidence differs from accepted evidence")
        if job in workflow.FINAL and state.get("cross_review_required"):
            questions = workflow.load_json(workflow.artifact(root, state["cross_evidence"]))
            if request["research_questions"] != questions:
                raise ValueError("Dispatch research questions differ from sealed calculations")
        if "supplement_resolution" in request:
            resolution = workflow.load_json(workflow.artifact(root, state["resolution"]))
            if request["supplement_resolution"] != resolution:
                raise ValueError("Dispatch supplement resolution differs from accepted record")
        if "short_term_check" in request:
            from .reader_research import short_term_check

            technical = workflow.load_json(workflow.artifact(root, inputs["technical"]))
            if request["short_term_check"] != short_term_check(technical):
                raise ValueError("Dispatch market check differs from approved input")
        kind = workflow.role(job)
        expected_execution = ("fresh_context" if workflow.fresh(job) else
                              "followup_original_agent" if kind == "reply" else "coordinator_allowed")
        if request["execution"] != expected_execution:
            raise ValueError("Dispatch execution policy mismatch")
        receipt = workflow.load_json(workflow.artifact(root, entry["execution"]))
        expected_kind = {"fresh_context": "spawn_agent", "followup_original_agent": "followup_task",
                         "coordinator_allowed": "coordinator"}[expected_execution]
        if receipt["kind"] == "unavailable":
            unavailable.append(job)
        elif receipt["kind"] != expected_kind:
            raise ValueError("Receipt does not match dispatch policy")
        if receipt["kind"] == "spawn_agent" and receipt.get("fork_turns", "none") != "none":
            raise ValueError("Fresh dispatch receipt indicates inherited conversation")
        locator = receipt["tool_call_id"]
        if not isinstance(locator, str) or not locator.strip() or locator in receipt_ids:
            raise ValueError("Missing or reused dispatch receipt locator")
        receipt_ids.add(locator)
        seen.add(job)
        jobs.append({"sequence": sequence, "job": job, "revision": request["revision"],
                     "agent_id": receipt["agent_id"], "execution_kind": receipt["kind"],
                     "context_mode": receipt.get("context_mode"), "receipt_locator": locator,
                     "request": files["request"], "prompt": files["prompt"], "inputs": visible,
                     "published_dependencies": request["dependencies"], "output": entry["output"],
                     "original": entry["original"], "receipt": entry["execution"]})
    policy = {
        "version": 1, "protocol": state["protocol"], "filesystem_enforced": False,
        "scope": "approved dispatch envelopes and context attestations in a shared workspace",
        "limitation": "No filesystem access control or independent observation of out-of-band input.",
        "prompt_templates": state["templates"],
        "jobs": {job: {"input_allowlist": sorted(workflow.allowed_inputs(job)),
                       "fresh_context": workflow.fresh(job),
                       "original_agent_reply": workflow.role(job) == "reply"}
                 for job in sorted(expected_jobs)},
    }
    eligible = state["mode"] == "research" and seen == expected_jobs and not unavailable and templates_match
    return policy, {"status": "limited" if eligible else "blocked",
                    "mode": state["mode"], "stock_id": state["stock_id"], "as_of": state["as_of"],
                    "unavailable_jobs": unavailable, "prompt_templates_verified": templates_match, "jobs": jobs,
                    "filesystem_enforced": False, "execution_independently_proven": False}
