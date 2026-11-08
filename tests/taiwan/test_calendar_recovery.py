import copy
import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from tradingagents.dataflows.taiwan.calendar import OfficialCalendarProvider, verify_calendar
from tradingagents.dataflows.taiwan.calendar_recovery import CalendarArchive, recover

NOW = datetime(2026, 11, 8, 6, tzinfo=UTC)


def archive(month="2026-11", dates=("2026-11-05", "2026-11-06"), captured="2026-11-06T15:00:00+00:00",
            exchange="twse"):
    query = {"date": month.replace("-", "") + "01", "response": "json"}
    payload = {"date": query["date"], "stat": "OK", "fields": ["日期"],
               "data": [[f"{int(d[:4])-1911}/{d[5:7]}/{d[8:]}"] for d in dates]}
    url = OfficialCalendarProvider.twse_url
    if exchange == "tpex":
        url = OfficialCalendarProvider.tpex_url
        query["date"] = month.replace("-", "/") + "/01"
        payload = {"date": month.replace("-", "")+"01", "stat": "ok",
                   "tables": [{"date": month.replace("-", "/"), "fields": ["Date"],
                               "totalCount": len(dates), "data": [[d.replace("-", "/")] for d in dates]}]}
    raw = json.dumps(payload, ensure_ascii=False)
    return {"source_url": url, "query": query, "retrieved_at": captured,
            "raw_response_utf8": raw, "response_sha256": hashlib.sha256(raw.encode()).hexdigest()}


def snapshot(a, as_of="2026-11-07", end="2026-11-05", sessions=("2026-11-05",), exchange="twse"):
    return {"instrument": {"stock_id": "1216", "exchange": exchange}, "as_of": as_of,
            "market_calendar": {"exchange": exchange, "start": "2026-11-01", "end": end,
                                "status": "ok", "sessions": list(sessions), "archives": [a]}}


class Source:
    def __init__(self, response=None, status=200):
        self.calls = []
        self.response = response
        self.status = status

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs["params"]))
        if self.response is None:
            raise TimeoutError("synthetic network outage")
        raw = self.response if isinstance(self.response, bytes) else json.dumps(self.response).encode()
        return type("Response", (), {"status_code": self.status, "content": raw})()


class RecoveryTests(unittest.TestCase):
    def test_prior_raw_month_can_cover_a_later_cutoff_without_new_requests(self):
        for exchange in ("twse", "tpex"):
            with self.subTest(exchange=exchange), tempfile.TemporaryDirectory() as tmp:
                prior = snapshot(archive(exchange=exchange), exchange=exchange)
                failed = copy.deepcopy(prior)
                failed["market_calendar"].update(status="unavailable", sessions=[], archives=[], end="2026-11-06")
                frozen = copy.deepcopy(failed)
                source = Source()
                result, receipt = recover(failed, tmp, seeds=[prior], transport=source, clock=lambda: NOW)
                self.assertEqual(receipt["status"], "recovered")
                self.assertEqual(source.calls, [])
                self.assertEqual(result["market_calendar"]["sessions"], ["2026-11-05", "2026-11-06"])
                self.assertEqual(result["market_calendar"]["archives"][0], prior["market_calendar"]["archives"][0])
                self.assertEqual(failed, frozen)
                self.assertEqual(verify_calendar(result["market_calendar"], exchange), result["market_calendar"]["sessions"])
                # A later process uses the persisted archive without a seed argument.
                again, again_receipt = recover(failed, tmp, transport=source, clock=lambda: NOW)
                self.assertEqual(again["market_calendar"]["sessions"], result["market_calendar"]["sessions"])
                self.assertEqual(again_receipt["requests"], 0)

    def test_stale_capture_cannot_infer_a_non_session_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CalendarArchive(tmp, lambda: NOW)
            store.add(archive(), "twse")
            source = Source()
            provider = OfficialCalendarProvider(source, lambda: NOW, archive_dir=tmp, live=False)
            result = provider.fetch("twse", "2026-11-01", "2026-11-08")
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["sessions"], [])
            self.assertEqual(source.calls, [])

    def test_snapshot_after_research_cutoff_is_not_usable_for_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CalendarArchive(tmp, lambda: NOW)
            store.add(archive(captured="2026-11-08T00:00:00+00:00"), "twse")
            provider = OfficialCalendarProvider(Source(), lambda: NOW, archive_dir=tmp, live=False)
            self.assertEqual(provider.fetch("twse", "2026-11-01", "2026-11-07")["status"], "unavailable")

    def test_tampered_archive_is_rejected_and_never_becomes_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CalendarArchive(tmp, lambda: NOW)
            store.add(archive(), "twse")
            path = next((Path(tmp) / "calendar-archives").glob("*.json"))
            data = json.loads(path.read_text("utf8"))
            data["archive"]["raw_response_utf8"] += " "
            path.write_text(json.dumps(data), encoding="utf8")
            provider = OfficialCalendarProvider(Source(), lambda: NOW, archive_dir=tmp, live=False)
            self.assertTrue(provider.archive.rejected)
            self.assertEqual(provider.fetch("twse", "2026-11-01", "2026-11-07")["sessions"], [])

    def test_future_or_foreign_market_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CalendarArchive(tmp, lambda: NOW)
            with self.assertRaises(ValueError):
                store.add(archive(exchange="tpex"), "twse")
            with self.assertRaises(ValueError):
                store.add(archive(captured="2026-11-09T00:00:00+00:00"), "twse")

    def test_only_missing_month_is_requested_and_new_month_is_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CalendarArchive(tmp, lambda: NOW)
            store.add(archive(month="2026-10", dates=("2026-10-30",),
                              captured="2026-11-01T00:00:00+00:00"), "twse")
            payload = json.loads(archive(captured=NOW.isoformat())["raw_response_utf8"])
            source = Source(payload)
            provider = OfficialCalendarProvider(source, lambda: NOW, archive_dir=tmp)
            result = provider.fetch("twse", "2026-10-01", "2026-11-08")
            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(source.calls), 1)
            self.assertEqual(source.calls[0][1]["date"], "20261101")
            self.assertEqual(result["recovery"]["reused_months"], ["2026-10-01"])

    def test_refusal_or_throttle_page_stops_host_and_survives_restart(self):
        for status, raw in [(429, b"rate limit"), (403, b"forbidden"), (200, "查詢過於頻繁".encode())]:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                source = Source(raw, status=status)
                first = OfficialCalendarProvider(source, lambda: NOW, archive_dir=tmp)
                self.assertEqual(first.fetch("twse", "2026-09-01", "2026-11-08")["status"], "unavailable")
                self.assertEqual(len(source.calls), 1)
                second = OfficialCalendarProvider(source, lambda: NOW, archive_dir=tmp)
                second.fetch("twse", "2026-09-01", "2026-11-08")
                self.assertEqual(len(source.calls), 1)

    def test_partial_month_failure_preserves_later_cached_months(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CalendarArchive(tmp, lambda: NOW)
            store.add(archive(month="2026-09", dates=("2026-09-30",),
                              captured="2026-10-01T00:00:00+00:00"), "twse")
            store.add(archive(captured=NOW.isoformat()), "twse")
            source = Source(b"forbidden", status=403)
            result = OfficialCalendarProvider(source, lambda: NOW, archive_dir=tmp).fetch(
                "twse", "2026-09-01", "2026-11-08")
            self.assertEqual(result["status"], "partial")
            self.assertIn("2026-11-06", result["sessions"])
            self.assertEqual(result["recovery"]["failed_months"], ["2026-10-01"])
            self.assertEqual(len(source.calls), 1)
            verify_calendar(result, "twse")

    def test_live_captures_cannot_run_after_cutoff(self):
        source = Source({})
        result = OfficialCalendarProvider(source, lambda: NOW).fetch("twse", "2026-11-01", "2026-11-07")
        self.assertEqual(source.calls, [])
        self.assertEqual(result["status"], "unavailable")

    def test_request_budget_and_duplicate_observations_are_not_waived(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = archive(dates=("2026-11-06", "2026-11-06"))
            with self.assertRaises(ValueError):
                CalendarArchive(tmp, lambda: NOW).add(bad, "twse")
            provider = OfficialCalendarProvider(Source(), lambda: NOW, max_requests=1)
            provider.fetch("twse", "2026-09-01", "2026-11-08")
            self.assertEqual(provider.requests, 1)

    def test_fixed_entry_binds_the_effective_snapshot_and_preserves_acquisition(self):
        from tests.taiwan.test_fixed_pipeline import market_snapshot
        from tradingagents.research.bundle import fingerprint
        from tradingagents.research.fixed_pipeline import prepare
        good = market_snapshot(1000)
        failed = copy.deepcopy(good)
        failed["market_calendar"].update(status="unavailable", sessions=[], archives=[])
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "fixed"
            state = prepare(failed, output, calendar_archive=Path(tmp) / "cache", calendar_seeds=[good])
            self.assertEqual(state["technical_status"], "ready")
            self.assertEqual(state["acquisition_snapshot_sha256"], fingerprint(failed))
            effective = json.loads((output / state["effective_snapshot"]).read_text("utf8"))
            self.assertEqual(fingerprint(effective), state["source_snapshot_sha256"])
            self.assertNotEqual(state["acquisition_snapshot_sha256"], state["source_snapshot_sha256"])
            self.assertEqual(failed["market_calendar"]["status"], "unavailable")

