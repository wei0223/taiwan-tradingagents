"""Explicit synthetic original disclosures for existing non-accounting tests."""

import base64
import calendar
import hashlib
import json
from datetime import date


def synthetic_review(stock, financial=(), cash=()):
    selected = [(r, False) for r in financial] + [(r, True) for r in cash]
    text = json.dumps({"synthetic_direct_single_quarter_diluted_disclosures": selected}, sort_keys=True).encode()
    doc = {"stock_id": stock, "revision_id": "synthetic-v1", "source_url": "https://example.org/synthetic",
           "sha256": hashlib.sha256(text).hexdigest(), "content_base64": base64.b64encode(text).decode(),
           "captured_at": "2024-05-20T12:00:00+00:00", "published_at": "2024-05-10"}
    observations = []
    for index, (row, is_cash) in enumerate(selected):
        field = "DilutedEPS" if row["type"] == "EPS" else row["type"]
        end = date.fromisoformat(row["date"][:10])
        month = ((end.month - 1) // 3 + 1) * 3
        period_end = date(end.year, month, calendar.monthrange(end.year, month)[1]).isoformat()
        observations.append({"id": f"synthetic-{index}", "stock_id": stock, "field": field,
                             "value": row["value"], "period_start": date(end.year, month - 2, 1).isoformat(),
                             "period_end": period_end, "basis": "single_quarter", "scope": "consolidated",
                             "unit": "TWD/share" if field == "DilutedEPS" else "TWD",
                             "version_set": "synthetic-v1", "document_ids": ["synthetic"],
                             "statement_type": "cash_flow" if is_cash else "income",
                             "reviewed_by": "synthetic disclosure fixture", "values_reviewed": True,
                             "locator": f"synthetic row {index}", "eps_kind": "diluted" if field == "DilutedEPS" else None})
    return {"stock_id": stock, "documents": {"synthetic": doc}, "observations": observations}
