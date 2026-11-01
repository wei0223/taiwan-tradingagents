# 單次執行入口

`scripts/project.json` 記錄本機儲存庫及輸出根目錄，request.json 保存本次參數。它們不含任何Secret。套件安裝路徑以目前環境為準，先使用既有依賴，不自動付費。

## 取得資料

本專案的既有GitHub流程：`wei0223/taiwan-tradingagents` → `taiwan-prepare.yml`，只能以 `ref=main` 執行。inputs使用request.json的workflow.inputs：symbols、start、as_of、mops_publications=true、extended=true。不得把Secret提供給feature/PR程式。

先找有dispatch／run／artifact能力的連接工具。有已登入gh時可用 `gh workflow run taiwan-prepare.yml --repo wei0223/taiwan-tradingagents --ref main --json`，標準輸入為inputs物件。若工具沒有dispatch，使用已登入GitHub網頁完成同一操作。只在確實需要登入時請使用者登入；不索取密碼、驗證碼或Token。

以股票與輸入日期、分支、觸發時間核對這次run，不能把別次成功run當本次結果。等待本次完成後下載artifact，解壓至request目錄的data/。單輪只有一次dispatch；需要重跑前先確認失敗原因且無重複工作正在執行。工作流程只準備資料，不會執行AI。資料缺件仍可能有artifact，讀取稽核結果而非只看綠色勾。

不要為本機便利讀回GitHub Secret。若使用者另有已配置本機FinMind環境，才可使用本機prepare；不列印Token。只在明示接受既有資料時沿用本機snapshot，不能將舊行情日期改成今天。

## 研究與報告

下載後核對snapshot公司、交易所、截止日與資料可用性。讀儲存庫 `docs/taiwan/report-reading.md`、`research-model.md`；需要財報審查時再讀 `review-rules.md`。舊技能 `taiwan-stock-research` 只供其機器JSON辯論模式使用，不能讓其「先備資料包／禁止前瞻EPS」舊規則覆蓋本次單次報告需求。

基本報告入口為：

```text
python -m tradingagents.research upgrade --snapshot <本次snapshot.json> --review-input <本次已審原件.json> --analysts <三分析師.json> --extension-input <公司模型.json> --debate-input <實際辯論.json> --output-dir <本次request目錄/report>
```

這些參數由Codex準備，不要求使用者自行寫JSON。缺資料的選用參數省略並在報告交代，不造假旗標。四季模型與研究計畫採現有 `--research-plan`／extension輸入契約，缺參數時先檢查目前CLI及文件，不猜schema。依目前環境可加 `--pdf-font`。正文可包含程式已算出的精確數字；舊辯論state-machine的無數字敘述限制不適用完整三分析師報告。

有新官方資料可另查核，但不得回填舊公布日或放寬財務期間／EPS資格檢查。當次分析不能取得的項目標為不可用；報告可以完整交代限制，不必啟動歷史缺口追蹤、正式發布或預測評分。

交付格式是實際產生的report.html、可生成時的report.pdf及evidence.zip，不是request.json。開啟報告確認三位分析師內容、引用與日期；ZIP需包含可重現的輸入、計算和原件，而非只包PDF。
