"""Official observed market sessions, never weekday guesses or inferred stock sessions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta

from tradingagents.dataflows.errors import VendorUnavailableError

from .provider import day
from .publications import TAIPEI, instant, roc_date


class OfficialCalendarProvider:
    twse_url = "https://www.twse.com.tw/exchangeReport/FMTQIK"
    tpex_url = "https://www.tpex.org.tw/www/en-us/indexInfo/inx"

    def __init__(self, transport=None, clock=None, *, archive_dir=None, live=True, max_requests=200):
        if transport is None:
            import requests
            transport = requests.Session()
        if not 1 <= max_requests <= 200:
            raise ValueError("Calendar request budget out of bounds")
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(UTC))
        self.cache, self.stopped = {}, set()
        self.requests, self.max_requests, self.live = 0, max_requests, live
        self.archive = None
        if archive_dir:
            from .calendar_recovery import CalendarArchive
            self.archive = CalendarArchive(archive_dir, self.clock)
            try:
                self.stopped.update(self.archive.store.recent_refusals(self.clock()))
            except (ValueError, KeyError, TypeError, UnicodeError):
                # Corrupt restriction history cannot authorize another network request.
                self.stopped.update({"www.twse.com.tw", "www.tpex.org.tw"})
                self.archive.rejected.append({"reason": "invalid_cooldown_receipt"})

    def query(self, url, params):
        from urllib.parse import urlsplit

        from .originals import source_restricted

        if url not in {self.twse_url, self.tpex_url}:
            raise ValueError("Official calendar URL required")
        key = (url, tuple(sorted(params.items())))
        if key in self.cache:
            return self.cache[key]
        host = urlsplit(url).netloc
        if not self.live or host in self.stopped or self.requests >= self.max_requests:
            raise VendorUnavailableError("Calendar capture unavailable or source stopped")
        self.requests += 1
        try:
            response = self.transport.get(url, params=params, timeout=20, allow_redirects=False)
            now = self.clock()
            if now.tzinfo is None:
                raise ValueError("Clock requires timezone")
            if response.status_code != 200 or source_restricted(response.content):
                if self.archive:
                    import base64
                    self.archive.store.capture({
                        "source_url": url, "query": params, "http_status": response.status_code,
                        "status": "source_rejected" if response.status_code in {403, 429}
                                  or source_restricted(response.content) else "unavailable",
                        "captured_at": now.isoformat(),
                        "content_base64": base64.b64encode(response.content).decode(),
                        "sha256": hashlib.sha256(response.content).hexdigest()})
                raise ValueError("Calendar source rejected request")
            raw = response.content.decode("utf-8-sig")
            payload = json.loads(raw)
            archive = {
                "source_url": url, "query": params,
                "retrieved_at": now.astimezone(UTC).isoformat(),
                "raw_response_utf8": response.content.decode("utf-8"),
                "response_sha256": hashlib.sha256(response.content).hexdigest(),
            }
            exchange = "twse" if url == self.twse_url else "tpex"
            from .calendar_recovery import archive_info
            archive_info(archive, exchange)
            if self.archive:
                self.archive.add(archive, exchange)
            self.cache[key] = payload, archive
            return payload, archive
        except Exception:
            self.stopped.add(host)
            raise VendorUnavailableError("Official session source unavailable or invalid") from None

    def fetch(self, exchange, start, as_of):
        from calendar import monthrange

        from .calendar_recovery import covers

        if exchange not in {"twse", "tpex"} or day(start) > day(as_of):
            raise ValueError("Invalid calendar request")
        end = day(as_of) - timedelta(days=1)
        cursor = day(start).replace(day=1)
        if (end.year - cursor.year) * 12 + end.month - cursor.month >= 200:
            raise ValueError("Calendar scope exceeds two hundred monthly captures")
        cutoff = instant(as_of + "T23:59:59.999999+08:00")
        result = {"exchange": exchange, "start": start, "end": end.isoformat(),
                  "sessions": [], "archives": [], "status": "unavailable", "warnings": [],
                  "basis": "verified official monthly originals; no weekday inference"}
        reused, failed, dates = [], [], []
        while cursor <= end:
            bound = min(end, cursor.replace(day=monthrange(cursor.year, cursor.month)[1]))
            url = self.twse_url if exchange == "twse" else self.tpex_url
            params = {"date": cursor.strftime("%Y%m%d" if exchange == "twse" else "%Y/%m/%d"),
                      "response": "json"}
            try:
                archive = self.archive.select(exchange, cursor, bound, cutoff) if self.archive else None
                if archive is not None:
                    payload = json.loads(archive["raw_response_utf8"].lstrip("\ufeff"))
                    reused.append(cursor.isoformat())
                else:
                    if self.clock() > cutoff:
                        raise VendorUnavailableError("New capture would be after research cutoff")
                    payload, archive = self.query(url, params)
                    if not covers(archive, exchange, bound, cutoff):
                        raise ValueError("Monthly original does not cover the required end")
                result["archives"].append(archive)
                dates.extend(month_sessions(payload, exchange, cursor))
            except (VendorUnavailableError, KeyError, TypeError, ValueError, IndexError):
                failed.append(cursor.isoformat())
            cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        if len(set(dates)) != len(dates):
            raise ValueError("Duplicate official market sessions")
        result["sessions"] = sorted(d.isoformat() for d in dates if day(start) <= d <= end)
        if result["sessions"]:
            result["status"] = "partial" if failed else "ok"
        if failed or not result["sessions"]:
            result["warnings"] = ["Official calendar coverage unavailable; do not infer weekdays"]
        result["recovery"] = {"reused_months": reused, "failed_months": failed,
                              "http_requests": self.requests}
        verify_calendar(result, exchange)
        return result




def month_sessions(payload, exchange, month):
    if not isinstance(payload, dict) or payload.get("date") != month.strftime("%Y%m%d"):
        raise ValueError("Unverified calendar month")
    if exchange == "twse":
        table = payload
        if payload.get("stat") != "OK" or table.get("fields", [])[:1] != ["日期"]:
            raise ValueError("Invalid TWSE response")
        parse = roc_date
    else:
        tables = payload.get("tables", [])
        if (
            payload.get("stat") != "ok"
            or len(tables) != 1
            or tables[0].get("fields", [])[:1] != ["Date"]
        ):
            raise ValueError("Invalid TPEx response")
        table = tables[0]
        if table.get("date") != month.strftime("%Y/%m") or table.get("totalCount") != len(
            table.get("data", [])
        ):
            raise ValueError("Incomplete TPEx month")

        def parse(value):
            return datetime.strptime(value, "%Y/%m/%d").date()

    if not table.get("data"):
        raise ValueError("No observed market sessions")
    observed = [parse(row[0]) for row in table["data"]]
    if any((d.year, d.month) != (month.year, month.month) for d in observed):
        raise ValueError("Calendar returned another month")
    return observed


def verify_calendar(calendar, exchange):
    if calendar["exchange"] != exchange or calendar["status"] not in {
        "ok",
        "partial",
        "unavailable",
    }:
        raise ValueError("Invalid official calendar")
    sessions = calendar["sessions"]
    if sessions != sorted(set(sessions)) or any(
        not day(calendar["start"]) <= day(d) <= day(calendar["end"]) for d in sessions
    ):
        raise ValueError("Invalid market sessions")
    recovered = set()
    months = set()
    for archive in calendar["archives"]:
        raw = archive["raw_response_utf8"]
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != archive["response_sha256"]:
            raise ValueError("Calendar archive changed")
        payload = json.loads(raw.lstrip("\ufeff"))
        captured = instant(archive["retrieved_at"]).astimezone(TAIPEI).date()
        if calendar["status"] == "unavailable":
            continue  # Keep malformed response archives for audit; never interpret them as sessions.
        url = (
            OfficialCalendarProvider.twse_url
            if exchange == "twse"
            else OfficialCalendarProvider.tpex_url
        )
        if archive["source_url"] != url or archive["query"].get("response") != "json":
            raise ValueError("Unexpected calendar source")
        requested = archive["query"]["date"]
        month = datetime.strptime(requested, "%Y%m%d" if exchange == "twse" else "%Y/%m/%d").date()
        if month.day != 1 or month.isoformat() in months:
            raise ValueError("Unverified/duplicate calendar month")
        months.add(month.isoformat())
        observed = month_sessions(payload, exchange, month)
        if any(d > captured for d in observed):
            raise ValueError("Future observed market session")
        if len(set(observed)) != len(observed):
            raise ValueError("Duplicate official calendar session")
        recovered.update(d.isoformat() for d in observed)
    if calendar["status"] == "ok":
        cursor = day(calendar["start"]).replace(day=1)
        expected = set()
        while cursor <= day(calendar["end"]):
            expected.add(cursor.isoformat())
            cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        if months != expected:
            raise ValueError("Missing official calendar month")
    if calendar["status"] == "unavailable" and sessions:
        raise ValueError("Unavailable calendar has sessions")
    bounded = sorted(d for d in recovered if calendar["start"] <= d <= calendar["end"])
    if calendar["status"] in {"ok", "partial"} and sessions != bounded:
        raise ValueError("Session list disagrees with official archive")
    return sessions
