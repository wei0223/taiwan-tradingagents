# 台股專用範圍與遷移

使用者已將原「保留美股」要求改為只研究台股。本次是有意移除功能的範圍變更，不是宣稱原版程式全屬死碼。

## 保留

- `tradingagents/dataflows/taiwan`、共用資料錯誤類別及全部 `tradingagents/research`。
- 固定公司資料 SOP、1,000 日技術引擎、公開 FactSet EPS、固定 AI 指令、Codex 技能與真實辯論入口。
- HTML／PDF／證據包、來源版本稽核、財務期間與數值檢查。
- 既有封存／歷史驗證入口：供舊資料相容與明確指定的離線驗證使用，不預設建立追蹤。

## 移除

- 原版 `cli`、LangGraph 圖與 Agent、交易／組合／記憶及原版回測。
- 雲端與本機模型供應商 SDK 連線（包括 OpenAI、Anthropic、Google、Bedrock、Ollama 等舊入口）。Codex 技能登入與多空研究不依賴這些 SDK。
- Yahoo、Alpha Vantage、SEC EDGAR、FRED、Polymarket、Reddit／StockTwits 供應商與跨市場代碼轉換。
- 對應美股測試、圖片、企業環境範例與 Docker Ollama 服務。

## 遷移

`tradingagents` 現在執行台股研究 argparse 入口，與 `taiwan-research`、`python -m tradingagents.research` 相同；不再顯示舊互動美股選單。`main.py` 不再示範 NVDA 或發起模型 API。舊 `TradingAgentsGraph`、`DEFAULT_CONFIG`、`cli.*` 與 `dataflows.vendors.*` 匯入已不支援；需要舊美股功能請參考本次變更前的 Git 版本。

只更新儲存庫程式、封裝與文件；不修改既交付報告、公司資料庫、來源原件、release、預測或個人技能設定。舊 runtime 快照仍保持原雜湊。新增的範圍測試檢查實際套件內容、阻擋模型／美股模組後的匯入與命令入口，不能用刪掉測試冒充回歸成功。
