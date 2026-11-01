"""One-shot research must not masquerade as forecast publication or old debate."""
import tempfile
import unittest
from pathlib import Path

from tests.taiwan.test_report_reading import notes, packet, sample
from tradingagents.research.reporting import package


class OnDemandViewTests(unittest.TestCase):
    def test_single_report_does_not_require_forecast_publication(self):
        source = sample()
        with tempfile.TemporaryDirectory() as directory:
            result = package(source, directory, analyst_prose=notes(), review_input=packet(),
                             extension_input={"stock_id": "3017", "as_of": source["as_of"], "one_shot": True})
            page = (Path(directory) / "report.html").read_text(encoding="utf-8")
            self.assertEqual(result["report_mode"], "on_demand")
            self.assertIn("本次單次研究", page)
            self.assertNotIn("發布狀態：", page)
            self.assertIn("不發布正式預測", page)
            self.assertFalse(result["independent_agents_executed"])
            self.assertIn("本次未執行獨立辯論", page)
            self.assertNotIn("本輪未重新執行辯論", page)
            self.assertIn("publishing_quality", result)
            self.assertFalse(result["publishing_quality"]["latest_market_gate"]["current_trade_conclusion_allowed"])

    def test_one_shot_view_does_not_change_financial_eligibility(self):
        source = sample()
        outputs = []
        for one_shot in [False, True]:
            with tempfile.TemporaryDirectory() as directory:
                outputs.append(package(source, directory, review_input=packet(),
                    extension_input={"stock_id": "3017", "as_of": source["as_of"], "one_shot": one_shot}))
        for key in ["review", "publishing_quality", "financial_period_conversion", "source_audit"]:
            self.assertEqual(outputs[0][key], outputs[1][key])


if __name__ == "__main__":
    unittest.main()
