import copy
import unittest

from tradingagents.research.earnings_bridge import bridge, eps
from tradingagents.research.predictive import (
    available,
    calibrated_weights,
    estimate,
    month_name,
    walk_forward,
)
from tradingagents.research.trade_rules import next_open, setup
from tradingagents.research.valuation_drivers import event_effect, forward_history, matrix


def observations():
    return [
        {
            "period": month_name(i),
            "value": 100 + i - 2020 * 12,
            "published_at": month_name(i + 1) + "-08T00:00:00+08:00",
            "captured_at": month_name(i + 1) + "-09T00:00:00+08:00",
            "source_sha256": "a" * 64,
            "revision_id": "first",
        }
        for i in range(2020 * 12, 2024 * 12)
    ]


class PredictiveTests(unittest.TestCase):
    def test_eps_bridge_sums_without_order_dependent_attribution(self):
        original = {
            "revenue_twd": 1000,
            "op_margin": 0.2,
            "nonoperating": 10,
            "tax_rate": 0.2,
            "minority_ratio": 0.1,
            "diluted_shares": 100,
            "dilution_adjustment": 0,
        }
        revised = {**original, "revenue_twd": 1200, "op_margin": 0.25, "minority_ratio": 0.15}
        result = bridge(original, revised)
        self.assertAlmostEqual(sum(result["contributions"].values()), eps(revised) - eps(original))
        self.assertAlmostEqual(result["contributions"]["tax_rate"], 0)

    def test_cutoff_excludes_later_publication_and_capture(self):
        rows = observations()
        cutoff = "2022-10-15T00:00:00+08:00"
        original = available(rows, cutoff)
        row = copy.deepcopy(rows[0])
        row["captured_at"] = "2024-01-01T00:00:00+08:00"
        self.assertNotIn(2020 * 12, available([row], cutoff))
        changed = copy.deepcopy(rows)
        for row in changed:
            if row["period"] >= "2022-10":
                row["value"] *= 1000
        args = (cutoff, ["2022-10", "2022-11", "2022-12"], "log_trend")
        self.assertEqual(estimate(rows, *args), estimate(changed, *args))
        self.assertEqual(len(original), 33)

    def test_three_methods_missing_industry_and_no_fabricated_weights(self):
        rows = observations()
        cut = "2022-10-15T00:00:00+08:00"
        target = ["2022-10", "2022-11", "2022-12"]
        for method in ["log_trend", "monthly_momentum"]:
            self.assertEqual(estimate(rows, cut, target, method)["status"], "available")
        self.assertEqual(estimate(rows, cut, target, "industry_growth")["status"], "unavailable")
        result = walk_forward(
            rows, [{"cutoff": cut, "targets": target}], "2024-02-01T00:00:00+08:00"
        )
        self.assertEqual(
            set(result["folds"][0]["absolute_errors"]), {"log_trend", "monthly_momentum"}
        )
        self.assertIsNone(calibrated_weights(result, "2024-02-02T00:00:00+08:00")["weights"])
        with self.assertRaises(ValueError):
            calibrated_weights(result, "2023-01-01T00:00:00+08:00")
        for row in rows:
            row["captured_at"] = "2024-01-10T00:00:00+08:00"
        replay = walk_forward(
            rows, [{"cutoff": cut, "targets": target}], "2024-02-01T00:00:00+08:00"
        )
        self.assertEqual(replay["status"], "unavailable")

    def test_valuation_is_conditional_and_rejects_future_denominator(self):
        self.assertEqual(matrix([100, 120], [20, 30])["prices"], [[2000, 3000], [2400, 3600]])
        row = {
            "basis": "forward_four_quarter_diluted",
            "horizon_months": 12,
            "source_sha256": "a" * 64,
            "eps_published_at": "2024-02-01T00:00:00+08:00",
            "eps_captured_at": "2024-02-01T00:00:00+08:00",
            "price_at": "2024-01-01T00:00:00+08:00",
            "price": 100,
            "eps": 10,
        }
        with self.assertRaises(ValueError):
            forward_history([row], "2024-03-01T00:00:00+08:00")
        self.assertEqual(forward_history([], "2024-03-01")["status"], "unavailable")

    def test_common_fold_calibration_does_not_claim_holdout_success(self):
        rows = observations()
        folds = []
        drivers = {}
        for month in [1, 2, 3, 4, 5, 6]:
            cutoff = f"2022-{month:02d}-15T00:00:00+08:00"
            folds.append({"cutoff": cutoff, "targets": [f"2022-{month:02d}"]})
            proof = {
                "source_url": "https://example.invalid/mock",
                "source_sha256": "a" * 64,
                "published_at": "2021-01-01",
                "captured_at": "2021-01-02",
            }
            drivers[cutoff] = {
                "independently_sourced": True,
                **{
                    k: {**proof, "value": v}
                    for k, v in [
                        ("industry_growth", 0.1),
                        ("exposure", 0.5),
                        ("other_growth", 0.02),
                    ]
                },
            }
        validation = walk_forward(rows, folds, "2024-02-01T00:00:00+08:00", drivers)
        result = calibrated_weights(validation, "2024-02-02T00:00:00+08:00")
        self.assertAlmostEqual(sum(result["weights"].values()), 1)
        self.assertFalse(result["holdout_superiority_proven"])
        self.assertEqual(result["paired_folds"], 6)

    def test_industry_event_has_no_fake_eps_and_driver_linkage_required(self):
        proof = {
            "id": "P",
            "source_url": "https://example.invalid/mock",
            "source_sha256": "a" * 64,
            "published_at": "2024-01-01",
            "captured_at": "2024-01-02",
        }
        self.assertEqual(event_effect({}, "2024-02-01")["grade"], "insufficient_evidence")
        event = {"evidence": [proof]}
        self.assertIsNone(event_effect(event, "2024-02-01")["eps_delta"])
        vals = {
            "market_units": 1000,
            "penetration_delta": 0.05,
            "company_share": 0.1,
            "content_asp_twd": 100,
            "cannibalized_revenue_twd": 0,
            "incremental_operating_margin": 0.2,
            "incremental_fixed_cost_twd": 0,
            "tax_rate": 0.2,
            "parent_profit_fraction": 1,
            "diluted_shares": 100,
        }
        event["inputs"] = {
            k: {"value": v, "kind": "assumption", "evidence_ids": ["P"]} for k, v in vals.items()
        }
        self.assertAlmostEqual(event_effect(event, "2024-02-01")["eps_delta"], 0.8)
        self.assertIsNone(event_effect(event, "2023-01-01")["eps_delta"])

    def test_trade_rules_calendar_volume_and_next_open_gap(self):
        bars = [
            {
                "date": f"2024-01-{i + 1:02d}",
                "open": 100,
                "high": 110,
                "low": 90,
                "close": 100,
                "volume": 100,
                "basis": "vendor_adjusted",
            }
            for i in range(22)
        ]
        dates = [b["date"] for b in bars]
        self.assertEqual(setup(bars, dates)["status"], "observe")
        with self.assertRaises(ValueError):
            setup(bars, dates[:-1])
        plan = {
            "status": "candidate_pending_next_open",
            "stop_twd": 90,
            "target_twd": 130,
            "thresholds": {"min_rr": 2},
        }
        self.assertEqual(next_open(plan, 110)["status"], "skip")
        self.assertEqual(next_open(plan, 100)["status"], "eligible_for_simulation")
        self.assertEqual(next_open(plan, 80)["status"], "skip")


if __name__ == "__main__":
    unittest.main()
