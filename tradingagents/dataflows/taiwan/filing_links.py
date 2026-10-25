"""Verify listing → download form → PDF bytes; do not infer historical event links."""

import base64
import hashlib
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

from .filings import ENDPOINT, HOST, ListingParser
from .originals import compact, html_data, review_original, source_restricted
from .publications import roc_date


class FilingRows(ListingParser):
    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if tag == "a":
            href = dict(attrs).get("href", "")
            match = re.fullmatch(
                r'javascript:readfile2\("([AB])","([0-9]{4,6})","([A-Za-z0-9_]+\.pdf)"\);?', href
            )
            if match:
                if match[1] == "A":
                    self.calls.pop()  # Replace the base parser's two-field call.
                self.calls.append(match.groups())


def bytes_from(receipt):
    raw = base64.b64decode(receipt["content_base64"], validate=True)
    if (
        hashlib.sha256(raw).hexdigest() != receipt["sha256"]
        or receipt.get("http_status") != 200
        or source_restricted(raw)
    ):
        raise ValueError("Unverified official response bytes")
    return raw


def candidates(row, listing):
    review = review_original(row, listing)
    if row["AN_CODE"] not in {"E01", "E02"} or review["status"] != "listing_only":
        raise ValueError("Official issuer-specific listing required")
    if row["AN_CODE"] == "E02":
        period = re.fullmatch(r"(\d{3})年第([1-4])季財務報告書", row["SUBJECT"])
        kind = "A"
    else:
        period = re.fullmatch(r"(\d{3})年(\d{1,2})月公開說明書", row["SUBJECT"])
        kind = "B"
    if not period:
        raise ValueError("Unsupported indexed filing period")
    year, part = int(period[1]) + 1911, int(period[2])
    if not 1 <= part <= (4 if kind == "A" else 12):
        raise ValueError("Invalid indexed period")
    raw = bytes_from(listing)
    _, encoding = html_data(raw)
    parser = FilingRows()
    parser.feed(raw.decode(encoding))
    headers = next(
        (cells for cells, _ in parser.rows if "電子檔案" in cells and "上傳日期" in cells), None
    )
    if not headers:
        raise ValueError("Official listing header unavailable")
    result = []
    for cells, calls in parser.rows:
        for source_kind, company, filename in calls:
            if company != str(row["COMPANY_ID"]) or not filename.startswith(
                f"{year}{part:02d}_{company}_"
            ):
                continue
            if len(cells) != len(headers) or len(calls) != 1:
                raise ValueError("Ambiguous document listing row")
            fields = dict(zip(headers, cells, strict=True))
            expected_period = f"{year - 1911}年" + (
                f"第{'一二三四'[part - 1]}季" if kind == "A" else f"{part}月"
            )
            if (
                source_kind != kind
                or fields["證券代號"] != company
                or compact(fields["資料年度"]) != expected_period
            ):
                raise ValueError("Listing issuer, kind or period differs from filename")
            if fields["資料類型"] != ("財務報告書" if kind == "A" else "公開說明書"):
                raise ValueError("Listing document category mismatch")
            stamp = fields["上傳日期"].split()
            uploaded = f"{roc_date(stamp[0]).isoformat()}T{stamp[1]}+08:00"
            result.append(
                {
                    "filename": filename,
                    "kind": kind,
                    "stock_id": company,
                    "listed_uploaded_at": uploaded,
                    "listed_size": int(fields["檔案大小"].replace(",", "")),
                    "listing_fields": fields,
                    "listing_sha256": listing["sha256"],
                    "initial_publication_proven": False,
                }
            )
    if len({d["filename"] for d in result}) != len(result):
        raise ValueError("Conflicting current filename rows")
    return result


def verify_chain(row, listing, document):
    choices = [d for d in candidates(row, listing) if d["filename"] == document["filename"]]
    if len(choices) != 1:
        raise ValueError("Document is not uniquely present in indexed period listing")
    candidate = choices[0]
    response, pdf = document["download_response"], document["pdf_response"]
    expected = {
        "step": "9",
        "kind": candidate["kind"],
        "co_id": candidate["stock_id"],
        "filename": candidate["filename"],
        "colorchg": "1",
        "DEBUG": "",
    }
    if response["source_url"] != ENDPOINT or response["query"] != expected:
        raise ValueError("Download form differs from the listed file")
    raw = bytes_from(response)
    _, encoding = html_data(raw)
    links = ListingParser()
    links.feed(raw.decode(encoding))
    paths = [p for p in links.links if re.fullmatch(r"/pdf/[A-Za-z0-9_]+\.pdf", p)]
    if len(paths) != 1 or not paths[0].startswith("/pdf/" + candidate["filename"][:-4] + "_"):
        raise ValueError("Unverified official PDF download target")
    if pdf["source_url"] != HOST + paths[0]:
        raise ValueError("PDF response does not match the download target")
    content = bytes_from(pdf)
    if not content.startswith(b"%PDF-") or len(content) != candidate["listed_size"]:
        raise ValueError("PDF bytes do not match current listed format/size")
    times = [datetime.fromisoformat(d["captured_at"]) for d in (listing, response, pdf)]
    uploaded = datetime.fromisoformat(candidate["listed_uploaded_at"])
    if any(t.tzinfo is None for t in times) or not uploaded <= times[0] <= times[1] <= times[2]:
        raise ValueError("Invalid listing/download/PDF capture chronology")
    # Exact publication-event linkage cannot be established by ordering files,
    # language, equal document counts, or a caller checkbox.
    return {
        **candidate,
        "status": "current_period_document_chain_verified",
        "pdf_sha256": pdf["sha256"],
        "pdf_source_url": pdf["source_url"],
        "known_since_capture": pdf["captured_at"],
        "event_version_link_verified": False,
        "numeric_content_reviewed": False,
        "reason": "Current filename and bytes verified; indexed event lacks file/version identifier",
    }


class FilingLinkClient:
    def __init__(self, transport=None, clock=None):
        if transport is None:
            import requests

            transport = requests.Session()
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(UTC))
        self.receipts = []

    def request(self, method, url, query=None):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "doc.twse.com.tw":
            raise ValueError("Official document host required")
        kwargs = {"timeout": 35, "allow_redirects": False}
        if query is not None:
            kwargs["data"] = query
        try:
            reply = getattr(self.transport, method)(url, **kwargs)
        except Exception as error:
            self.receipts.append(
                {
                    "source_url": url,
                    "query": query,
                    "captured_at": self.clock().isoformat(),
                    "status": "unavailable",
                    "error_kind": type(error).__name__,
                }
            )
            raise ValueError("Official document transport unavailable") from None
        raw = reply.content
        receipt = {
            "source_url": url,
            "query": query,
            "http_status": reply.status_code,
            "captured_at": self.clock().isoformat(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "content_base64": base64.b64encode(raw).decode(),
        }
        self.receipts.append(receipt)
        return receipt

    def download(self, candidate):
        params = {
            "step": "9",
            "kind": candidate["kind"],
            "co_id": candidate["stock_id"],
            "filename": candidate["filename"],
            "colorchg": "1",
            "DEBUG": "",
        }
        response = self.request("post", ENDPOINT, params)
        raw = bytes_from(response)
        _, encoding = html_data(raw)
        parser = ListingParser()
        parser.feed(raw.decode(encoding))
        paths = [p for p in parser.links if re.fullmatch(r"/pdf/[A-Za-z0-9_]+\.pdf", p)]
        if len(paths) != 1 or not paths[0].startswith("/pdf/" + candidate["filename"][:-4] + "_"):
            raise ValueError("Official PDF target unavailable")
        return {
            "filename": candidate["filename"],
            "download_response": response,
            "pdf_response": self.request("get", HOST + paths[0]),
        }
