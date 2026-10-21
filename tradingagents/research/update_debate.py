"""Validate actual independent update rounds without claiming fresh blind contexts."""

import base64
import hashlib
import json

from .bundle import fingerprint
from .knowledge import exact
from .vintages import verify_receipt_times


def validate(packet, stock, cutoff, documents):
    if not packet.get("prior_context_disclosure") or packet.get("fresh_blind_context") is not False:
        raise ValueError("Update rounds must disclose prior model exposure")
    originals, replies, identities = {}, {}, []
    artifacts = packet.get("evidence_artifacts", {})
    company_sources = {d["sha256"] for d in documents.values() if d.get("stock_id") == stock}
    for artifact in artifacts.values():
        raw = base64.b64decode(artifact["content_base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
            raise ValueError("Update evidence artifact hash mismatch")
        if artifact.get("format") == "json":
            verify_receipt_times(json.loads(raw), exact(cutoff))

    def refs(items):
        if not items:
            raise ValueError("Claim/rebuttal evidence required")
        paths = []
        for ref in items:
            name = ref.get("file")
            if name not in artifacts or not ref.get("locator"):
                raise ValueError("Unbound update evidence artifact/locator")
            artifact = artifacts[name]
            if artifact.get("format") == "json":
                content = json.loads(base64.b64decode(artifact["content_base64"]))
                company = content.get("stock_id") or content.get("instrument", {}).get("stock_id")
                if company != stock:
                    raise ValueError("Update evidence company mismatch")
            elif (
                artifact["sha256"] not in company_sources
                and artifact.get("original_source_sha256") not in company_sources
            ):
                raise ValueError("Update original source not bound to company review")
            if ref.get("original_bytes_file"):
                original = artifacts.get(ref["original_bytes_file"], {})
                if original.get("sha256") != ref.get("original_bytes_sha256"):
                    raise ValueError("Extracted text must bind its official original")
            paths.append(name + "#" + ref["locator"])
        return paths

    for role in ("bull", "bear"):
        raw = base64.b64decode(packet["opening_file_bytes_base64"][role], validate=True)
        sha = hashlib.sha256(raw).hexdigest()
        if sha != packet["opening_file_sha256"][role]:
            raise ValueError("Opening bytes changed")
        opening = json.loads(raw)
        if opening != packet[role + "_opening"] or opening.get("role") != role:
            raise ValueError("Original update opening mismatch")
        reply_raw = base64.b64decode(packet["reply_file_bytes_base64"][role], validate=True)
        if hashlib.sha256(reply_raw).hexdigest() != packet["reply_file_sha256"][role]:
            raise ValueError("Reply bytes changed")
        reply = json.loads(reply_raw)
        identity = opening["agent_id"]
        if identity != reply.get("agent_id") or reply.get("own_opening_sha256") != sha:
            raise ValueError("Update reply identity/own opening mismatch")
        opponent = "bear" if role == "bull" else "bull"
        if reply.get("opponent_opening_sha256") != packet["opening_file_sha256"][opponent]:
            raise ValueError("Update reply opponent hash mismatch")
        if exact(opening["created_at"]) > exact(reply["created_at"]) or exact(
            reply["created_at"]
        ) > exact(cutoff):
            raise ValueError("Update round chronology mismatch")
        identities.append(identity)
        companies = [c for c in opening["companies"] if c["stock_id"] == stock]
        responses = [c for c in reply["companies"] if c["stock_id"] == stock]
        if len(companies) != 1 or len(responses) != 1:
            raise ValueError("Exactly one company update required")
        company, response = companies[0], responses[0]
        if (
            not company.get("independent_model")
            or response["position_before"] != company["position"]
        ):
            raise ValueError("Original independent model and position required")
        normalized_claims = []
        for claim in company["claims"]:
            if not claim.get("counterevidence") or not claim.get("revision_condition"):
                raise ValueError("Counterevidence and revision conditions required")
            normalized_claims.append({**claim, "evidence_paths": refs(claim["evidence"])})
        changes = []
        for change in response["responses"]:
            if not isinstance(change.get("changed_position"), bool) or not change.get("reason"):
                raise ValueError("Explicit response and position change required")
            changes.append(
                {
                    **change,
                    "claim_id": change["opponent_claim_id"],
                    "changed_my_position": change["changed_position"],
                    "evidence_paths": refs(change["evidence"]),
                }
            )
        originals[role] = {
            **company,
            "claims": normalized_claims,
            "model": company["independent_model"],
        }
        replies[role] = {**response, "changes": changes}
    if len(set(identities)) != 2:
        raise ValueError("Separate update agents required")
    for role, opponent in (("bull", "bear"), ("bear", "bull")):
        expected = {c["id"] for c in originals[opponent]["claims"]}
        actual = [c["claim_id"] for c in replies[role]["changes"]]
        if len(actual) != len(expected) or set(actual) != expected:
            raise ValueError("Respond to every opposing update claim once")
    risk = packet["risk"]
    raw_risk = base64.b64decode(packet["risk_file_bytes_base64"], validate=True)
    if (
        hashlib.sha256(raw_risk).hexdigest() != packet["risk_file_sha256"]
        or json.loads(raw_risk) != risk
    ):
        raise ValueError("Risk review source bytes mismatch")
    if not risk.get("agent_id") or risk["agent_id"] in identities:
        raise ValueError("Separate risk reviewer required")
    rulings = [c for c in packet["judgment"]["companies"] if c["stock_id"] == stock]
    if len(rulings) != 1 or not all(
        rulings[0].get(k)
        for k in (
            "before",
            "after",
            "stronger_case",
            "evidence_that_changed_judgment",
            "unresolved",
            "revision_conditions",
        )
    ):
        raise ValueError("Evidence-led company ruling required")
    return {
        "status": "one_rebuttal_round_completed",
        "independent_agents_executed": True,
        "fresh_blind_context": False,
        "prior_context_disclosure": packet["prior_context_disclosure"],
        "packet_sha256": fingerprint(packet),
        "openings": originals,
        "replies": replies,
        "risk": risk,
        "judgment": rulings[0],
        "confidence_interval": None,
        "performance_evidence": False,
    }
