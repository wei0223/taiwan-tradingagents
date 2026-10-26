"""Append-only registration of real prior releases; registration never reissues."""

import json
from datetime import UTC, datetime
from pathlib import Path

from .bundle import canonical, fingerprint
from .delivery import read_bytes
from .publication import read_release
from .scenarios import timestamp


def register(root, release_path, model_path=None):
    import hashlib

    raw = read_bytes(release_path)
    release = json.loads(raw)
    if release.get("schema_version") == "conditional-model-release-v1":
        if model_path is None:
            raise ValueError("Conditional receipt requires its original hashed model")
        model_raw = read_bytes(model_path)
        model = json.loads(model_raw)
        if (hashlib.sha256(model_raw).hexdigest() != release["model_file_sha256"]
                or model["model_sha256"] != release["model_sha256"]
                or fingerprint({k: v for k, v in model.items() if k != "model_sha256"}) != model["model_sha256"]
                or fingerprint({k: v for k, v in release.items() if k != "release_sha256"}) != release["release_sha256"]):
            raise ValueError("Conditional release/model changed")
        issued = release["published_at"]
        cutoff = release["knowledge_cutoff_at"]
        if timestamp(cutoff) > timestamp(issued):
            raise ValueError("Release predates inputs")
    else:
        release = read_release(release_path)
        issued = release["issued_at"]
    now = datetime.now(UTC)
    if timestamp(issued) > now:
        raise ValueError("Cannot register a future release")
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (release["release_sha256"] + ".json")
    if target.exists():
        prior = json.loads(read_bytes(target))
        if prior["record_sha256"] != fingerprint({k: v for k, v in prior.items() if k != "record_sha256"}):
            raise ValueError("Journal record changed")
        if prior["release_file_sha256"] != hashlib.sha256(raw).hexdigest():
            raise ValueError("Registered release changed")
        return target
    entry = {"stock_id": release["stock_id"], "release_sha256": release["release_sha256"],
             "release_file_sha256": hashlib.sha256(raw).hexdigest(), "issued_at": issued,
             "registered_at": now.isoformat(), "predictions": release["predictions"],
             "initial_registration_is_not_historical_daily_capture": True,
             "new_forecast_issued": False, "scoring_status": "pending_comparable_official_actuals"}
    entry["record_sha256"] = fingerprint(entry)
    with target.open("x", encoding="utf8") as stream:
        stream.write(canonical(entry) + "\n")
    return target


def error_summary(rows):
    """Aggregate already source-reviewed scorer outputs; pending stays out."""
    selected = [r for r in rows if r.get("status") in
                {"scored_selected_initial_actual", "scored_selected_actual"}]
    # Explicit evaluation records must identify prediction, actual, time and basis.
    valid = [r for r in selected if r.get("release_sha256") and r.get("source_sha256")
             and r.get("revision_id") and r.get("evaluated_at") and r.get("error") is not None]
    if len(valid) != len(selected):
        raise ValueError("Scoring provenance missing")
    identities = [(r["release_sha256"], r["source_sha256"], r["revision_id"]) for r in valid]
    if len(identities) != len(set(identities)):
        raise ValueError("Duplicate prediction/actual pair cannot inflate sample count")
    return {"n": len(valid), "pending_or_ineligible": len(rows) - len(valid),
            "mae": sum(abs(r["error"]) for r in valid) / len(valid) if valid else None,
            "mape": None, "mape_reason": "actual denominator not included in scorer output",
            "independent_sample_count": None, "overlapping_horizons_are_not_independent": True,
            "empirical_advantage_proven": False}


def evaluate(pairs, stock, documents, cutoff):
    """Future parameter outcomes enter only through official-note and release checks."""
    from .parameter_tracking import score
    from .review import Unavailable, _packet, _period

    results = []
    for pair in pairs:
        release, actual = pair["release"], pair["actual"]
        if release["stock_id"] != stock or actual["stock_id"] != stock:
            raise ValueError("Evaluation company mismatch")
        if (release.get("metric"), release.get("unit"), release.get("basis")) != (
                "eps", "TWD/share", "diluted_single_quarter"):
            raise ValueError("EPS calibration cannot mix revenue, basic EPS or annual measures")
        packet = pair["source_packet"]
        try:
            value = _packet(packet, stock, documents, timestamp(cutoff), ("value",))["value"]
        except Unavailable:
            results.append({"status": "pending_official_actual_source"})
            continue
        _period(packet, "single_quarter_diluted" if actual["metric"] == "eps" else "single_quarter")
        period = packet["period_end"][:4] + "-Q" + str(int(packet["period_end"][5:7]) // 3)
        if (value != actual["value"] or period != actual["period"]
                or actual["source_sha256"] not in {documents[k]["sha256"] for k in packet["document_ids"]}
                or actual["revision_id"] not in {documents[k]["revision_id"] for k in packet["document_ids"]}):
            raise ValueError("Actual value/quarter/version not bound to reviewed original")
        required = ("source_value_reviewed", "official_source_reviewed", "initial_publication_history_verified")
        if not all(actual.get(k) is True for k in required) or actual.get("revision_policy") != "first_official_release":
            results.append({"status": "pending_initial_actual_version"})
            continue
        results.append(score(release, actual, cutoff))
    return error_summary(results)
