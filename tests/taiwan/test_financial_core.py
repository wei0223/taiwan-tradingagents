import copy
import unittest

from tests.taiwan.test_fixed_pipeline import company_input
from tradingagents.research.company_database import validate
from tradingagents.research.financial_core import build


class FinancialCoreTests(unittest.TestCase):
    def company(self):
        company = validate(company_input())
        revenue = company["facts"][0]
        company["facts"].append(
            {**revenue, "id": "profit", "metric": "operating_profit", "normalized_value": "246800"}
        )
        return company

    def test_ratio_is_program_calculated_and_period_bound(self):
        company = self.company()
        before = copy.deepcopy(company)
        result = build(company)
        self.assertEqual(result["periods"][0]["metrics"]["operating_margin_pct"]["value"], 20)
        self.assertEqual(result["periods"][0]["basis"], "single_quarter")
        self.assertEqual(company, before)

    def test_ytd_is_not_combined_with_single_quarter(self):
        company = self.company()
        company["facts"][1].update(basis="ytd", period_start="2026-01-01")
        self.assertTrue(
            all(
                row["metrics"]["operating_margin_pct"]["value"] is None
                for row in build(company)["periods"]
            )
        )

    def test_source_version_or_scope_mismatch_stops_ratio(self):
        for key, value in [
            ("source_sha256", "0" * 64),
            ("scope", "individual"),
            ("unit", "shares"),
        ]:
            company = self.company()
            company["facts"][1][key] = value
            self.assertTrue(
                all(
                    row["metrics"]["operating_margin_pct"]["value"] is None
                    for row in build(company)["periods"]
                )
            )

    def test_zero_denominator_and_missing_are_not_zero_filled(self):
        company = self.company()
        company["facts"][0]["normalized_value"] = "0"
        metrics = build(company)["periods"][0]["metrics"]
        self.assertIsNone(metrics["operating_margin_pct"]["value"])
        self.assertIsNone(metrics["cfo_parent_profit_ratio"]["value"])

    def test_no_eps_or_weighted_share_differencing(self):
        self.assertFalse(build(self.company())["eps_forecast_calculated"])
        self.assertFalse(
            any("eps" in name for name in build(self.company())["periods"][0]["metrics"])
        )

    def test_agent_run_binds_program_financial_package(self):
        import json
        import tempfile

        from tests.taiwan.test_agent_protocol import fixture
        from tradingagents.research.agent_protocol import prepare_prompt

        with tempfile.TemporaryDirectory() as d:
            run, *_ = fixture(d)
            prepare_prompt(run, "fundamental")
            request = json.loads((run / "stages/fundamental/request.json").read_text("utf8"))
            self.assertIn("financial", request["inputs"])
