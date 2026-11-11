import unittest

from tradingagents.research.decision_conditions import build


def inputs():
    day = "2026-11-06"
    data = {"stock_id": "1216", "as_of": "2026-11-07",
            "reader_research": {"short_term_check": {"date": day, "status": "confirmed"}},
            "dynamics": {"latest_session": day, "windows": {"5": {
                "status": "available", "start": "2026-11-02", "end": day,
                "institutions": {"foreign": {"coverage_complete": True, "window_net_shares": 123}}}}}}
    company = {"facts": [{"id": "eps", "kind": "numeric", "metric": "diluted_eps", "derivation": "direct",
                          "unit": "TWD/share", "scope": "consolidated", "basis": "single_quarter",
                          "period_end": "2026-09-30", "normalized_value": "1.13", "source_sha256": "a" * 64}]}
    setup = {"stock_id": "1216", "as_of": "2026-11-07", "rationale": "Synthetic hypothetical assumptions",
             "earnings": {"fact_id": "eps", "minimum_eps_twd": 1.0},
             "entry_twd": 100, "invalidation_twd": 95, "target_twd": 110,
             "entry_cost_bps": 10, "exit_cost_bps": 40}
    return data, company, setup


class DecisionConditionsTests(unittest.TestCase):
    def test_confirmation_and_costed_scenario(self):
        result = build(*inputs())
        self.assertEqual(result["status"], "conditional_candidate")
        trade = result["hypothetical"]
        self.assertAlmostEqual(trade["net_reward_twd_per_share"], 9.46)
        self.assertAlmostEqual(trade["net_loss_twd_per_share"], 5.48)
        self.assertAlmostEqual(trade["net_reward_risk"], 9.46 / 5.48)
        self.assertEqual([(x["sessions"], x["status"]) for x in result["validation_windows"]],
                         [(5, "pending"), (20, "pending")])
        self.assertFalse(result["execution"])

    def test_confirmed_technical_without_setup_stays_observe(self):
        data, company, _ = inputs()
        result = build(data, company)
        self.assertEqual(result["status"], "observe")
        self.assertIsNone(result["hypothetical"])

    def test_misaligned_or_incomplete_flow_does_not_fill_zero(self):
        for field, value in (("end", "2026-11-05"), ("status", "unavailable")):
            with self.subTest(field=field):
                data, company, setup = inputs()
                data["dynamics"]["windows"]["5"][field] = value
                result = build(data, company, setup)
                self.assertEqual(result["status"], "observe")
                self.assertIsNone(result["institutional"]["foreign_net_shares"])

    def test_costs_or_newer_earnings_can_block(self):
        data, company, setup = inputs()
        setup["target_twd"] = 100.1
        self.assertIsNone(build(data, company, setup)["hypothetical"]["net_reward_risk"])
        company["facts"].append({**company["facts"][0], "id": "newer", "period_end": "2026-10-31"})
        self.assertEqual(build(data, company, setup)["fundamental"]["status"], "unavailable")

    def test_invalid_levels_are_rejected(self):
        data, company, setup = inputs()
        setup["invalidation_twd"] = 105
        with self.assertRaisesRegex(ValueError, "long levels"):
            build(data, company, setup)
