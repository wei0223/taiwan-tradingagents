import base64
import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tests.taiwan.test_filing_links import fixture
from tradingagents.dataflows.taiwan.official_sources import OfficialSources
from tradingagents.dataflows.taiwan.source_store import SourceStore
from tradingagents.research.source_registry import assess, refresh, rolling_plan

COMPANY = {"stock_id": "3017", "name": "奇鋐", "exchange": "twse"}
PLAN = {
    "companies": [COMPANY],
    "financial_periods": ["2026-Q2"],
    "financial_editions": ["AI1"],
    "monthly_periods": ["2026-09"],
    "correction_periods": ["2026-Q2"],
    "fundraising_year": 2026,
    "historical_start": "2016-01-01",
}
STAMP = "2026-10-20T12:00:00+00:00"


def receipt(raw=b"original", status=200, at=STAMP):
    return {
        "source_url": "https://doc.twse.com.tw/pdf/original.pdf",
        "captured_at": at,
        "http_status": status,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "content_base64": base64.b64encode(raw).decode(),
    }


class Transport:
    def __init__(self):
        self.calls = []
        self.month_value = 1234

    def response(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        path = urlsplit(url).path
        _, listing, doc = fixture()
        if path.endswith("ajax_t05st03"):
            text = "本資料由奇鋐公司提供 股票代號3017 已發行普通股數或TDR原股發行股數392,670,107股"
        elif path.endswith("ajax_t05st05"):
            text = "本資料由奇鋐公司提供 變更公司執照時間<table><tr><td>115年9月</td></tr></table>"
        elif path.endswith("ajax_t05st10_ifrs"):
            text = f"本資料由奇鋐公司提供 民國115年09月 單位新台幣仟元 營業收入淨額<table><tr><td>本月</td><td>{self.month_value}</td></tr></table>"
        elif path.endswith("ajax_t56sb31_q1"):
            text = "資料年度：115 季別：02 公司代號 更(補)正內容<table><tr><th>公司代號</th></tr></table>"
        elif method == "post":
            return type(
                "Response",
                (),
                {
                    "status_code": 200,
                    "content": base64.b64decode(doc["download_response"]["content_base64"]),
                },
            )()
        elif path.startswith("/pdf/"):
            return type(
                "Response",
                (),
                {
                    "status_code": 200,
                    "content": base64.b64decode(doc["pdf_response"]["content_base64"]),
                },
            )()
        elif parse_qs(urlsplit(url).query).get("mtype") == ["B"]:
            text = "公司名稱：奇鋐<table><tr><th>電子檔案</th><th>上傳日期</th></tr></table>"
        else:
            return type(
                "Response",
                (),
                {"status_code": 200, "content": base64.b64decode(listing["content_base64"])},
            )()
        return type("Response", (), {"status_code": 200, "content": text.encode()})()

    def get(self, url, **kwargs):
        return self.response("get", url, **kwargs)

    def post(self, url, **kwargs):
        return self.response("post", url, **kwargs)


class RegistryTests(unittest.TestCase):
    def test_invalid_or_future_scopes_rejected_before_network_or_output(self):
        with tempfile.TemporaryDirectory() as temp:
            for field, value in [
                ("monthly_periods", ["2026-11"]),
                ("financial_periods", ["2026-Q4"]),
                ("monthly_periods", ["2026-13"]),
                ("companies", [COMPANY, COMPANY]),
            ]:
                plan = copy.deepcopy(PLAN)
                plan[field] = value
                transport = Transport()
                with self.assertRaises(ValueError):
                    refresh(
                        plan,
                        Path(temp) / "ledger",
                        Path(temp) / "run",
                        transport,
                        lambda: datetime.fromisoformat(STAMP),
                    )
                self.assertEqual(transport.calls, [])
                self.assertFalse((Path(temp) / "run").exists())

    def test_rolling_scope_rolls_year_without_modifying_saved_plan(self):
        from datetime import date

        result = rolling_plan(PLAN, date(2027, 1, 10))
        self.assertEqual(result["monthly_periods"], ["2026-10", "2026-11", "2026-12"])
        self.assertEqual(result["financial_periods"], ["2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"])
        self.assertEqual(PLAN["monthly_periods"], ["2026-09"])

    def test_five_families_and_current_facts_do_not_certify_historical_backtest(self):
        with tempfile.TemporaryDirectory() as temp:
            result = refresh(
                PLAN,
                Path(temp) / "ledger",
                Path(temp) / "run",
                Transport(),
                lambda: datetime.fromisoformat(STAMP),
            )
            data = json.loads((Path(temp) / "run/3017.json").read_text())
            self.assertEqual(len(data["families"]), 5)
            self.assertTrue(data["current_facts"]["specified_monthly_revenue_originals_available"])
            self.assertEqual(data["current_issued_share_reference"]["shares"], 392670107)
            self.assertFalse(data["historical_backtest"]["strict_pit_ready"])
            self.assertFalse(data["current_research"]["full_current_research_ready"])
            self.assertGreater(result["request_count"], 0)

    def test_unchanged_changed_and_unavailable_recovery_keep_original_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SourceStore(temp)
            a = store.capture(receipt())
            first = store.append(
                "3017",
                PLAN,
                [
                    {
                        "key": "x",
                        "status": "verified_current",
                        "content_sha256": receipt()["sha256"],
                        "receipt_ids": [a],
                    }
                ],
                STAMP,
            )
            failed = store.append(
                "3017", PLAN, [{"key": "x", "status": "pending", "receipt_ids": []}], STAMP
            )
            self.assertEqual(
                store.read_snapshot(failed)["changes"][0]["change"],
                "not_comparable_source_unavailable",
            )
            recovered = store.append(
                "3017",
                PLAN,
                [
                    {
                        "key": "x",
                        "status": "verified_current",
                        "content_sha256": receipt()["sha256"],
                        "receipt_ids": [a],
                    }
                ],
                STAMP,
            )
            self.assertEqual(store.read_snapshot(recovered)["changes"][0]["change"], "unchanged")
            changed_receipt = receipt(b"replacement")
            b = store.capture(changed_receipt)
            changed = store.append(
                "3017",
                PLAN,
                [
                    {
                        "key": "x",
                        "status": "verified_current",
                        "content_sha256": changed_receipt["sha256"],
                        "receipt_ids": [b],
                    }
                ],
                STAMP,
            )
            self.assertIn("content_changed", store.read_snapshot(changed)["changes"][0]["change"])
            self.assertEqual(
                store.read_snapshot(first)["resources"][0]["content_sha256"], receipt()["sha256"]
            )

    def test_big5_http200_restriction_stops_remaining_requests(self):
        class Refused:
            count = 0

            def get(self, *args, **kwargs):
                self.count += 1
                return type(
                    "Response",
                    (),
                    {"status_code": 200, "content": "查詢過量,請稍後再查詢!".encode("cp950")},
                )()

        with tempfile.TemporaryDirectory() as temp:
            transport = Refused()
            client = OfficialSources(SourceStore(temp), transport)
            first, _ = client.request("get", "https://doc.twse.com.tw/server-java/t57sb01")
            second, _ = client.request("get", "https://doc.twse.com.tw/pdf/other.pdf")
            self.assertEqual(first["status"], "source_rejected")
            self.assertIn("skipped", second["status"])
            self.assertEqual(transport.count, 1)

    def test_changed_cached_numbers_rejected_against_bound_raw_original(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SourceStore(Path(temp) / "ledger")
            result = refresh(
                PLAN,
                store.root,
                Path(temp) / "run",
                Transport(),
                lambda: datetime.fromisoformat(STAMP),
            )
            snap = store.read_snapshot(result["companies"]["3017"]["snapshot_sha256"])
            for resource in snap["resources"]:
                if resource["key"] == "capital:basic":
                    resource["data"]["issued_common_shares"] = 999
            altered = store.append("3017", PLAN, snap["resources"], STAMP)
            with self.assertRaises(ValueError):
                assess(store, altered)

    def test_corrupt_blob_future_receipt_and_invalid_archive_identity_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SourceStore(temp)
            r = receipt()
            key = store.capture(r)
            with self.assertRaises(ValueError):
                store.receipt("../elsewhere")
            future = store.capture(receipt(at="2026-10-21T00:00:00+00:00"))
            with self.assertRaises(ValueError):
                store.append("3017", PLAN, [{"key": "x", "receipt_ids": [future]}], STAMP)
            (store.root / "blobs" / (r["sha256"] + ".bin")).write_bytes(b"changed")
            with self.assertRaises(ValueError):
                store.receipt(key)

    def test_no_secrets_redirects_or_unbounded_request_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            transport = Transport()
            client = OfficialSources(SourceStore(temp), transport, max_requests=1)
            client.request("get", "https://mopsov.twse.com.tw/mops/web/ajax_t05st03")
            second, _ = client.request("get", "https://mopsov.twse.com.tw/mops/web/ajax_t05st05")
            self.assertIn("skipped", second["status"])
            self.assertFalse(transport.calls[0][2]["allow_redirects"])
            self.assertNotIn("headers", transport.calls[0][2])

    def test_portable_export_verifies_history_and_overwrite_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SourceStore(Path(temp) / "ledger")
            result = refresh(
                PLAN,
                store.root,
                Path(temp) / "run",
                Transport(),
                lambda: datetime.fromisoformat(STAMP),
            )
            key = result["companies"]["3017"]["snapshot_sha256"]
            store.export(key, Path(temp) / "portable")
            self.assertEqual(
                SourceStore(Path(temp) / "portable").read_snapshot(key)["stock_id"], "3017"
            )
            with self.assertRaises(FileExistsError):
                store.export(key, Path(temp) / "portable")

    def test_report_exports_registry_and_rejects_future_or_other_company_snapshot(self):
        from tests.taiwan.test_research import snapshot
        from tradingagents.research.reporting import package

        with tempfile.TemporaryDirectory() as temp:
            store = SourceStore(Path(temp) / "ledger")
            run = refresh(
                PLAN,
                store.root,
                Path(temp) / "run",
                Transport(),
                lambda: datetime.fromisoformat(STAMP),
            )
            key = run["companies"]["3017"]["snapshot_sha256"]
            source = snapshot("3017")
            source["as_of"] = "2026-10-20"
            result = package(
                source,
                Path(temp) / "report",
                official_source_store=store.root,
                official_source_snapshot=key,
            )
            self.assertFalse(
                result["official_source_registry"]["historical_backtest"]["strict_pit_ready"]
            )
            self.assertIn(
                'id="official-source-registry"',
                (Path(temp) / "report/report.html").read_text(encoding="utf8"),
            )
            exported = SourceStore(Path(temp) / "report/official-sources/ledger")
            self.assertEqual(exported.read_snapshot(key)["stock_id"], "3017")
            source["as_of"] = "2026-10-19"
            with self.assertRaisesRegex(ValueError, "after report cutoff"):
                package(
                    source,
                    Path(temp) / "future",
                    official_source_store=store.root,
                    official_source_snapshot=key,
                )
            source = snapshot("2330")
            source["as_of"] = "2026-10-20"
            with self.assertRaisesRegex(ValueError, "company mismatch"):
                package(
                    source,
                    Path(temp) / "other",
                    official_source_store=store.root,
                    official_source_snapshot=key,
                )


if __name__ == "__main__":
    unittest.main()
