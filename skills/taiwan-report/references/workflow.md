> 新研究使用八階段v2與references/fixed-agents.md。下文資料傳輸仍適用；凡agent-bind/agent-audit在v2改用workflow-bind/workflow-audit，技術及財務規則不變。舊v1依保存版本執行。

# 單次執行入口

`scripts/project.json` 記錄本機儲存庫及輸出根目錄，request.json 保存本次參數。它們不含任何Secret。套件安裝路徑以目前環境為準，先使用既有依賴，不自動付費。

## 取得資料

本專案的既有GitHub流程：`wei0223/taiwan-tradingagents` → `taiwan-prepare.yml`，只能以 `ref=main` 執行。inputs使用request.json的workflow.inputs：symbols、start、as_of、mops_publications=true、extended=true。不得把Secret提供給feature/PR程式。

執行 `python scripts/prepare_data.py --request <完整request.json路徑>`。程式依序檢查CLI與登入、記錄main SHA和執行前run清單、只dispatch一次、綁定本次run、等待並下載原始artifact。`scripts/project.json`可設定`gh_executable`；設定是本機路徑，不是Token。GitHub CLI首次需要使用者完成官方授權，日後沿用目前作業系統使用者的登入；不同執行帳號不一定共用授權。沒有CLI或登入時先解決該前置條件，不先啟動瀏覽器操作。

這是可續跑入口，不是阻塞等待：遇到`awaiting_run`／`awaiting_workflow_completion`時，隔一段短時間重新執行同一request；最多等待25分鐘且不重送。讀`preparation.json`取得狀態，保留`preparation-history/`。`dispatch_uncertain_do_not_redispatch`也只查既有run，不能手動再按Run；`ambiguous_runs_requires_review`、逾時、缺artifact或損毀下載須先釐清，不能拿最新成功run充數。程式以actor、main SHA、event、前次清單及啟動時間核對；同一時段有多筆符合便停止選擇。每次重新呼叫都驗證請求hash，完成後也驗證snapshot hash。

`data_available`或`data_available_with_workflow_warnings`才表示資料已下載。後者代表工作流程本身未全部通過，必須閱讀`data/audit.json`逐項判斷用途，不宣稱完整成功。主輸入固定為`data/<代碼>-<分析日>/snapshot.json`；`integrated/snapshot.json`是流程內另產生的副本，不能因有兩份而判斷下載失敗。只在同公司、同分析日且捕獲時間對應本次run時採用；財報原件、期間與數值的資格另由研究審查決定。`report_generated=false`在此階段正常，仍需完成下節八階段研究與交付，不能停在資料包。

不要為本機便利讀回GitHub Secret。若使用者另有已配置本機FinMind環境，才可使用本機prepare；不列印Token。只在明示接受既有資料時沿用本機snapshot，不能將舊行情日期改成今天。

## 研究與報告

下載後核對snapshot公司、交易所、截止日與資料可用性。讀儲存庫 `docs/taiwan/report-reading.md`、`research-model.md`；需要財報審查時再讀 `review-rules.md`。舊技能 `taiwan-stock-research` 只供其機器JSON辯論模式使用，不能讓其「先備資料包／禁止前瞻EPS」舊規則覆蓋本次單次報告需求。

基本報告入口為：

```text
python -m tradingagents.research upgrade --fixed-pipeline --company-snapshot <本次company-reference.json> --snapshot <本次snapshot.json> --review-input <本次已審原件.json> --agent-run <fixed-inputs/agents> --extension-input <公司模型.json> --output-dir <本次request目錄/report>
```

這些參數由Codex準備，不要求使用者自行寫JSON。三分析師、辯論與最終正文只用固定agent-run的已接受輸出；操作依fixed-agents.md，不另寫--analysts、--debate-input或reader_research正文。缺資料的選用參數省略並在報告交代，不造假旗標。四季模型與研究計畫採現有 `--research-plan`／extension輸入契約，缺參數時先檢查目前CLI及文件，不猜schema。依目前環境可加 `--pdf-font`。正文可包含程式已算出的精確數字；舊辯論state-machine的無數字敘述限制不適用完整三分析師報告。

有新官方資料可另查核，但不得回填舊公布日或放寬財務期間／EPS資格檢查。當次分析不能取得的項目標為不可用；報告可以完整交代限制，不必啟動歷史缺口追蹤、正式發布或預測評分。

交付格式是實際產生的report.html、可生成時的report.pdf及evidence.zip，不是request.json。開啟報告確認三位分析師內容、引用與日期；ZIP需包含可重現的輸入、計算和原件，而非只包PDF。

本模式的extension輸入必須包含`stock_id`、`as_of`與`one_shot=true`；即使沒有完整模型也要傳入這個基本物件。這會使用單次研究版面，不把正式預測發布關卡當成報告前置作業；數值、期間、來源與估值資格檢查保持有效。request中的`upgrade_extension_defaults`供此用途。


## 固定資料入口

先執行技能的prepare_research，再依company-instructions完成原件查閱與制式輸入。程式入口：

```text
python -m tradingagents.research fixed-inputs --snapshot <snapshot.json> --output-dir <新目錄>
python -m tradingagents.research company-plan --stock 3017 --as-of YYYY-MM-DD --output <新plan.json>
python -m tradingagents.research company-latest --stock 3017 --as-of YYYY-MM-DD --database-dir <公司資料庫> --output <新selection.json>
python -m tradingagents.research company-import --input <已查核company-input.json> --database-dir <公司資料庫> --output <新company-reference.json>
python -m tradingagents.research company-audit --reference <company-reference.json> --output <新audit.json>
```

company-latest只選既有版本，不聲稱最新完整；新分析日期要重新核對可用來源、另匯入公司快照。company-input-template的pending要按本次查閱結果分類。來源公布未知維持null；只能以捕獲時間作已知，不自行補初版證明。模板及欄位限制見docs/taiwan/fixed-research-pipeline.md。

本機報告與CLI必須能載入相同的requirements-fixed-research.txt依賴。個人project.json可設定technical_dependency_path及base_dependency_path，prepare_research會加入Python搜尋路徑；執行upgrade時也須使用相同環境。完整1000日不足會在最多三段、每次向前兩年查詢後停止；內部缺交易日、來源拒絕或日曆失效立即停止，不重試繞過。

公司資料庫根目錄優先使用scripts/project.json中的company_database_root；沒有設定才使用專案work/company-database。不得每次另猜新資料庫路徑。
