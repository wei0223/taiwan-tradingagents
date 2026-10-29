"""Offline rehearsal using existing releases and explicitly fictional actuals."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from .publication import read_release
from .scenarios import timestamp
from .tracking import _path, read_tracking, track
from .tracking_report import write_tracking_report

CASES = ("initial", "restated", "wrong_period", "basic_eps", "missing_review",
         "missing_time", "future_capture", "preissue", "missing_source", "tampered_source", "no_actuals")


def rehearse(release_paths, output_root):
    """Create a new mock-only directory. Never call any release publisher."""
    root = _path(output_root)
    paths = [_path(p) for p in release_paths]
    if any(root.is_relative_to(p.parent) for p in paths):
        raise ValueError("Mock output cannot be inside a release directory")
    # An existing directory is intentionally refused, including any delivery or tracking directory.
    root.mkdir(parents=True, exist_ok=False)
    (root / "MOCK_ONLY.txt").write_text("虛構實績演練，禁止納入正式績效。\n", encoding="utf-8")
    before = {str(p): p.read_bytes() for p in paths}
    results = []
    with patch("tradingagents.research.publication.publish", side_effect=AssertionError("No publishing")), \
            patch("tradingagents.research.prospective.publish", side_effect=AssertionError("No publishing")):
        for path in paths:
            release = read_release(path)
            # Simulated future clock is isolated here; this is not a real capture/publication date.
            now = max(timestamp(release["issued_at"]) + timedelta(days=500),
                      timestamp("2028-12-01T00:00:00+00:00"))
            for case in CASES:
                case_dir = root / release["stock_id"] / case
                case_dir.mkdir(parents=True)
                actuals = []
                seen = set()
                for i, p in enumerate(release["predictions"]):
                    identity = tuple(p[k] for k in ("stock_id", "period", "metric", "basis"))
                    if identity in seen:
                        continue
                    seen.add(identity)
                    source = case_dir / (str(i) + "-MOCK.txt")
                    value = p["value"] * 0.9
                    source.write_text("MOCK fictional actual: " + str(value), encoding="utf-8")
                    row = {k: p[k] for k in ("stock_id", "period", "metric", "basis")}
                    row.update(value=value, evidence_kind="mock", source_file=str(source),
                        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                        source_url="https://example.invalid/MOCK", revision_id="MOCK-initial",
                        source_locator="MOCK numeric cell; not an official document",
                        published_at=(now - timedelta(days=2)).isoformat(),
                        captured_at=(now - timedelta(days=1)).isoformat(), vintage_kind="initial",
                        initial_publication_verified=True, source_value_reviewed=True,
                        initial_publication_history_verified=True, revision_policy="first_official_release",
                        official_source_reviewed=True)
                    if case == "restated":
                        row.update(vintage_kind="restated", revision_id="MOCK-corrected", revision_policy="selected_revision")
                    elif case == "wrong_period":
                        row["period"] = "2099-Q4"
                    elif case == "basic_eps":
                        if row["metric"] == "eps" or not any(p["metric"] == "eps" for p in release["predictions"]):
                            row.update(metric="eps", basis="basic_single_quarter")
                    elif case == "missing_review":
                        row["source_value_reviewed"] = False
                    elif case == "missing_time":
                        row["published_at"] = None
                    elif case == "future_capture":
                        row["captured_at"] = (now + timedelta(days=1)).isoformat()
                    elif case == "preissue":
                        row["published_at"] = (timestamp(release["issued_at"]) - timedelta(days=1)).isoformat()
                    elif case == "missing_source":
                        row.pop("source_url")
                    elif case == "tampered_source":
                        source.write_text("MOCK corrupted bytes", encoding="utf-8")
                    actuals.append(row)
                if case == "no_actuals":
                    actuals = []
                (case_dir / "MOCK-actuals.json").write_text(json.dumps(actuals, ensure_ascii=False, indent=2), encoding="utf-8")
                rejected = case in {"missing_source", "tampered_source"}
                try:
                    record_path = track(path, actuals, case_dir / "mock-tracking", clock=lambda now=now: now, mock=True)
                except ValueError as exc:
                    if not rejected:
                        raise
                    result = {"stock_id": release["stock_id"], "case": case, "passed": True,
                              "expected_rejection": str(exc)}
                else:
                    if rejected:
                        raise AssertionError("Invalid source accepted")
                    record = read_tracking(record_path)
                    primary = record["primary_initial_vintage_results"]
                    expected = len(release["predictions"]) if case == "initial" else 0
                    if case == "basic_eps":
                        expected = sum(p["metric"] != "eps" for p in release["predictions"]) if any(p["metric"] == "eps" for p in release["predictions"]) else 0
                    assert len(primary["resolved"]) == expected, (case, primary)
                    if not expected:
                        assert not primary["mae_by_model_metric_basis"]
                    if case == "initial":
                        for score in primary["resolved"]:
                            prediction = next(p for p in release["predictions"] if all(p[k] == score[k] for k in ("stock_id", "period", "metric", "basis", "model_version")))
                            actual = next(a for a in actuals if all(a[k] == score[k] for k in ("stock_id", "period", "metric", "basis")))
                            assert score["error"] == prediction["value"] - actual["value"]
                    if case == "restated":
                        assert len(record["results"]["resolved"]) == len(release["predictions"])
                    report = write_tracking_report(path, record_path, mock=True)
                    result = {"stock_id": release["stock_id"], "case": case, "passed": True,
                              "primary_resolved": expected, "primary_pending": len(primary["pending"]),
                              "report": str(Path(str(report)).relative_to(root)),
                              "tracking": str(Path(str(record_path)).relative_to(root))}
                results.append(result)
    assert all(p.read_bytes() == before[str(p)] for p in paths)
    manifest = {"evaluation_mode": "mock_rehearsal_not_performance", "cases": results,
                "passed": len(results), "original_release_bytes_unchanged": True,
                "new_releases": 0, "network_calls": 0,
                "sources_are_fictional": True, "simulated_clock_is_not_real_publication": True}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
