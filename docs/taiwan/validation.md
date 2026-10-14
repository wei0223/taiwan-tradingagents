# 分階段驗證紀錄

最新擴充見本文末「研究功能補齊」；下面保留各階段當時的驗證與未完成狀態，不把舊紀錄
中的缺口當作現在仍缺少，也不將本次成功追溯成先前已通過。

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

## 研究功能補齊（2026-10-14）

獨立分支 `feature/taiwan-research-completion`，基準 main `03ac6a32415975fdf2871195d23a15149dd1f45e`。
新增能力与邊界見 [擴充操作文件](research-completion.md)。没有修改美股 Graph 或付費模型預設，
台股走既有 ChatGPT 登入 Codex 原生多方／空方／風險研究流程。

成功：79 項離線台股 unittest；所有來源 transport／clock 可 Mock，測試封鎖網路。
包括 F26/F28、未來公布與取得日、衝突／不安全新聞列排除、非代表性社群樣本、
原始列綁定／重算、不覆寫版本及更正保存、偽造較早收據、官方月份／缺交易日、
公司行動（包括面額變更）不同 schema、下一交易日評分、缺价 pending、中性不算勝利、
原始 PDF 非官方連結拒絕與內容毀損、報告來源／日期／HTML 防護。
成功：全庫 Ruff、git diff --check。完整上游 regression 仍交由本 PR GitHub CI 的乾淨環境；
不為本機安裝或測試開通 LLM API。遠端檢查結果以本 PR Checks 為準。

### 真實來源檢查

不讀取 GitHub Secret，以匿名 FinMind 取得新數值並接真實 MOPS 公布時間；
2025-01-01 至 2026-10-14，市場觀測截止 2026-10-13。
新版新聞解析用本次原始回應重放，保留原取得時間，沒有改寫先前保存的資料包。

| 股票 | 估值列 | 現金流科目列 | 資產負債科目列 | 合格新聞標題列 | 官方市場交易日 | 封存原始財報 PDF |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2330 | 430 | 162 | 604 | 68（partial） | 430 | 4 |
| 3017 | 430 | 146 | 520 | 3（ok） | 430 | 4 |
| 3231 | 430 | 158 | 510 | 61（partial） | 430 | 4 |

2330 新聞含 HTTP 列而排除；3231 存在相同 URL／timestamp 不同標題或 publisher，
相關矛盾版本全部排除，其他合格列仍保留。partial 不冒充全量新聞、全文查核或市場共識。
三檔現金流／資產負債科目都驗證 MOPS 公司、報表期間與正式申報時間。
現金流查核發現來源以累計期間呈現，程式保留 as-reported，不當單季現金流。
三檔查詢公司行動成功；無減資／分割／面額變更列只代表該次查詢無相關事件，非全歷史保證。
所有文件都重新核對 raw listing／download response／PDF SHA256；真實資料只保存本機。

另外以匿名即時查詢驗證 1101、1216、2317、2454、2881、6488、8069、0050：
上市／上櫃及 .TW／.TWO 識別成功，每檔 2026-10-01 起取得八筆合格日行情，
日期皆在對應 TWSE／TPEx 官方月份交易日內。這八檔另有合成增補資料 Mock 契約測試；
真實檢查限 identity／OHLCV／交易日，不宣稱全資料研究或長期穩定性已驗證。

### 失敗與待驗證

- 匿名還原股價請求被來源拒絕，三檔 ADJUSTED 標記 unavailable，完整增補研究須判資料不足。
  已備妥 main-only Sponsor 增補工作流程；需本 PR 合併後使用已設定的 Secret 驗證，
  不讀取 Token、不把 Secret 提供給 feature／PR。
- 首輪真實新聞查核曾因重複 URL 及 HTTP 列整組排除；已修正為按完整 timestamp 識別、
  明確排除矛盾／不安全列並保留其他資料，新增回歸測試。最初 lint 的 import／lambda／
  dict 排版項目已修正，最後檢查通過。
- 原始財報從未留存的舊更正版不能復原；已提供從現在起保存數值／PDF 多版本與安全重放。
  歷史身份、官方數位簽章、所有歷次舊 PDF 的數值自動解析未實作，不替代目前來源防護。
- 長期 uptime 需要累積不同日期的真實 audit 紀錄；已提供重复稽核入口，不宣稱一天測試
  等於數週觀察。沒有自行設定週期排程。
- 投組成交／費用／淨值與真實下單不在本次研究改造範圍，原版的独立決策評估也不是
  完整投組帳務。尚未到期的觀測結果維持 pending，不以未來假資料補滿。
