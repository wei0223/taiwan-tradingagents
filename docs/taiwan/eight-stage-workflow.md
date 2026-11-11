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

## 交叉研究與最終裁決

新研究在補查結案後凍結 `agent-run/supplement/cross-evidence.json`。封存紀錄保存問題、計算時間、輸入雜湊、共用證據 ID、方法與適用限制。同一項外資與股價統計可供不同問題引用，應視為一項證據。期間或口徑不合時，保留缺口。

多方與空方回應、風險審查及裁決逐一提交 `cross_review`。每列填寫證據 ID、受影響的主張、影響方向及理由。角色無法執行時僅能回覆未定。程序驗證只確認欄位與封存對應，研究語意仍須審查。舊流程重出的報告若未使用新統計，標示事後補算。核心證據的 `limitation` 與缺口另列，不計為反向證據。

## 單次研究的固定觀察與續跑

- 交叉研究先提供已對齊的 20 日價籌統計。技術與法人配對即使初判次要，仍保留可計算的分組下跌率、兩組樣本數及分別移除首日／末日的敏感度。任一組少於五日或方向對視窗敏感時只列弱描述證據，仍可維持原判。
- 公司計畫的 `required_research` 隨產業設定，通用公司須從原件辨識事業、業外及現金傳導。Planner 保留必查題並新增至少一題公司專屬問題。分析師逐題交代原件、財務傳導、反證或不可用原因。
- 缺四季模型時，只用研究截止日前已完成收盤除以示意倍數，列現價所需 EPS。營收與毛利門檻仍以四季模型為前提。公開年度 EPS 保留其口徑。
- 固定短線觀察檢查最新已完成交易日所屬週的候選日，要求當日收盤高於此前 20 日最高價，且當日成交量至少為此前 20 日均量的 1.5 倍，且同一完整週收盤站上同一價位。最新收盤跌破當期 20 日均線時列失效。分析師自訂條件另外說明。
- 中斷後沿用同一請求、資料包與 agent-run。用 `workflow-status` 確認已接受工作、可重用提示、公司綁定及待完成階段。只有輸出原始 bytes、結果及執行收據都相同才可重送已接受工作。使用 `upgrade --agent-run` 時，報告先在暫存目錄完成驗收再移入指定的新目錄；渲染失敗可用原指令重試，不必重跑資料或 Agent。

### 公司原件補齊入口

planner 驗收後、workflow-bind 前，執行 company-intake --snapshot 本次snapshot.json --output-dir 新目錄 --archive-dir 共用來源庫 --live。可重複使用 --saved-input 指定已保存原件包。以股票 metadata 建立最近八季官方 AI1 查詢，最新季優先，最多25次請求及8份PDF；沿用來源庫冷卻，403/429立即停止該主機。只接受新輸出目錄。

讀取 review-queue.json 和 source-search.json，按固定公司研究計畫查年報、分部、EPS附註、現金流、募資及更正，並另查公開FactSet。AI逐份核對原文、表頭、頁碼、單季／累計、合併範圍、單位與版本，再產出company-import輸入。下載的原件維持pending，只有查詢後的缺件才unavailable。review-instructions.md固定這些語意審查步驟。一般公司沿用通用研究問題，不能套另一家公司數值。

程式保留原件bytes、SHA256、URL、捕獲時間與官方下載鏈。公布時間未知維持null，以捕獲時間決定可用性。新批次可沿用舊bytes和原擷取時間，語意審查另留實際時間。company-import/audit仍是workflow-bind的前置關卡；沒有足夠的財務欄位時繼續已通過面向的分析。此入口不宣稱歷史初版完整，也不自動將供應商欄位核准為單季。
稅費抄錄若使用損益表的帶正負號影響金額，審查者須明示 statement_sign=signed_profit_effect；計算核心以負稅費影響除以稅前獲利計有效稅率。既有正數費用口徑沿用原算式。投資收益及非控制權益以各自直接欄位保存，不用歸母占比變動冒充金額變動。

## STEP 2 派工稽核

交付包提供 `agent-access-policy.json` 與 `agent-execution-audit.json`，可另用 `workflow-audit --run <agents> --execution-output-dir <新目錄>` 重驗。程式核對實際封存的派工提示、指定輸入、已發布依賴、接受順序、角色與收據。多空開場使用新上下文，雙方皆接受後才交由原角色反駁。

Codex 角色共用工作區。目前驗收涵蓋上下文、派工輸入與收據，結果為 limited，檔案系統強制存取控制尚未實作。封存資料無法證明工具外傳遞或角色實際讀取範圍。收據缺漏、上下文重用或未發布論點出現在派工包時拒收。Mock 與無法完成派工的研究不能取得 limited 通關。

## STEP 3 資料品質與市場預期差

單次正式報告的證據包包含 `data-quality-report.json` 與 `expectation-gap.json`。前者列出資料可用性、期間、原件雜湊、版本、公布與擷取時間。資料集逐筆來源可由 `research.json` 的 source_audit.lineage 追溯。公司欄位則保留事實 ID、原件 ID 與頁碼。未知時間保留空值；截止日後捕獲的供應商版本另列為回顧資料。

預期差只使用同公司、同指標、同期、同合併範圍與同單位的實績及共識中位數。EPS 還須區分基本及稀釋。共識的原件須在已驗證首次實績公布前捕獲。百分比以共識絕對值為分母；零共識只列絕對差。共識口徑未確認、首次公布時點缺證、缺來源或版本衝突時保留 unavailable。公司展望與 AI 假設另列來源類別。

目前單次報告的共識來源為公開 FactSet 年度 EPS 轉引，仍保留轉引原文未證實的口徑。營收欄位在缺少核可共識來源時顯示 unavailable。公司資料庫的目前版本尚不能自動證實首次公布時點。可比較的 EPS 及營收差額算法已用固定輸入驗證。

## STEP 4 重大主張與推論審查

以 `workflow-reasoning --run <agents> --output-dir <新目錄>` 建立重大主張清單。清單涵蓋每面向選入核心的主張、最終發現和四個裁決。保留原主張、證據、反證、時間尺度、改判條件、原作者及完整內容雜湊。

獨立審查者可用 `--review <JSON>` 提交具名審查。JSON 須含 stock_id、as_of 與依主張 ID 索引的 assessments。每筆包含 claim_sha256、reviewer、status、inference、reason，並可附 numeric_checks。數值檢查以 evidence_indices 指定封存引證，支援 value、difference 和 percent_change，expected 指定結果，places 指定小數位數。來源欄位、期間與單位沿用原件及封存計算合約。自由文字推論的合理性仍由具名審查者判斷。

審查結果分為 supported、challenged、insufficient。待審及有爭議主張不得進入已確認核心。challenged 會留下 retry_required 與原作者定位，退回原角色時應建立新研究或審查版，保留原始工作結果及發布資料。輸出從新目錄另存。報告可用 extension_input.reasoning_review 附入同份審查，並將 `reasoning-audit.json` 和 `claim-validation.json` 收入證據包；HTML 與 PDF 顯示相應標籤。具名審查是可追溯判斷，程式可驗證綁定及選定數值，無法替代獨立語意判斷。

## STEP 5 估值與時間尺度

正式單次報告附上 `valuation-scenarios.json` 與 `horizon-analysis.json`。前者分列直接公布的單季稀釋 EPS、公開年度共識及已核四季模型。三情境只採公司與研究日相符、四季連續且通過既有模型驗證的輸出。四季加總與來源參數可追溯至 forward-estimate.json。

使用合格未還原收盤價及模型明示的倍數，分列 EPS × 倍數的條件價格、現價除以倍數所需的 EPS、相對已公布四季 EPS 的所需變化，以及基準情境與現價要求的差。供應商歷史倍數另列為描述性背景，分母未驗證時不升格成估值錨。採用的公司假設、倍數理由及風險保留在證據包。缺模型、缺合格價格或口徑不一致時保留缺值。

企業中長期判斷、估值與短期價格觀察分開呈現，保留原主張的推論審查狀態。

## STEP 6 條件式交易研究

單次報告附上 `decision-conditions.json`。價量採已驗固定技術觀察，使用前二十交易日高點、量比與同週完整週收盤。法人要求同截止日、分類完整的五交易日外資淨額為正。獲利門檻須綁定同公司最新已核、合併單季直接稀釋 EPS。新財報、更正或重大公告後重驗獲利條件。企業中長期估值另見 valuation-scenarios.json。

選填 extension_input.decision_conditions，含 stock_id、as_of、rationale、entry_twd、invalidation_twd、target_twd、entry_cost_bps、exit_cost_bps，以及 earnings.fact_id 與 earnings.minimum_eps_twd。成本為情境輸入，買賣各自明示，沒有預填費率。報酬與損失以成本後每股差額衡量；沒有正報酬或條件不齊時維持 observe。待驗證候選使用確認後首個官方交易日起算五日與二十日，並保留原失效門檻。結果為未成交的研究情境。
