"""MOPS announcement timestamps, separate from FinMind observation/value vintages.

Never backdate a newly downloaded numeric row to an earlier announcement. The
first usable date is at least the Taipei capture date of both sources.
"""

from __future__ import annotations

import base64
import calendar
import hashlib
import json
import re
import time
from datetime import UTC, date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from requests.exceptions import ConnectionError, Timeout

from tradingagents.dataflows.errors import VendorUnavailableError

from .provider import FINANCIAL, REVENUE, day

TAIPEI = timezone(timedelta(hours=8))
CASHFLOW = "TaiwanStockCashFlowsStatement"
BALANCE = "TaiwanStockBalanceSheet"
ITEMS = {REVENUE: "F22", FINANCIAL: "F27", CASHFLOW: "F28", BALANCE: "F26"}
PATHS = {
    REVENUE: "/mops/web/ajax_t05st10_ifrs",
    FINANCIAL: "/mops/web/ajax_t164sb04",
    CASHFLOW: "/mops/web/ajax_t164sb05",
    BALANCE: "/mops/web/ajax_t164sb03",
}
STATEMENT_NAMES = {FINANCIAL: "綜合損益表", CASHFLOW: "現金流量表", BALANCE: "資產負債表"}


def digest(value):
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def instant(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Timezone required")
    return parsed


def roc_date(value):
    match = re.fullmatch(r"(\d{3})/(\d{2})/(\d{2})", value)
    if not match:
        raise ValueError("Invalid MOPS ROC date")
    year, month, number = map(int, match.groups())
    return date(year + 1911, month, number)


def query_date(value):
    parsed = day(value)
    if parsed.year < 2013:
        raise ValueError("MOPS ezSearch supports dates from 2013")
    return f"{parsed.year - 1911:03d}/{parsed.month:02d}/{parsed.day:02d}"


def row_period(dataset, row):
    if dataset == REVENUE:
        return date(int(row["revenue_year"]), int(row["revenue_month"]), 1).strftime("%Y-%m")
    if dataset in STATEMENT_NAMES:
        parsed = day(row["date"])
        if (
            parsed.month not in (3, 6, 9, 12)
            or parsed.day != calendar.monthrange(parsed.year, parsed.month)[1]
        ):
            raise ValueError("Financial period must end on quarter end")
        return parsed.isoformat()
    raise ValueError("Unsupported publication dataset")


def parse_announcement(dataset, stock_id, raw):
    """Strict period/company/link agreement; unsupported announcements fail closed."""
    if raw["COMPANY_ID"] != stock_id or raw["AN_CODE"] != ITEMS[dataset]:
        raise ValueError("MOPS company/item mismatch")
    if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", raw["CTIME"]):
        raise ValueError("Invalid announcement time")
    published = roc_date(raw["CDATE"])
    stamp = datetime.fromisoformat(f"{published}T{raw['CTIME']}+08:00")
    pattern = (
        r"(\d{3})年(\d{1,2})月營業收入"
        if dataset == REVENUE
        else r"(\d{3})年第([1-4])季" + STATEMENT_NAMES[dataset]
    )
    match = re.fullmatch(pattern, raw["SUBJECT"].strip())
    if not match:
        raise ValueError("Unsupported announcement subject")
    year, part = map(int, match.groups())
    month = part if dataset == REVENUE else part * 3
    period_date = date(
        year + 1911, month, 1 if dataset == REVENUE else calendar.monthrange(year + 1911, month)[1]
    )
    if dataset == REVENUE:
        # A report cannot be released before its entire revenue month has ended.
        if published <= period_date.replace(day=calendar.monthrange(period_date.year, month)[1]):
            raise ValueError("Publication precedes period end")
    elif published < period_date:
        raise ValueError("Publication precedes quarter end")
    link = urlsplit(raw["HYPERLINK"])
    if (
        link.scheme != "https"
        or link.netloc != "mopsov.twse.com.tw"
        or link.path != PATHS[dataset]
        or link.fragment
    ):
        raise ValueError("Untrusted MOPS document URL")
    params = parse_qs(link.query, keep_blank_values=True)
    expected = {
        "co_id": stock_id,
        "year": str(year),
        "month" if dataset == REVENUE else "season": f"{part:02d}"
        if dataset == REVENUE
        else str(part),
    }
    if any(params.get(key) != [value] for key, value in expected.items()):
        raise ValueError("Announcement period/link mismatch")
    return {
        "period": period_date.strftime("%Y-%m") if dataset == REVENUE else period_date.isoformat(),
        "published_at": published.isoformat(),
        "published_timestamp": stamp.isoformat(),
        "document_url": raw["HYPERLINK"],
        "raw_announcement": raw,
        "announcement_sha256": digest(raw),
    }


class MopsAnnouncementClient:
    endpoint = "https://mopsov.twse.com.tw/mops/web/ezsearch_query"

    def __init__(self, transport=None, clock=None, sleeper=None):
        if transport is None:
            import requests

            transport = requests.Session()
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(UTC))
        self.sleeper = sleeper or time.sleep

    def query(self, dataset, stock_id, start, as_of):
        failures = []
        for attempt in range(3):
            try:
                archive = self._query_once(dataset, stock_id, start, as_of)
                archive["failed_attempts"] = failures
                archive["attempt_count"] = attempt + 1
                return archive
            except VendorUnavailableError as exc:
                failures.append(exc.failure_receipt)
                if not exc.retryable or attempt == 2:
                    exc.failure_receipt = {**exc.failure_receipt, "failed_attempts": failures}
                    raise
                self.sleeper((2, 5)[attempt])

    def _query_once(self, dataset, stock_id, start, as_of):
        if (
            dataset not in ITEMS
            or not re.fullmatch(r"\d{4,6}", stock_id)
            or day(start) > day(as_of)
        ):
            raise ValueError("Invalid MOPS query")
        params = {
            "step": "00",
            "RADIO_CM": "2",
            "TYPEK": "",
            "CO_MARKET": "",
            "CO_ID": stock_id,
            "PRO_ITEM": ITEMS[dataset],
            "SUBJECT": "",
            "SDATE": query_date(start),
            "EDATE": query_date(as_of),
            "lang": "TW",
            "AN": "",
        }
        response = None
        try:
            # Own transport; never forward FinMind Authorization headers to MOPS.
            response = self.transport.post(
                self.endpoint, data=params, timeout=20, allow_redirects=False
            )
            if response.status_code != 200:
                raise ValueError("HTTP failure")
            payload = json.loads(response.content.decode("utf-8-sig").strip())
            rows = payload["data"]
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("Invalid schema")
            empty = (
                payload.get("status") == "fail"
                and not rows
                and payload.get("message") == ["查無公告資料"]
            )
            if payload.get("status") != "success" and not empty:
                raise ValueError("API failure")
            if len(rows) >= 1000:
                raise ValueError("Query may be truncated; use a smaller window")
            now = self.clock()
            if now.tzinfo is None:
                raise ValueError("Clock must be timezone-aware")
        except Exception as exc:
            error = VendorUnavailableError(
                "MOPS announcement query unavailable or invalid; no dates inferred"
            )
            status = getattr(response, "status_code", None)
            content = getattr(response, "content", b"")
            error.retryable = status in {429, 500, 502, 503, 504} or isinstance(exc, (Timeout, ConnectionError))
            error.failure_receipt = {
                "dataset": dataset, "stock_id": stock_id, "query": params,
                "source_url": self.endpoint, "retrieved_at": self.clock().astimezone(UTC).isoformat(),
                "raw_rows": [], "http_status": status, "retryable": error.retryable,
                "response_sha256": hashlib.sha256(content).hexdigest(),
                "raw_response_base64": base64.b64encode(content).decode("ascii"),
                "error_kind": type(exc).__name__,
            }
            raise error from None
        return {
            "dataset": dataset,
            "stock_id": stock_id,
            "query": params,
            "source": "MOPS:ezSearch:" + ITEMS[dataset],
            "source_url": self.endpoint,
            "retrieved_at": now.astimezone(UTC).isoformat(),
            "raw_rows": rows,
            "raw_response_utf8": response.content.decode("utf-8"),
            "response_sha256": hashlib.sha256(response.content).hexdigest(),
        }


class MopsPublicationResolver:
    """Lazy query cache and audit archive for one bounded preparation operation."""

    def __init__(self, start, as_of, client=None):
        query_date(start)
        query_date(as_of)
        self.start, self.as_of = start, as_of
        self.client = client or MopsAnnouncementClient()
        self.archives = {}

    def __call__(self, dataset, row):
        if dataset not in ITEMS:
            return None
        stock_id = row["stock_id"]
        candidates = []
        period = row_period(dataset, row)
        # Bound public queries by calendar year. Multi-year requests may silently
        # fail upstream; retain each real response rather than fabricating a merge.
        for year in range(
            max(day(self.start).year, int(period[:4])),
            min(day(self.as_of).year, int(period[:4]) + 1) + 1,
        ):
            key = (dataset, stock_id, year)
            if key not in self.archives:
                try:
                    archive = self.client.query(
                        dataset,
                        stock_id,
                        max(self.start, f"{year}-01-01"),
                        min(self.as_of, f"{year}-12-31"),
                    )
                    archive["warnings"] = []
                except VendorUnavailableError as exc:
                    archive = {
                        **getattr(exc, "failure_receipt", {}),
                        "dataset": dataset,
                        "stock_id": stock_id,
                        "raw_rows": [],
                        "warnings": ["MOPS unavailable; publication dates remain unknown"],
                    }
                self.archives[key] = archive
            archive = self.archives[key]
            for raw in archive["raw_rows"]:
                try:
                    candidate = parse_announcement(dataset, stock_id, raw)
                except (ValueError, KeyError, TypeError):
                    archive["warnings"] = ["Invalid/unsupported announcement; excluded"]
                    continue
                if candidate["period"] == period:
                    candidates.append((candidate, archive))
        if not candidates:
            return None
        # Never choose the earliest announcement across corrections/resubmissions.
        chosen, archive = max(candidates, key=lambda item: item[0]["published_timestamp"])
        if (
            len(
                {
                    digest(item)
                    for item, _ in candidates
                    if item["published_timestamp"] == chosen["published_timestamp"]
                }
            )
            > 1
        ):
            return None
        return {
            **chosen,
            "dataset": dataset,
            "stock_id": stock_id,
            "source": archive["source"],
            "source_url": archive["source_url"],
            "retrieved_at": archive["retrieved_at"],
            "value_row_sha256": digest(row),
            "value_vintage": "capture_date_only; original_announcement_values_unverified",
        }

    def audit(self, stock_id):
        return [value for (_, code, _), value in sorted(self.archives.items()) if code == stock_id]


def validate_publication(dataset, row, proof, value_retrieved_at):
    """Verify persisted source/row binding and compute a conservative usable date."""
    stock_id = row["stock_id"]
    parsed = parse_announcement(dataset, stock_id, proof["raw_announcement"])
    for field in parsed:
        if proof[field] != parsed[field]:
            raise ValueError("Publication evidence modified")
    if (
        proof["dataset"] != dataset
        or proof["stock_id"] != stock_id
        or proof["value_row_sha256"] != digest(row)
        or proof["period"] != row_period(dataset, row)
        or proof["source"] != "MOPS:ezSearch:" + ITEMS[dataset]
        or proof["source_url"] != MopsAnnouncementClient.endpoint
        or proof["value_vintage"] != "capture_date_only; original_announcement_values_unverified"
    ):
        raise ValueError("Publication evidence does not bind this value row")
    captured = max(instant(proof["retrieved_at"]), instant(value_retrieved_at))
    if instant(proof["published_timestamp"]) > captured:
        raise ValueError("Announcement occurs after capture")
    return max(day(proof["published_at"]), captured.astimezone(TAIPEI).date()).isoformat()
