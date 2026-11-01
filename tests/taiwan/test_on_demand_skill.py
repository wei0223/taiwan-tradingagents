"""Offline request safety and one-shot defaults; no Sponsor or LLM calls."""

import importlib.util
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills/taiwan-report/scripts/request_report.py"
if not SCRIPT.exists():
    SCRIPT = Path(__file__).parent / "taiwan-report/scripts/request_report.py"
spec = importlib.util.spec_from_file_location("request_report_skill", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
create_request = module.create_request
NOW = datetime(2026, 10, 31, 17, 0, tzinfo=UTC)


class OnDemandSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        target = self.repo / "tradingagents/research/__main__.py"
        target.parent.mkdir(parents=True)
        target.write_text("# synthetic repository fixture", encoding="utf-8")

    def request(self, symbol, **kwargs):
        return create_request(symbol, self.repo, self.root / "reports", clock=lambda: NOW, **kwargs)

    def test_three_stocks_use_one_shot_research_not_monitoring(self):
        for stock in ["2330", "3017", "3231"]:
            with self.subTest(stock=stock):
                path = self.request(stock)
                record = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(record["stock_id"], stock)
                self.assertEqual(record["as_of"], "2026-11-01")
                self.assertFalse(record["create_or_resume_monitor"])
                self.assertFalse(record["publish_forecasts"])
                self.assertEqual(record["upgrade_extension_defaults"], {"stock_id": stock, "as_of": "2026-11-01", "one_shot": True})
                self.assertFalse(record["backtest_required"])
                self.assertTrue(record["source_review_required"])
                self.assertFalse(Path(record["report_dir"]).exists())

    def test_suffix_is_preserved_without_guessing_exchange(self):
        record = json.loads(self.request(" 3324.two ").read_text(encoding="utf-8"))
        self.assertEqual(record["symbol"], "3324.TWO")
        self.assertEqual(record["exchange"], "resolve_with_provider_metadata")
        self.assertEqual(record["workflow"]["inputs"]["symbols"], "3324.TWO")

    def test_non_identifiers_cannot_create_paths_or_commands(self):
        for value in ["../2330", "2330;echo secret", "2330,3017", "TSMC", "2330.US", "123"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.request(value)
        self.assertFalse((self.root / "reports").exists())

    def test_future_and_reversed_dates_rejected_before_output(self):
        for args in [{"as_of": "2026-11-02"}, {"as_of": "2026-10-01", "start": "2026-10-02"}]:
            with self.assertRaises(ValueError):
                self.request("2330", **args)
        self.assertFalse((self.root / "reports").exists())

    def test_existing_request_is_never_overwritten(self):
        path = self.request("3017")
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            self.request("3017")
        self.assertEqual(path.read_bytes(), before)

    def test_private_reports_cannot_be_placed_into_checkout(self):
        with self.assertRaises(ValueError):
            create_request("3231", self.repo, self.repo / "reports", clock=lambda: NOW)
        self.assertFalse((self.repo / "reports").exists())

    def test_unconfigured_repository_does_not_silently_select_other_project(self):
        with self.assertRaises(ValueError):
            create_request("2330", self.root / "missing", self.root / "reports", clock=lambda: NOW)
        self.assertFalse((self.root / "reports").exists())

    def test_naive_clock_rejected(self):
        with self.assertRaises(ValueError):
            create_request("2330", self.repo, self.root / "reports", clock=lambda: datetime(2026, 11, 1))


if __name__ == "__main__":
    unittest.main()
