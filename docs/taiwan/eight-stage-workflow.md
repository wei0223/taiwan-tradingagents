# 正式八階段台股研究流程 v2

版本 `taiwan-eight-stage-v2`。新單次請求與 `fixed-inputs` 命令預設此版；舊 `fixed-research-agents-v1` 的模組、提示詞及封存維持可讀，不能把舊 run 就地轉成 v2。Python `fixed_pipeline.prepare` 的既有呼叫預設仍是 v1；新呼叫可明選 `agent_protocol`。

## 八階段與通關規則

| 階段 | 必須產出 | 通關條件 |
|---|---|---|
| 1 資料準備 | snapshot、固定技術包、公司計畫、planner、已核公司版本、公開 EPS 或明列缺件 | planner 通過後才 bind；公司與截止日一致、原件/事實/計算可核；期間未知或技術不足不補值 |
| 2 三面向獨立分析 | fundamental、technical、institutional | 三個不同的新 Agent，分面向輸入隔離；固定檢查逐項完成或 unavailable，基本面逐題回覆公司計畫 |
| 3 各面向多空辯論 | 每面向 bull、bear、bull_reply、bear_reply、judge，共15份 | 各新多空開場先保存；回原 Agent 對每個對方 finding 恰好反駁一次；另一新 Agent 作面向裁決 |
| 4 核心證據萃取 | core，三面向主張及證據點 | 每面向一個已接受主張；最多3個證據點，恰好1個 primary，其餘 support/counter；全包最多9點，重複不得計數，缺件不湊數 |
| 5 跨面向研究 | cross 的三組比較 | 基本×技術、基本×籌碼、技術×籌碼都需明列；區分一致、衝突、尺度不同、未建立關係與不足；時間對齊、影響及缺件不可省略 |
| 6 自主補查與更新 | supplement、captures、resolution；有合格新版本才新增五份 update | 每查詢連結重大/未知且有缺件的 cross 問題；一次最多3查詢/9次HTTP；每筆必結案。新原件須重新匯入查證才建立 revision1，強制三分析師、core、cross 全部更新 |
| 7 最終多空與風險 | 整體多空開場/反駁及 risk | 使用最新分析/核心/交叉/補查；重新派獨立多空，再回原 Agent 反駁；另一新 Agent 分會計、現金、估值、短期四軸審查 |
| 8 裁決與交付 | decision、HTML、可生成時PDF、完整ZIP、workflow-review、workflow-delivery | 較強論點、關鍵分歧、真實改判與風險明確；檔案與ZIP驗證另通過才能產生交付收據，研究通關不冒充檔案交付 |

基礎28個固定job；補查有合格新公司版本時再增加5個，共33個。正常研究需16個不同的新研究上下文，另有8次回原Agent反駁；補查更新再增加4個新上下文。可依可用並行額度分批執行，沒有真實派工能力就保存 unavailable，不以協調者代演。收據是實際工具紀錄的證言，程式不能單靠ID證明上下文真的獨立。

`gate=passed` 代表前置與格式引用檢查完成；`research_status=limited/unavailable` 仍須揭露。STEP8研究完成只會 `awaiting_delivery`，實際檔案通關另見 `workflow-delivery.json`。公司近三年/八季/同業與歷史版本是否完整仍由原有稽核判定；沒有新增預測或交易績效的資格。

## 共通輸出格式

每個 `workflow-prompt` 生成 `jobs/<job>/prompt.md`、`request.json`、`contract.json`。照實際contract提交，不自行猜欄位。

```json
{
  "protocol": "taiwan-eight-stage-v2",
  "job": "fundamental",
  "stock_id": "3017",
  "as_of": "2026-11-05",
  "revision": 0,
  "prompt_sha256": "使用本次程式生成的雜湊",
  "agent_id": "實際派工回傳的識別",
  "status": "limited",
  "stance": "uncertain",
  "summary": "完整論述：主張、證據、反證、分歧與條件。",
  "findings": [],
  "gaps": ["缺少原件或某項研究未完成"],
  "conditions": ["可用新原件或同口徑數值判斷的改判條件"],
  "payload": {"依job使用程式產生的格式": "不可提交本示意值"}
}
```

可用研究最多6個 finding，每筆包含 `id=<job>.<局部ID>`、`claim`、`horizon`、`evidence`、`counterargument`、`revision_condition`、`confidence`。horizon明選 long_term/short_term/both/unspecified，信心只是 uncalibrated_low/medium/high。

證據固定為 `{input_id,pointer,quote}`，quote必須精確等於已核輸入純量的 `str(value)`；不能引用null/容器。公司只可引用選定facts，財務只可引用期間計算，技術非ready及公開EPS unavailable不得引用。

### 各job的payload

- planner：questions每筆 `{id,question,financial_transmission,required_sources}`；business_hypotheses字串陣列；entities/peers每筆 `{name,stock_id或null,relationship,reason,status:candidate_requires_verification}`。
- 三分析師：checks逐項 `{id,status:completed/unavailable,explanation,evidence}`，基本面另有answers `{question_id,status:answered/unavailable,answer,evidence}`。
- 開場：payload空物件，主張寫findings。反駁：position_before及responses，每筆 `{claim_id,assessment:accept/dispute/uncertain,reason,evidence,changed_my_position}`。
- 面向judge：`{primary_claim_id或null,reason,key_disagreement}`，stance作初判。
- core：faces恰三筆 `{dimension,claim_id或null,reason,items,unavailable_reason}`。items每筆 `{role:primary/support/counter,claim_id,reference,correlation_group}`；reference只能複用該主張已有證據。程式另附claim、horizon及來源/期間脈絡。
- cross：relations每筆 `{id,dimensions:[兩個面向],claim_ids,relationship,materiality,time_alignment,explanation,missing_data,impact}`。relationship明選 alignment/conflict/different_horizon/no_established_relation/insufficient，materiality明選 material/minor/unknown。
- supplement：`{queries,skip_reason}`。查詢每筆 `{id,issue_id,operation,period,edition,rationale,affected_dimensions}`。
- risk：rulings四筆 `{axis,verdict:supported/challenged/uncertain/unavailable,reason,evidence}`。axis為 accounting_earnings/cash_sustainability/valuation/short_term。
- decision：rulings加 position_before、stronger_case、key_disagreement、evidence_change、changed_claim_ids；改判ID必須真的存在。沒有實質改判就空清單，不虛構新證據。

「最多九點」是閱讀摘要上限；其餘數據、原件及辯論仍全部打包。correlation_group說明同源關係，程式另附保守來源群組與 `independence_statistically_verified=false`，不把多個動能指標變成獨立統計樣本。

## 命令與受控補查

```text
python -m tradingagents.research fixed-inputs --snapshot <snapshot.json> --output-dir <全新目錄>
python -m tradingagents.research workflow-status --run <agents>
python -m tradingagents.research workflow-prompt --run <agents> --job <next_job>
python -m tradingagents.research workflow-submit --run <agents> --job <job> --result <原始JSON> --execution <真實收據JSON>
python -m tradingagents.research workflow-bind --run <agents> --company-reference <已核公司版本> --public-eps <轉引原件輸入>
python -m tradingagents.research workflow-fetch --run <agents> --archive-dir <共用official-source-ledger> --live
python -m tradingagents.research workflow-resolve --run <agents> --decisions <逐筆處置JSON> [--company-reference <新公司版本>] [--review-input <新財務審查>]
python -m tradingagents.research workflow-audit --run <agents>
python -m tradingagents.research upgrade --snapshot <原snapshot> --output-dir <新交付目錄> --fixed-pipeline --company-snapshot <最新已核版本> --agent-run <agents> --extension-input <本次v2設定> [--pdf-font <中文字型>]
```

補查只開放同公司四種固定操作：financial_listing（YYYY）、financial_original（YYYY-Qn、AI1/AIA）、monthly_revenue（YYYY-MM）、capital_current（period/edition為null）。日期不得在截止日之後，財務季/月需已結束。來源限程式內建MOPS/TWSE，不接受任意URL、shell、Token或付費服務；共用ledger冷卻與403/429/限制頁停機規則。

其他IR、FactSet或行情需求目前保留為具體缺口，不能假裝已有任意自主瀏覽器。無合理查詢時空queries附skip_reason，fetch不發HTTP，resolve使用空物件。HTTP失敗只能unavailable，no_new_information須本批原件bytes與已綁原件完全一致；每個reviewed查詢各自須有對應新版本選定事實，不能借另一筆查詢通關。每個有查詢的disposition固定 `{status:unavailable/no_new_information/captured_pending_review/reviewed,reason}`。

原件捕獲不是數值驗證。只有company-import/audit通過的新公司版本，而且新事實綁定本批captured bytes，才能reviewed。歷史截止前未取得的現今回補原件仍不能穿越日期門檻。新版本清空舊數值review，須明選新的review-input；原始snapshot/技術包/公開EPS不在此輪暗換。

查詢開始前先留不可覆寫reservation。執行中斷時禁止重送，使用 `workflow-fetch --abandon-reason <實際原因>`（仍帶archive-dir）結束未明批次，再將各查詢處置為unavailable；共享ledger已捕獲原件保留，不自動當成研究證據。下一份研究才另起新批次。

## 檔案與驗收界線

輸入、原件、各revision、提示詞、contract、原始AI回覆、接受版本、實際工具收據、補查與改判均進 `agent-run/`。HTML/PDF顯示八階段摘要、核心證據與交叉研究；`workflow-review.json`記研究通關，`workflow-delivery.json`記實際檔案/雜湊/證據包驗證。

Mock只供軟體驗證，明選mode=mock及agent_report_mode=mock；不能混入正式研究、預測績效或真實派工。沒有合格資料可交付受限報告；沒有真實Agent能力就明列unavailable。新協定仍需真實股票端到端驗收，離線通過不證明研究品質。
