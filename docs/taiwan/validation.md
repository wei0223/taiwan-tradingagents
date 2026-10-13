# 分階段驗證紀錄

日期：2026-10-13；上游 0.6.0 / SHA 1394a3f72aa4393e1a98f51b382434c4b4c2d972。
本機 Python 3.13.5，獨立分支 feature/taiwan-market-provider，沒有操作 main 或推送。

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
- 成功：README 更新，新增 Python 3.11–3.14 data-only CI；遠端 CI 尚未執行。

## 環境與失敗

完整 editable/dev 安裝在本機 pip build dependencies 階段未完成；minimal pip 安裝也
持續無進度，已中止。另從 PyPI 官方下載 wheel，驗 SHA256，解壓到工作區隔離
test-deps 執行台股測試（不是修改使用者 Python）。安裝輔助檔僅在 work，不屬交付程式。
實際版本：python-dotenv 1.2.4、requests 2.34.2、pytest 9.1.1、ruff 0.17.0。
最初以 python -m ruff 執行未找到 wheel 的 executable，改用工作區 wheel 內執行檔。
第一輪 lint 發現兩個格式問題，修正後全庫 `ruff check .` 通過。
完整上游 `python -m pytest -q` 在 conftest 的 pytest_configure 階段失敗：
`ModuleNotFoundError: No module named 'yfinance'`（退出碼 3；未收集測試），因此完整
上游 regression 未驗證。22 項台股 unittest 不依賴 yfinance/LangGraph，全部通過。
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
- 完整上游 regression 與遠端 CI 尚未通過；不宣稱完整美股回歸已驗證。

## 重現

只需 python-dotenv（package import）及 requests（live transport）：
`python -m unittest discover -s tests/taiwan -v`。
完整套件安装成功後：`python -m pytest -q`，`ruff check .`。

不要為測試設定真實付費 LLM key；台股 contract tests 不需要任何 API key。
