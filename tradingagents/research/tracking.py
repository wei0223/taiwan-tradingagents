"""Append-only outcomes against a sealed release; no retrospective forecast substitution."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .bundle import canonical, fingerprint
from .publication import read_release
from .scenarios import timestamp
from .validation import forecast_errors


def track(release_path, actuals, output_root, clock=None):
    release = read_release(release_path)
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
            hashlib.sha256(Path(row["source_file"]).read_bytes()).hexdigest()
            != row["source_sha256"]
        ):
            raise ValueError("Actual archived source bytes mismatch")
    available = [
        row
        for row in actuals
        if max(timestamp(row["published_at"]), timestamp(row["captured_at"])) <= now
    ]
    result = {
        "release_sha256": release["release_sha256"],
        "checked_at": now.isoformat(),
        "actuals": actuals,
        "results": forecast_errors(release["predictions"], available, now.date().isoformat()),
        "scope": "quarterly forecasts only; no claimed trading signal or strategy return",
    }
    result["tracking_sha256"] = fingerprint(result)
    path = Path(output_root) / release["release_sha256"] / (result["tracking_sha256"] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical(result) + "\n")
    return path


def read_tracking(path):
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        result["tracking_sha256"]
        != fingerprint({k: v for k, v in result.items() if k != "tracking_sha256"})
        or Path(path).stem != result["tracking_sha256"]
    ):
        raise ValueError("Tracking record modified")
    return result
