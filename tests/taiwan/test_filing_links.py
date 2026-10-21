import base64
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from tests.taiwan.test_originals import original, row
from tests.taiwan.test_v3_acceptance import CTX, index_doc, windows
from tradingagents.dataflows.taiwan.filing_links import (
    ENDPOINT,
    HOST,
    FilingLinkClient,
    candidates,
    verify_chain,
)
from tradingagents.research import announcement_coverage, filing_link_audit


def fixture(kind="A"):
    event = row(
        "E02" if kind == "A" else "E01",
        "115年第2季財務報告書" if kind == "A" else "115年9月公開說明書",
    )
    event["HYPERLINK"] = ENDPOINT + f"?co_id=3017&year=115&mtype={kind}"
    filename = "202602_3017_AI1.pdf" if kind == "A" else "202609_3017_B11.pdf"
    content = b"%PDF-1.4\nsynthetic chain fixture; not a financial report"
    period = "115 年 第二季" if kind == "A" else "115 年 9月"
    category = "財務報告書" if kind == "A" else "公開說明書"
    body = f'''公司名稱：奇鋐<table><tr><th>證券代號</th><th>資料年度</th><th>資料類型</th><th>電子檔案</th><th>檔案大小</th><th>上傳日期</th></tr>
    <tr><td>3017</td><td>{period}</td><td>{category}</td><td><a href='javascript:readfile2("{kind}","3017","{filename}");'>{filename}</a></td><td>{len(content):,}</td><td>115/08/12 16:00:26</td></tr></table>'''
    listing = original(body, event)
    path = "/pdf/" + filename[:-4] + "_20261021_210000.pdf"
    download = original(f'<a href="{path}">download</a>')
    download.update(
        source_url=ENDPOINT,
        query={
            "step": "9",
            "kind": kind,
            "co_id": "3017",
            "filename": filename,
            "colorchg": "1",
            "DEBUG": "",
        },
    )
    pdf = {
        **download,
        "source_url": HOST + path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode(),
    }
    return (
        event,
        listing,
        {"filename": filename, "download_response": download, "pdf_response": pdf},
    )


class FilingLinkTests(unittest.TestCase):
    def test_current_chain_does_not_establish_historical_event_or_numeric_approval(self):
        event, listing, doc = fixture()
        proof = verify_chain(event, listing, doc)
        self.assertEqual(proof["status"], "current_period_document_chain_verified")
        self.assertFalse(proof["event_version_link_verified"])
        self.assertFalse(proof["initial_publication_proven"])
        self.assertFalse(proof["numeric_content_reviewed"])

    def test_prospectus_kind_b_is_bound_separately_from_financial_kind_a(self):
        event, listing, doc = fixture("B")
        self.assertEqual(verify_chain(event, listing, doc)["kind"], "B")

    def test_exact_counts_and_caller_flag_do_not_prove_event_version_assignment(self):
        event, listing, doc = fixture()
        spec = {
            "events": [
                {
                    "row": event,
                    "listing_id": "list",
                    "document_ids": ["pdf"],
                    "event_version_link_verified": True,
                }
            ],
            "listings": {"list": listing},
            "documents": {"pdf": doc},
        }
        with tempfile.TemporaryDirectory() as temp:
            result = filing_link_audit.audit(spec, Path(temp) / "audit")
            with self.assertRaises(FileExistsError):
                filing_link_audit.audit(spec, Path(temp) / "audit")
        self.assertTrue(result["events"][0]["current_period_document_set_captured"])
        self.assertEqual(result["verified_event_version_links"], 0)

    def test_wrong_company_period_and_download_kind_rejected(self):
        event, listing, doc = fixture()
        for old, new in [
            ("<td>3017</td>", "<td>3231</td>"),
            ("115 年 第二季", "115 年 第三季"),
            ('readfile2("A"', 'readfile2("B"'),
        ]:
            text = base64.b64decode(listing["content_base64"]).decode()
            bad = original(text.replace(old, new), event)
            with self.subTest(new=new), self.assertRaises(ValueError):
                verify_chain(event, bad, doc)

    def test_tampered_listing_bytes_and_wrong_file_reference_rejected(self):
        event, listing, doc = fixture()
        bad = {**listing, "sha256": "bad"}
        with self.assertRaises(ValueError):
            verify_chain(event, bad, doc)
        with self.assertRaises(ValueError):
            verify_chain(event, listing, {**doc, "filename": "202602_3231_AI1.pdf"})

    def test_form_query_pdf_target_size_and_capture_chronology_bound(self):
        event, listing, doc = fixture()
        for change in ["query", "target", "size", "time", "http"]:
            bad = copy.deepcopy(doc)
            if change == "query":
                bad["download_response"]["query"]["kind"] = "B"
            if change == "target":
                bad["pdf_response"]["source_url"] += "?alternate=1"
            if change == "size":
                raw = b"%PDF-1.4\nwrong length"
                bad["pdf_response"].update(
                    content_base64=base64.b64encode(raw).decode(),
                    sha256=hashlib.sha256(raw).hexdigest(),
                )
            if change == "time":
                bad["pdf_response"]["captured_at"] = "2026-10-19T22:00:00+08:00"
            if change == "http":
                bad["pdf_response"]["http_status"] = 403
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify_chain(event, listing, bad)

    def test_refused_form_stops_before_pdf_request_and_keeps_receipt(self):
        class Transport:
            count = 0

            def post(self, url, **kwargs):
                self.count += 1
                self.kwargs = kwargs
                return type("Response", (), {"status_code": 429, "content": b"refused"})()

        event, listing, _ = fixture()
        transport = Transport()
        client = FilingLinkClient(transport)
        with self.assertRaises(ValueError):
            client.download(candidates(event, listing)[0])
        self.assertEqual(transport.count, 1)
        self.assertEqual(client.receipts[0]["http_status"], 429)
        self.assertNotIn("headers", transport.kwargs)
        self.assertFalse(transport.kwargs["allow_redirects"])

    def test_even_bound_total_does_not_prove_all_mops_source_families(self):
        ctx = {**CTX, "mode": "latest_research", "all_mops_announcements_proven": True}
        result = announcement_coverage.reconcile(
            ctx, {"index": index_doc([], 0)}, [], windows(), {"index"}
        )
        self.assertTrue(result["source_coverage_verified"])
        self.assertFalse(result["all_mops_announcements_proven"])
        self.assertFalse(result["latest_complete_research_allowed"])
        self.assertFalse(result["formal_valuation_allowed"])

    def test_transport_failure_keeps_safe_unavailable_receipt(self):
        class Transport:
            def post(self, *args, **kwargs):
                raise TimeoutError("private transport detail")

        event, listing, _ = fixture()
        client = FilingLinkClient(Transport())
        with self.assertRaises(ValueError):
            client.download(candidates(event, listing)[0])
        self.assertEqual(client.receipts[0]["error_kind"], "TimeoutError")
        self.assertNotIn("private transport detail", str(client.receipts))

    def test_daily_gate_records_current_pdf_but_keeps_event_link_pending(self):
        event, listing, doc = fixture()
        inventory = [
            {
                "id": announcement_coverage.identity(event),
                "original_url": event["HYPERLINK"],
                "document_id": "listing",
                "filing_documents": [doc],
            }
        ]
        result = announcement_coverage.reconcile(
            CTX,
            {"index": index_doc([event], 1), "listing": listing},
            inventory,
            windows(),
            {"index", "listing"},
        )
        pending = result["unprocessed_events"][0]
        self.assertTrue(pending["current_period_document_set_captured"])
        self.assertFalse(pending["event_version_link_verified"])
        self.assertEqual(pending["status"], "listing_only")

    def test_future_pdf_never_becomes_known_from_earlier_index_date(self):
        event, listing, doc = fixture()
        doc["pdf_response"]["captured_at"] = "2026-10-21T15:00:00+08:00"
        inventory = [
            {
                "id": announcement_coverage.identity(event),
                "original_url": event["HYPERLINK"],
                "document_id": "listing",
                "filing_documents": [doc],
            }
        ]
        result = announcement_coverage.reconcile(
            CTX,
            {"index": index_doc([event], 1), "listing": listing},
            inventory,
            windows(),
            {"index", "listing"},
        )
        pending = result["unprocessed_events"][0]
        self.assertFalse(pending["current_period_document_set_captured"])
        self.assertEqual(pending["excluded_filing_chains"][0]["reason"], "captured_after_cutoff")


if __name__ == "__main__":
    unittest.main()
