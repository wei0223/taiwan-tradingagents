"""Outcome rehearsal isolation, exact scoring and readable report contracts."""

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from tests.taiwan.test_review_tracking import actual_fixture, sealed_fixture
from tradingagents.research.__main__ import main
from tradingagents.research.bundle import canonical, fingerprint
from tradingagents.research.tracking import track
from tradingagents.research.tracking_drill import CASES, rehearse
from tradingagents.research.tracking_report import render_tracking, write_tracking_report


class TrackingDrillTests(unittest.TestCase):
    def test_all_rehearsal_paths_without_mutating_release(self):
        with tempfile.TemporaryDirectory() as root:
            sealed = Path(root) / "sealed"
            sealed.mkdir()
            release = sealed_fixture(sealed)
            original = release.read_bytes()
            output = Path(root) / "mock-only"
            result = rehearse([release], output)
            self.assertEqual(result["passed"], len(CASES))
            self.assertEqual(release.read_bytes(), original)
            self.assertEqual(result["new_releases"], 0)
            self.assertTrue((output / "MOCK_ONLY.txt").exists())
            for case in result["cases"]:
                if "report" in case:
                    self.assertIn("不是正式績效", (output / case["report"]).read_text(encoding="utf-8"))
            with self.assertRaises(FileExistsError):
                rehearse([release], output)
            with self.assertRaisesRegex(ValueError, "release directory"):
                rehearse([release], sealed / "inside-release")

    def setup_report(self, root, rows=None):
        release = sealed_fixture(root)
        actual = actual_fixture(root)
        path = track(release, [actual] if rows is None else rows, Path(root) / "tracking",
                     clock=lambda: datetime(2026, 11, 11, tzinfo=UTC))
        return release, path

    def test_primary_report_prediction_actual_and_error(self):
        with tempfile.TemporaryDirectory() as root:
            release, path = self.setup_report(root)
            report, page = render_tracking(release, path)
            momentum = next(r for r in report["rows"] if r["model_version"] == "momentum")
            self.assertEqual((momentum["prediction"], momentum["actual"], momentum["error_prediction_minus_actual"]), (120, 100, 20))
            self.assertEqual(report["assumption_attribution"]["status"], "unavailable")
            self.assertIn("正值表示高估", page)
            sidecar = write_tracking_report(release, path)
            before = sidecar.read_bytes()
            self.assertEqual(sidecar, write_tracking_report(release, path))
            self.assertEqual(sidecar.read_bytes(), before)

    def test_pending_has_no_invented_error(self):
        with tempfile.TemporaryDirectory() as root:
            release, path = self.setup_report(root, [])
            report, _ = render_tracking(release, path)
            self.assertTrue(all(r["actual"] is None and r["absolute_error"] is None for r in report["rows"]))
            self.assertFalse(report["primary_metrics"]["mae_by_model_metric_basis"])

    def test_mock_cannot_render_as_official(self):
        with tempfile.TemporaryDirectory() as root:
            release = sealed_fixture(root)
            row = actual_fixture(root)
            row["evidence_kind"] = "mock"
            _, path = self.setup_report(root, [row])
            with self.assertRaisesRegex(ValueError, "Mock actuals"):
                render_tracking(release, path)
            report, _ = render_tracking(release, path, mock=True)
            self.assertEqual(report["evaluation_mode"], "mock_rehearsal_not_performance")

    def test_report_rejects_other_prediction_set_even_with_valid_tracking_hash(self):
        with tempfile.TemporaryDirectory() as root:
            release, path = self.setup_report(root)
            result = json.loads(path.read_text(encoding="utf-8"))
            result["predictions_sha256"] = "0" * 64
            result.pop("tracking_sha256")
            result["tracking_sha256"] = fingerprint(result)
            altered = path.parent / (result["tracking_sha256"] + ".json")
            altered.write_text(canonical(result), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "sealed release"):
                render_tracking(release, altered)

    def test_restatement_report_never_displays_diagnostic_as_primary_actual(self):
        with tempfile.TemporaryDirectory() as root:
            row = actual_fixture(root)
            row["vintage_kind"] = "restated"
            release, path = self.setup_report(root, [row])
            report, _ = render_tracking(release, path)
            self.assertTrue(all(r["actual"] is None for r in report["rows"]))
            self.assertEqual(len(report["selected_revision_diagnostics_not_primary"]["resolved"]), 3)

    def test_cli_rejects_mock_without_label_before_creating_outputs(self):
        with tempfile.TemporaryDirectory() as root:
            release = sealed_fixture(root)
            row = actual_fixture(root)
            row["evidence_kind"] = "mock"
            actuals = Path(root) / "actuals.json"
            actuals.write_text(json.dumps([row]), encoding="utf-8")
            output = Path(root) / "official"
            self.assertNotEqual(main(["track-release", "--release", str(release),
                "--actuals", str(actuals), "--output-dir", str(output)]), 0)
            self.assertFalse(output.exists())

    def test_cli_pending_report_uses_only_existing_release(self):
        with tempfile.TemporaryDirectory() as root:
            release = sealed_fixture(root)
            before = release.read_bytes()
            output = Path(root) / "reports"
            self.assertEqual(main(["track-release", "--release", str(release),
                "--output-dir", str(output), "--report"]), 0)
            pages = list(output.glob("*/*.html"))
            self.assertEqual(len(pages), 1)
            self.assertIn("待合格實績", pages[0].read_text(encoding="utf-8"))
            self.assertEqual(release.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
