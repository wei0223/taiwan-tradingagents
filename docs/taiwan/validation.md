# 分階段驗證紀錄

日期：2026-10-13；上游 0.6.0 / SHA 1394a3f72aa4393e1a98f51b382434c4b4c2d972。
本機 Python 3.13.5，獨立分支 feature/taiwan-market-provider，已推送並建立 draft PR #1，
未修改或合併 main。

## A / B

- 成功：直接讀取實際 Graph、Agent、資料路由／vendor、CLI、回測、報告、測試及 CI。
- 成功：93 個上游 Python 原始檔 AST 解析、pyproject TOML 解析、git diff --check。
- 成功：辨認 Yahoo 旁路、付費預設、PIT 公布時間缺口、非投組回測限制；設計先於實作完成。
- 受阻：完整上游 baseline suite 需要安裝宣告依賴；工作區最初無 pytest。

## C / D / E

- 成功：22 項 unittest tests 全部通過（包含 subTest 三代碼及多種邊界案例）。
- 成功：全測試使用 synthetic transport/clock/publication archive；socket 被封鎖。
- 成功：2330 / 3017 / 3231 bare 及 .TW，另測上櫃及前導零，不只測上市。
- 成功：OHLCV 日期截斷、資料來源／原始列／更新時間、未知公布日與未來公布日排除。
- 成功：SMA、Wilder RSI、SMA-seeded MACD warm-up；營收 YoY、EPS QoQ/YoY、三率。
- 成功：外資含外資自營商、投信、自營商、買賣反轉／零／缺日，不重複計算自營商。
- 成功：HTTP/API failure、空資料、schema、NaN、負量、重複列、錯股票防護。
- 成功：Token 在 Authorization header，query／匯出／例外訊息不包含 Token。
- 成功：data-only CLI Mock JSON 匯出、缺資料 exit code、原版美股 normalize_symbol regression。
- 成功：README 更新，新增 Python 3.11–3.14 data-only CI；遠端台股 CI 全部通過。
- 成功：完整上游 CI（Python 3.11、3.12、3.13、3.14 及 America/New_York）全部通過，
  clean-install smoke 與全庫 Ruff 通過。Python 3.11 日誌：1284 passed、1 skipped、
  1 deselected、118 subtests passed；skip 為未安裝選用 langchain_aws，integration 預設排除。
  驗證程式 commit：9f858b6ba4eaff6a8a11bed7d5d3fd04ad64dbd5。
  [完整 CI](https://github.com/wei0223/taiwan-tradingagents/actions/runs/38414440762)、
  [台股 CI](https://github.com/wei0223/taiwan-tradingagents/actions/runs/38414439119)。

## 環境與失敗

完整 editable/dev 安裝在本機 pip build dependencies 階段未完成；minimal pip 安裝也
持續無進度，已中止。另從 PyPI 官方下載 wheel，驗 SHA256，解壓到工作區隔離
test-deps 執行台股測試（不是修改使用者 Python）。安裝輔助檔僅在 work，不屬交付程式。
實際版本：python-dotenv 1.2.4、requests 2.34.2、pytest 9.1.1、ruff 0.17.0。
最初以 python -m ruff 執行未找到 wheel 的 executable，改用工作區 wheel 內執行檔。
第一輪 lint 發現兩個格式問題，修正後全庫 `ruff check .` 通過。
完整上游 `python -m pytest -q` 在 conftest 的 pytest_configure 階段失敗：
`ModuleNotFoundError: No module named 'yfinance'`（退出碼 3；未收集測試），因此完整
本機上游 regression 未驗證；後續 GitHub CI 在乾淨環境完整安裝依賴並通過全套。
22 項台股 unittest 不依賴 yfinance/LangGraph，本機與遠端全部通過。
GitHub 連接完成後已 clone 使用者 Fork，main 精確匹配上述 SHA；重新驗證成果。

## 尚未實作／未驗證

- 使用者 Fork 實際 commit 比對完成；遠端分支／PR 狀態以最終交付連結為準。
- FinMind 匿名 live OHLCV：2330、3017、3231 在 2026-10-01 至 2026-10-13 查詢區間，
  各取得 7 筆 eligible records；TaiwanStockInfo 三檔識別成功。不帶 Token、沒有付費 API。
  這不代表財報公布日、所有資料集或所有時段均已驗證；基本面仍預設 fail closed。
- MOPS 實際公布日 adapter、可重現 revision archive；目前只有可注入契約。
- TWSE / TPEx live adapters、官方交易曆；三大法人天數是觀測窗口，不宣稱完整交易曆連續性。
- 完整台股 Agent Graph、台股專用工具／新聞／情緒、本機 Ollama profile。
- 台股歷史市場 identity、還原價／corporate actions、投組回測與程式化硬風控。
- 本機完整依賴安裝仍未解決；遠端完整上游 regression 已通過，但真實付費 LLM integration
  不在驗證範圍。本次未開通或使用任何付費 API，也沒有實際下單。

## 重現

只需 python-dotenv（package import）及 requests（live transport）：
`python -m unittest discover -s tests/taiwan -v`。
完整套件安装成功後：`python -m pytest -q`，`ruff check .`。

不要為測試設定真實付費 LLM key；台股 contract tests 不需要任何 API key。

## Sponsor 實際資料驗證（2026-10-14）

[資料準備流程](https://github.com/wei0223/taiwan-tradingagents/actions/runs/38545775294)
在 main 執行成功，股票 2330、起日 2025-01-01、分析日 2026-10-14。
`--require-token` 檢查及四個資料集請求成功；沒有讀取或輸出 Secret，也沒有呼叫 LLM。
這驗證已設定 Token 可以取得資料，未另行驗證帳號方案等級。

| 資料集 | 原始筆數 | 符合分析限制筆數 | 結果 |
| --- | ---: | ---: | --- |
| 每日行情 | 431 | 430 | 最新可用日 2026-10-13 |
| 法人買賣 | 2155 | 2150 | 最新可用日 2026-10-13 |
| 財報 | 102 | 0 | 缺可信公布日期，排除 |
| 月營收 | 22 | 0 | 缺可信公布日期，排除 |

下載 artifact 的 SHA256 與 GitHub 提供的 digest 一致，保存的 bundle 與狀態雜湊一致。
逐欄重算確認數值及來源一致，未發現憑證欄位。資料仍標記
`requires_insufficient_data=true`，不能產生完整基本面投資結論。
原始 Sponsor 資料未提交到公開儲存庫；artifact 僅保留一天。

驗證另發現舊版證據編號依賴欄位插入順序；排序保存 snapshot 後重建會改變編號，
但不改變數值及來源。修正為固定排序後分配編號，新增序列化往返回歸測試；
本機台股 unittest 共 39 項通過。既有 artifact 保留原始編號與雜湊，不就地改寫。

## MOPS 公布日期與三檔 Sponsor 驗證（2026-10-14）

新開發分支 `feature/publication-provenance` 以已合併 main `6fe84339d523d2ad7eca2075865cb65740ef43f9`
為基準。FinMind Sponsor schema 與官方說明確認未提供正式基本面公告時間；新增 MOPS
公告快易查 F22/F27 adapter、完整回應 archive、原始數值列與公告雜湊綁定、台北時區及
保守版本取得日限制。詳細政策見 [公布日期文件](publications.md)。

[三檔 Sponsor 流程](https://github.com/wei0223/taiwan-tradingagents/actions/runs/38551514641)
在既有 main 成功執行；查詢 2025-01-01 至 2026-10-14。下載 artifact 驗 SHA256
`9f2f7c872b668e8efa290bd7a4658ed19eb2aa4a4b6e2bbf69519a86c68c5a82`，各保存的 bundle
與 state 雜湊一致。以新分支程式重放 Sponsor 原始列（保留原取得時間，不再次呼叫 FinMind），
另取得真實 MOPS 公告並建立新的 snapshot/bundle。這是本機 adapter 驗證，原 Actions
artifact 沒有就地改寫，也不代表尚未合併的工作流程修改已上線。

| 股票 | 可用月營收列 | 可用財報科目列 | 最新營收公告（台北） | 最新損益表申報（台北） |
| --- | ---: | ---: | --- | --- |
| 2330 | 22 | 102 | 2026-10-08 14:51:19 | 2026-08-14 13:46:30 |
| 3017 | 22 | 96 | 2026-10-07 07:46:50 | 2026-08-12 15:25:39 |
| 3231 | 22 | 96 | 2026-10-08 13:54:01 | 2026-08-07 18:35:53 |

最新營收期間皆為 2026-09，財報期間為 2026-06-30。F27 是正式損益表申報時間，
不將較早法說會／董事會消息当此申報時間。數值版本可用日起點皆為 2026-10-14。
今日的日終 snapshot 基本面 coverage 為 ok；不宣稱以前日期可用同一數值版本。

成功：三檔未來公告截止測試、2026-10-13 的版本取得日截止測試、重新序列化雜湊驗證。
成功：原始 Sponsor 包及增補包皆未發現憑證欄位，未傳送 Token 給 MOPS，未呼叫 LLM、未下單。
成功：51 項台股離線 unittest（網路封鎖），全庫 Ruff 與變更空白檢查。
首次 lint 發現新測試 import 排版一項問題，修正後通過。
本機未安裝完整上游依賴，完整 regression 交由 PR CI 驗證，最終狀態以 PR 檢查為準。

未實作：原始財報所有歷史更正版本的文件解析與單季轉換、公告時刻的盤中回測、
外部數值版本的官方數位簽章。SHA256 僅做完整性比對；本次接入不取代歷史版本資料庫。
MOPS 官網／API 無法提供或辨識的公告仍會排除，沒有以期限或出表日期補填。
三檔原始真實數值包仅留存本機／一天 artifact，公開 repo 只記錄 schema、測試及驗證結果。
