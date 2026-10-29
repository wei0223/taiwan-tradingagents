"""Append-only outcomes against a sealed release; no retrospective forecast substitution."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from .bundle import canonical, fingerprint
from .publication import read_release
from .scenarios import timestamp
from .validation import forecast_errors


def _path(value):
    path = Path(value)
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(path.resolve()))
    return path


def track(release_path, actuals, output_root, clock=None, *, mock=False):
    release_path = _path(release_path)
    release = read_release(release_path)
    original_bytes = Path(release_path).read_bytes()
    now = (clock or (lambda: datetime.now(UTC)))()
    if now.tzinfo is None:
        raise ValueError("Tracking time requires timezone")
    now = now.astimezone(UTC)
    for row in actuals:
        if row.get("stock_id") != release["stock_id"]:
            raise ValueError("Actual company mismatch")
        if not row.get("source_url") or not row.get("source_sha256") or not row.get("revision_id"):
            raise ValueError("Actual source and selected revision required")
        if len(row["source_sha256"]) != 64:
            raise ValueError("Invalid source digest")
        if (
            hashlib.sha256(_path(row["source_file"]).read_bytes()).hexdigest()
            != row["source_sha256"]
        ):
            raise ValueError("Actual archived source bytes mismatch")
    available = [
        row
        for row in actuals
        if row.get("published_at")
        and row.get("captured_at")
        and max(timestamp(row["published_at"]), timestamp(row["captured_at"])) <= now
    ]
    already_public = [
        r for r in available if timestamp(r["published_at"]) <= timestamp(release["issued_at"])
    ]
    available = [r for r in available if r not in already_public]
    initial = [
        r
        for r in available
        if r.get("vintage_kind") == "initial"
        and r.get("initial_publication_verified") is True
        and r.get("source_value_reviewed") is True
        and r.get("initial_publication_history_verified") is True
        and r.get("revision_policy") == "first_official_release"
        and r.get("official_source_reviewed") is True
        and r.get("source_locator")
    ]
    result = {
        "evaluation_mode": "mock_rehearsal_not_performance"
        if mock or any(r.get("evidence_kind") == "mock" for r in actuals) else "official_tracking",
        "release_sha256": release["release_sha256"],
        "checked_at": now.isoformat(),
        "actuals": actuals,
        "results": forecast_errors(release["predictions"], available, now.date().isoformat()),
        "primary_initial_vintage_results": forecast_errors(
            release["predictions"], initial, now.date().isoformat()
        ),
        "primary_vintage_unverified": [r for r in available if r not in initial],
        "excluded_already_public": already_public,
        "unavailable_time_metadata": [
            r for r in actuals if not r.get("published_at") or not r.get("captured_at")
        ],
        "selected_revision_policy": "results are selected-revision diagnostics; only primary_initial_vintage_results score verified first announcements",
        "predictions_sha256": fingerprint(release["predictions"]),
        "original_issued_at": release["issued_at"],
        "scope": "Exact sealed metric/period/basis only, including monthly revenue; no claimed trading or independent overlapping samples",
    }
    result["tracking_sha256"] = fingerprint(result)
    if Path(release_path).read_bytes() != original_bytes:
        raise ValueError("Original release changed during tracking")
    path = _path(output_root) / release["release_sha256"] / (result["tracking_sha256"] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if read_tracking(path) != result:
            raise ValueError("Tracking hash collision")
    else:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical(result) + "\n")
    return path


def read_tracking(path):
    path = _path(path)
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        result["tracking_sha256"]
        != fingerprint({k: v for k, v in result.items() if k != "tracking_sha256"})
        or Path(path).stem != result["tracking_sha256"]
    ):
        raise ValueError("Tracking record modified")
    return result
