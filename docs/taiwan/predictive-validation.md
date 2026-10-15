# 預測校準與時間尺度

本擴充不改寫已封存預測、不呼叫LLM API、不下單。長期EPS／條件估值與5/20交易日訊號分開。

## 營收與walk-forward

`predictive.estimate` 提供三個不同機制，並非三組統計獨立誤差：

- log_trend：最近連續12個月log收入OLS外推，沒有聲稱識別長期季節性。
- monthly_momentum：最近三個月同比倍數平均，乘目標同月去年收入；不補缺月、不遞迴造歷史。
- industry_growth：獨立產業成長×公司暴露比重＋其他業務成長；每個驅動須來源、公布與捕獲時間。

觀測JSON陣列每列需 period=YYYY-MM、value(TWD)、published_at、captured_at、source_sha256、revision_id。
來源hash是資料契約，呼叫者須以原始封存驗證其真實性，單憑一個字串不證明來源可信。
只在公布與捕獲均不晚於cutoff時可供訓練；現今回補歷史值不會因此變成當時版本。
`walk_forward` 於各cutoff重訓，目標實際公布不得早於cutoff；同一fold比較季節性naive基準。
`calibrated_weights` 至少六個三法共同有效fold，按反MAE校準；結果只供後續未觸碰保留集驗證。
評分實績也須在模型發布前已可得，否則拒絕；樣本少或第三法缺失就不產生加權最佳預測。
六fold是預先指定最低門檻，不代表足夠證明統計優勢；重疊目標與多模型選擇仍須另外處理。

```sh
python -m tradingagents.research.predictive_cli --observations observations.json --cutoff 2026-10-15T08:00:00+00:00 --targets 2026-10,2026-11,2026-12 --folds folds.json --output results.json
```

folds為 `{cutoff, targets:[YYYY-MM,...]}` 陣列。CLI不會自動下載或取得API key。
當期產業檔不會偷偷用於過去fold；歷史產業版本須由Python介面按cutoff明確傳入。

## 估值與產業傳導

`valuation_drivers.matrix` 為EPS×PE條件矩陣，不是合理價。
`forward_history` 只接受當時已捕獲的12個月forward稀釋EPS；拒絕以事後EPS取代歷史分母。
TWSE歷史PE為落後型參考，不能轉稱歷史forward PE。
`growth_adjusted` 要求同12個月forward稀釋口徑、獨立業務可比的至少兩同業；
PEG=PE/百分點成長率，成長為零或負時不硬算，比例關係不是估值真理。

`event_effect` 分insufficient_evidence、directional_only、quantifiable_conditional。
液冷滲透率增加5個百分點，不等於公司營收增加5%。必須有市場出貨、滲透差、份額、單機內容價、
替代收入、增量營益率、固定成本、稅、歸母比例與稀釋股數。每個驅動分fact/assumption並連到來源。
缺量價或暴露資料不產生EPS；全為假設時即使可算，仍只稱條件敏感度，不稱已證實事件效果。

## 獨立模型與Q3 nowcast

多空先收到排除基準EPS、PE與對方結論的相同事實檔，分開輸出完整營收及利潤模型，固定hash後才比較。
主程式重算EPS；機制、遺漏變數與共同假設比微調參數幅度更重要。獨立Agent不等於模型準確。
Q3收入已知、獲利未納入時只能稱earnings nowcast；須核對實際公告時間才可計入事前評分。
不能把已結束季度誤標為全季事前forecast，也不能由季度EPS相加冒充IFRS全年稀釋EPS。

## 短期規則

`trade_rules.setup`：連續兩日收盤高於各自MA20；最新收盤突破前20日高點（排除確認兩日）；
最新量達前20日均量1.2倍；失效價取最近10日低點×0.995與收盤減1.5倍14日簡單平均ATR中較低者，
避免極近低點誇大RR；測幅目標=前20日高點＋該20日高低差；
暫估RR至少2。所有閾值是固定研究假設，未宣稱有歷史優勢。
次日真實開盤重新檢查RR，跳空越過失效價或目標就跳過；沒有真實開盤資料不假設成交。
這裡只有setup與次日資格，沒有盤中停損成交模擬器。五／二十日結果、費用與基準要另外評估；
缺當時調整價版本、日曆、資金限制與保留集，不給策略勝率或alpha。缺資料不補零。
