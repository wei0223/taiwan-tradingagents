# 在Codex輸入股票，取得單次報告

預設使用方式：

```text
$taiwan-report 2330
```

也可自然輸入「研究台股3017」或「台股報告3231」。技能在Codex中執行一次資料準備、三分析師研究、多空交叉反駁與裁決，再交付報告；完成後結束。沒有指定日期則用台北當天，行情日期另列最近可用交易日。

報告包含基本面、技術面、籌碼面三位分析師，前瞻EPS及條件估值、較有力的論點、關鍵分歧、改判條件與來源限制。沒有證據的項目保持不可用，但不要求先做多年回測或等未來實績才交付報告。

資料準備由Codex代辦既有main的FinMind Sponsor工作流程；不要求使用者每次手動下載。Secret留在GitHub，AI使用目前Codex登入與模型，不呼叫付費LLM API、不下單。若GitHub／Codex真的需要登入或權限，才請使用者處理；資料／能力受限就明確交代，不以舊資料或模擬Agent冒充完成。

每次另建日期目錄。交付HTML、可產生時的PDF與完整證據ZIP；`request.json`只是執行請求，不是研究報告。新增來源、數字、假設與引用必須實際核對，不能因縮短操作流程而放寬期間、EPS或版本檢查。

本模式不發布正式預測、不評分舊預測、不建立或恢復排程，不持續追蹤。原報告、原預測及其發布時間保留；現有四項監測保持暫停。發布／追蹤入口仍供明確要求時使用，不再是預設操作。

個人技能可安裝在使用者 `.agents/skills/taiwan-report`，`scripts/project.json`只保存本機儲存庫與輸出路徑，不含Token。開源技能原件位於儲存庫 `skills/taiwan-report`；測試不使用Sponsor額度或真實股票資料。

## 可續跑的資料入口

技能以`prepare_data.py --request <request.json>`使用GitHub CLI，首次完成官方授權後不需每次操作Edge。CLI預檢在dispatch之前，等待、下載或Codex中斷都續用同一請求；不因重試產生重複run。準備狀態與報告產生狀態分開，workflow品質檢查失敗仍可下載artifact，但需逐項讀取警示，不能當完整成功。主snapshot與integrated副本分開；取得資料後仍須完成研究才交付。

閱讀版首頁為研究結論；正式upgrade已整合[研究深度與閱讀分層](reader-research.md)。完整工程狀態在audit.html，資料與原件在可攜證據包。
