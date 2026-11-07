"""Bounded company-original intake before semantic review and workflow binding."""

import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

from tradingagents.dataflows.taiwan.filing_links import candidates
from tradingagents.dataflows.taiwan.filings import ENDPOINT
from tradingagents.dataflows.taiwan.official_sources import OfficialSources
from tradingagents.dataflows.taiwan.source_store import SourceStore

from .company_database import latest, plan, read, validate
from .review import _end_of_day
from .scenarios import timestamp

REVIEW_INSTRUCTIONS = """依 review-queue.json 及公司研究計畫逐份查閱原件。不能把下載成功當數值已核對。
先查看最新合併季報的損益表、EPS附註、現金流、分部與投資收益，再看年報與法說。
每筆抄錄沿用 company-input-template.json 的 facts 契約，保留頁碼、原句、表頭、
單位、期間、合併範圍、版本及具名審查人。EPS與加權股數只能直接抄錄，不差分。
區分營益、稅前、歸母、非控制權益及基本/稀釋。現金流須核對同版橋接再轉單季。
缺原件先按 source-search.json 逐題查公司官方IR及同業原件，記錄URL與捕獲時間，
新增原件另存版本再匯入。公開FactSet轉引另走public-eps-input，不填入公司實績。
只有完成查詢仍無法取得的項目列 unavailable，未審原件列 pending。
company-import及company-audit通過後才能workflow-bind。部分缺件仍可交付受限報告。
"""


def collect(snapshot, output, archive_dir, *, saved_packets=(), company_database=None, live=False, transport=None, clock=None):
    if transport is None and not live:
        raise ValueError("Live source capture must be explicit")
    stock = snapshot["instrument"]["stock_id"]
    as_of = snapshot["as_of"]
    spec = plan(stock, as_of, snapshot["instrument"]["exchange"])
    now = clock or (lambda: datetime.now(UTC))
    if now().tzinfo is None or timestamp(now().isoformat()) > _end_of_day(as_of):
        raise ValueError("Start a new research batch before capturing after cutoff")
    root = Path(output)
    if root.exists():
        raise ValueError("Use a new original intake directory")
    # Validate saved packets before making requests or writing outputs.
    packets = list(saved_packets)
    if company_database:
        selected = latest(company_database, stock, as_of)
        if selected["status"] != "not_found":
            previous = read(selected)
            previous_store = SourceStore(selected["source_store"])
            packets.append({"stock_id": stock, "as_of": as_of,
                            "sources": {k: previous_store.receipt(v) for k, v in previous["receipt_ids"].items()},
                            "facts": [], "assumptions": [], "gaps": []})
    for packet in packets:
        if packet["stock_id"] != stock:
            raise ValueError("Saved originals belong to another company")
        validate({**packet, "as_of": as_of, "facts": [], "assumptions": []})
    root.mkdir(parents=True)
    store = SourceStore(archive_dir)
    client = OfficialSources(store, transport=transport, clock=now, max_requests=25, max_pdfs=8)
    sources, queue = {}, []
    for packet in packets:
        for source in packet["sources"].values():
            key = source["sha256"]
            if key not in sources:
                sources[key] = source
                queue.append({"source_id": key, "category": source["category"],
                              "status": "pending_numeric_review", "origin": "saved_original"})
    # Latest completed quarter first, consolidated AI1. Earlier versions stay distinct.
    for period in reversed(spec["financial_quarters"]):
        year, quarter = int(period[:4]), int(period[-1])
        url = ENDPOINT + "?" + urlencode({"step": "1", "colorchg": "1", "co_id": stock,
                                         "year": str(year - 1911), "seamon": "", "mtype": "A"})
        row = {"COMPANY_ID": stock, "COMPANY_NAME": snapshot["instrument"].get("name", stock),
               "AN_CODE": "E02", "SUBJECT": f"{year - 1911}年第{quarter}季財務報告書",
               "HYPERLINK": url}
        listing, listing_id = client.request("get", url)
        try:
            choices = candidates(row, listing)
            chosen = [c for c in choices if c["filename"] == f"{year}0{quarter}_{stock}_AI1.pdf"]
            if len(chosen) != 1:
                raise ValueError("Consolidated edition not uniquely listed")
            proof, receipts = client.document(row, listing, chosen[0])
            pdf = store.receipt(receipts[-1])
            key = pdf["sha256"]
            source = {"stock_id": stock, "category": "financial_statement",
                      "revision_id": key, "source_url": proof["pdf_source_url"],
                      "published_at": None, "captured_at": pdf["captured_at"],
                      "sha256": key, "content_base64": pdf["content_base64"]}
            # Preserve capture chronology and hash checks before handing to a reviewer.
            validate({"stock_id": stock, "as_of": as_of, "sources": {key: source},
                    "facts": [], "assumptions": [], "gaps": []})
            sources.setdefault(key, source)
            queue.append({"source_id": key, "period": period, "category": "financial_statement",
                          "status": "pending_numeric_review", "receipt_ids": [listing_id, *receipts],
                          "checks": ["income", "eps_note", "cashflow", "segments", "noncontrolling_interests"],
                          "initial_publication_proven": False})
        except (ValueError, KeyError, TypeError, UnicodeError) as error:
            queue.append({"period": period, "category": "financial_statement",
                          "status": "unavailable_after_query", "reason": str(error),
                          "receipt_ids": [listing_id]})
    packet = {"stock_id": stock, "as_of": as_of, "exchange": spec["exchange"],
              "sources": sources, "facts": [], "assumptions": [],
              "gaps": [{"category": c, "status": "pending", "reason": "Complete original discovery and semantic review"}
                       for c in spec["source_categories"]]}
    search = {"stock_id": stock, "categories": [
        {"category": c, "query": f'{stock} {snapshot["instrument"].get("name", "")} '
         + {"annual_report": "年報 官方", "investor_presentation": "法說 投資人關係 官方",
            "capital_event": "股本 募資 EPS附註 官方", "correction_index": "財務報告 更補正 官方",
            "monthly_revenue": "月營收 官方"}.get(c, "合併 財務報告 EPS附註 官方")}
        for c in spec["source_categories"]],
        "public_eps_query": f"{stock} FactSet EPS 中位數 site:news.cnyes.com/news/id",
        "verification": "Confirm issuer identity and official IR link before capture; never infer publication time"}
    for name, value in [("company-input-template.json", packet), ("review-queue.json", queue),
                        ("source-search.json", search), ("company-plan.json", spec)]:
        (root / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    (root / "review-instructions.md").write_text(REVIEW_INSTRUCTIONS, encoding="utf8")
    for key, source in sources.items():
        raw = base64.b64decode(source["content_base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != key:
            raise ValueError("Original changed")
        (root / (key + (".pdf" if raw.startswith(b"%PDF") else ".html"))).write_bytes(raw)
    return {"status": "originals_require_review", "stock_id": stock, "sources": len(sources),
            "requests": client.requests, "review_queue": str(root / "review-queue.json"),
            "facts_approved": 0}



