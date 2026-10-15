import json
import tempfile
import unittest
from datetime import UTC, datetime

from tests.taiwan.test_filings import Source
from tests.taiwan.test_publications import Transport, numeric_rows
from tradingagents.dataflows.taiwan.calendar import OfficialCalendarProvider, verify_calendar
from tradingagents.dataflows.taiwan.filings import MopsFilingArchive
from tradingagents.dataflows.taiwan.history_archive import capture_year
from tradingagents.dataflows.taiwan.provider import REVENUE
from tradingagents.dataflows.taiwan.publications import (
    MopsAnnouncementClient,
    MopsPublicationResolver,
)
from tradingagents.dataflows.taiwan.statements import parse_income
from tradingagents.research.historical_validation import (
    nowcast_error,
    retrospective_revenue,
    short_horizon,
)


class HistoryTests(unittest.TestCase):
    def test_nowcast_rejects_already_public_actual_and_inferred_shares(self):
        prediction = {"stock_id": "3017", "period": "2026-Q3", "inputs": {}}
        issued = "2026-10-15T00:00:00+08:00"
        self.assertIsNone(nowcast_error(prediction, None, issued)["errors"])
        actual = {"stock_id": "3017", "published_at": "2026-10-14T00:00:00+08:00"}
        self.assertEqual(
            nowcast_error(prediction, actual, issued)["status"], "ineligible_actual_already_public"
        )
        actual.update(
            published_at="2026-11-01T00:00:00+08:00",
            basis="single_quarter_diluted",
            period="2026-Q3",
            source_sha256="a" * 64,
        )
        self.assertEqual(
            nowcast_error(prediction, actual, issued)["status"], "pending_reviewed_actual_inputs"
        )
        actual.update(earliest_profit_public_at="2026-10-10T00:00:00+08:00")
        self.assertEqual(
            nowcast_error(prediction, actual, issued)["status"], "ineligible_actual_already_public"
        )

    def test_long_calendar_is_bounded_and_all_raw_months_verified(self):
        class CalendarSource:
            def get(self, url, params, **kwargs):
                year = int(params["date"][:4])
                month = int(params["date"][4:6])
                payload = {
                    "date": params["date"],
                    "stat": "OK",
                    "fields": ["日期"],
                    "data": [[f"{year - 1911:03d}/{month:02d}/02"]],
                }

                class Reply:
                    status_code = 200
                    content = json.dumps(payload).encode()

                return Reply()

        provider = OfficialCalendarProvider(
            CalendarSource(), lambda: datetime(2026, 10, 15, tzinfo=UTC)
        )
        result = provider.fetch("twse", "2020-01-01", "2026-10-15")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["archives"]), 82)
        self.assertEqual(len(verify_calendar(result, "twse")), 82)

    def test_yearly_publication_requests_preserve_raw_windows(self):
        transport = Transport()
        now = datetime(2025, 5, 20, tzinfo=UTC)
        resolver = MopsPublicationResolver(
            "2020-01-01", "2025-05-20", MopsAnnouncementClient(transport, lambda: now)
        )
        row = {**numeric_rows(REVENUE)[0], "stock_id": "2330"}
        proof = resolver(REVENUE, row)
        self.assertIsNotNone(proof)
        self.assertEqual(len(transport.posts), 2)
        for _, request in transport.posts:
            self.assertEqual(request["data"]["SDATE"][:3], request["data"]["EDATE"][:3])
        self.assertEqual(len(resolver.audit("2330")), 2)
        resolver(REVENUE, row)
        self.assertEqual(len(transport.posts), 2)

    def test_statement_q1_and_q3_eps_not_annual_difference(self):
        def source(header):
            return (
                "合併綜合損益表 新台幣仟元<table><tr><th>會計項目</th><th>"
                + header
                + "</th></tr><tr><td>稀釋每股盈餘</td><td></td></tr><tr><td>稀釋每股盈餘</td><td>3.49</td></tr>"
                "<tr><td>營業收入合計</td><td>1,000</td></tr></table>"
            ).encode()

        q1 = parse_income(source("111年01月01日至111年03月31日"), 2022, 1)
        self.assertEqual(q1["values"]["revenue_twd"], 1000000)
        self.assertEqual(q1["values"]["eps_diluted"], 3.49)
        with self.assertRaises(ValueError):
            parse_income(source("111年01月01日至111年09月30日"), 2022, 3)
        self.assertIsNone(parse_income(source("111年度"), 2022, 4)["eps_diluted"])

    def test_partial_document_failure_keeps_listing(self):
        source = Source()

        def fail(*args, **kwargs):
            raise RuntimeError("synthetic failure")

        source.post = fail
        with tempfile.TemporaryDirectory() as folder:
            path = capture_year(
                folder,
                "2330",
                2024,
                MopsFilingArchive(folder, source, lambda: datetime(2024, 5, 20, tzinfo=UTC)),
            )
            import json

            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["documents"][0]["status"], "unavailable")
            self.assertIn("raw_response_base64", value["listing"])

    def test_diagnostic_does_not_invent_pit_or_weights(self):
        result = retrospective_revenue([], [])
        self.assertFalse(result["strict_pit"])
        self.assertIsNone(result["production_weights"])
        with self.assertRaises(ValueError):
            short_horizon([], [], "2020-01-01", "2020-12-31")

    def test_short_exit_costs_and_ambiguous_bar_use_stop_first(self):
        bars = []
        for i in range(20):
            bars.append(
                {
                    "date": f"2024-01-{i + 1:02d}",
                    "open": 100 if i < 10 else 119,
                    "close": 100 if i < 10 else 119,
                    "high": 120,
                    "low": 80 if i < 10 else 118,
                    "volume": 100,
                    "basis": "vendor_adjusted",
                }
            )
        bars.extend(
            [
                {
                    "date": "2024-01-21",
                    "open": 119,
                    "close": 120.5,
                    "high": 121,
                    "low": 119,
                    "volume": 100,
                    "basis": "vendor_adjusted",
                },
                {
                    "date": "2024-01-22",
                    "open": 120.5,
                    "close": 121,
                    "high": 122,
                    "low": 120,
                    "volume": 300,
                    "basis": "vendor_adjusted",
                },
                {
                    "date": "2024-01-23",
                    "open": 122,
                    "close": 120,
                    "high": 170,
                    "low": 80,
                    "volume": 100,
                    "basis": "vendor_adjusted",
                },
            ]
        )
        result = short_horizon(
            bars, [b["date"] for b in bars], "2024-01-22", "2024-01-31", horizon=5
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["events"][0]["exit_reason"], "stop_first_if_both_touched")
        self.assertLess(result["events"][0]["net_return"], 0)
        self.assertIsNone(result["events"][0]["same_entry_horizon_hold_net_return"])
        self.assertFalse(result["empirical_edge_proven"])


if __name__ == "__main__":
    unittest.main()
