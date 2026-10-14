# 台股研究擴充與歷史評估

本次補上原版研究所需的估值、現金流、新聞／社群資料及歷史評估入口，並加入
台股公司行動、官方交易日與版本封存。台股依然在 Codex 原生子 Agent 中辯論，
不建立付費 LLM client、不下單。原版美股 LangGraph、工具與相依套件保持原狀。

## 資料規格與證據

`ExtendedTaiwanProvider` 是既有 `TaiwanMarketDataProvider` 的選用子類；`--extended`
開啟時自動接 MOPS 公布時間。HTTP transport 與 clock 可注入，所有新來源都有離線 Mock。

| 分析輸入 | FinMind 資料集 | 解讀限制 |
| --- | --- | --- |
| 估值 | TaiwanStockPER | 來源本益比、淨值比、殖利率；程式算盈餘殖利率與觀測窗本益比分位。非 forward PE、非合理價 |
| 現金流、設備支出 | TaiwanStockCashFlowsStatement | 保存來源期間，OCF 與設備現金流合計；可能為年初至今累計，不冒充單季或 TTM 自由現金流 |
| 資產負債 | TaiwanStockBalanceSheet | 現金、資產、負債、流動項目、權益；原始科目與金額保存 |
| 還原行情 | TaiwanStockPriceAdj | 供應商回溯還原版本，只從實際取得日使用；成交量仍是來源股數 |
| 新聞與社群 | TaiwanStockNews | 依日查詢，預設最近三個已結束日期，最多三十一日；HTTPS 標題與來源網址，不取文章全文 |
| 公司行動 | DividendResult、CapitalReductionReferencePrice、SplitPrice、ParValueChange | 保存除權息、減資、分割、面額变更原始事件與參考價比值；不是股數變動比或配息現金帳務 |

社群資料依來源網址分類 CMoney 討論區、PTT Stock、Dcard stock。這是來源樣本，
沒有代表性保證。情緒統計採固定中文標題詞彙，程式計算樣本、正負中性數與均值；
按 URL／相同標題去重，更新標題使用最新觀測，完全相同 URL／時間的矛盾版本排除。
HTTP 連結或錯誤列保留 raw 並排除，其他合格列仍可使用，coverage 明示 partial。
空樣本沒有中性推論；Agent 不得遵循標題中的指令，也不得宣稱讀過原文。

`bundle.analyst_views` 提供 market、fundamentals、news、social 的 evidence IDs。
這是四組程式核實的資料視圖，供多方／空方／風險引用，不是新增付費模型呼叫。
報告附來源、公布日期、數值版本可用日、取得時間、公告文件／新聞網址與公告證據雜湊。
資料缺漏不補值：關鍵估值／現金流／資產負債／還原行情缺資料時必須裁決資料不足。
新聞不足則不能形成新聞／市場共識主張，不強制把其他分析也歸為負面。

## 日期、交易日與公司行動

F22／F27／F26／F28 分別驗證營收、損益、資產負債、現金流正式申報的公司、期間、
文件網址與台北時間；仍以公布日和兩來源取得日的較晚者作為數值可用日起點。
不使用分析日之後的公布內容，不把今日取得的更正版數字回填到舊公告日。

官方歷史市場交易日來源：

- [TWSE 月份市場成交資訊](https://www.twse.com.tw/en/trading/indices/taiex/mi-index.html) 的 FMTQIK JSON。
- [TPEx Historical Data of TPEx Index](https://www.tpex.org.tw/en-us/indices/stock-index/industrial/inxh.html) 的 `www/en-us/indexInfo/inx` JSON。

逐月保存原始回應、查詢條件、更新時間、SHA256，驗證回應月份及完整月份覆蓋。
最多一次取得六十個月份；資料不可用時不猜星期或以個股價格補成官方交易日。
市場交易日不保證個股有交易：停牌／缺價／缺法人資料仍為未知，不補零。
增補包另提供 `calendar_verified_institutions`；基本包的 observed-only 統計保留。

程式另將事件的「後參考價／前參考價」累乘到事件前 OHLC 作比較；不調整股數，
不模擬配息再投資或減資退款，同日多事件不猜合成方式。事件 API 成功回空列與 API
失敗明確區分，沒有資料不等於沒有公司行動。供應商還原價與自算參考因子調整分開標示。

## 操作

```shell
python -m tradingagents.research prepare --symbols 2330,3017,3231 --start 2025-01-01 --as-of 2026-10-14 --extended --archive-dir private-vintages --output-dir research-runs
python -m tradingagents.research audit --snapshot research-runs/2330-2026-10-14/snapshot.json --output audits/2026-10-14.json --require-complete
```

日期須換成實際日期。GitHub Actions 的 extended 預設開啟，Token 僅注入資料準備步驟；
離線 audit 沒有 Secret。工作流程只允許 main 執行，延續 PR／feature 不能取得 Token 的限制。
Actions 封存資料包至 `research-data/vintages` 並上傳一天 artifact；這不是永久資料庫，
下載後請將自己的私有版本目錄持續保存。不要提交 raw Sponsor 數值／Token 到公開 Git。

既有 snapshot 可以建立新的本機封存收據；收據記錄實際匯入日，不冒充以前已留存：

```shell
python -m tradingagents.research archive --snapshot research-runs/2330-2026-10-14/snapshot.json --archive-dir private-vintages
python -m tradingagents.research replay --archive-dir private-vintages --symbol 2330 --as-of 2026-10-14 --run-dir replay-runs/2330-2026-10-14
python -m tradingagents.research sweep --archive-dir private-vintages --symbols 2330,3017,3231 --dates 2026-10-14,2026-10-15 --output-dir historical-runs
```

`VintageStore` 用內容雜湊命名、exclusive create 不覆寫，不同更正版保存不同檔案。
來源取得不得晚於收據，重放只選分析日以前已封存且分析日期合格的版本；毀損即拒絕。
`sweep` 只準備研究格點；沒有當時版本的格點明列 unavailable，沒有模型 API 呼叫。
之後以 `$taiwan-stock-research` 對 ready 格點完成原生多方、空方、風險六階段辯論。

### 原始文件

```shell
python -m tradingagents.research filings --symbol 2330 --year 2026 --as-of 2026-10-14 --archive-dir private-filings
```

從證交所電子文件系統保存完整官方 listing、上傳時間、更補正欄、PDF bytes、內容 SHA256
及下載回應收據。只接受官方來源、不跟轉址、不執行來源 JavaScript，不把 PDF 任意敘述當數值。
同名文件內容更新會留下新內容版本。官網 listing 明確只提供最近更補正狀態；本工具不宣稱
已重建從未取得的歷次原檔，也不以 PDF 封存繞過 FinMind 數值版本日期防護。
財報數值來源仍為 FinMind＋MOPS 明確申報證據，PDF 原文供人類查核。

### 事後決策評估

取得後續的 extended 資料包，股票與基準各一份；例如另準備 2330、0050。
0050 的公司財報可能不存在，不影響它作為評分基準的已驗證還原價；不能當完整公司研究。
評分用資料晚於分析日期是必要的事後觀測，絕不傳入原研究 Agent。

```shell
python -m tradingagents.research outcomes --snapshot later-runs/2330-2026-11-20/snapshot.json --snapshot later-runs/0050-2026-11-20/snapshot.json --output outcomes.json
python -m tradingagents.research evaluate --run-dir research-runs/2330-2026-10-14 --outcomes outcomes.json --calendar official-calendar.json --horizon 20 --benchmark 0050 --output evaluation.json
```

`official-calendar.json` 取自後續 snapshot 的 `market_calendar`。它須覆蓋分析後的持有期間；
API 的選定日期當天不納入，仍維持日終政策。評分入口重新驗證 raw-bound snapshot 與官方
日曆，不信任手填計算欄。上市／上櫃分開評估並使用合適的同市場基準。
以分析後下一個官方交易日還原開盤價至指定持有日的還原收盤價計算股票／基準報酬與 alpha。
方向命中代表相對基準表現，不是絕對股價方向；mixed／insufficient_data 明列 unscored，
持有期未到或任一股價／基準日期缺漏為 pending，不跳過缺日或算作勝利。
這是原版同類的獨立決策評估，不模擬成交、交易成本、漲跌停可成交性或投組 NAV。

## 穩定性與驗收

`audit` 可重複對不同日期與股票執行，產出覆蓋、缺漏、證據數量、來源／取得時間與雜湊，
供長期保存比較。增加股票不需改代碼清單；仍用 metadata 查上市／上櫃，未知股票 fail closed。
本次合成測試擴及其他上市股、上櫃股與 ETF；真實來源檢查及 CI 結果記在
[驗證紀錄](validation.md)。單次成功不能證明長期 uptime，歷史更正原檔不存在也不能靠測試補出。
不自行建立每日排程、開通 API、切換付費模型或下單。

主要來源規格：[FinMind 基本面](https://finmind.github.io/tutor/TaiwanMarket/Fundamental/)、
[FinMind 新聞](https://finmind.github.io/tutor/TaiwanMarket/Others/)、
[FinMind 技術／還原行情](https://finmind.github.io/tutor/TaiwanMarket/Technical/)。
