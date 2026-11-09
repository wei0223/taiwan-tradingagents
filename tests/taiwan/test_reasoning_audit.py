"""Human-authored reasoning cases and workflow binding regression tests."""

import unittest

from tradingagents.research.reasoning_audit import review


def fixture():
    finding = {
        "id": "fundamental_bull.eps", "claim": "獲利年增，因此股價下季必漲。",
        "horizon": "short_term", "evidence": [{"input_id": "company", "pointer": "/facts/0/normalized_value", "quote": "12"}],
        "counterargument": "市場預期與估值仍需另查。", "revision_condition": "比較可驗證的定價及報酬資料。",
        "confidence": "uncalibrated_low",
    }
    return {
        "stock_id": "1216", "as_of": "2026-11-07", "workflow": {"core": {"faces": [{"claim_id": finding["id"]}]}},
        "results": {
            "fundamental_bull": {"agent_id": "bull", "job": "fundamental_bull", "prompt_sha256": "a" * 64, "findings": [finding]},
            "decision": {"agent_id": "decision", "job": "decision", "prompt_sha256": "b" * 64, "findings": [],
                         "conditions": ["查明來源"], "payload": {"key_disagreement": "市場資料缺漏", "rulings": []}},
        },
    }


class ReasoningAuditTests(unittest.TestCase):
    def setUp(self):
        self.assembled = fixture()
        self.key = "fundamental_bull.eps"
        _, validation = review(self.assembled)
        self.packet = {
            "stock_id": "1216", "as_of": "2026-11-07", "assessments": {
                self.key: {"claim_sha256": validation["claims"][self.key]["claim_sha256"], "reviewer": "independent-reviewer",
                           "status": "challenged", "inference": "獲利資料只支持所列期間的公司實績。",
                           "reason": "缺乏估值、市場預期及價格傳導證據，無法由獲利年增推出股價必漲。"}
            },
        }

    def test_human_label_preserves_challenge_and_routes_to_author(self):
        audit, validation = review(self.assembled, self.packet)
        row = validation["claims"][self.key]
        self.assertEqual((audit["status"], row["status"], row["next_action"], row["return_to"]),
                         ("limited", "challenged", "retry_required", "bull"))
        self.assertFalse(row["confirmed_core"])

    def test_unreviewed_claim_with_valid_citation_is_not_confirmed(self):
        audit, validation = review(self.assembled)
        self.assertEqual(audit["status"], "limited")
        self.assertFalse(validation["claims"][self.key]["confirmed_core"])

    def test_stale_and_self_reviews_are_rejected(self):
        row = self.packet["assessments"][self.key]
        row["reviewer"] = " Bull "
        with self.assertRaisesRegex(ValueError, "author"):
            review(self.assembled, self.packet)
        row["reviewer"] = "independent-reviewer"
        self.assembled["results"]["fundamental_bull"]["findings"][0]["claim"] = "New claim"
        with self.assertRaisesRegex(ValueError, "bound"):
            review(self.assembled, self.packet)

    def test_supported_review_confirms_only_bound_core(self):
        finding = self.assembled["results"]["fundamental_bull"]["findings"][0]
        finding["claim"] = "已核資料列示單期獲利為 12。"
        finding["horizon"] = "unspecified"
        _, intake = review(self.assembled)
        self.packet["assessments"][self.key].update(
            claim_sha256=intake["claims"][self.key]["claim_sha256"], status="supported",
            inference="來源欄位直接支持期間內的實績。", reason="限定於原件欄位及期間。")
        audit, validation = review(self.assembled, self.packet)
        self.assertEqual(audit["status"], "passed")
        self.assertTrue(validation["claims"][self.key]["confirmed_core"])

    def test_mislabeled_numeric_evidence_is_rejected(self):
        self.packet["assessments"][self.key]["numeric_checks"] = [
            {"operation": "value", "evidence_indices": [0], "expected": "21", "places": 0}
        ]
        with self.assertRaisesRegex(ValueError, "differs from archived evidence"):
            review(self.assembled, self.packet)
