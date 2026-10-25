"""Synthetic fixtures only; no live FinMind, LLM, account creation, or orders."""

import json
import os
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.taiwan.__main__ import main
from tradingagents.dataflows.taiwan.calculations import ema, financial_metrics, growth, rsi
from tradingagents.dataflows.taiwan.provider import (
    FINANCIAL,
    INSTITUTIONS,
    PRICE,
    REVENUE,
    FinMindClient,
    TaiwanMarketDataProvider,
)

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)


class Response:
    def __init__(self, rows, status=200, api_status=200):
        self.status_code = status
        self.payload = {"status": api_status, "data": rows}

    def json(self):
        return self.payload


class Transport:
    def __init__(self, fixtures=None):
        self.fixtures = fixtures or {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        params = kwargs["params"]
        if params["dataset"] == "TaiwanStockInfo":
            return Response(self.fixtures.get("TaiwanStockInfo", [
                {"stock_id": code, "type": "twse", "stock_name": "SYNTHETIC " + code,
                 "date": "2026-10-13"} for code in ("2330", "3017", "3231")
            ]))
        rows = self.fixtures.get(params["dataset"], [])
        if isinstance(rows, Response):
            return rows
        return Response([{**row, "stock_id": row.get("stock_id", params["data_id"])} for row in rows])


def price_rows(n=40):
    return [{"date": (date(2024, 1, 1) + timedelta(days=i)).isoformat(),
             "open": 100 + i, "max": 102 + i, "min": 99 + i, "close": 101 + i,
             "Trading_Volume": 1000 + i} for i in range(n)]


def provider(fixtures=None, publication=False):
    transport = Transport(fixtures)
    resolver = (lambda dataset, row: row.get("published_at")) if publication else None
    return TaiwanMarketDataProvider(FinMindClient(transport, lambda: NOW), resolver), transport


class TaiwanProviderTests(unittest.TestCase):
    def setUp(self):
        # unittest bypasses the upstream pytest conftest; block sockets here too.
        for method in ("connect", "connect_ex"):
            guard = patch("socket.socket." + method, side_effect=AssertionError("Network forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_target_symbols_and_suffixes(self):
        for code in ("2330", "3017", "3231"):
            with self.subTest(code=code):
                p, transport = provider({PRICE: price_rows()})
                for symbol in (code, code + ".TW"):
                    result = p.snapshot(symbol, "2024-01-01", "2024-02-10")
                    self.assertEqual(result["instrument"]["yahoo_symbol"], code + ".TW")
                    self.assertEqual(result["datasets"][PRICE]["status"], "ok")
                    self.assertEqual(result["technical"]["rsi_14"], 100)
                    self.assertAlmostEqual(result["technical"]["macd"], 7)
                    self.assertAlmostEqual(result["technical"]["macd_signal"], 7)
                    self.assertAlmostEqual(result["technical"]["macd_histogram"], 0)
                    self.assertEqual(result["technical"]["ma"]["5"], 138)
                    self.assertEqual(result["technical"]["volume_shares"], 1039)
                    self.assertEqual(result["datasets"][PRICE]["raw_rows"][0]["stock_id"], code)
                self.assertTrue(all(c[1]["params"].get("data_id", code) == code for c in transport.calls))

    def test_otc_and_leading_zero(self):
        for code in ("6488", "0050"):
            with self.subTest(code=code):
                p, _ = provider({"TaiwanStockInfo": [{"stock_id": code, "type": "tpex",
                                                       "date": "2026-10-13"}]})
                self.assertEqual(p.resolve_symbol(code).yahoo_symbol, code + ".TWO")
                with self.assertRaises(ValueError):
                    p.resolve_symbol(code + ".TW")

    def test_latest_market_metadata(self):
        p, _ = provider({"TaiwanStockInfo": [
            {"stock_id": "2330", "type": "emerging", "date": "2020-01-01"},
            {"stock_id": "2330", "type": "twse", "date": "2026-10-13"},
        ]})
        self.assertEqual(p.resolve_symbol("2330").exchange, "twse")

    def test_conflicting_metadata(self):
        p, _ = provider({"TaiwanStockInfo": [
            {"stock_id": "2330", "type": market, "date": "2026-10-13"}
            for market in ("twse", "tpex")
        ]})
        with self.assertRaises(VendorUnavailableError):
            p.resolve_symbol("2330")

    def test_unknown_unsupported_and_bad_symbol(self):
        p, _ = provider()
        with self.assertRaises(NoMarketDataError):
            p.resolve_symbol("9999")
        for symbol in ("../../2330", "AAPL", "2330.SH", "2330;bad"):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                p.resolve_symbol(symbol)
        p, _ = provider({"TaiwanStockInfo": [{"stock_id": "2330", "type": "emerging",
                                               "date": "2026-10-13"}]})
        with self.assertRaises(NoMarketDataError):
            p.resolve_symbol("2330")

    def test_cutoff_and_lineage(self):
        p, _ = provider({PRICE: list(reversed(price_rows(5)))})
        result = p.fetch(PRICE, "2330", "2024-01-01", "2024-01-03")
        self.assertEqual([r["data_date"] for r in result.records], ["2024-01-01", "2024-01-02"])
        self.assertEqual(len(result.raw_rows), 5)
        row = result.records[0]
        self.assertIsNone(row["published_at"])
        self.assertIn("actual_publication_unknown", row["publication_basis"])
        self.assertEqual(row["retrieved_at"], NOW.isoformat())
        self.assertEqual(row["source"], "FinMind:TaiwanStockPrice")
        self.assertEqual(row["units"]["volume"], "shares")

    def test_revenue_unknown_publication_fails_closed(self):
        row = {"date": "2024-05-01", "revenue_year": 2024, "revenue_month": 4,
               "revenue": 100, "create_time": "2024-05-02", "published_at": "2024-05-03"}
        p, _ = provider({REVENUE: [row]})
        result = p.fetch(REVENUE, "2330", "2023-01-01", "2024-06-01")
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(len(result.raw_rows), 1)
        self.assertIn("publication date unknown", " ".join(result.warnings))

    def test_revenue_growth_and_future_publication(self):
        rows = [
            {"date": "2023-05-01", "revenue_year": 2023, "revenue_month": 4,
             "revenue": 100, "published_at": "2023-05-08"},
            {"date": "2024-05-01", "revenue_year": 2024, "revenue_month": 4,
             "revenue": 120, "published_at": "2024-05-08"},
        ]
        p, _ = provider({REVENUE: rows}, publication=True)
        early = p.snapshot("2330", "2023-01-01", "2024-05-07")
        self.assertEqual(len(early["monthly_revenue"]), 1)
        late = p.snapshot("2330", "2023-01-01", "2024-05-08")
        self.assertAlmostEqual(late["monthly_revenue"][-1]["yoy_pct"], 20)

    def test_quarterly_metrics_and_publication(self):
        rows = []
        for period, published, eps in (("2023-03-31", "2023-05-15", 2),
                                       ("2023-12-31", "2024-03-31", 3),
                                       ("2024-03-31", "2024-05-15", 4)):
            for kind, value in {"EPS": eps, "Revenue": 200, "GrossProfit": 100,
                                "OperatingIncome": 60, "IncomeAfterTaxes": 40}.items():
                rows.append({"date": period, "type": kind, "value": value, "published_at": published})
        p, _ = provider({FINANCIAL: rows}, publication=True)
        early = p.snapshot("2330", "2023-01-01", "2024-05-14")
        self.assertEqual(len(early["quarterly_financials"]), 2)
        late = p.snapshot("2330", "2023-01-01", "2024-05-15")["quarterly_financials"][-1]
        # An announcement date proves availability, not per-field quarter/dilution basis.
        for key in ("eps_qoq_pct", "eps_yoy_pct", "gross_margin_pct", "operating_margin_pct", "net_margin_pct"):
            self.assertIsNone(late[key])
        self.assertEqual(late["status"], "unavailable_unverified_field_periods")

    def test_no_quarter_skipping_and_no_zero_base_growth(self):
        self.assertIsNone(growth(2, 0))
        self.assertIsNone(growth(2, -1))
        records = [{"period": period, "values": {"EPS": eps}}
                   for period, eps in (("2023-09-30", 1), ("2024-03-31", 2))]
        self.assertIsNone(financial_metrics(records)[-1]["eps_qoq_pct"])

    def test_institutions_complete_streak_and_missing_day(self):
        rows = []
        for trading_day in ("2024-01-01", "2024-01-02", "2024-01-03"):
            for name in ("Foreign_Investor", "Foreign_Dealer_Self", "Investment_Trust",
                         "Dealer_self", "Dealer_Hedging"):
                rows.append({"date": trading_day, "name": name, "buy": 10, "sell": 2})
        p, _ = provider({PRICE: price_rows(3), INSTITUTIONS: rows})
        metrics = p.snapshot("2330", "2024-01-01", "2024-01-04")["institutions"]["groups"]
        self.assertEqual(metrics["foreign"]["signed_days"], 3)
        self.assertEqual(metrics["foreign"]["window_net_shares"], 48)
        self.assertEqual(metrics["dealer"]["latest_net_shares"], 16)
        self.assertTrue(metrics["foreign"]["left_censored"])
        p, _ = provider({PRICE: price_rows(3), INSTITUTIONS: [r for r in rows if r["date"] != "2024-01-02"]})
        metrics = p.snapshot("2330", "2024-01-01", "2024-01-04")["institutions"]["groups"]["foreign"]
        self.assertEqual(metrics["signed_days"], 1)
        self.assertEqual(metrics["streak_net_shares"], 16)
        self.assertIsNone(metrics["window_net_shares"])
        self.assertFalse(metrics["coverage_complete"])

    def test_dealer_ambiguity_not_double_counted(self):
        rows = [{"date": "2024-01-01", "name": name, "buy": 10, "sell": 0}
                for name in ("Dealer", "Dealer_self", "Dealer_Hedging")]
        p, _ = provider({PRICE: price_rows(1), INSTITUTIONS: rows})
        group = p.snapshot("2330", "2024-01-01", "2024-01-03")["institutions"]["groups"]["dealer"]
        self.assertIsNone(group["latest_net_shares"])

    def test_warmup_and_known_values(self):
        self.assertEqual(ema([1, 2, 3, 4], 3), [None, None, 2, 3])
        self.assertIsNone(rsi([1, 2]))
        self.assertEqual(rsi([10] * 20), 50)
        self.assertEqual(rsi(list(range(20, 0, -1))), 0)
        p, _ = provider({PRICE: price_rows(2)})
        tech = p.snapshot("2330", "2024-01-01", "2024-01-03")["technical"]
        self.assertIsNone(tech["ma"]["5"])
        self.assertIsNone(tech["macd"])

    def test_invalid_numeric_duplicate_and_wrong_stock(self):
        for rows in ([{**price_rows(1)[0], "close": "NaN"}], price_rows(1) * 2,
                     [{**price_rows(1)[0], "stock_id": "3017"}],
                     [{**price_rows(1)[0], "Trading_Volume": -1}],
                     [{**price_rows(1)[0], "close": 999}]):
            with self.subTest(rows=rows):
                p, _ = provider({PRICE: rows})
                result = p.fetch(PRICE, "2330", "2024-01-01", "2024-01-03")
                self.assertEqual(result.status, "unavailable")
                self.assertFalse(result.records)

    def test_empty_http_api_and_bad_schema(self):
        p, _ = provider()
        self.assertEqual(p.fetch(PRICE, "2330", "2024-01-01", "2024-01-03").status, "unavailable")
        for response in (Response([], status=429), Response([], api_status=401), Response({})):
            with self.subTest(response=response):
                p, _ = provider({PRICE: response})
                with self.assertRaises(VendorUnavailableError):
                    p.fetch(PRICE, "2330", "2024-01-01", "2024-01-03")
                result = p.snapshot("2330", "2024-01-01", "2024-01-03")
                self.assertEqual(result["datasets"][PRICE]["status"], "unavailable")

    def test_transport_exception_redacted(self):
        transport = Transport()
        with patch.object(transport, "get", side_effect=RuntimeError("SECRET")), \
                self.assertRaises(VendorUnavailableError) as caught:
            FinMindClient(transport).query(PRICE)
        self.assertNotIn("SECRET", str(caught.exception))

    def test_token_header_not_query_or_report(self):
        p, transport = provider({PRICE: price_rows(2)})
        with patch.dict(os.environ, {"FINMIND_TOKEN": "TEST_SECRET"}):
            result = p.snapshot("2330", "2024-01-01", "2024-01-03")
        self.assertNotIn("TEST_SECRET", json.dumps(result))
        self.assertEqual(transport.calls[0][1]["headers"], {"Authorization": "Bearer TEST_SECRET"})
        self.assertNotIn("token", transport.calls[0][1]["params"])
        self.assertEqual(transport.calls[0][1]["timeout"], 20)

    def test_date_validation(self):
        p, _ = provider()
        for start, end in (("2024-01-02", "2024-01-01"), ("2024-1-1", "2024-01-03")):
            with self.subTest(start=start), self.assertRaises(ValueError):
                p.fetch(PRICE, "2330", start, end)

    def test_cli_export_without_llm(self):
        p, _ = provider({PRICE: price_rows(2)})
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "report.json"
            result = main(["--symbol", "2330", "--start", "2024-01-01", "--as-of", "2024-01-03",
                           "--output", str(target)], provider=p)
            self.assertEqual(result, 1)  # Missing fundamentals are explicitly partial/unavailable.
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["instrument"]["stock_id"], "2330")
            self.assertEqual(payload["datasets"][FINANCIAL]["status"], "unavailable")

    def test_institutional_sell_zero_and_reversal(self):
        for nets, expected in (([5, -3, -4], -2), ([5, 3, 0], 0), ([-1, 2, 4], 2)):
            rows = []
            for index, net in enumerate(nets):
                rows.append({"date": price_rows(3)[index]["date"], "name": "Investment_Trust",
                             "buy": max(net, 0), "sell": max(-net, 0)})
            p, _ = provider({PRICE: price_rows(3), INSTITUTIONS: rows})
            group = p.snapshot("2330", "2024-01-01", "2024-01-04")["institutions"]["groups"]["investment_trust"]
            self.assertEqual(group["signed_days"], expected)
            self.assertEqual(group["window_net_shares"], sum(nets))

    def test_financial_unknown_and_duplicate_publications(self):
        row = {"date": "2024-03-31", "type": "EPS", "value": 4, "published_at": "2024-05-15"}
        p, _ = provider({FINANCIAL: [row]})
        self.assertFalse(p.fetch(FINANCIAL, "2330", "2024-01-01", "2024-06-01").records)
        p, _ = provider({FINANCIAL: [row, row]}, publication=True)
        result = p.fetch(FINANCIAL, "2330", "2024-01-01", "2024-06-01")
        self.assertFalse(result.records)
        self.assertIn("duplicate", " ".join(result.warnings))

    def test_original_us_symbols_unchanged(self):
        from tradingagents.dataflows.symbols import normalize_symbol

        for symbol in ("AAPL", "MSFT", "SPY"):
            self.assertEqual(normalize_symbol(symbol), symbol)


if __name__ == "__main__":
    unittest.main()
