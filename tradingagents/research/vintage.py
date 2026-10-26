"""Separate present capture time from byte-bound official historical versions.

An old date, mutable filename, or reviewer boolean never proves old contents.
Historical reconstruction requires the official registry bytes to bind the exact
version hash and its validity interval. No existing capture date is rewritten.
"""

import base64
import hashlib
import json
from urllib.parse import urlsplit

from .scenarios import timestamp

OFFICIAL_HOSTS = {"doc.twse.com.tw", "mopsov.twse.com.tw", "mops.twse.com.tw",
                  "www.twse.com.tw", "www.tpex.org.tw"}


def original_bytes(receipt):
    url = urlsplit(receipt["source_url"])
    if url.scheme != "https" or url.hostname not in OFFICIAL_HOSTS or url.username or url.port:
        raise ValueError("Official HTTPS historical evidence required")
    if receipt.get("http_status") != 200:
        raise ValueError("Historical evidence source unavailable")
    raw = base64.b64decode(receipt["content_base64"], validate=True)
    if hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
        raise ValueError("Historical evidence bytes changed")
    timestamp(receipt["captured_at"])
    return raw


def official_binding(record, evaluated_at):
    """Supported registry contract is parsed from archived bytes, never flags.

    No matching official registry has been obtained for the seven legacy gaps.
    This validator defines what future evidence must establish; its synthetic
    tests do not count as successful historical financial validation.
    """
    proof = record["historical_version_proof"]
    receipt = proof["registry_receipt"]
    raw = original_bytes(receipt)
    if timestamp(receipt["captured_at"]) > timestamp(evaluated_at):
        raise ValueError("Historical proof not yet observed at evaluation time")
    registry = json.loads(raw.decode("utf-8-sig"))
    semantics = registry.get("semantics")
    if semantics not in {"official_content_addressed_version_history", "official_immutable_attachment_version_history"}:
        raise ValueError("A current listing is not a historical version registry")
    rows = [r for r in registry["versions"] if r["stock_id"] == record["stock_id"]
            and r["object_id"] == record["object_id"] and r["revision_id"] == record["revision_id"]]
    if len(rows) != 1:
        raise ValueError("Unique exact historical version identity missing")
    bound = rows[0]
    target_hash = record.get("source_sha256") or record.get("sha256")
    if semantics == "official_content_addressed_version_history":
        if bound["content_sha256"] != target_hash:
            raise ValueError("Historical registry does not bind these numeric/document bytes")
    else:
        target = proof["attachment_receipt"]
        attachment = original_bytes(target)
        if (target["source_url"] != bound["immutable_attachment_url"]
                or target["sha256"] != target_hash or not attachment
                or timestamp(target["captured_at"]) != timestamp(record["captured_at"])
                or timestamp(target["captured_at"]) > timestamp(evaluated_at)
                or bound["revision_id"] not in target["source_url"]):
            raise ValueError("Immutable official attachment does not bind this exact version")
    if not record.get("published_at") or timestamp(bound["published_at"]) != timestamp(record["published_at"]):
        raise ValueError("Historical publication time differs from official version")
    start, end = timestamp(bound["published_at"]), bound.get("replaced_at")
    if end and timestamp(end) <= start:
        raise ValueError("Invalid official version interval")
    complete = timestamp(registry["history_complete_through"])
    if complete > timestamp(receipt["captured_at"]):
        raise ValueError("Registry cannot certify future revision history")
    return {"available_at": bound["published_at"], "replaced_at": end,
            "history_complete_through": registry["history_complete_through"],
            "proof_observed_at": receipt["captured_at"], "proof_sha256": receipt["sha256"],
            "captured_at": record["captured_at"], "mode": "official_historical_version_reconstruction",
            "historically_issued_prediction": False}


def knowledge(record, cutoff, evaluated_at=None):
    """Return a decision-time eligibility receipt, preserving both clocks."""
    boundary = timestamp(cutoff)
    capture = timestamp(record["captured_at"])
    published = record.get("published_at")
    if record.get("historical_version_proof"):
        if evaluated_at is None:
            raise ValueError("Retrospective official proof needs an explicit evaluation time")
        result = official_binding(record, evaluated_at)
        known = timestamp(result["available_at"]) <= boundary
        known = known and boundary <= timestamp(result["history_complete_through"])
        known = known and (not result["replaced_at"] or boundary < timestamp(result["replaced_at"]))
        return {**result, "eligible": bool(known), "reason": "official_exact_version_interval"}
    if published:
        available = max(capture, timestamp(published))
    elif (record.get("public_capture_verified") is True
          and record.get("publication_time_status") == "unknown_current_revision"):
        available = capture
    else:
        raise ValueError("Unknown publication requires verified public capture")
    return {"eligible": available <= boundary, "available_at": available.isoformat(),
            "captured_at": record["captured_at"], "published_at": published,
            "mode": "observed_capture", "reason": "capture_and_publication_boundary",
            "historically_issued_prediction": False}
