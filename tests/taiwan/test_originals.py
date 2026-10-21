import base64
import copy
import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from tests.taiwan.test_v3_acceptance import CTX, index_doc, windows
from tradingagents.dataflows.taiwan.originals import MopsOriginalClient, review_original
from tradingagents.research import announcement_audit, announcement_coverage, knowledge


def row(code="F22", subject="115年9月營業收入"):
    return {
        "COMPANY_ID": "3017",
        "COMPANY_NAME": "奇鋐",
        "AN_CODE": code,
        "SUBJECT": subject,
        "CDATE": "115/10/10",
        "CTIME": "15:00:00",
        "HYPERLINK": "https://mopsov.twse.com.tw/mops/web/original?co_id=3017&seq=1",
    }


def original(text=None, event=None, encoding="utf8"):
    event = event or row()
    text = (
        text
        or "本資料由奇鋐公司提供 民國115年09月 新台幣仟元 營業收入淨額<table><tr><td>本月</td><td>21,018,128</td></tr></table>"
    )
    raw = text.encode(encoding)
    return {
        "stock_id": "3017",
        "source_url": event["HYPERLINK"],
        "content_base64": base64.b64encode(raw).decode(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "http_status": 200,
        "captured_at": "2026-10-20T22:00:00+08:00",
        "public_capture_verified": True,
        "published_at": None,
    }


class OriginalTests(unittest.TestCase):
    def test_normalized_monthly_amount_and_no_initial_version_claim(self):
        result = review_original(row(), original())
        self.assertTrue(result["content_verified"])
        self.assertEqual(result["numeric_observations"][0]["value"], 21018128000)
        self.assertFalse(result["numeric_impact_reviewed"])
        self.assertFalse(result["historical_initial_version_proven"])

    def test_blank_wrong_company_period_unit_and_ambiguous_amount_rejected(self):
        good = base64.b64decode(original()["content_base64"]).decode()
        for text in [
            "<html></html>",
            good.replace("奇鋐", "緯創"),
            good.replace("09月", "08月"),
            good.replace("仟元", "元"),
            good.replace("</table>", "<tr><td>本月</td><td>2</td></tr></table>"),
        ]:
            with self.subTest(text=text[:40]):
                self.assertFalse(review_original(row(), original(text))["content_verified"])

    def test_big5_annual_listing_never_certifies_individual_upload(self):
        event = row("E02", "115年第2季財務報告書")
        receipt = original("公司名稱：奇鋐 202602_3017_AI1.pdf", event, "cp950")
        result = review_original(event, receipt)
        self.assertEqual(result["status"], "listing_only")
        self.assertFalse(result["content_verified"])

    def test_encoded_index_subject_matches_decoded_original(self):
        event = row("M99", "代&#12070;公司公告")
        self.assertTrue(
            review_original(event, original("本資料由奇鋐公司提供 主旨 代⼦公司公告", event))[
                "content_verified"
            ]
        )

    def test_registration_requires_body_month_and_capital_table(self):
        event = row("F03", "115年9月變更登記")
        text = "本資料由奇鋐公司提供 <table><tr><td>變更公司執照時間</td><td>115年9月</td></tr></table> 實收股本股數(股) 實收股本金額(元)"
        self.assertTrue(review_original(event, original(text, event))["content_verified"])
        self.assertFalse(
            review_original(event, original(text.replace("9月", "8月"), event))["content_verified"]
        )

    def test_equity_half_year_is_cumulative_not_quarterly(self):
        event = row("F29", "115年第2季權益變動表")
        text = (
            "本資料由奇鋐公司提供 合併權益變動表 民國115年上半年度 新台幣仟元<table>"
            + "<tr><td>100</td></tr>" * 3
            + "</table>"
        )
        self.assertTrue(review_original(event, original(text, event))["content_verified"])
        self.assertFalse(
            review_original(event, original(text.replace("115年", "114年"), event))[
                "content_verified"
            ]
        )

    def test_url_and_hash_are_bound_to_original_not_review_flags(self):
        for field, value in [("sha256", "bad"), ("source_url", row()["HYPERLINK"] + "&year=114")]:
            doc = original()
            doc[field] = value
            with self.assertRaises(ValueError):
                review_original(row(), doc)

    def test_capture_uses_no_token_headers_and_preserves_rejection(self):
        class Transport:
            calls = []

            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return type("Response", (), {"status_code": 429, "content": b"rate limited"})()

        transport = Transport()
        result = MopsOriginalClient(transport, lambda: datetime(2026, 10, 21, tzinfo=UTC)).capture(
            row()
        )
        self.assertEqual(result["status"], "source_rejected")
        self.assertEqual(transport.calls[0][1], {"timeout": 25, "allow_redirects": False})
        self.assertIn("content_base64", result)

    def test_official_restriction_http200_is_not_content(self):
        self.assertEqual(
            review_original(row(), original("查詢過於頻繁"))["reason"], "source_restriction"
        )

    def test_company_mentioned_in_other_issuer_body_does_not_verify_identity(self):
        text = base64.b64decode(original()["content_base64"]).decode()
        text = text.replace("本資料由奇鋐公司提供", "本資料由緯創公司提供 交易對手奇鋐")
        self.assertFalse(review_original(row(), original(text))["content_verified"])

    def test_network_failure_keeps_unavailable_receipt(self):
        class Transport:
            def get(self, *args, **kwargs):
                raise TimeoutError("private message must not leak")

        result = MopsOriginalClient(Transport()).capture(row())
        self.assertEqual(result["error_kind"], "TimeoutError")
        self.assertNotIn("private", json.dumps(result))

    def test_blank_original_and_conflicting_url_inventory_cannot_pass(self):
        event = row()
        docs = {"index": index_doc([event], 1), "a": original("<html>奇鋐</html>"), "b": original()}
        inventory = [{"original_url": event["HYPERLINK"], "document_id": key} for key in ["a", "b"]]
        for entries in [inventory[:1], inventory, list(reversed(inventory))]:
            result = announcement_coverage.reconcile(CTX, docs, entries, windows(), set(docs))
            self.assertFalse(result["source_coverage_verified"])
            self.assertEqual(len(result["unprocessed_events"]), 1)

    def test_exact_event_identity_resolves_conflict_without_insertion_order(self):
        event = row()
        docs = {"index": index_doc([event], 1), "a": original("<html>奇鋐</html>"), "b": original()}
        inventory = [
            {"original_url": event["HYPERLINK"], "document_id": "a"},
            {
                "id": announcement_coverage.identity(event),
                "original_url": event["HYPERLINK"],
                "document_id": "b",
            },
        ]
        result = announcement_coverage.reconcile(CTX, docs, inventory, windows(), set(docs))
        self.assertTrue(result["source_coverage_verified"])
        self.assertFalse(result["matched_events"][0]["numeric_impact_reviewed"])

    def test_future_current_capture_excluded_even_with_past_index_publication(self):
        event, doc = row(), original()
        doc["captured_at"] = "2026-10-21T15:00:00+08:00"
        inventory = [
            {
                "id": "monthly",
                "published_at": "2026-10-10",
                "original_url": event["HYPERLINK"],
                "document_id": "o",
            }
        ]
        result = knowledge.completeness(
            CTX, {"index": index_doc([event], 1), "o": doc}, inventory, windows()
        )
        self.assertIn("o", result["excluded_sources"])

    def test_offline_audit_is_append_only(self):
        spec = {
            "context": CTX,
            "documents": {"index": index_doc([], 0)},
            "inventory": [],
            "coverage": windows(),
        }
        before = copy.deepcopy(spec)
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / "new"
            result = announcement_audit.audit(spec, dest)
            self.assertFalse(result["forecast_modified"])
            with self.assertRaises(FileExistsError):
                announcement_audit.audit(spec, dest)
        self.assertEqual(spec, before)

    def test_formal_cli_runs_offline_and_refuses_overwrite(self):
        from tradingagents.research.__main__ import main

        spec = {
            "context": CTX,
            "documents": {"index": index_doc([], 0)},
            "inventory": [],
            "coverage": windows(),
        }
        with tempfile.TemporaryDirectory() as temp:
            source, dest = Path(temp) / "input.json", Path(temp) / "audit"
            source.write_text(json.dumps(spec), encoding="utf8")
            args = ["audit-announcements", "--input", str(source), "--output-dir", str(dest)]
            self.assertEqual(main(args), 0)
            self.assertEqual(main(args), 2)
            self.assertTrue((dest / "announcement-audit.json").is_file())

    def test_audit_rejects_future_cutoff(self):
        spec = {
            "context": {**CTX, "cutoff_at": "2099-10-20T23:00:00+08:00"},
            "documents": {},
            "inventory": [],
            "coverage": [],
        }
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError):
            announcement_audit.audit(spec, Path(temp) / "new")

    def test_capture_rejects_wrong_query_period_before_requests(self):
        event = row()
        event["CDATE"] = "115/09/01"
        doc = index_doc([event])
        doc["http_status"] = 200
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError):
            announcement_audit.capture(doc, Path(temp) / "new")

    def test_bounded_capture_stops_after_source_refusal(self):
        first, second = row(), row()
        second["HYPERLINK"] += "&other=2"

        class Client:
            count = 0

            def capture(self, event):
                self.count += 1
                return {"http_status": 403, "status": "source_rejected"}

        client = Client()
        doc = index_doc([first, second])
        doc["http_status"] = 200
        with tempfile.TemporaryDirectory() as temp:
            result = announcement_audit.capture(doc, Path(temp) / "new", client=client)
        self.assertEqual(client.count, 1)
        self.assertTrue(result["source_refused"])
        self.assertEqual(len(result["pending_event_ids"]), 1)

    def test_identical_official_duplicates_do_not_duplicate_capture_inventory(self):
        event = row()

        class Client:
            count = 0

            def capture(self, event):
                self.count += 1
                return original(event=event)

        client = Client()
        doc = index_doc([event, event], 2)
        doc["http_status"] = 200
        with tempfile.TemporaryDirectory() as temp:
            result = announcement_audit.capture(doc, Path(temp) / "new", client=client)
        self.assertEqual(client.count, 1)
        self.assertEqual(len(result["inventory"]), 1)
        self.assertEqual(result["identical_duplicate_rows"], 1)


if __name__ == "__main__":
    unittest.main()
