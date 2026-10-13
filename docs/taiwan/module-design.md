# B. Taiwan Market Data Provider 設計

## 邊界與分層

第一版在 `tradingagents/dataflows/taiwan/` 新增模組。
`FinMindClient` 注入 transport、clock；`TaiwanMarketDataProvider` 注入 client，
對外提供 resolve_symbol / fetch / snapshot；`MarketDataProvider` Protocol 是未來
TWSE、TPEx、MOPS adapter 的共同契約。`python -m tradingagents.dataflows.taiwan`
僅取資料與 JSON 匯出，不初始化 LLM、不呼叫下單介面。

上游 Graph、AgentState、DEFAULT_CONFIG 及美股路由保留。下一階段才新增台股 profile、
台股專用工具與統一 snapshot/identity/settlement provider 入口。不能只修改
VENDOR_METHODS 就宣稱 Graph 全部已切換 FinMind。

## Instrument identity

以 stock_id 字串保留前導零；接受 bare numeric、`.TW`、`.TWO`。
從 TaiwanStockInfo 最新日期的 `type=twse/tpex` 判斷；不同市場同日衝突要拒絕。
FinMind 使用 bare stock_id；Yahoo 上市 `.TW`、上櫃 `.TWO`。
後綴與 metadata 不一致要報错；未知／興櫃 MVP 明確拒絕，不預設上市。
2330、3017、3231 使用 synthetic 上市 metadata 做 contract test，不能把測試假資料說成 API 實測。
另有上櫃測試，不能只测三個上市代碼。
metadata 日期、來源、更新時間應與 identity 一起匯出；歷史市場別另需生效區間。

## 資料契約與 point-in-time

每筆記錄：`dataset`, `stock_id`, `data_date`（原始 date）、`period`、
`published_at`（nullable）、`retrieved_at`（UTC ISO timestamp）、`source`、
`publication_basis`、`raw`（完整 API row）、`values`（標準化）、`units`。
Result：records、status（ok/partial/unavailable）、warnings、分析日期及 query。
缺資料為 null/空 records + 原因，不填零、不让 LLM 補值。原始回應（含被排除列）
保存到 result.raw_rows；CLI JSON 同時保存，方便核查。

as_of 為 Asia/Taipei 的「當日結束後」研究語意。日資料僅使用嚴格早於 as_of 的
交易日，因來源缺當日公布時間，保守延後一日可用；published_at 保持 null，
publication_basis 表示 daily conservative availability，絕不把交易日冒充真實公布日。
盤中與精確時點研究未實作。

財報及營收只有可信 `published_at`（由日後 MOPS adapter／archive 提供）才准入；
FinMind period `date`、create_time／擷取時間都不自动升格為公布日。
未知 publication 仍保存 raw，但排除於 values 及 growth。未建立 revision archive 的
FinMind 歷史 fundamentals 不宣稱完整 PIT，預設會缺資料，這是保護而不是補日期。

## 資料集／計算

| dataset | MVP 標準值 | 計算／限制 |
|---|---|---|
| TaiwanStockPrice | open/max/min/close/Trading_Volume → OHLCV | TWD、股、未還原。檢查 finite、OHLC 範圍、非負 volume；日期先过滤再計算。 |
| TaiwanStockMonthRevenue | revenue、revenue_year/month | period=YYYY-MM；YoY 比對前一年同月，正基期才計算。缺公布日排除。 |
| TaiwanStockFinancialStatements | type/value | EPS、Revenue、GrossProfit、OperatingIncome、IncomeAfterTaxes 映射；本期與同比都准入才算 EPS QoQ/YoY；利潤／營收比率程式計算。缺公布日排除；MVP 不做未核實的 YTD 轉單季。 |
| TaiwanStockInstitutionalInvestorsBuySell | name/buy/sell | net=buy-sell 股；外資含外資自營商、自營商合併或自行+避險擇一、投信；需完整組別，未知類別保留 raw 並警告。 |

技術指標：SMA 5/20/50/200；RSI 14 使用 Wilder smoothing，前 14 個變動為 seed；
MACD EMA12−EMA26、signal EMA9、hist=line−signal，EMA 首次 SMA seed；
不足 warm-up 時 null；成交量及 SMA20；所有算式纯 Python，輸入為已過濾 OHLCV。

法人連買／賣：以可用價格日期為交易序列；逐日淨額 sign 同向累計至零、反轉或缺列，
輸出 signed_days、streak_net_shares、window_net_shares、left_censored。
不可把觀測不到的交易日跳過，窗口累計明示覆蓋起迄及不完整狀態。

## 錯誤、安全及擴充

API 使用 requests（既有依賴），固定 HTTPS endpoint、20 秒 timeout、最多單次 request
（使用者可自行重試）；HTTP/API status、JSON schema、symbol mismatch 都明確失敗。
token 只讀 FINMIND_TOKEN，透過 Bearer header，不在 query/log/report 中寫入。
沒有登入、付費方案開通或 LLM 調用。

未來 registry 用市場+capability 路由，TWSE/TPEx 負責價格、交易曆、法人；MOPS 負責
真實公布時間、單季/累計尺度、修訂版本。回應欄位不綁 FinMind；raw 保留來源差異。
source switch 不能吞掉 quality warning；cache key 納入 provider/schema/query/as_of/vintage。

## 測試與驗收

A：讀 source、dependency manifests、CI、existing tests；B：審核資料日期及旁路。
C/D：全網路 Mock；2330/3017/3231 bare/suffix、上櫃、衝突／未知、空資料、HTTP quota、
auth、無 publication、未来 publication、warm-up、已知計算、法人缺交易日、原始 metadata。
E：新測試適用上游預設網路封鎖；另用 minimal test directory 支援無 LLM SDK 的環境；
README 提供無 LLM CLI 及實作限制。全上游測試需成功安装完整宣告依賴才可執行。

## 官方格式參考

- [FinMind 技術面](https://finmind.github.io/tutor/TaiwanMarket/Technical/)
- [FinMind 基本面](https://finmind.github.io/tutor/TaiwanMarket/Fundamental/)
- [FinMind 籌碼面](https://finmind.github.io/tutor/TaiwanMarket/Chip/)

來源格式查核日 2026-10-13；API 實際回應才是最終驗收依據。
