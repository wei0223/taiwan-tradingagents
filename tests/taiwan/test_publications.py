"""No network: real endpoint schema represented with explicitly synthetic values."""

import copy
import hashlib
import json
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.taiwan.provider import (
    FINANCIAL,
    REVENUE,
    FinMindClient,
    TaiwanMarketDataProvider,
)
from tradingagents.dataflows.taiwan.publications import (
    MopsAnnouncementClient,
    MopsPublicationResolver,
    digest,
    parse_announcement,
    roc_date,
    validate_publication,
)
from tradingagents.research.bundle import build_bundle

NOW = datetime(2024, 5, 20, 12, tzinfo=UTC)


def announcement(code="2330", item="F22", published="113/05/10", stamp="14:30:00"):
    revenue = item == "F22"
    path = "ajax_t05st10_ifrs" if revenue else "ajax_t164sb04"
    return {"COMPANY_ID": code, "AN_CODE": item, "CDATE": published, "CTIME": stamp,
            "SUBJECT": "113年4月營業收入" if revenue else "113年第1季綜合損益表",
            "HYPERLINK": f"https://mopsov.twse.com.tw/mops/web/{path}?co_id={code}&year=113&"
                         + ("month=04" if revenue else "season=1"),
            "COMPANY_NAME": "SYNTHETIC", "TYPEK": "上市"}


def numeric_rows(dataset):
    if dataset == REVENUE:
        return [{"date": "2024-05-01", "revenue_year": 2024, "revenue_month": 4, "revenue": 100}]
    if dataset == FINANCIAL:
        return [{"date": "2024-03-31", "type": "EPS", "value": 2, "origin_name": "synthetic EPS"}]
    return []


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload
        self.content = ("\ufeff \r\n" + json.dumps(payload, ensure_ascii=False)).encode("utf-8")

    def json(self):
        return self.payload


class Transport:
    def __init__(self, raw=None):
        self.raw = raw
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        query = kwargs["data"]
        rows = self.raw if self.raw is not None else [announcement(query["CO_ID"], query["PRO_ITEM"])]
        return Response({"status": "success", "data": rows})

    def get(self, url, **kwargs):
        query = kwargs["params"]
        if query["dataset"] == "TaiwanStockInfo":
            rows = [{"stock_id": c, "type": "twse", "date": "2024-05-20"}
                    for c in ("2330", "3017", "3231")]
        else:
            rows = [{**row, "stock_id": query["data_id"]} for row in numeric_rows(query["dataset"])]
        return Response({"status": 200, "data": rows})


def provider(transport=None, as_of="2024-05-20", captured=NOW):
    transport = transport or Transport()
    resolver = MopsPublicationResolver("2024-01-01", as_of,
                                     MopsAnnouncementClient(transport, lambda: captured))
    return TaiwanMarketDataProvider(FinMindClient(transport, lambda: captured), resolver), transport


class PublicationTests(unittest.TestCase):
    def setUp(self):
        for name in ("connect", "connect_ex"):
            guard = patch("socket.socket." + name, side_effect=AssertionError("Network forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_three_symbols_publications_provenance_and_cache(self):
        for code in ("2330", "3017", "3231"):
            with self.subTest(code=code):
                p, transport = provider()
                snapshot = p.snapshot(code, "2024-01-01", "2024-05-20")
                self.assertEqual(len(transport.posts), 2)
                for dataset in (REVENUE, FINANCIAL):
                    row = snapshot["datasets"][dataset]["records"][0]
                    self.assertEqual(row["published_at"], "2024-05-10")
                    self.assertEqual(row["available_at"], "2024-05-20")
                    proof = row["publication_evidence"]
                    self.assertEqual(proof["stock_id"], code)
                    self.assertEqual(proof["published_timestamp"], "2024-05-10T14:30:00+08:00")
                    self.assertEqual(proof["value_row_sha256"], digest(row["raw"]))
                    self.assertEqual(proof["announcement_sha256"], digest(proof["raw_announcement"]))
                    self.assertEqual(proof["retrieved_at"], NOW.isoformat())
                self.assertEqual(len(snapshot["publication_audit"]), 2)
                for archive in snapshot["publication_audit"]:
                    self.assertEqual(hashlib.sha256(archive["raw_response_utf8"].encode("utf-8")).hexdigest(),
                                     archive["response_sha256"])
                bundle = build_bundle(snapshot)
                self.assertTrue(any(e["publication_sources"] for e in bundle["evidence"].values()))

    def test_after_analysis_publications_excluded_for_three_symbols(self):
        for code in ("2330", "3017", "3231"):
            for dataset in (REVENUE, FINANCIAL):
                with self.subTest(code=code, dataset=dataset):
                    p, _ = provider(as_of="2024-05-09")
                    result = p.fetch(dataset, code, "2024-01-01", "2024-05-09")
                    self.assertEqual(result.records, [])
                    self.assertIn("published after as_of", " ".join(result.warnings))
                    self.assertTrue(result.raw_rows)

    def test_old_announcements_do_not_backdate_new_values(self):
        for dataset in (REVENUE, FINANCIAL):
            p, _ = provider(as_of="2024-05-15")
            result = p.fetch(dataset, "2330", "2024-01-01", "2024-05-15")
            self.assertEqual(result.records, [])
            self.assertIn("value vintage captured after as_of", " ".join(result.warnings))

    def test_exact_day_allowed_and_taipei_capture_boundary(self):
        p, _ = provider(captured=datetime(2024, 5, 20, 16, 1, tzinfo=UTC))
        result = p.fetch(REVENUE, "2330", "2024-01-01", "2024-05-20")
        self.assertEqual(result.records, [])  # UTC night is the next date in Taipei.
        p, _ = provider()
        self.assertEqual(len(p.fetch(REVENUE, "2330", "2024-01-01", "2024-05-20").records), 1)

    def test_publication_on_analysis_day_allowed_for_eod(self):
        for dataset in (REVENUE, FINANCIAL):
            p, _ = provider(as_of="2024-05-10", captured=datetime(2024, 5, 10, 12, tzinfo=UTC))
            record = p.fetch(dataset, "2330", "2024-01-01", "2024-05-10").records[0]
            self.assertEqual(record["published_at"], "2024-05-10")
            self.assertEqual(record["available_at"], "2024-05-10")

    def test_later_correction_and_identical_duplicates(self):
        first = announcement()
        correction = announcement(published="113/05/18")
        p, _ = provider(Transport([first, correction, copy.deepcopy(correction)]))
        result = p.fetch(REVENUE, "2330", "2024-01-01", "2024-05-20")
        self.assertEqual(result.records[0]["published_at"], "2024-05-18")
        p, _ = provider(Transport([first, correction]), as_of="2024-05-15")
        self.assertEqual(p.fetch(REVENUE, "2330", "2024-01-01", "2024-05-15").records, [])

    def test_unknown_and_invalid_announcements_do_not_enable_values(self):
        variants = [[], [{**announcement(), "SUBJECT": "unknown"}],
                    [{**announcement(), "COMPANY_ID": "3017"}],
                    [{**announcement(), "CDATE": "113/04/30"}],
                    [{**announcement(), "HYPERLINK": "https://evil.example/"}]]
        for rows in variants:
            p, _ = provider(Transport(rows))
            self.assertEqual(p.fetch(REVENUE, "2330", "2024-01-01", "2024-05-20").records, [])

    def test_subject_link_period_company_and_date_validation(self):
        for fields in ({"HYPERLINK": announcement()["HYPERLINK"].replace("month=04", "month=05")},
                       {"HYPERLINK": announcement()["HYPERLINK"] + "&co_id=3017"},
                       {"CDATE": "113/02/30"}, {"CTIME": "25:00:00"}, {"AN_CODE": "M31"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                parse_announcement(REVENUE, "2330", {**announcement(), **fields})
        self.assertEqual(roc_date("113/02/29").isoformat(), "2024-02-29")

    def test_archive_future_or_tampered_values_rejected_on_rebuild(self):
        p, _ = provider()
        source = p.snapshot("2330", "2024-01-01", "2024-05-20")
        for field, value in (("available_at", "2024-05-10"), ("published_at", "2024-05-09"),
                             ("values", {"revenue": 999})):
            modified = copy.deepcopy(source)
            modified["datasets"][REVENUE]["records"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                build_bundle(modified)
        modified = copy.deepcopy(source)
        modified["as_of"] = "2024-05-15"
        with self.assertRaises(ValueError):
            build_bundle(modified)
        del source["datasets"][REVENUE]["records"][0]["publication_evidence"]
        with self.assertRaises(ValueError):
            build_bundle(source)

    def test_row_hash_and_metadata_tampering(self):
        p, _ = provider()
        record = p.fetch(REVENUE, "2330", "2024-01-01", "2024-05-20").records[0]
        for field, value in (("announcement_sha256", "0" * 64), ("stock_id", "3017"),
                             ("value_row_sha256", "0" * 64), ("retrieved_at", "2024-05-01T00:00:00Z")):
            proof = {**record["publication_evidence"], field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_publication(REVENUE, record["raw"], proof, "2024-05-01T00:00:00Z")

    def test_errors_empty_schema_and_truncation_redacted(self):
        transport = Transport()
        client = MopsAnnouncementClient(transport, lambda: NOW)
        for payload in ({"status": "fail", "data": [], "message": ["private error"]},
                        {"status": "success", "data": [{}] * 1000},
                        {"status": "success", "data": {}}, {"status": "success", "data": [None]}):
            with patch.object(transport, "post", return_value=Response(payload)), self.assertRaises(VendorUnavailableError):
                client.query(REVENUE, "2330", "2024-01-01", "2024-05-20")
        with patch.object(transport, "post", side_effect=RuntimeError("secret-test-value")):
            with self.assertRaises(VendorUnavailableError) as context:
                client.query(REVENUE, "2330", "2024-01-01", "2024-05-20")
            self.assertNotIn("secret-test-value", str(context.exception))
            p, _ = provider(transport)
            result = p.fetch(REVENUE, "2330", "2024-01-01", "2024-05-20")
            self.assertEqual(result.records, [])
            self.assertTrue(result.raw_rows)
        with patch.object(transport, "post", return_value=Response(
                {"status": "fail", "data": [], "message": ["查無公告資料"]})):
            self.assertEqual(client.query(REVENUE, "2330", "2024-01-01", "2024-05-20")["raw_rows"], [])

    def test_token_not_forwarded_and_strict_query(self):
        transport = Transport()
        with patch.dict("os.environ", {"FINMIND_TOKEN": "synthetic-token"}):
            MopsAnnouncementClient(transport, lambda: NOW).query(REVENUE, "2330", "2024-01-01", "2024-05-20")
        self.assertNotIn("headers", transport.posts[0][1])
        self.assertEqual(transport.posts[0][1]["data"]["SDATE"], "113/01/01")
        self.assertEqual(transport.posts[0][1]["timeout"], 20)
        for code, start, end in (("../2330", "2024-01-01", "2024-05-20"),
                                 ("2330", "2012-01-01", "2024-05-20"),
                                 ("2330", "2024-06-01", "2024-05-20")):
            with self.assertRaises(ValueError):
                MopsAnnouncementClient(transport).query(REVENUE, code, start, end)


if __name__ == "__main__":
    unittest.main()
