"""Resumable annual official filings: preserve listing and each document failure separately."""

import hashlib
import json
import re
from pathlib import Path

from tradingagents.research.bundle import fingerprint

from .filings import ENDPOINT, HOST, ListingParser, MopsFilingArchive, response_archive
from .publications import roc_date


def capture_year(root, stock_id, year, client=None):
    if not re.fullmatch(r"\d{4,6}", stock_id) or not 2012 <= year <= 2100:
        raise ValueError("Invalid annual filing request")
    client = client or MopsFilingArchive(root)
    folder = Path(root) / stock_id
    folder.mkdir(parents=True, exist_ok=True)
    query = {
        "step": "1",
        "colorchg": "1",
        "co_id": stock_id,
        "year": str(year - 1911),
        "seamon": "",
        "mtype": "A",
    }
    reply = client.request("get", ENDPOINT, params=query, timeout=25, allow_redirects=False)
    listing = response_archive(reply, ENDPOINT, query, client.timestamp())
    parser = ListingParser()
    parser.feed(reply.content.decode("big5", errors="strict"))
    headers = next(
        (cells for cells, _ in parser.rows if "上傳日期" in cells and "電子檔案" in cells), None
    )
    if not headers or "財務報告更(補)正" not in headers:
        raise ValueError("Official listing schema unavailable")
    manifest = {
        "stock_id": stock_id,
        "year": year,
        "listing": listing,
        "documents": [],
        "limitations": [
            "Current listing cannot reconstruct removed historical revisions",
            "Chinese consolidated/annual standalone originals plus all listed corrections; English variants inventoried",
        ],
    }
    for cells, calls in parser.rows:
        if not calls:
            continue
        if len(calls) != 1 or len(cells) != len(headers):
            raise ValueError("Ambiguous filing row")
        code, filename = calls[0]
        if code != stock_id or not re.fullmatch(
            rf"{year}0[1-4]_{stock_id}_[A-Za-z0-9]+\.pdf", filename
        ):
            raise ValueError("Filing identity mismatch")
        fields = dict(zip(headers, cells, strict=True))
        stamp = fields["上傳日期"].split()
        published = roc_date(stamp[0]).isoformat() + "T" + stamp[1] + "+08:00"
        doc = {"filename": filename, "listing_fields": fields, "published_at": published}
        if not filename.endswith(("_AI1.pdf", "_AI3.pdf")) and fields["財務報告更(補)正"] == "無":
            doc["status"] = "listed_other_language_not_downloaded"
            manifest["documents"].append(doc)
            continue
        try:
            params = {
                "step": "9",
                "kind": "A",
                "co_id": code,
                "filename": filename,
                "colorchg": "1",
                "DEBUG": "",
            }
            reply = client.request("post", ENDPOINT, data=params, timeout=25, allow_redirects=False)
            doc["download_response"] = response_archive(reply, ENDPOINT, params, client.timestamp())
            links = ListingParser()
            links.feed(reply.content.decode("big5", errors="strict"))
            urls = [u for u in links.links if re.fullmatch(r"/pdf/[A-Za-z0-9_]+\.pdf", u)]
            if len(urls) != 1 or not urls[0].startswith("/pdf/" + filename[:-4] + "_"):
                raise ValueError("Official download unavailable")
            doc["source_url"] = HOST + urls[0]
            pdf = client.request("get", doc["source_url"], timeout=35, allow_redirects=False)
            if (
                pdf.status_code != 200
                or not pdf.content.startswith(b"%PDF-")
                or len(pdf.content) > 25_000_000
            ):
                raise ValueError("Official PDF unavailable")
            sha = hashlib.sha256(pdf.content).hexdigest()
            target = folder / (sha + ".pdf")
            if target.exists():
                if target.read_bytes() != pdf.content:
                    raise ValueError("Stored PDF mismatch")
            else:
                with target.open("xb") as f:
                    f.write(pdf.content)
            doc.update(status="captured", pdf_sha256=sha, retrieved_at=client.timestamp())
        except Exception as exc:
            doc.update(status="unavailable", error=type(exc).__name__)
        manifest["documents"].append(doc)
    manifest["captured_at"] = client.timestamp()
    manifest["sha256"] = fingerprint(manifest)
    path = folder / (manifest["sha256"] + ".history.json")
    with path.open("x", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return path
