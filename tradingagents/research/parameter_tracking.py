"""Score a released parameter forecast only against a selected official actual."""

from .bundle import fingerprint
from .scenarios import finite, timestamp


def score(release, actual, evaluated_at):
    expected = fingerprint({k: v for k, v in release.items() if k != "release_sha256"})
    if expected != release.get("release_sha256"):
        raise ValueError("Parameter release modified")
    if any(
        actual.get(k) != release.get(k) for k in ("stock_id", "period", "metric", "unit", "basis")
    ):
        raise ValueError("Actual parameter is not comparable")
    if (
        not actual.get("source_sha256")
        or not actual.get("locator")
        or not actual.get("revision_id")
    ):
        return {"status": "pending_official_version_review", "error": None}
    if not actual.get("initial_publication_verified") or actual.get("vintage_kind") != "initial":
        return {"status": "pending_initial_actual_version", "error": None}
    if timestamp(actual["published_at"]) <= timestamp(release["issued_at"]):
        return {"status": "ineligible_already_public", "error": None}
    if max(timestamp(actual["published_at"]), timestamp(actual["captured_at"])) > timestamp(
        evaluated_at
    ):
        return {"status": "pending_actual", "error": None}
    prediction, observed = finite(release["value"]), finite(actual["value"])
    return {
        "status": "scored_selected_initial_actual",
        "release_sha256": expected,
        "source_sha256": actual["source_sha256"],
        "revision_id": actual["revision_id"],
        "error": prediction - observed,
        "absolute_error": abs(prediction - observed),
        "evaluated_at": evaluated_at,
        "original_release_modified": False,
    }
