"""Append-only announcement capture and offline audit, separate from forecasts."""

import base64
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from tradingagents.dataflows.taiwan.originals import MopsOriginalClient
from tradingagents.dataflows.taiwan.publications import MopsAnnouncementClient

from .announcement_coverage import _query_day, canonical, classify, identity, published
from .knowledge import completeness, context, exact


def capture(index, output_dir, max_documents=50, client=None):
    """Opt-in bounded public requests. A search/list receipt is never a filing."""
    if not 1 <= max_documents <= 100:
        raise ValueError("Capture budget must be between 1 and 100")
    query = index["query"]
    if index["source_url"] != MopsAnnouncementClient.endpoint or any(
        query.get(k) for k in ("AN", "PRO_ITEM", "SUBJECT")
    ):
        raise ValueError("Unfiltered official index required")
    raw = base64.b64decode(index["content_base64"], validate=True)
    if hashlib.sha256(raw).hexdigest() != index["sha256"]:
        raise ValueError("Index bytes changed")
    payload = json.loads(raw.decode("utf-8-sig"))
    rows = payload.get("data")
    if (
        index.get("http_status") != 200
        or payload.get("status") != "success"
        or not isinstance(rows, list)
    ):
        raise ValueError("Successful official index required")
    stock = str(query["CO_ID"])
    if any(str(row.get("COMPANY_ID")) != stock for row in rows):
        raise ValueError("Index company mismatch")
    if any(
        not _query_day(query["SDATE"])
        <= published(row).date().isoformat()
        <= _query_day(query["EDATE"])
        for row in rows
    ):
        raise ValueError("Index row outside declared query dates")
    unique = {}
    for row in rows:
        key = identity(row)
        if key in unique and unique[key] != row:
            raise ValueError("Conflicting rows share official index identity")
        unique[key] = row
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=False)
    client = client or MopsOriginalClient()
    documents, inventory, by_url, pending = {"index": index}, [], {}, []
    stopped = False
    for row in unique.values():
        if not classify(row):
            continue
        link = canonical(row["HYPERLINK"])
        if link not in by_url:
            if stopped or len(by_url) >= max_documents:
                pending.append(identity(row))
                continue
            key = f"original-{len(by_url):03d}"
            receipt = client.capture(row)
            by_url[link] = key
            documents[key] = receipt
            (target / f"{key}.json").write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            stopped = (
                receipt.get("http_status") in {403, 429}
                or receipt.get("original_content_review", {}).get("reason") == "source_restriction"
            )
        inventory.append(
            {
                "id": identity(row),
                "document_id": by_url[link],
                "original_url": row["HYPERLINK"],
                "published_at": published(row).isoformat(),
                "material": True,
            }
        )
    result = {
        "documents": documents,
        "inventory": inventory,
        "pending_event_ids": pending,
        "identical_duplicate_rows": len(rows) - len(unique),
        "source_refused": stopped,
        "forecast_modified": False,
        "limitation": "Current original capture is not proof of initial publication or numeric impact review",
    }
    (target / "capture.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def audit(spec, output_dir):
    """Input context is explicit; capture failures remain listed and cannot pass."""
    documents = spec["documents"]
    valid = {
        key: value
        for key, value in documents.items()
        if value.get("sha256") and value.get("captured_at")
    }
    supplied = spec["context"]
    as_of = exact(supplied["cutoff_at"]).astimezone(ZoneInfo("Asia/Taipei")).date().isoformat()
    checked = context(supplied, supplied["stock_id"], as_of)
    result = completeness(checked, valid, spec["inventory"], spec["coverage"])
    result["capture_failures"] = sorted(set(documents) - set(valid))
    result["forecast_modified"] = False
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=False)
    (target / "audit-input.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (target / "announcement-audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
