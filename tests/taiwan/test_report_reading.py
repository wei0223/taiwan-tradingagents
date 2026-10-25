"""Presentation must preserve evidence, expose gaps, and keep all three analysts readable."""

import base64
import hashlib
import json
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from tests.taiwan.test_research import snapshot
from tests.taiwan.test_review_rules import inputs
from tradingagents.research.report_view import CSS, ROLES, normalize_notes, prepare_sources
from tradingagents.research.reporting import package


def notes(stock="3017"):
    return {
        "schema_version": 2,
        "stock_id": stock,
        "as_of": "2026-10-15",
        **{
            role: {
                "position": "Synthetic judgment [F02]",
                "stronger_case": "Synthetic stronger argument",
                "disagreement": "Synthetic disagreement",
                "evidence_change": "Synthetic change, not issuer analysis",
                "paragraphs": ["Synthetic narrative [M01][T01]"],
                "conditions": ["Synthetic condition"],
            }
            for role in ROLES
        },
    }


def sample():
    source = snapshot("3017")
    source["as_of"] = "2026-10-15"
    return source


def packet(stock="3017"):
    p = inputs(stock)
    p["documents"]["q2"] = p["documents"].pop("doc")
    for key in ("earnings", "prior_earnings", "cash_ytd", "previous_cash_ytd"):
        if key in p:
            p[key]["document_ids"] = ["q2"]
    return p


class Structure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_details = False
        self.hidden_analysts = []
        self.analysts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "details":
            self.in_details = True
        if attrs.get("id", "").startswith("analyst-"):
            self.analysts.append(attrs["id"])
            if self.in_details:
                self.hidden_analysts.append(attrs["id"])

    def handle_endtag(self, tag):
        if tag == "details":
            self.in_details = False


class ReadingTests(unittest.TestCase):
    def render(self, prose=None, review_input=None):
        with tempfile.TemporaryDirectory() as directory:
            result = package(sample(), directory, analyst_prose=prose, review_input=review_input)
            root = Path(directory)
            return result, (root / "report.html").read_text(encoding="utf-8")

    def test_structured_notes_bound_to_company(self):
        with self.assertRaises(ValueError):
            normalize_notes(notes("3231"), "3017", "2026-10-15")

    def test_structured_notes_bound_to_analysis_date(self):
        with self.assertRaises(ValueError):
            normalize_notes(notes(), "3017", "2026-10-16")

    def test_missing_sections_are_not_automatically_approved(self):
        result, page = self.render({"fundamental": ["Legacy narrative"]})
        self.assertEqual(result["reading_review"]["status"], "editorial_review_required")
        self.assertIn("尚未提供具體條件", page)
        self.assertFalse(result["reading_review"]["semantic_truth_verified_by_template"])

    def test_all_three_analysts_are_open_sections(self):
        result, page = self.render(notes(), packet())
        tree = Structure()
        tree.feed(page)
        self.assertEqual(tree.analysts, ["analyst-" + role for role in ROLES])
        self.assertFalse(tree.hidden_analysts)
        self.assertEqual(result["reading_review"]["status"], "complete_structure")

    def test_unknown_reference_visible_and_not_a_fake_link(self):
        prose = notes()
        prose["technical"]["position"] = "Unknown [NOT_BOUND]"
        result, page = self.render(prose, packet())
        self.assertEqual(result["reading_review"]["unresolved_evidence"], ["NOT_BOUND"])
        self.assertIn("NOT_BOUND：未綁定來源", page)
        self.assertNotIn('href="#NOT_BOUND"', page)

    def test_source_alias_not_guessed_from_unrelated_document(self):
        result, _ = self.render(notes(), inputs("3017"))
        self.assertIn("F02", result["reading_review"]["unresolved_evidence"])

    def test_prose_is_escaped(self):
        prose = notes()
        prose["fundamental"]["paragraphs"] = ['<script>alert("x")</script>']
        result, page = self.render(prose, packet())
        self.assertEqual(result["html_audit"]["status"], "passed")
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_invalid_document_does_not_become_a_citation(self):
        p = packet()
        p["documents"]["q2"]["sha256"] = "0" * 64
        entries, refs, assets = prepare_sources(p, "3017")
        self.assertFalse(entries)
        self.assertNotIn("F02", refs)
        self.assertFalse(assets)

    def test_non_pdf_source_does_not_execute_as_html(self):
        _, _, assets = prepare_sources(packet(), "3017")
        self.assertTrue(all(name.endswith(".txt") for name in assets))

    def test_documents_and_snapshot_export_with_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            p = packet()
            raw = b"%PDF-Synthetic source bytes for archival test only"
            doc = p["documents"]["q2"]
            doc.update(
                content_base64=base64.b64encode(raw).decode(),
                sha256=hashlib.sha256(raw).hexdigest(),
            )
            original = sample()
            package(original, directory, analyst_prose=notes(), review_input=p)
            root = Path(directory)
            self.assertEqual((root / "documents" / (doc["sha256"] + ".pdf")).read_bytes(), raw)
            self.assertEqual(
                json.loads((root / "source-snapshot.json").read_text(encoding="utf-8")), original
            )
            self.assertTrue(json.loads((root / "period-conversion.json").read_text(encoding="utf-8"))["raw_values_unchanged"])

    def test_editorial_format_cannot_change_financial_axes(self):
        before, _ = self.render({"fundamental": ["Legacy"]}, packet())
        after, _ = self.render(notes(), packet())
        for key in ("review", "forecast", "dynamics", "decision", "forecast_preservation"):
            self.assertEqual(before[key], after[key])

    def test_sources_and_methods_are_separate_printable_sections(self):
        _, page = self.render(notes(), packet())
        self.assertIn('id="evidence"', page)
        self.assertIn('id="evidence-market"', page)
        self.assertIn('id="evidence-rules"', page)
        self.assertIn("原件定位", page)

    def test_table_accessibility_and_print_headers(self):
        _, page = self.render(notes(), packet())
        self.assertIn('<th scope="col">', page)
        self.assertIn("<thead>", page)
        self.assertIn('aria-label="資料表，可左右捲動"', page)
        self.assertIn("thead{display:table-header-group}", CSS)

    def test_mobile_and_print_styles_do_not_require_remote_scripts(self):
        self.assertIn("@media(max-width:640px)", CSS)
        self.assertIn("@page{size:A4", CSS)
        self.assertIn("min-width:0!important", CSS)
        _, page = self.render(notes(), packet())
        self.assertNotIn("<script", page)
        self.assertNotIn("<iframe", page)

    def test_no_claim_of_browser_visual_review(self):
        result, _ = self.render(notes(), packet())
        self.assertEqual(
            result["reading_review"]["visual_browser_review"], "not_performed_by_template"
        )

    def test_empty_narrative_does_not_pass_with_only_judgment_labels(self):
        prose = notes()
        prose["fundamental"]["paragraphs"] = []
        result, _ = self.render(prose, packet())
        self.assertIn(
            "paragraphs", result["reading_review"]["missing_editorial_fields"]["fundamental"]
        )
        self.assertEqual(result["reading_review"]["status"], "editorial_review_required")


if __name__ == "__main__":
    unittest.main()
