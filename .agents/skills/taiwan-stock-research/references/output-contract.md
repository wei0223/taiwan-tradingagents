# Stage JSON contract

Each pending file is a UTF-8 JSON object with exactly these fields:

Use state.bundle_sha256 verbatim. The Python status command verifies canonical JSON hashes,
which omit file whitespace/newlines; raw file SHA256 is not the contract.

```json
{
  "stage": "bull_opening",
  "bundle_sha256": "COPY_FROM_STATE",
  "agent_id": "ACTUAL_REGISTERED_AGENT_ID",
  "summary": "繁體中文質性摘要",
  "claims": [
    {
      "id": "bull_c1",
      "text": "價格動能支持偏多觀點，但未證明基本面改善",
      "evidence_ids": ["E0001"],
      "invalidated_by": "若後續核實的價格趨勢反轉，則此論點失效"
    }
  ],
  "responses": [],
  "verdict": "not_applicable",
  "limitations": ["基本面資料不足，無法形成完整投資判斷"]
}
```

The example is a format, not real evidence. Select IDs only from the supplied bundle.
No numeric literals or quantities spelled in words in summary/text/reason/invalidated_by/limitations.
IDs, hashes and stage fields may contain digits. The renderer inserts exact figures and dates.

- stages: bull_opening, bear_opening, bull_reply, bear_reply, risk, decision.
- opening claims: up to three; IDs bull_c1..bull_c3 or bear_c1..bear_c3. At least one
  when critical data is complete; zero is allowed for an insufficient-data bundle.
- every claim has exactly id, text, evidence_ids, invalidated_by. Refer to one to eight
  distinct available evidence IDs. Prose fields are non-empty and at most 1800 characters.
- opening responses are empty; all later claims are empty (no new claims mid-debate).
- response object: exactly claim_id, assessment, reason, evidence_ids. assessment is
  accept/dispute/uncertain. Replies cover every opposing opening claim exactly once;
  risk and decision cover every opening claim from both sides exactly once.
- openings and replies verdict: not_applicable. risk/decision verdict:
  supports_bull/supports_bear/mixed/insufficient_data. This is a research stance, not an order.
- requires_insufficient_data=true mandates insufficient_data; decision must honor risk's veto.
- decision agent_id is the literal coordinator. Other stages identify their registered subagent.
- limitations: up to eight non-empty qualitative strings. Explain unknown publication dates,
  unadjusted prices and observed-only institutional streaks where applicable.

On import failure, inspect your pending result and contract; make at most one correction.
Never weaken the validator or edit accepted outputs to make a result pass.
