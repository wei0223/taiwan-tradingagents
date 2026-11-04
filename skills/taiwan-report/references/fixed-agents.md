# 固定 AI 研究與辯論執行

這是本次單次報告的必經入口。固定資料準備會建立fixed-inputs/agents，先產生planner的prompt.md、request.json與result-template.json。不要自行重寫角色Prompt，也不要使用舊runner或其他技能的辯論格式。

1. 讀agents/stages/planner/prompt.md及result-template.json，由協調者按模板完成研究規劃。保留實際回覆與execution收據（kind=coordinator、agent_id用實際當次執行識別、tool_call_id指實際執行紀錄，不能捏造工具派工）；先agent-submit接受planner，再依plan.questions查原件、候選關係/代碼及同業。規劃候選不當已證實關係。
2. 完成company-import/audit，取得明選公司版本；公開EPS按既有規則取得。使用agent-bind封存公司、EPS及財務review。公開EPS缺失可不傳，視為不可用；有review必須在此綁定，報告也使用同份。
3. agent-prompt分別產生fundamental、technical、institutional、bull_opening、bear_opening。三分析師可由協調者按各自固定指令完成；基本面須逐題回應planner問題。多空開場必須實際spawn_agent(fork_turns=none)，只送程式生成的指令檔及其列明資料，不加其他分析師/基準估值或另一方結論。不得依模型名稱自行換付費API。
4. 每份回覆另存原始JSON，用agent-submit送驗，同時傳execution收據。收據必含{agent_id,prompt_sha256,kind,tool_call_id,evidence_kind:observed_tool_receipt}；新多空/風險派工另加context_mode:fresh。這是依實際工具紀錄保存的協調者證言，不是程式能獨立證明派工。
5. 雙方開場通過後才產生bull_reply、bear_reply，使用followup_task回原Agent逐項反駁。保存原agent_id、position_before、opponent的每個finding.id，不能省略或多回。收據kind=followup_task。
6. 雙方反駁完成，才產生risk，由第三個獨立新Agent執行。最後產生decision，由協調者按模板裁決；分會計獲利/現金/估值/短期四軸，列較有力論點、關鍵分歧、證據增量及改判條件。
7. 執行agent-audit。upgrade帶--fixed-pipeline --company-snapshot <同一版本> --agent-run <agents>，extension使用request的defaults，並提供已綁定的同份public_eps及review。不要再傳--analysts或--debate-input，不另寫reader_research的AI正文。程式直接使用已驗收分析師與最終裁決，完整指令/回覆/收據進證據包。

CLI：
```text
python -m tradingagents.research agent-prompt --run <agents> --stage <stage>
python -m tradingagents.research agent-submit --run <agents> --stage <stage> --result <原始回覆.json> --execution <實際執行收據.json>
python -m tradingagents.research agent-bind --run <agents> --company-reference <公司參照.json> --public-eps <轉引輸入.json> --review-input <財務審查.json>
python -m tradingagents.research agent-audit --run <agents>
```

缺資料：每個step仍須填unavailable及原因，stage用limited/unavailable；每位分析師/裁判都保存受限回覆。工具不可用或額度不足：不得假扮Agent；kind=unavailable、agent_id=not_dispatched、tool_call_id指真實失敗/能力不足紀錄、status=unavailable、findings空、steps全部unavailable。反駁保留原開場身分；有對方主張但無法執行時，每筆responses只能uncertain、說明無法審查原因、evidence留空且changed_my_position=false，不能假裝已研究。報告保持受限，不宣稱完整獨立辯論。

接受的內容、輸入、指令與收據不可覆寫。模板改變、來源/公司/截止日或EPS變動應另建新研究，不能將新版本塞回舊run。不要重新發布預測、建立追蹤或改舊報告。
