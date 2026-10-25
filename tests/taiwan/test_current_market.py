"""Public-market supplements keep raw capture proofs and never invent adjustments."""

import copy
import json
import unittest
from datetime import datetime
from types import SimpleNamespace

from tests.taiwan.test_publishing import current_snapshot
from tradingagents.dataflows.taiwan.current_market import CurrentMarketProvider


class Transport:
    def __init__(self, status=200, missing_group=False):
        self.calls, self.status, self.missing_group = [], status, missing_group

    def get(self, url, params, **kwargs):
        self.calls.append((url, params))
        if "FMTQIK" in url:
            payload = {
                "stat": "OK",
                "date": "20261001",
                "fields": ["日期"],
                "data": [[f"115/10/{d}"] for d in [19, 20, 21, 22]],
            }
        elif "STOCK_DAY" in url:
            payload = {
                "stat": "OK",
                "date": "20261001",
                "title": "3017 daily",
                "fields": ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
                "data": [
                    [f"115/10/{d}", "1000", "100000", "100", "101", "99", "100"]
                    for d in [19, 20, 21, 22]
                ],
            }
        else:
            fields = [
                "證券代號",
                "證券名稱",
                "外陸資買進股數(不含外資自營商)",
                "外陸資賣出股數(不含外資自營商)",
                "外資自營商買進股數",
                "外資自營商賣出股數",
                "投信買進股數",
                "投信賣出股數",
                "自營商買進股數(自行買賣)",
                "自營商賣出股數(自行買賣)",
                "自營商買進股數(避險)",
                "自營商賣出股數(避險)",
            ]
            if self.missing_group:
                fields[-1] = "unrecognized"
            payload = {
                "stat": "OK",
                "date": params["date"],
                "fields": fields,
                "data": [["3017", "synthetic", *(["100"] * 10)]],
            }
        return SimpleNamespace(status_code=self.status, content=json.dumps(payload).encode())


def snapshot():
    s = current_snapshot()
    s["instrument"]["stock_id"] = "3017"
    for data in s["datasets"].values():
        data["records"] = []
        data["raw_rows"] = []
    s["datasets"]["TaiwanStockPrice"]["records"] = [
        {
            "data_date": "2026-10-19",
            "values": {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
        }
    ]
    return s


class CurrentMarketTests(unittest.TestCase):
    def client(self, transport):
        return CurrentMarketProvider(
            transport, lambda: datetime.fromisoformat("2026-10-25T01:00:00+00:00")
        )

    def test_refresh_all_new_sessions_leaves_adjusted_and_original_untouched(self):
        transport = Transport()
        s = snapshot()
        before = copy.deepcopy(s)
        r = self.client(transport).refresh(s)
        self.assertEqual(s, before)
        self.assertEqual(
            r["market_refresh"]["new_sessions"], ["2026-10-20", "2026-10-21", "2026-10-22"]
        )
        self.assertEqual(r["extensions"], s["extensions"])
        self.assertEqual(r["technical"]["data_date"], "2026-10-22")
        self.assertEqual(len(transport.calls), 5)
        flows = r["datasets"]["TaiwanStockInstitutionalInvestorsBuySell"]["records"]
        self.assertEqual(len(flows), 15)
        self.assertTrue(all("TWSE" in row["source"] and row["raw_sha256"] for row in flows))

    def test_rejection_kept_no_retry_or_subsequent_calls(self):
        t = Transport(status=429)
        c = self.client(t)
        with self.assertRaises(ValueError):
            c.refresh(snapshot())
        with self.assertRaises(ValueError):
            c.refresh(snapshot())
        self.assertEqual(len(t.calls), 1)
        self.assertEqual(c.receipts[0]["http_status"], 429)

    def test_missing_institution_field_is_error_not_zero(self):
        with self.assertRaises((ValueError, KeyError)):
            self.client(Transport(missing_group=True)).refresh(snapshot())

    def test_tpex_never_uses_twse_adapter(self):
        s = snapshot()
        s["instrument"]["exchange"] = "tpex"
        t = Transport()
        with self.assertRaises(ValueError):
            self.client(t).refresh(s)
        self.assertFalse(t.calls)
