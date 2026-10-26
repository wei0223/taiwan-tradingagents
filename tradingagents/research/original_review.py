"""Exact original quotations, numeric transcriptions and conservative alias joins."""

import base64
import copy
import hashlib
import re
from decimal import Decimal, InvalidOperation

from tradingagents.dataflows.taiwan.originals import compact, html_data, review_original
from tradingagents.dataflows.taiwan.publications import roc_date

from .announcement_coverage import canonical
from .bundle import fingerprint
from .scenarios import timestamp


def bind_aliases(work, index_rows):
    """Company + exact publication second + full decoded subject; never time alone."""
    rows, evidence = {}, []
    for item in work["items"]:
        item = copy.deepcopy(item)
        candidates = []
        if item.get("subject") and item.get("published_at"):
            for raw in index_rows:
                published = roc_date(raw["CDATE"]).isoformat()+"T"+raw["CTIME"]+"+08:00"
                if (str(raw["COMPANY_ID"]) == item["company"]
                        and timestamp(published) == timestamp(item["published_at"])
                        and compact(raw["SUBJECT"]) == compact(item["subject"])):
                    candidates.append(raw)
        if len(candidates) == 1:
            raw = candidates[0]
            url = canonical(raw["HYPERLINK"])
            # A supplied URL disagreement is a conflict, not a reason to overwrite it.
            if item.get("source_url") and canonical(item["source_url"]) != url:
                key = item["event_id"]
            else:
                key = fingerprint([item["company"], item["published_at"], compact(item["subject"]), url, raw["AN_CODE"]])
                evidence.append({"old_event_id": item["event_id"], "bound_identity": key,
                                 "index_row_sha256": fingerprint(raw), "rule": "company_exact_second_full_subject_unique"})
                item.update(source_url=url, announcement_code=raw["AN_CODE"])
        else:
            key = item["event_id"]
        if key in rows:
            rows[key]["aliases"].extend(x for x in item["aliases"] if x not in rows[key]["aliases"])
            rows[key]["bound_original_event_ids"] = sorted(set(rows[key]["bound_original_event_ids"])
                | set(item.get("bound_original_event_ids", [])) | {item["event_id"]})
        else:
            item["bound_original_event_ids"] = sorted(set(item.get("bound_original_event_ids", [])) | {item["event_id"]})
            rows[key] = item
    counts = {**work["counts"], "deduplicated_items": len(rows)}
    for key, kind in (("announcements", "announcement"), ("scope_gaps", "scope_gap"),
                      ("historical_gap_groups", "historical_gap")):
        counts[key] = sum(r["record_kind"] == kind for r in rows.values())
    return {**work, "items": list(rows.values()), "counts": counts, "identity_crosswalk": evidence,
            "input_count": len(work["items"]), "canonical_count": len(rows),
            "unidentified_items_not_merged": True}


def audit(work, index_rows, documents, transcriptions, cutoff):
    joined = bind_aliases(work, index_rows)
    reviewed, rejected = [], []
    seen = set()
    canonical_seen = set()
    for entry in transcriptions:
        key = entry["event_id"]
        if key in seen:
            raise ValueError("Duplicate numeric review for event")
        seen.add(key)
        item = next((r for r in joined["items"] if key in r["bound_original_event_ids"]), None)
        if item and item["event_id"] in canonical_seen:
            raise ValueError("Distinct aliases cannot create duplicate numeric reviews")
        if item:
            canonical_seen.add(item["event_id"])
        try:
            if item is None or not entry.get("reviewed_by") or not entry.get("impact_conclusion"):
                raise ValueError("Named original and impact review required")
            if timestamp(entry["reviewed_at"]) > timestamp(cutoff):
                raise ValueError("Review after evaluation boundary")
            doc = documents[entry["source_id"]]
            if doc.get("stock_id") != item["company"] or doc["sha256"] != entry["source_sha256"]:
                raise ValueError("Numeric review source company/hash differs")
            if timestamp(doc["captured_at"]) > timestamp(cutoff):
                raise ValueError("Original captured after evaluation boundary")
            raw = base64.b64decode(doc["content_base64"], validate=True)
            if hashlib.sha256(raw).hexdigest() != doc["sha256"]:
                raise ValueError("Numeric review original bytes changed")
            matching = [r for r in index_rows if canonical(r["HYPERLINK"]) == canonical(item["source_url"])
                        and str(r["COMPANY_ID"]) == item["company"]]
            if len(matching) != 1 or not review_original(matching[0], doc)["content_verified"]:
                raise ValueError("Original issuer/subject/index not uniquely verified")
            parser, _ = html_data(raw)
            text = compact(" ".join(parser.parts))
            if not entry.get("observations"):
                raise ValueError("Original review needs explicit quoted observations")
            for obs in entry["observations"]:
                quote = compact(obs["source_quote"])
                if not quote or quote not in text or not obs.get("metric") or not obs.get("qualification"):
                    raise ValueError("Observation quotation or semantic qualification missing")
                if obs.get("number_text") is not None:
                    token = obs["number_text"]
                    if compact(token) not in quote or not re.fullmatch(r"-?\d[\d,]*(?:\.\d+)?", token):
                        raise ValueError("Numeric token absent from quoted original")
                    scale, value = Decimal(str(obs["scale"])), Decimal(str(obs["value"]))
                    if (not obs.get("unit") or not scale.is_finite() or not value.is_finite()
                            or scale <= 0 or Decimal(token.replace(",", ""))*scale != value):
                        raise ValueError("Original unit scaling differs from transcription")
                elif obs.get("value") is not None:
                    raise ValueError("Numeric values require an exact source token")
            remaining = list(entry.get("remaining_requirements", []))
            if any(o.get("unit") == "shares" for o in entry["observations"]):
                remaining.append("new_quarter_EPS_note_and_effective_weighting_not_proven_by_issued_shares")
            if any(o.get("qualification") in {"budget_not_cash_paid", "guarantee_not_debt_or_expense"}
                   for o in entry["observations"]):
                remaining.append("cash_timing_recognition_and_duplicate_commitment_review_required")
            record = {**entry, "remaining_requirements": sorted(set(remaining)),
                      "company": item["company"], "aliases_reviewed": item["bound_original_event_ids"],
                      "original_bytes_verified": True, "numeric_transcription_verified": True,
                      "initial_version_proven": False, "model_changed": False,
                      "resolution_state": "NUMBERS_VERIFIED_SCOPE_PENDING" if remaining
                      else "RESOLVED_SELECTED_CURRENT_FACTS", "review_scope": "quoted_current_facts_not_blanket_valuation_clearance"}
            record["review_sha256"] = fingerprint(record)
            reviewed.append(record)
            item["numeric_review"] = record
            item["resolution_state"] = record["resolution_state"]
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            rejected.append({"event_id": key, "reason": str(exc)})
    return {"stock_id": work["stock_id"], "queue": joined, "reviewed": reviewed,
            "rejected": rejected, "numeric_reviews_completed": len(reviewed),
            "model_or_forecast_changed": False, "blanket_P0_waivers": []}
