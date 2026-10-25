"""Turn the captured all-category announcement search into explicit review gaps."""

import base64
import copy
import hashlib
import json
import re
from datetime import UTC, datetime

from .financial_model import profile
from .review import _end_of_day
from .scenarios import timestamp


def prepare(snapshot, review_input=None, clock=None):
    archive = snapshot.get("material_event_audit")
    if not archive:
        return review_input, None
    stock, as_of = snapshot["instrument"]["stock_id"], snapshot["as_of"]
    if archive.get("content_base64"):
        from tradingagents.dataflows.taiwan.publications import MopsAnnouncementClient

        raw = base64.b64decode(archive["content_base64"], validate=True)
        if (
            hashlib.sha256(raw).hexdigest() != archive.get("sha256")
            or archive.get("source_url") != MopsAnnouncementClient.endpoint
        ):
            raise ValueError("Material search original changed")
        if archive.get("rows", []) != json.loads(raw.decode("utf-8-sig")).get("data", []):
            raise ValueError("Material rows disagree with captured original")
        if any(str(r.get("COMPANY_ID")) != stock for r in archive.get("rows", [])):
            raise ValueError("Material announcement company mismatch")
    elif archive.get("rows"):
        raise ValueError("Material rows need captured original bytes")
    now = (clock or (lambda: datetime.now(UTC)))()
    cutoff = min(now, _end_of_day(as_of))
    if (review_input or {}).get("cutoff_at"):
        cutoff = timestamp(review_input["cutoff_at"])
    mode = "latest_research" if cutoff == now else "retrospective_diagnostic"
    data = copy.deepcopy(review_input or {"stock_id": stock})
    data["cutoff_at"] = cutoff.isoformat()
    docs = data.setdefault("documents", {})
    if archive.get("content_base64") and archive.get("sha256"):
        docs["material_search"] = {
            **archive,
            "revision_id": "current-material-search-" + archive["sha256"],
            "publication_time_status": "unknown_current_revision",
        }
    from .announcement_coverage import CATEGORIES, classify

    events = []
    for i, row in enumerate(archive.get("rows", [])):
        if not classify(row) and row.get("AN_CODE") not in {"M20", "M22", "M19", "M99"}:
            continue
        subject = row["SUBJECT"]
        y, m, d = map(int, row["CDATE"].split("/"))
        period = None
        match = re.search(r"(20\d{2}|\d{3})年(?:度)?第([一二三四1-4])季", subject)
        if match:
            year = int(match[1])
            year += 1911 if year < 1911 else 0
            quarter = str("一二三四".index(match[2]) + 1) if match[2] in "一二三四" else match[2]
            period = f"{year}-Q{quarter}"
        earnings = bool(
            period and ("每股盈餘" in subject or ("財務報告" in subject and "通過" in subject))
        )
        events.append(
            {
                "id": "material-" + str(i),
                "kind": "earnings" if earnings else "material_event",
                "period": period if earnings else None,
                "subject": subject,
                "published_at": f"{y + 1911}-{m:02d}-{d:02d}T{row['CTIME']}+08:00",
                "material": True,
                "document_id": None,
                "original_url": row.get("HYPERLINK"),
                "reason": "Search discovered event; official original and financial impact require review",
            }
        )
    declared = []
    if archive.get("query", {}).get("SDATE"):
        y, m, d = map(int, archive["query"]["SDATE"].split("/"))
        declared = [
            {
                "category": key,
                "document_id": "material_search",
                "from_at": f"{y + 1911}-{m:02d}-{d:02d}T00:00:00+08:00",
                "through_at": cutoff.isoformat(),
            }
            for key in CATEGORIES
        ]
    plan = {
        "context": {"stock_id": stock, "mode": mode, "cutoff_at": cutoff.isoformat()},
        "inventory": events,
        "coverage": declared,
        "industry_kind": profile(stock)["key"],
        "operating_inputs": {},
        "forward_scenarios": None,
    }
    return data, {"stock_id": stock, "as_of": as_of, "research_plan": plan}
