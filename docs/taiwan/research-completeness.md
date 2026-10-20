# 研究完整性、股本、四季模型與驗收

正式研究繼續使用 `python -m tradingagents.research upgrade`。新增的檢查不呼叫付費 LLM、不下單，也不更動既有 release。

## 日常入口

Sponsor workflow 的 extended 模式增加 `--material-events`，以獨立的公開 MOPS transport 保存最近 90 日各類公告查詢。FinMind 的 Authorization 不會轉交 MOPS。原始回應、SHA256、實際捕獲時刻與查詢範圍寫入 snapshot 的 `material_event_audit`。

`acceptance` 將已發現但缺原件的重大公告接入報告。即使資料擷取及日曆驗收通過，`research_ready` 仍為 false；營運成功不代表研究完整、估值成立或預測準確。搜尋列表不能取代數值原件，也不能證明從未發生更正。

完整人工／Agent 原件審查輸入使用：

```sh
python -m tradingagents.research upgrade --snapshot snapshot.json --review-input review.json --extension-input drivers.json --research-plan plan.json --analysts analysts.json --debate-input debate.json --output-dir new-edition
```

`--research-plan` 與 extension 內的同名欄位只能擇一。沒有 plan 的舊版入口保持相容，但報告明示未指定完整性邊界，不能稱即時完整研究或嚴格 PIT 績效。

## 時間與版本

`context` 必須含 `stock_id`、`mode` 與帶時區的 `cutoff_at`。模式有三種：

| mode | 適用範圍 |
| --- | --- |
| historical_replay | 公布及實際捕獲均不得晚於截止；原封存版本不可改寫 |
| retrospective_diagnostic | 允許現今回補舊期資料，但不算當年已知，也不算事前績效 |
| latest_research | 截止須在實際執行前 24 小時內；資料捕獲不得晚於截止 |

日期精度的公告按台北該日結束才可用。公布時刻未知的原件只從已驗證公開捕獲起可知。HTTP 拒絕頁、明示失敗狀態及未驗證公開捕獲不具公告資格。文件內容需綁 SHA256 與公司。

`inventory` 列重大事件及原件 id；`coverage` 分別聲明 earnings、capital、monthly_revenue、corrections 的官方查詢、範圍與審查。已發現公告缺件或類別未覆蓋即標 incomplete。已公布獲利季度不得成為四季預測標的。通過也只限所聲明搜尋範圍。

原 provider 的每日資料維持次日可用。最新研究另提供 `latest_market_observation`：只能用當日台北 16:30 後、截止前的公開捕獲資料，保存來源、原始列雜湊及未還原價格口徑；不改寫原 snapshot 的指標、不回填為歷史行情初版，也不補發交易訊號。此時間規則不是官方公布時刻的證明。

## 股本與稀釋

`capital_model` 分開保存授權、核准、發行、註冊、股款憑證／普通股掛牌、庫藏及註銷。掛牌日不自動充當 EPS 認列起日。缺認列審查時，加權股數不可用。

已發行股數、庫藏股、基本加權及潛在稀釋增量分開。庫藏股註銷須同時扣除已發行與庫藏，不能再減一次流通股。已轉換的同事件／證券不能再加入潛在股份。反稀釋測試與分子調整需 EPS 附註，不由四捨五入 EPS 反推股數。

`quarterly_capital` 須精確對應季度起訖；期初股數需前一日的 issuer 範圍來源、庫藏股與逐季分母／分子調整相符，才可聲明股數銜接。單一季度或全年分母不能代替其他季度。F03 的 issuer 股本資料不當作合併財務損益或自由流通股。

## 四季、估值與產業傳導

`forward_scenarios` 的 bear/base/bull 每個情境各有四個有序季度，每季使用共同的損益 waterfall。每個輸入必須是來源相符的 fact、說明依據的 assumption，或值為 null 且具原因的 unknown。缺必要變數時只計算仍可識別的損益橋，稀釋 EPS 及四季 EPS 和維持不可用。

四季稀釋 EPS 的相加僅是條件指標，非正式年度 EPS。反向估值可由現價／假設 PE 解所需 EPS、營收倍率及共同毛利率變化；其餘假設固定，不是已觀測市場共識或合理價。未知基準不強算成長。公司指引與一致預期必須同公司、幣別、期間、稀釋口徑及已核對數值，否則不比較。

營收包含季節單純基準、12 個月對數趨勢、近三月同比動能。按 1–12 個月各期限的共同歷史起點比較 MAE；足夠觀測時用反 MAE 權重，否則等權。這些權重在本次現行版本上擬合，只是回顧診斷，不是當年可部署權重或真正樣本外成績。方法最小／最大不是信心區間。

營運公式分三類：

| 類型 | 收入與成本／現金驅動 |
| --- | --- |
| foundry | 晶圓產能×稼動×先進／成熟製程 ASP，加上不重複的封裝收入；現金成本、折舊、海外增量成本分列 |
| thermal | 伺服器量×供應份額×液冷／氣冷 ASP；材料耗用按良率，檢查良品產能，固定成本與折舊另列 |
| odm | AI／其他出貨×ASP 與單位成本；應收、存貨、應付及可用現金推估資金缺口，融資成本進業外 |

`operating_scenarios` 若聲明產業模型已傳導，逐季營收與 COGS 必須與 waterfall 相等；ODM 融資費用須在業外扣一次。否則拒收。沒有這項銜接的模型明標 top-down sensitivity，不能包裝成產業因果預測。缺量價、良率、產能或客戶資料即保留未知。

## 逐期回測與辯論

固定規則、成本、股票池與年度測試段先保存計畫 SHA。每檔價格與日曆另綁雜湊。重播 5／10／20 日結果保存逐筆進出、成本、報酬、單筆回撤及市場環境。以公司＋訊號日計獨立進場識別；不同期限與同業不是獨立樣本。

少於 30 筆不呈現勝率或報酬分布摘要，改列筆數及單筆；30 筆也只准描述，不能因門檻達成便宣稱統計優勢。零價／無可成交 OHLCV 不進模擬。當下下載的歷史還原價，不能靠 caller 的 PIT 布林旗標升格為真正樣本外。

`independent_source_update_v1` 保存兩位 Agent 原始開場及反駁 bytes、互相綁定的 SHA、引用文件、獨立風險審查、裁決與前後立場。這種更新回合必須揭露舊研究接觸，不能冒充全新盲測。原 fresh-context 協定保持嚴格要求。Agent id／hash 是稽核線索，不是獨立執行的密碼學證明；協調者仍須實際派發不同 Agent。

## 交付與限制

PDF、HTML、source-ledger、數據、計算、假設、Agent 原稿、辯論、品質檢查與原件應一起封裝。新版本用新目錄與實際產生時間；原 release 的數值、時間及版本不變。測試綠燈不會把未知營運參數變成事實。
