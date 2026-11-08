"""Verified calendar originals and recovery without inferred trading sessions."""

import copy
import hashlib
import json
from calendar import monthrange
from datetime import timedelta

from tradingagents.research.bundle import fingerprint

from .calendar import OfficialCalendarProvider, month_sessions, verify_calendar
from .provider import day
from .publications import TAIPEI, instant
from .source_store import SourceStore


def archive_info(archive, exchange):
    """Validate one whole monthly response even if its enclosing fetch failed."""
    raw = archive["raw_response_utf8"]
    if hashlib.sha256(raw.encode("utf8")).hexdigest() != archive["response_sha256"]:
        raise ValueError("Calendar archive changed")
    query = archive["query"]
    requested = query["date"].replace("/", "")
    month = day(f"{requested[:4]}-{requested[4:6]}-{requested[6:]}")
    if month.day != 1:
        raise ValueError("Calendar month must start on day one")
    dates = [d.isoformat() for d in month_sessions(json.loads(raw.lstrip("\ufeff")), exchange, month)]
    end = month.replace(day=monthrange(month.year, month.month)[1]).isoformat()
    verify_calendar({"exchange": exchange, "status": "partial", "start": month.isoformat(),
                     "end": end, "sessions": sorted(dates), "archives": [archive]}, exchange)
    return month, dates, instant(archive["retrieved_at"])


def covers(archive, exchange, end, cutoff):
    _, dates, captured = archive_info(archive, exchange)
    if captured > cutoff:
        return False
    # A later capture covers non-session days; an explicitly observed end day
    # also works when a prior snapshot only selected an earlier part of the raw month.
    return captured.astimezone(TAIPEI).date() > end or max(dates) >= end.isoformat()


class CalendarArchive:
    def __init__(self, root, clock):
        self.store = SourceStore(root)
        self.clock = clock
        self.entries = {}
        self.rejected = []
        for path in (self.store.root / "calendar-archives").glob("*.json"):
            try:
                item = json.loads(path.read_text("utf8"))
                if fingerprint(item) != path.stem:
                    raise ValueError("Calendar cache metadata changed")
                self.add(item["archive"], item["exchange"], persist=False)
            except (ValueError, KeyError, TypeError, UnicodeError):
                self.rejected.append({"object": path.stem, "reason": "invalid_cached_archive"})

    def add(self, archive, exchange, persist=True):
        month, _, captured = archive_info(archive, exchange)
        if captured > self.clock():
            raise ValueError("Calendar capture is in the future")
        item = {"exchange": exchange, "archive": archive}
        key = fingerprint(item)
        self.entries.setdefault((exchange, month), {})[key] = archive
        if persist:
            self.store._object("calendar-archives", key, json.dumps(
                item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())

    def select(self, exchange, month, end, cutoff):
        candidates = [a for a in self.entries.get((exchange, month), {}).values()
                      if covers(a, exchange, end, cutoff)]
        return max(candidates, key=lambda a: instant(a["retrieved_at"])) if candidates else None

    def seed(self, snapshot):
        exchange = snapshot["instrument"]["exchange"]
        calendar = snapshot.get("market_calendar", {})
        if calendar.get("exchange") != exchange:
            raise ValueError("Snapshot and calendar market mismatch")
        pending = [calendar]
        while pending:
            part = pending.pop()
            if part.get("exchange", exchange) != exchange:
                raise ValueError("Mixed calendar markets")
            for archive in part.get("archives", []):
                try:
                    self.add(archive, exchange)
                except (ValueError, KeyError, TypeError, UnicodeError):
                    self.rejected.append({"reason": "invalid_seed_archive"})
            pending.extend(part.get("failed_parts", []))


def recover(snapshot, archive_dir, *, seeds=(), live=False, transport=None, clock=None):
    """Return a separate source edition; cached bytes retain their original times."""
    original = copy.deepcopy(snapshot)
    calendar = original.get("market_calendar")
    if not calendar:
        raise ValueError("An original calendar query scope is required")
    provider = OfficialCalendarProvider(transport, clock, archive_dir=archive_dir, live=live)
    for seed in [snapshot, *seeds]:
        provider.archive.seed(seed)
    market = original["instrument"]["exchange"]
    as_of = original["as_of"]
    # Even a successful input must be reverified before its originals enter cache.
    cutoff = instant(as_of + "T23:59:59.999999+08:00")
    required_end = day(as_of) - timedelta(days=1)
    if calendar.get("status") == "ok" and day(calendar["end"]) >= required_end:
        verify_calendar(calendar, market)
        eligible = True
        for archive in calendar["archives"]:
            month, _, _ = archive_info(archive, market)
            month_end = month.replace(day=monthrange(month.year, month.month)[1])
            if not covers(archive, market, min(required_end, month_end), cutoff):
                eligible = False
                break
        if eligible:
            return original, {"status": "already_verified", "requests": 0}
    result = provider.fetch(market, calendar["start"], as_of)
    receipt = {"status": "recovered" if result["status"] == "ok" else "unavailable",
               "original_snapshot_sha256": fingerprint(snapshot),
               "requests": provider.requests, "reused_months": result["recovery"]["reused_months"],
               "failed_months": result["recovery"]["failed_months"],
               "rejected_archives": provider.archive.rejected}
    if result["status"] != "ok":
        return original, receipt
    original["market_calendar"] = result
    original["calendar_recovery"] = receipt
    original.pop("fixed_technical", None)
    if "extended_research" in original:
        from .extensions import extension_metrics
        original["extended_research"] = extension_metrics(original)
    return original, receipt

