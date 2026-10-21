"""Mockable read-only original capture with issuer/period/content validation."""

import base64
import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

STATEMENTS = {"F26": "資產負債表", "F27": "綜合損益表", "F28": "現金流量表", "F29": "權益變動表"}


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", unescape(value)))


class HtmlData(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.rows, self._rows, self._cells = [], [], [], []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1
        if self.ignored:
            return
        if tag == "tr":
            self._rows.append([])
        elif tag in {"td", "th"}:
            self._cells.append([])

    def handle_data(self, data):
        if self.ignored:
            return
        self.parts.append(data)
        if self._cells:
            self._cells[-1].append(data)

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)
            return
        if self.ignored:
            return
        if tag in {"td", "th"} and self._cells:
            value = " ".join(self._cells.pop()).strip()
            if self._rows:
                self._rows[-1].append(value)
        elif tag == "tr" and self._rows:
            self.rows.append(self._rows.pop())


def html_data(raw):
    declared = re.search(rb'charset\s*=\s*["\x27]?([a-zA-Z0-9_-]+)', raw[:4000], re.I)
    encodings = [declared[1].decode("ascii")] if declared else []
    encodings += ["utf-8-sig", "cp950"]
    for encoding in dict.fromkeys(encodings):
        try:
            text = raw.decode(encoding)
        except (UnicodeError, LookupError):
            continue
        parser = HtmlData()
        parser.feed(text)
        return parser, encoding
    raise ValueError("Original HTML character encoding unavailable")


def validate_url(row):
    link = urlsplit(row["HYPERLINK"])
    if (
        link.scheme != "https"
        or link.netloc not in {"mopsov.twse.com.tw", "doc.twse.com.tw"}
        or link.username
        or link.fragment
    ):
        raise ValueError("Only indexed official HTTPS originals allowed")
    params = parse_qs(link.query, keep_blank_values=True)
    company = params.get("COMPANY_ID", params.get("co_id"))
    if company != [str(row["COMPANY_ID"])]:
        raise ValueError("Original URL issuer differs from index row")


def review_original(row, receipt):
    validate_url(row)
    requested, captured = urlsplit(row["HYPERLINK"]), urlsplit(receipt.get("source_url", ""))
    if (
        requested.scheme,
        requested.netloc,
        requested.path,
        parse_qs(requested.query, keep_blank_values=True),
    ) != (
        captured.scheme,
        captured.netloc,
        captured.path,
        parse_qs(captured.query, keep_blank_values=True),
    ):
        raise ValueError("Captured URL differs from indexed original")
    raw = base64.b64decode(receipt["content_base64"], validate=True)
    if hashlib.sha256(raw).hexdigest() != receipt.get("sha256"):
        raise ValueError("Original response bytes changed")
    base = {
        "index_row_sha256": hashlib.sha256(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "original_sha256": receipt["sha256"],
        "content_verified": False,
        "numeric_observations": [],
        "numeric_impact_reviewed": False,
        "historical_initial_version_proven": False,
    }
    if receipt.get("http_status") != 200:
        return {**base, "status": "source_rejected", "reason": "non_success_http"}
    parser, encoding = html_data(raw)
    text = compact(" ".join(parser.parts))
    base["encoding"] = encoding
    if any(term in text for term in ("查詢過於頻繁", "您查詢過於頻繁", "存取遭拒")):
        return {**base, "status": "source_rejected", "reason": "source_restriction"}
    issuer = compact(row.get("COMPANY_NAME", ""))
    issuer_matches = bool(
        issuer
        and re.search(
            r"本資料由(?:\([^)]*\))?(?:"
            + re.escape(str(row["COMPANY_ID"]))
            + r")?"
            + re.escape(issuer)
            + r"公司提供",
            text,
        )
    ) or bool(re.search(r"公司代號[:：]?" + re.escape(str(row["COMPANY_ID"])), text))
    if row["AN_CODE"] in {"E01", "E02"} and issuer:
        issuer_matches = issuer_matches or "公司名稱:" + issuer in text
    if not issuer_matches:
        return {
            **base,
            "status": "issuer_or_payload_unverified",
            "reason": "no_matching_issuer_content",
        }
    code, subject = row["AN_CODE"], compact(row["SUBJECT"])
    if code in {"E01", "E02"}:
        return {
            **base,
            "status": "listing_only",
            "reason": "year_listing_cannot_identify_specific_upload_or_revision",
        }
    if code == "F22":
        match = re.fullmatch(r"(\d{3})年(\d{1,2})月營業收入", subject)
        if not match or not re.search(rf"{match[1]}年0?{int(match[2])}月", text):
            return {**base, "status": "period_mismatch", "reason": "revenue_month_not_verified"}
        if "新台幣仟元" not in text or "營業收入淨額" not in text:
            return {**base, "status": "unit_or_table_unverified", "reason": "revenue_unit_required"}
        candidates = [
            cells for cells in parser.rows if len(cells) >= 2 and compact(cells[0]) == "本月"
        ]
        if len(candidates) != 1 or not re.fullmatch(r"-?[\d,]+", compact(candidates[0][1])):
            return {
                **base,
                "status": "numeric_row_unverified",
                "reason": "unique_month_amount_required",
            }
        value = int(compact(candidates[0][1]).replace(",", "")) * 1000
        base["numeric_observations"] = [
            {
                "metric": "revenue_twd",
                "period": f"{int(match[1]) + 1911}-{int(match[2]):02d}",
                "value": value,
                "unit": "TWD",
                "locator": "營業收入淨額／本月；來源單位新台幣仟元",
            }
        ]
    elif code in STATEMENTS:
        period = re.search(r"(\d{3})年第([1-4])季", subject)
        matches_period = bool(period and re.search(rf"{period[1]}年第?{period[2]}季", text))
        # Equity statements are cumulative: Q2 is headed 上半年度, not 第二季.
        if period and code == "F29":
            cumulative = {"1": "第1季", "2": "上半年度", "3": "前三季", "4": "年度"}
            matches_period = matches_period or f"民國{period[1]}年{cumulative[period[2]]}" in text
        if not period or STATEMENTS[code] not in text or not matches_period:
            return {
                **base,
                "status": "period_or_statement_mismatch",
                "reason": "statement_heading_and_period_required",
            }
        if "新台幣仟元" not in text or len(parser.rows) < 3:
            return {
                **base,
                "status": "unit_or_table_unverified",
                "reason": "full_statement_table_required",
            }
        base["statement_period_semantics"] = (
            "point_in_time_balance"
            if code == "F26"
            else "cumulative_equity_changes_not_single_quarter"
            if code == "F29"
            else "row_periods_and_versions_require_numeric_review_before_use"
        )
    elif code == "F03":
        period = re.fullmatch(r"(\d{3})年(\d{1,2})月變更登記", subject)
        registration_rows = [
            compact(" ".join(cells))
            for cells in parser.rows
            if "變更公司執照時間" in compact(" ".join(cells))
        ]
        period_matches = period and any(
            re.search(rf"變更公司執照時間{period[1]}年0?{int(period[2])}月", cells)
            for cells in registration_rows
        )
        if not period_matches or "實收股本股數(股)" not in text or "實收股本金額(元)" not in text:
            return {
                **base,
                "status": "registration_period_or_table_unverified",
                "reason": "dated_registration_and_issued_capital_table_required",
            }
    elif code == "F07":
        if "員工認股權" not in text or len(parser.rows) < 2:
            return {
                **base,
                "status": "option_payload_unverified",
                "reason": "option_table_required",
            }
    else:
        if subject not in text or ("主旨" not in text and "序號" not in text):
            return {
                **base,
                "status": "subject_or_payload_unverified",
                "reason": "full_index_subject_required",
            }
    return {
        **base,
        "status": "verified_current_original",
        "content_verified": True,
        "reason": "issuer_period_or_subject_and_payload_match_index_not_numeric_impact_certification",
    }


class MopsOriginalClient:
    def __init__(self, transport=None, clock=None):
        if transport is None:
            import requests

            transport = requests.Session()
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(UTC))

    def capture(self, row):
        validate_url(row)
        receipt = {
            "stock_id": str(row["COMPANY_ID"]),
            "source_url": row["HYPERLINK"],
            "source_kind": "mops_index_original",
            "index_row": row,
            "published_at": None,
            "publication_basis": "index_time_is_not_initial_version_proof",
        }
        try:
            response = self.transport.get(row["HYPERLINK"], timeout=25, allow_redirects=False)
        except Exception as error:
            return {
                **receipt,
                "captured_at": self.clock().isoformat(),
                "status": "unavailable",
                "public_capture_verified": False,
                "error_kind": type(error).__name__,
            }
        raw = response.content
        receipt.update(
            captured_at=self.clock().isoformat(),
            http_status=response.status_code,
            sha256=hashlib.sha256(raw).hexdigest(),
            content_base64=base64.b64encode(raw).decode(),
            public_capture_verified=response.status_code == 200,
        )
        try:
            review = review_original(row, receipt)
        except (ValueError, KeyError, TypeError):
            review = {"status": "original_parse_unavailable", "content_verified": False}
        return {
            **receipt,
            "status": review["status"],
            "original_content_review": review,
            "version": "current_capture_not_initial_publication_vintage",
        }
