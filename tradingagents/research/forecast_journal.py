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


def _research_json(path):
    return json.loads(read_bytes(path))


def _research_file_hash(path):
    import hashlib

    return hashlib.sha256(read_bytes(path)).hexdigest()


def read_research(directory):
    """Read a portable one-shot record and verify its bound research files."""
    from pathlib import PurePosixPath

    root = Path(directory).resolve()
    record = _research_json(root / "forecast-record.json")
    manifest = _research_json(root / "forecast-manifest.json")
    if (record["schema_version"] != "one-shot-research-v1"
            or manifest["schema_version"] != record["schema_version"]
            or manifest["research_id"] != record["research_id"]
            or manifest["record_file_sha256"] != _research_file_hash(root / "forecast-record.json")
            or manifest["manifest_sha256"] != fingerprint({k: v for k, v in manifest.items() if k != "manifest_sha256"})):
        raise ValueError("Research archive identity or record changed")
    files = manifest["files"]
    if not {"research.json", "snapshot.json", "report.html", "agent-run/state.json"} <= files.keys():
        raise ValueError("Missing research archive evidence")
    for name, digest in files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise ValueError("Invalid research archive member")
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or _research_file_hash(target) != digest:
            raise ValueError("Research archive evidence changed: " + name)
    return record, manifest


def seal_research(directory, data, parent_directory=None):
    """Append an immutable local edition inside the completed report's staging directory."""
    from uuid import uuid4

    from .scenarios import known_date

    root = Path(directory).resolve()
    record_path = root / "forecast-record.json"
    manifest_path = root / "forecast-manifest.json"
    if record_path.exists() or manifest_path.exists():
        raise ValueError("Never overwrite a sealed research edition")
    state = _research_json(root / "agent-run/state.json")
    if state["mode"] != "research" or (state["stock_id"], state["as_of"]) != (data["stock_id"], data["as_of"]):
        raise ValueError("Research archive needs a matching non-mock workflow")
    generated = timestamp(data["generated_at"])
    retrospect = known_date(data["generated_at"]).isoformat() > data["as_of"]
    previous = None
    if parent_directory is not None:
        prior, manifest = read_research(parent_directory)
        if prior["stock_id"] != data["stock_id"] or timestamp(prior["recorded_at"]) >= generated:
            raise ValueError("Superseded research must be an earlier edition of the same issuer")
        previous = {"research_id": prior["research_id"], "version": prior["version"],
                    "manifest_sha256": manifest["manifest_sha256"]}
    conditions = data["decision_conditions"]
    valuation = data["valuation_scenarios"]
    model = data.get("forward_estimate") or {}
    analyst_files = {name: f"agent-run/jobs/{name}/accepted.json"
                     for name in ("fundamental", "technical", "institutional")}
    if state["active_revision"]:
        analyst_files = {name: f"agent-run/jobs/{name}_update/accepted.json" for name in analyst_files}
    analysts = {}
    for name, path in analyst_files.items():
        analyst = _research_json(root / path)
        analysts[name] = {k: analyst[k] for k in ("agent_id", "stance", "summary", "conditions", "prompt_sha256")}
    final = _research_json(root / "agent-run/jobs/decision/accepted.json")
    record = {
        "schema_version": "one-shot-research-v1", "research_id": uuid4().hex,
        "version": previous["version"] + 1 if previous else 1, "supersedes": previous,
        "stock_id": data["stock_id"], "data_as_of": data["as_of"], "recorded_at": generated.isoformat(),
        "knowledge_cutoff_at": (data.get("extension", {}).get("research_plan", {}).get("context") or {}).get("cutoff_at"),
        "archive_kind": "retrospective_research" if retrospect else "same_day_research",
        "formal_prediction_release_issued": False,
        "snapshot_sha256": state["snapshot_sha256"], "input_revision": state["active_revision"],
        "prompt_templates": state["templates"], "agent_protocol": state["protocol"],
        "valuation_model_version": model.get("model_version"), "valuation_model_sha256": model.get("model_sha256"),
        "codex_model_version": None,
        "analysts": analysts, "final": {k: final[k] for k in ("stance", "summary", "conditions", "prompt_sha256")},
        "confidence_by_claim": {r["id"]: r["confidence"] for r in final["findings"]},
        "horizons": data["horizon_analysis"], "decision_conditions": conditions,
        "conditional_valuation": {"periods": valuation["periods"], "scenarios": valuation["scenarios"],
                                  "eligibility": valuation["status"]},
        "scoring_status": "ineligible_retrospective" if retrospect else
                          "pending" if conditions["status"] == "conditional_candidate" or valuation["periods"]
                          else "not_applicable",
    }
    # Snapshot all reproducibility files already rendered, before sealing the transport ZIP.
    files = {}
    for file in sorted(root.rglob("*")):
        if not file.is_file():
            continue
        name = file.relative_to(root).as_posix()
        if name in {"evidence.zip", "delivery-manifest.json", "portability.json", "workflow-delivery.json"}:
            continue
        if not file.resolve().is_relative_to(root):
            raise ValueError("Research archive member escapes report")
        files[name] = _research_file_hash(file)
    with record_path.open("x", encoding="utf8") as stream:
        stream.write(canonical(record) + "\n")
    manifest = {"schema_version": record["schema_version"], "research_id": record["research_id"],
                "record_file_sha256": _research_file_hash(record_path), "files": files}
    manifest["manifest_sha256"] = fingerprint(manifest)
    with manifest_path.open("x", encoding="utf8") as stream:
        stream.write(canonical(manifest) + "\n")
    return record
