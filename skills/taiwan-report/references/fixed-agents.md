# 正式八階段研究執行

新request的protocol為taiwan-eight-stage-v2。先讀儲存庫 docs/taiwan/eight-stage-workflow.md；所有輸出由workflow-prompt生成，不自行改schema或角色Prompt。若恢復的fixed-state明示fixed-research-agents-v1，改讀本目錄fixed-agents-v1.md，不就地改版。

1. 資料：按agents/jobs/planner/prompt.md及contract.json完成公司研究計畫，以workflow-submit驗收。按規劃與固定SOP查原件、核期間/原句，company-import/audit後workflow-bind；EPS缺件按不可用。
2. 三面向：workflow-status取得next_jobs；fundamental、technical、institutional必須各自實際spawn_agent(fork_turns=none)，只送程式生成指令與列明資料。此任務授權這些研究子Agent與後續固定角色；不要為程式開發另派Agent。不得協調者代寫再標成獨立分析。
3. 分面向辯論：每面向依序完成<dimension>_bull、_bear（各新Agent）、_bull_reply、_bear_reply（followup_task回原Agent逐項反駁）、_judge（另一新Agent）。先保存雙方開場才能交換；不同面向不交換未列明分析。
4. 核心：core由協調者按固定指令選每面向一個主張及最多三個來源綁定證據點，原件完整保留；不能自己新增主張或湊滿九點。
5. 交叉：cross派新Agent，只看生成的核心包，核三個面向配對、時間尺度與重大未知。
6. 補查：supplement按cross產生有界計畫。使用workflow-fetch、共用來源ledger及明確live旗標；一次最多3查詢/9次HTTP，無合理需求就空查詢並說明。查不到逐筆unavailable，原件未複核captured_pending_review。只有company-import/audit新事實通過才workflow-resolve帶新company-reference；程式要求三個<dimension>_update、core_update、cross_update，全部完成才進STEP7。截日時點及來源冷卻不得放寬，中斷先abandon不能重送。
7. 整體辯論：bull_opening、bear_opening各派新Agent，讀最新核心/交叉/補查，然後回原Agent完成bull_reply、bear_reply，另一新Agent完成risk。所有實際研究依可用並行額度分批，不預先捏造派工ID。
8. 裁決：decision由協調者按固定契約完成。workflow-audit後upgrade帶--fixed-pipeline、--company-snapshot最新版本、--agent-run及本次request的extension設定；公開EPS與review必須等於綁定版本。不另傳--analysts/--debate-input或自寫reader_research。交付檔案通關看workflow-delivery.json；只有workflow-audit不是檔案完成。

每筆execution含{agent_id,prompt_sha256,kind,tool_call_id,evidence_kind:observed_tool_receipt}；新角色kind=spawn_agent/context_mode=fresh，回覆kind=followup_task並保留自己原agent_id。ID只能用真實工具回傳，不捏造；工具ID未提供時明示定位限制。這是協調者依工具紀錄保存的證言，不是程式可證明獨立性的密碼學憑證。

工具或額度不足時保留真實失敗紀錄，kind=unavailable、status/stance=unavailable、findings空、每個必要檢查明列不可用。反駁仍逐筆responses但只能uncertain、changed_my_position=false。依賴、資料不足與語意未知不可冒充complete。沒有可執行能力時交付受限狀態，不假扮多Agent。

沒有補查更新的完整研究共16個新的獨立研究上下文、8次回原Agent反駁；若有合格更新再增加4個新上下文。不要同時超過當前工具額度；不需要付費LLM API、額外服務或長期追蹤。所有原有監測維持暫停。

AI角色可在額度內並行，對同一run的workflow-submit/bind/fetch/resolve須由單一協調者依序執行，避免兩個程序同時改寫state。

日曆復原後，本文「本次snapshot」及fixed-inputs均指prepare_research輸出的effective_snapshot_path及fixed_input_dir。不得將原下載快照與復原後agent-run混用。原件捕獲時間、分析截止日與1000日規則維持不變。
