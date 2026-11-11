import unittest

from tests.taiwan.test_forward_estimate import fixture
from tradingagents.research.forward_estimate import build as estimate
from tradingagents.research.valuation_horizons import build


class ValuationHorizonTests(unittest.TestCase):
    def data(self):
        spec, plan, quality, docs = fixture()
        model = estimate(spec, plan, quality, docs, "3017")
        return {"stock_id": "3017", "as_of": "2026-10-25", "forward_estimate": model,
                "public_eps": {"estimates": [{"eps": "7", "period_kind": "annual"}]},
                "dynamics": {"latest_session": "2026-10-22"}}

    def test_condition_price_and_implied_eps_from_approved_model(self):
        result, horizons = build(self.data(), None)
        self.assertEqual(set(result["scenarios"]), {"bear", "base", "bull"})
        self.assertEqual(result["scenarios"]["base"]["price_sensitivity"][0]["conditional_price_twd"], 143.2)
        self.assertEqual(result["implied_growth_conditions"][0]["required_eps_twd"], 10)
        self.assertAlmostEqual(result["implied_growth_conditions"][0]["base_case_minus_required_eps_twd"], 4.32)
        self.assertIsNone(result["implied_growth_conditions"][0]["required_change_vs_reported_four_quarters_pct"])
        self.assertFalse(result["multiple_basis"]["fair_value_verified"])
        self.assertEqual(horizons["short_term"]["market_session"], "2026-10-22")

    def test_public_annual_estimate_cannot_substitute_four_quarters(self):
        data = self.data()
        data.pop("forward_estimate")
        result, _ = build(data, None)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["public_annual_consensus"][0]["eps"], "7")
        self.assertTrue(all(row["eps_sum_twd"] is None for row in result["scenarios"].values()))
        self.assertFalse(result["implied_growth_conditions"])

    def test_stale_price_and_mixed_periods_block_comparison(self):
        data = self.data()
        data["forward_estimate"]["price_fresh"] = False
        result, _ = build(data, None)
        self.assertIsNotNone(result["scenarios"]["base"]["eps_sum_twd"])
        self.assertFalse(result["implied_growth_conditions"])
        data["forward_estimate"]["scenarios"]["bull"]["quarters"][0]["period"] = "2025-Q3"
        result, _ = build(data, None)
        self.assertTrue(all(row["eps_sum_twd"] is None for row in result["scenarios"].values()))
