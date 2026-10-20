"""Persist real independent research openings, one rebuttal and evidence-led rulings.

Role identifiers and hashes support an audit, not a proof of independent execution.
The Codex coordinator must actually dispatch separate fresh contexts.
"""

import base64
import hashlib
import json
import re

from .bundle import fingerprint
from .review import _end_of_day, _packet
from .scenarios import timestamp


def evidence_path(path, stock, snapshot, review_input):
    if not isinstance(path, str) or f"/{stock}/" not in path:
        raise ValueError("Evidence must identify the same company")
    name, separator, locator = path.partition("#")
    if not separator:
        raise ValueError("Evidence locator required")
    if name.endswith(".pdf"):
        match = re.fullmatch(r"(?:PDF-page|physical_page)=([1-9][0-9]*)", locator)
        hashes = {d["sha256"] for d in (review_input or {}).get("documents", {}).values()}
        if not match or name.rsplit("/", 1)[-1][:-4] not in hashes:
            raise ValueError("Unknown source PDF or page locator")
        key = next(
            k
            for k, d in review_input["documents"].items()
            if d["sha256"] == name.rsplit("/", 1)[-1][:-4]
        )
        proof = {
            "stock_id": stock,
            "scope": "consolidated",
            "unit": "TWD_and_shares",
            "values_reviewed": True,
            "reviewed_by": "Citation integrity validation, not numeric attestation",
            "locator": locator,
            "version_set": "citation",
            "document_ids": [key],
            "values": {},
        }
        _packet(proof, stock, review_input["documents"], _end_of_day(snapshot["as_of"]))
        return
    roots = {"snapshot.json": snapshot, "review-input.json": review_input}
    root = roots.get(name.rsplit("/", 1)[-1])
    if root is None or not (locator.startswith("$.") or locator.startswith("/")):
        raise ValueError("Only packaged JSON sources may support claims")
    value = root
    tokens = locator[2:].split(".") if locator.startswith("$.") else locator[1:].split("/")
    for token in tokens:
        match = re.fullmatch(r"([^\[\]]+)((?:\[[^\]]+\])*)", token)
        if not match or match[1] not in value:
            raise ValueError("Unresolved evidence JSON path")
        value = value[match[1]]
        for selector in re.findall(r"\[([^\]]+)\]", match[2]):
            if selector.isdigit():
                value = value[int(selector)]
            else:
                constraints = [p.split("=", 1) for p in selector.split(",")]
                if any(len(c) != 2 for c in constraints):
                    raise ValueError("Invalid evidence selector")

                def matches(row, constraints=constraints):
                    for key, expected in constraints:
                        actual = str(row.get(key, ""))
                        if ".." in expected:
                            low, high = expected.split("..", 1)
                            if not low <= actual <= high:
                                return False
                        elif actual != expected:
                            return False
                    return True

                value = [row for row in value if matches(row)]
                if not value:
                    raise ValueError("Empty evidence selection")
    if value is None:
        raise ValueError("Unavailable evidence cannot support a claim")
    if "raw_rows" in locator:
        selected = value if isinstance(value, list) else [value]
        datasets = [
            *snapshot.get("datasets", {}).values(),
            *snapshot.get("extensions", {}).values(),
        ]
        eligible = set()
        for dataset in datasets:
            for row in dataset.get("records", []):
                if row.get("available_at", row.get("data_date", "9999")) <= snapshot[
                    "as_of"
                ] and timestamp(row["retrieved_at"]) <= _end_of_day(snapshot["as_of"]):
                    eligible.add(fingerprint(row.get("raw", {})))
        if any(fingerprint(row) not in eligible for row in selected):
            raise ValueError("Excluded raw rows cannot be used as evidence")


def validate(packet, stock, snapshot, review_input):
    if not packet:
        return {"status": "not_executed", "independent_agents_executed": False}
    if packet.get("protocol") == "independent_source_update_v1":
        from .update_debate import validate as validate_update

        cutoff = (review_input or {}).get("cutoff_at")
        if not cutoff:
            raise ValueError("Exact update-debate cutoff required")
        return validate_update(packet, stock, cutoff, (review_input or {}).get("documents", {}))
    openings = {}
    responses = {}
    agent_ids = []
    for role in ("bull", "bear"):
        opening, reply = packet[f"{role}_opening"], packet[f"{role}_reply"]
        if (
            opening.get("role") != role
            or opening.get("stage") != "opening"
            or opening.get("context_mode") != "fresh_no_baseline"
        ):
            raise ValueError("Independent fresh opening metadata required")
        identity = opening.get("agent_id")
        if (
            not identity
            or reply.get("agent_id") != identity
            or reply.get("role") != role
            or reply.get("stage") != "reply"
        ):
            raise ValueError("Rebuttal must identify the original agent")
        agent_ids.append(identity)
        raw = base64.b64decode(
            packet.get("opening_file_bytes_base64", {}).get(role, ""), validate=True
        )
        if not raw or hashlib.sha256(raw).hexdigest() != packet.get("opening_file_sha256", {}).get(
            role
        ):
            raise ValueError("Opening source bytes and hash required")
        if json.loads(raw) != opening:
            raise ValueError("Opening changed after independent submission")
        opponent = "bear" if role == "bull" else "bull"
        if reply.get("opponent_opening_sha256") != packet.get("opening_file_sha256", {}).get(
            opponent
        ):
            raise ValueError("Rebuttal must bind to the archived opponent opening bytes")
        selected = [c for c in opening["companies"] if c["stock_id"] == stock]
        selected_reply = [c for c in reply["companies"] if c["stock_id"] == stock]
        if len(selected) != 1 or len(selected_reply) != 1:
            raise ValueError("Exactly one company opening and reply required")
        company = selected[0]
        if not company.get("position") or not company.get("model") or not company.get("claims"):
            raise ValueError("Independent model or explicit missing-driver assessment required")
        ids = []
        for claim in company["claims"]:
            ids.append(claim["id"])
            if not all(
                claim.get(k)
                for k in (
                    "claim",
                    "strongest_counterevidence",
                    "confidence",
                    "revision_condition",
                    "evidence_paths",
                )
            ):
                raise ValueError("Claims need counterevidence, confidence and revision conditions")
            if claim["confidence"].get("kind") != "uncalibrated_qualitative":
                raise ValueError("This review has no calibrated probability intervals")
            for ref in claim["evidence_paths"]:
                evidence_path(ref, stock, snapshot, review_input)
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate claim ID")
        openings[role], responses[role] = company, selected_reply[0]
    if len(set(agent_ids)) != 2:
        raise ValueError("Separate bull and bear agents required")
    for role, opponent in (("bull", "bear"), ("bear", "bull")):
        reply = responses[role]
        if reply.get("position_before") != openings[role]["position"] or not reply.get(
            "position_after"
        ):
            raise ValueError("Before and after positions must be retained")
        expected = {c["id"] for c in openings[opponent]["claims"]}
        changes = reply["changes"]
        if len(changes) != len(expected) or {c["claim_id"] for c in changes} != expected:
            raise ValueError("Rebut every opposing claim exactly once")
        for change in changes:
            if (
                change.get("assessment") not in {"accept", "dispute", "uncertain"}
                or not change.get("reason")
                or not change.get("evidence_paths")
            ):
                raise ValueError("Evidence-based rebuttal required")
            if not isinstance(change.get("changed_my_position"), bool):
                raise ValueError("Explicit position change flag required")
            for ref in change["evidence_paths"]:
                evidence_path(ref, stock, snapshot, review_input)
    judge = packet["judgment"]
    risk = packet["risk"]
    if not risk.get("agent_id") or risk["agent_id"] in agent_ids:
        raise ValueError("Independent risk reviewer required")
    rulings = [c for c in judge["companies"] if c["stock_id"] == stock]
    risk_rows = [c for c in risk["companies"] if c["stock_id"] == stock]
    if len(rulings) != 1 or len(risk_rows) != 1:
        raise ValueError("Company-specific risk review and ruling required")
    ruling = rulings[0]
    for key in (
        "before",
        "after",
        "evidence_that_changed_judgment",
        "stronger_case",
        "unresolved",
        "revision_conditions",
    ):
        if not ruling.get(key):
            raise ValueError("Ruling must explain judgment changes and unresolved questions")
    return {
        "status": "one_rebuttal_round_completed",
        "independent_agents_executed": True,
        "execution_attestation": packet.get("execution_attestation", "not_provided"),
        "packet_sha256": fingerprint(packet),
        "openings": openings,
        "replies": responses,
        "risk": risk_rows[0],
        "judgment": ruling,
        "confidence_interval": None,
        "performance_evidence": False,
    }
