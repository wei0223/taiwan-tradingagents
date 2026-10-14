"""Archive official filing bytes as captured, retaining successive corrections without overwrite."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from tradingagents.dataflows.errors import VendorUnavailableError

from .publications import TAIPEI, instant, roc_date

HOST = "https://doc.twse.com.tw"
ENDPOINT = HOST + "/server-java/t57sb01"


class ListingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.cells, self.cell, self.calls, self.links = [], [], None, [], []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cells, self.calls = [], []
        if tag in {"td", "th"}:
            self.cell = []
        if tag == "a":
            href = dict(attrs).get("href", "")
            self.links.append(href)
            match = re.fullmatch(r'''javascript:readfile2\("A","([0-9]{4,6})","([A-Za-z0-9_]+\.pdf)"\);?''', href)
            if match:
                self.calls.append(match.groups())

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.cells.append(" ".join("".join(self.cell).split()))
            self.cell = None
        if tag == "tr" and self.cells:
            self.rows.append((self.cells, self.calls))


def response_archive(response, url, query, captured):
    if response.status_code != 200:
        raise ValueError("Official filing source unavailable")
    raw = response.content
    if len(raw) > 25_000_000:
        raise ValueError("Filing response exceeds archive limit")
    return {"source_url": url, "query": query, "retrieved_at": captured,
            "raw_response_base64": base64.b64encode(raw).decode("ascii"),
            "response_sha256": hashlib.sha256(raw).hexdigest()}


class MopsFilingArchive:
    """The listing exposes current documents, not all past revisions. Start collecting now."""

    def __init__(self, root, transport=None, clock=None):
        if transport is None:
            import requests

            transport = requests.Session()
        self.transport, self.root = transport, Path(root)
        self.clock = clock or (lambda: datetime.now(UTC))

    def request(self, method, url, **kwargs):
        try:
            return getattr(self.transport, method)(url, **kwargs)
        except Exception:
            raise VendorUnavailableError("Official filing request failed; credentials and server text omitted") from None

    def timestamp(self):
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("Filing clock requires timezone")
        return now.astimezone(UTC).isoformat()

    def capture(self, stock_id, year, as_of):
        if not re.fullmatch(r"[0-9]{4,6}", stock_id) or isinstance(year, bool) or not 2012 <= year <= 2100:
            raise ValueError("Invalid filing request")
        now = self.clock()
        if now.tzinfo is None or instant(as_of + "T23:59:59+08:00").date() > datetime.now(TAIPEI).date():
            raise ValueError("Invalid filing capture date")
        stamp = now.astimezone(UTC).isoformat()
        query = {"step": "1", "colorchg": "1", "co_id": stock_id, "year": str(year - 1911),
                 "seamon": "", "mtype": "A"}
        response = self.request("get", ENDPOINT, params=query, timeout=20, allow_redirects=False)
        listing = response_archive(response, ENDPOINT, query, self.timestamp())
        parser = ListingParser()
        parser.feed(response.content.decode("big5", errors="strict"))
        headers = next((cells for cells, _ in parser.rows if "上傳日期" in cells and "電子檔案" in cells), None)
        if not headers or "財務報告更(補)正" not in headers:
            raise ValueError("Unrecognized official filing listing")
        documents = []
        folder = self.root / stock_id
        folder.mkdir(parents=True, exist_ok=True)
        for cells, calls in parser.rows:
            if not calls:
                continue
            if len(cells) != len(headers) or len(calls) != 1:
                raise ValueError("Ambiguous filing row")
            fields = dict(zip(headers, cells, strict=True))
            code, filename = calls[0]
            if code != stock_id or not re.fullmatch(rf"{year}0[1-4]_{stock_id}_[A-Za-z0-9]+\.pdf", filename):
                raise ValueError("Unexpected filing company/period")
            uploaded = fields["上傳日期"].split()
            if len(uploaded) != 2:
                raise ValueError("Unknown filing upload time")
            published = instant(f"{roc_date(uploaded[0]).isoformat()}T{uploaded[1]}+08:00")
            # Neither old upload dates nor mutable filenames authorize backdated captures.
            if max(now.astimezone(TAIPEI).date().isoformat(), published.date().isoformat()) > as_of:
                continue
            params = {"step": "9", "kind": "A", "co_id": stock_id, "filename": filename,
                      "colorchg": "1", "DEBUG": ""}
            response = self.request("post", ENDPOINT, data=params, timeout=20, allow_redirects=False)
            download = response_archive(response, ENDPOINT, params, self.timestamp())
            links = ListingParser()
            links.feed(response.content.decode("big5", errors="strict"))
            pdf_links = [link for link in links.links if re.fullmatch(r"/pdf/[A-Za-z0-9_]+\.pdf", link)]
            if len(pdf_links) != 1 or not pdf_links[0].startswith("/pdf/" + filename[:-4] + "_"):
                raise ValueError("Unrecognized official PDF link")
            url = HOST + pdf_links[0]
            pdf = self.request("get", url, timeout=30, allow_redirects=False)
            if pdf.status_code != 200 or not pdf.content.startswith(b"%PDF-") or len(pdf.content) > 25_000_000:
                raise ValueError("Invalid official PDF")
            sha = hashlib.sha256(pdf.content).hexdigest()
            path = folder / (sha + ".pdf")
            if path.exists():
                if path.read_bytes() != pdf.content:
                    raise ValueError("Corrupt existing filing")
            else:
                with path.open("xb") as handle:
                    handle.write(pdf.content)
            retrieved = self.timestamp()
            if instant(retrieved).astimezone(TAIPEI).date().isoformat() > as_of:
                continue
            documents.append({"filename": filename, "uploaded_at": published.isoformat(),
                              "retrieved_at": retrieved, "source_url": url, "pdf_sha256": sha,
                              "listing_fields": fields, "download_response": download})
        stamp = self.timestamp()
        receipt = {"stock_id": stock_id, "year": year, "captured_at": stamp,
                   "listing": listing, "documents": documents,
                   "limitations": ["Current official listing; absent historical revisions cannot be reconstructed",
                                   "PDF bytes archived, not numerically parsed or passed as model instructions"]}
        from tradingagents.research.bundle import canonical, fingerprint

        receipt["receipt_sha256"] = fingerprint(receipt)
        path = folder / (receipt["receipt_sha256"] + ".json")
        if not path.exists():
            with path.open("x", encoding="utf-8") as handle:
                handle.write(canonical(receipt) + "\n")
        self.verify(path)
        return path

    @staticmethod
    def verify(path):
        from tradingagents.research.bundle import fingerprint

        path = Path(path)
        receipt = json.loads(path.read_text(encoding="utf-8"))
        unsigned = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
        if fingerprint(unsigned) != receipt["receipt_sha256"] or path.stem != receipt["receipt_sha256"]:
            raise ValueError("Filing receipt changed")
        captured = instant(receipt["captured_at"])
        for archive in [receipt["listing"], *[d["download_response"] for d in receipt["documents"]]]:
            if archive["source_url"] != ENDPOINT or hashlib.sha256(base64.b64decode(
                    archive["raw_response_base64"], validate=True)).hexdigest() != archive["response_sha256"]:
                raise ValueError("Filing response changed")
            if instant(archive["retrieved_at"]) > captured:
                raise ValueError("Filing retrieval after receipt")
        for document in receipt["documents"]:
            sha = document["pdf_sha256"]
            if not re.fullmatch(r"[a-f0-9]{64}", sha):
                raise ValueError("Invalid filing content hash")
            url = urlsplit(document["source_url"])
            if url.scheme != "https" or url.netloc != "doc.twse.com.tw" or not url.path.startswith("/pdf/"):
                raise ValueError("Unexpected filing source")
            if hashlib.sha256((path.parent / (sha + ".pdf")).read_bytes()).hexdigest() != sha:
                raise ValueError("Filing PDF changed")
            if instant(document["uploaded_at"]) > captured or instant(document["retrieved_at"]) > captured:
                raise ValueError("Filing time after receipt")
        return receipt
