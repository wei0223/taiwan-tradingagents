"""Sealed monthly forecasts, original vintages and immutable review delivery."""

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from tests.taiwan.test_quality_upgrade import model_spec
from tests.taiwan.test_research import snapshot
from tradingagents.research.__main__ import main
from tradingagents.research.bundle import build_bundle, canonical, fingerprint
from tradingagents.research.publication import read_release
from tradingagents.research.reporting import package
from tradingagents.research.tracking import read_tracking, track
from tradingagents.research.validation import forecast_errors


def sealed_fixture(root):
    record = {
        "schema_version": 2,
        "release_kind": "monthly_revenue_only_research_delivery",
        "stock_id": "3231",
        "issued_at": "2026-10-15T11:25:47+00:00",
        "input_bundle": {},
        "specification": {},
        "debate": {},
        "predictions": [],
    }
    for field in ("bundle", "specification", "debate"):
        record[field + "_sha256"] = fingerprint(
            record["input_bundle" if field == "bundle" else field]
        )
    for model, value in [("momentum", 120), ("trend", 110), ("naive", 100)]:
        record["predictions"].append(
            {
                "stock_id": "3231",
                "period": "2026-10",
                "metric": "revenue",
                "basis": "monthly_consolidated_revenue_twd",
                "value": value,
                "model_version": model,
                "issued_at": record["issued_at"],
                "input_snapshot_sha256": fingerprint({}),
            }
        )
    record["release_sha256"] = fingerprint(record)
    path = Path(root) / (record["release_sha256"] + ".json")
    path.write_text(canonical(record), encoding="utf-8")
    return path


def actual_fixture(root):
    path = Path(root) / "synthetic-original-announcement.txt"
    path.write_bytes(b"Synthetic October revenue 100 TWD")
    return {
        "stock_id": "3231",
        "period": "2026-10",
        "metric": "revenue",
        "basis": "monthly_consolidated_revenue_twd",
        "value": 100,
        "published_at": "2026-11-10T01:00:00+00:00",
        "captured_at": "2026-11-10T02:00:00+00:00",
        "source_file": str(path),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source_url": "https://example.invalid/synthetic",
        "revision_id": "fixture-initial",
        "vintage_kind": "initial",
        "initial_publication_verified": True,
        "source_value_reviewed": True,
        "initial_publication_history_verified": True,
        "revision_policy": "first_official_release",
        "official_source_reviewed": True,
        "source_locator": "Synthetic first announcement numeric cell",
    }


class ReviewTrackingTests(unittest.TestCase):
    def test_monthly_schema_two_keeps_legacy_read_and_tracking_contract(self):
        with tempfile.TemporaryDirectory() as root:
            path = sealed_fixture(root)
            before = path.read_bytes()
            release = read_release(path)
            self.assertEqual(release["schema_version"], 2)
            self.assertEqual(release["release_kind"], "monthly_revenue_only_research_delivery")
            self.assertNotIn("sources", release)
            result = read_tracking(track(path, [], root, lambda: datetime(2026, 10, 26, tzinfo=UTC)))
            self.assertEqual(len(result["results"]["pending"]), 3)
            self.assertEqual(result["original_issued_at"], release["issued_at"])
            self.assertEqual(path.read_bytes(), before)

    def test_unknown_actual_publication_retained_but_not_scored(self):
        with tempfile.TemporaryDirectory() as root:
            path = sealed_fixture(root)
            row = actual_fixture(root)
            row["published_at"] = None
            result = self.check_result(path, [row], root)
            self.assertEqual(
                result["primary_initial_vintage_results"]["status"], "pending_future_actuals"
            )
            self.assertEqual(len(result["unavailable_time_metadata"]), 1)

    def test_changed_model_cannot_be_attached_to_original_release_as_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            path = sealed_fixture(root)
            stock_snapshot = snapshot("3231")
            model = model_spec(build_bundle(stock_snapshot))
            with self.assertRaisesRegex(ValueError, "Changed model requires"):
                package(stock_snapshot, Path(root) / "new-review", model, release_path=path)

    def test_pairing_uses_common_targets_not_method_specific_samples(self):
        with tempfile.TemporaryDirectory() as root:
            path = sealed_fixture(root)
            predictions = json.loads(path.read_text(encoding="utf-8"))["predictions"]
            actual = actual_fixture(root)
            predictions[0]["period"] = "2026-11"
            r = forecast_errors(predictions, [actual], "2026-11-12")
            paired = r["paired_by_metric_basis"]["revenue:monthly_consolidated_revenue_twd"]
            self.assertFalse(paired["common_company_periods"])
            self.assertIsNone(paired["models"]["trend"]["mae"])

    def check_result(self, release, actuals, root, day=11):
        return read_tracking(
            track(
                release,
                actuals,
                Path(root) / "tracking",
                lambda: datetime(2026, 11, day, tzinfo=UTC),
            )
        )

    def test_monthly_primary_mae_wape_and_release_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            path = sealed_fixture(root)
            before = path.read_bytes()
            row = actual_fixture(root)
            result = self.check_result(path, [row], root)
            primary = result["primary_initial_vintage_results"]
            self.assertEqual(len(primary["resolved"]), 3)
            self.assertEqual(
                primary["mae_by_model_metric_basis"][
                    "momentum:revenue:monthly_consolidated_revenue_twd"
                ],
                20,
            )
            self.assertEqual(
                primary["wape_by_model_metric_basis"][
                    "momentum:revenue:monthly_consolidated_revenue_twd"
                ],
                0.2,
            )
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(result, self.check_result(path, [row], root))

    def test_restatement_or_missing_initial_history_cannot_enter_primary(self):
        for field, value in [
            ("vintage_kind", "restated"),
            ("initial_publication_history_verified", False),
            ("source_value_reviewed", False),
            ("official_source_reviewed", False),
        ]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                path = sealed_fixture(root)
                row = actual_fixture(root)
                row[field] = value
                result = self.check_result(path, [row], root)
                self.assertEqual(
                    result["primary_initial_vintage_results"]["status"], "pending_future_actuals"
                )
                self.assertEqual(result["results"]["status"], "available")

    def test_capture_after_check_is_not_scored_and_preissue_actual_excluded(self):
        with tempfile.TemporaryDirectory() as root:
            path = sealed_fixture(root)
            row = actual_fixture(root)
            row["captured_at"] = "2026-11-12T00:00:00+00:00"
            self.assertEqual(
                self.check_result(path, [row], root)["results"]["status"], "pending_future_actuals"
            )
            row["published_at"] = "2026-10-14T00:00:00+00:00"
            row["captured_at"] = "2026-10-15T00:00:00+00:00"
            result = self.check_result(path, [row], root)
            self.assertEqual(len(result["excluded_already_public"]), 1)
            self.assertFalse(result["results"]["resolved"])

    def test_official_quarter_revenue_cannot_score_monthly_release_sum(self):
        with tempfile.TemporaryDirectory() as root:
            path = sealed_fixture(root)
            row = actual_fixture(root)
            row.update(period="2026-Q4", basis="single_quarter_twd")
            self.assertFalse(self.check_result(path, [row], root)["results"]["resolved"])

    def test_altered_original_source_and_ambiguous_revisions_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = sealed_fixture(root)
            row = actual_fixture(root)
            with self.assertRaises(ValueError):
                self.check_result(path, [row, row], root)
            Path(row["source_file"]).write_bytes(b"corrupted")
            with self.assertRaises(ValueError):
                self.check_result(path, [row], root)

    def test_cli_pending_does_not_reissue_predictions(self):
        with tempfile.TemporaryDirectory() as root:
            path = sealed_fixture(root)
            before = path.read_bytes()
            self.assertEqual(
                main(
                    [
                        "track-release",
                        "--release",
                        str(path),
                        "--output-dir",
                        str(Path(root) / "out"),
                    ]
                ),
                0,
            )
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
