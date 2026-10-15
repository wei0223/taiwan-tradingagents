import copy
import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime

from tests.taiwan.test_quality_upgrade import model_spec
from tests.taiwan.test_research import snapshot
from tradingagents.research.bundle import build_bundle, fingerprint
from tradingagents.research.numeric_review import recalculate, validate_opening
from tradingagents.research.publication import publish, read_release
from tradingagents.research.tracking import read_tracking, track


class NumericPublicationTests(unittest.TestCase):
    def setUp(self):
        self.bundle = build_bundle(snapshot())
        self.spec = model_spec(self.bundle)
        self.spec["created_at"] = "2024-02-10T00:00:00+00:00"
        self.ref = next(
            k for k, v in self.bundle["evidence"].items() if v["availability"] == "available"
        )
        self.change = {
            "id": "bull_x1",
            "target_scenario": "base",
            "period": "2024-Q1",
            "field": "revenue_twd",
            "proposed_value": 220,
            "rationale": "Explicit revenue stress test",
            "evidence_ids": [self.ref],
        }

    def test_single_and_joint_counterfactual_and_minority_policy(self):
        isolated = recalculate(self.bundle, self.spec, [self.change], False)
        coherent = recalculate(self.bundle, self.spec, [self.change])
        self.assertGreater(coherent["eps_delta"], 0)
        self.assertLess(coherent["eps_delta"], isolated["eps_delta"])
        pe = {
            **self.change,
            "id": "bull_x2",
            "period": None,
            "field": "valuation_pe",
            "proposed_value": 12,
        }
        value = recalculate(self.bundle, self.spec, [pe])
        self.assertAlmostEqual(value["eps_delta"], 0)
        self.assertGreater(value["price_delta"], 0)
        with self.assertRaises(ValueError):
            recalculate(self.bundle, self.spec, [pe, pe])

    def test_opening_schema_and_unavailable_evidence(self):
        claim = {"id": "bull_c1", "evidence_ids": [self.ref]}
        opening = {
            "role": "bull",
            "stage": "opening",
            "bundle_sha256": fingerprint(self.bundle),
            "summary": "Synthetic review",
            "claims": [claim, {**claim, "id": "bull_c2"}],
            "challenges": [self.change],
            "limitations": [],
        }
        self.assertTrue(validate_opening(opening, "bull", self.bundle))
        invalid = copy.deepcopy(opening)
        invalid["challenges"][0]["proposed_value"] = float("nan")
        with self.assertRaises(ValueError):
            validate_opening(invalid, "bull", self.bundle)

    def test_release_is_issued_now_exclusive_and_hash_verified(self):
        debate = {
            "complete": True,
            "bundle_sha256": fingerprint(self.bundle),
            "independent_execution": "synthetic_fixture",
        }

        def clock():
            return datetime(2024, 2, 10, 1, tzinfo=UTC)

        with tempfile.TemporaryDirectory() as folder:
            p = publish(folder, self.bundle, self.spec, debate, "test-v1", clock)
            r = read_release(p)
            self.assertEqual(r["forecast_status"], "pending_future_actuals")
            self.assertEqual(r["issued_at"], "2024-02-10T01:00:00+00:00")
            self.assertEqual(len(r["predictions"]), 48)
            with self.assertRaises(FileExistsError):
                publish(folder, self.bundle, self.spec, debate, "test-v1", clock)
            r["predictions"][0]["value"] = 999
            p.write_text(json.dumps(r), encoding="utf-8")
            with self.assertRaises(ValueError):
                read_release(p)

    def test_old_inputs_cannot_be_backdated_into_a_forecast(self):
        debate = {"complete": True, "bundle_sha256": fingerprint(self.bundle)}
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                publish(
                    folder,
                    self.bundle,
                    self.spec,
                    debate,
                    "v1",
                    lambda: datetime(2026, 10, 15, tzinfo=UTC),
                )
            with self.assertRaises(ValueError):
                publish(
                    folder,
                    self.bundle,
                    self.spec,
                    {**debate, "complete": False},
                    "v1",
                    lambda: datetime(2024, 2, 10, 1, tzinfo=UTC),
                )

    def test_tracking_pending_and_explicit_source_vintage(self):
        debate = {"complete": True, "bundle_sha256": fingerprint(self.bundle)}
        with tempfile.TemporaryDirectory() as folder:
            release = publish(
                folder,
                self.bundle,
                self.spec,
                debate,
                "fixture",
                lambda: datetime(2024, 2, 10, 1, tzinfo=UTC),
            )
            pending = track(release, [], folder, lambda: datetime(2024, 2, 11, tzinfo=UTC))
            self.assertEqual(read_tracking(pending)["results"]["status"], "pending_future_actuals")
            source = pending.parent / "original-filing.txt"
            source.write_bytes(b"synthetic reviewed original source")
            actual = {
                "stock_id": self.bundle["stock_id"],
                "period": "2024-Q1",
                "metric": "eps",
                "basis": "diluted_single_quarter",
                "value": 10,
                "published_at": "2024-05-15T00:00:00+00:00",
                "captured_at": "2024-05-16T00:00:00+00:00",
                "source_file": str(source),
                "source_url": "https://example.invalid/mock",
                "revision_id": "original",
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
            result = track(release, [actual], folder, lambda: datetime(2024, 5, 17, tzinfo=UTC))
            self.assertEqual(len(read_tracking(result)["results"]["resolved"]), 3)
            actual["captured_at"] = "2024-05-17T23:00:00+00:00"
            early = track(release, [actual], folder, lambda: datetime(2024, 5, 17, tzinfo=UTC))
            self.assertEqual(read_tracking(early)["results"]["status"], "pending_future_actuals")
            actual["source_sha256"] = "0" * 64
            with self.assertRaises(ValueError):
                track(release, [actual], folder, lambda: datetime(2024, 5, 17, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
