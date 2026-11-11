import unittest

from tradingagents.research.debate_impact import _trace


def opinion(job, stance, findings=(), responses=()):
    return {"job": job, "agent_id": job.split("_")[0], "stance": stance, "summary": job,
            "prompt_sha256": job, "status": "complete", "findings": list(findings),
            "payload": {"responses": list(responses)}}


class DebateImpactTests(unittest.TestCase):
    def test_linked_and_unlinked_stance_changes(self):
        bull = opinion("bull_opening", "neutral")
        bear = opinion("bear_opening", "bearish")
        reply = opinion("bull_reply", "bearish")
        self.assertEqual(_trace(bull, bear, reply)["change_types"], ["unverified_stance_change"])
        reply["payload"]["responses"] = [{"claim_id": "bear.claim", "assessment": "accept",
                                           "changed_my_position": True, "evidence": [{"quote": "7"}]}]
        result = _trace(bull, bear, reply)
        self.assertEqual(result["change_types"], ["stance_changed"])
        self.assertEqual(result["linked_stance_change_claim_ids"], ["bear.claim"])

    def test_confidence_requires_same_claim_and_horizon(self):
        before = {"id": "bull.old", "claim": "Same claim", "horizon": "short_term",
                  "confidence": "uncalibrated_low", "evidence": [{"quote": "1"}]}
        after = {**before, "id": "bull.new", "confidence": "uncalibrated_medium"}
        bull = opinion("bull_opening", "neutral", [before])
        bear = opinion("bear_opening", "bearish")
        reply = opinion("bull_reply", "neutral", [after])
        self.assertEqual(_trace(bull, bear, reply)["change_types"], ["confidence_changed"])
        reply["findings"][0]["horizon"] = "long_term"
        result = _trace(bull, bear, reply)
        self.assertEqual(result["change_types"], ["no_material_change"])
        self.assertEqual(result["confidence_comparison"], "unavailable")

    def test_new_citation_and_dispute_do_not_count_as_changed_stance(self):
        bull = opinion("bull_opening", "neutral")
        bear = opinion("bear_opening", "bearish")
        reply = opinion("bull_reply", "neutral", responses=[{"claim_id": "bear.claim", "assessment": "dispute",
                        "changed_my_position": False, "evidence": [{"quote": "2"}]}])
        trace = _trace(bull, bear, reply)
        self.assertEqual(trace["change_types"], ["no_material_change"])
        self.assertEqual(trace["disputed_claim_ids"], ["bear.claim"])
        self.assertEqual(trace["additional_citations"], [{"quote": "2"}])
