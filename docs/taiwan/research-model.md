# 財務模型、現金品質、交叉質詢與完整交付

正式 `python -m tradingagents.research upgrade` 共用計算核心，保留原美股路徑與既有封存。
這一層增加可計算的研究能力，不自動把條件情境升格成正式預測、合理價或交易訊號。

## 執行與輸出

```sh
python -m tradingagents.research upgrade --snapshot snapshot.json --review-input review-input.json --analysts analysts.json --extension-input extension-input.json --debate-input debate-input.json --output-dir new-edition
```

可加 `--pdf-font <已安裝的中文字型.ttf或.ttc>` 產生獨立紙本版；選用相依套件為
`pip install '.[research-pdf]'`。不用 PDF 時無需 reportlab，也不呼叫瀏覽器或 LLM API。
報告目錄不可覆寫。`extension.json`、`debate-review.json`、所有輸入、bundle、snapshot、
來源 PDF、分析師文字與封存 release 副本均納入 `evidence.zip`，由
`delivery-manifest.json` 逐檔 SHA256 覆核。PDF 不再是唯一交付檔案。
另行增加圖像／品質紀錄後，必須再用 `delivery.seal(directory)` 更新交付包；不要改原封存。

## 營運到稀釋 EPS

`financial_model.waterfall` 的純函式不含公司專屬倍數或成長率：

1. 營收減變動成本、固定成本及製造折舊，得到毛利。
2. 毛利減變動費用、固定費用與費用端折舊，加其他營業項目，得到營業利益。
3. 加業外、扣稅、扣非控制權益，得到歸母。
4. 加稀釋分子調整，除直接核對的加權稀釋股數，得到 EPS。

非控制權益可用明示金額或合併稅後利益比例，兩者擇一；不能重扣。
稀釋分子調整可為負，不能擅自改為可轉債利息加回。新增股數須有實發時間及加權依據，
授權不視為實發，亦不得由四捨五入 EPS 反推股數。

`extension-input` 綁定 `stock_id`、`as_of`。`financial_packets` 遵循原件審查 packet：
`document_ids`、`values_reviewed`、`reviewed_by`、`locator`、`version_set`、
期間、scope、unit 與數值皆需提供；原件 bytes/hash、公布／捕獲時間及截止時間由既有審查驗證。
證據鍵為 `packet_id.field`。每個 `cases` 項含 `name`、`period`（YYYY-Qn）、`inputs`，
每個 input 含 `value`、`kind=fact|assumption`、`evidence_ids`；assumption 另需 rationale。
fact 必須等於其唯一數值證據，假設不得偽裝實績。

成本分類不明時，可明示將總成本視為比例的機械試算；獨立折舊／固定成本為零只代表
「沒有另加成本」的表達方式，不代表公司沒有折舊或成本全可變，也不能量化擴產因果。
`cost_classification` 必須說明限制。液冷滲透等事件可沿用 `valuation_drivers.event_effect`，
完整來源與量價/市占/替代/固定成本/稅/歸屬/股數不足時只列方向或證據不足。
本層不產生統計信心區間，也不自動封存為正式預測；新預測須另走 publication 流程。

## 公司規則與現金品質

`financial_model.profile` 載入 foundry、thermal、odm 或 generic 研究問題；
預設代碼只用來挑研究清單，不能帶入另一家公司利潤率、成長率、股數或倍數。

`cash_quality` 可接受 `flow`（H1等累計流量）、`opening`、`ending`。
流量包含營收、COGS、合併淨利、CFO、折舊、PPE/無形現金支出、
應收/存貨/應付的帶符號現金影響與籌資淨額。來源仍為相同文件 packet。
開閉餘額必須銜接流量期間，scope/unit/version_set 相同，並說明票據與關係人範圍。
餘額packet使用 `basis=balance`、`as_of_date`，不得繼承流量的period_start/period_end或EPS附註。

CFO/歸母僅保留為描述，明示合併現金與歸母利益的歸屬差異，不作跨業排名。
同期間 CFO/合併淨利另列。FCF 提供 CFO減PPE，以及再減無形支出的兩種明示定義，
不將租賃、預付設備或併購偷偷納入／省略成「全數可分配現金」。
DSO/DIO 採兩點平均，DPO以COGS代賒購只稱代理，CCC同為代理。
間接法資產變動、餘額差與現金回收不同；完整現金/非現金/減損/重分類橋接未核對時，
回款繼續不可用。負CFO是解釋需求，不自動給看空裁決。

## 市場預期與短期驗證

來源報列 PE 歷史可以畫描述分布，但未驗分母時不改稱 TTM 或歷史 Forward PE。
已核對 NTM 稀釋 EPS 才能算條件 Forward PE；單季乘四不可用。
正式入口另要求 forward/trailing 的 `source_packet` 遵循上述原件審查契約，數值與
source_sha256必須一致，forward明示 `horizon_months=12`；不能只附一個hash或勾選旗標。
反向估值為現價/明示PE的所需EPS等式。缺基期EPS不算成長率，不能宣稱市場隱含共識。
歷史分位數作倍數軸只屬敏感度，不代表合理退出倍數或估值校準。

固定 breakout-v1 重播提供5、10、20交易日、交易成本、淨報酬分布、勝率、
單筆持有期間標記收盤最大回撤，以及訊號日相對MA60的行情狀態。
不是投資組合回撤，也沒有保證停損或可成交性。每個期限內避免重疊持倉，跨分割邊界
未成熟結果留 pending；期限間仍重疊，不作獨立樣本。
`backtest` 輸入需 `rule_version=breakout-v1`、實際 `selected_at`、`test_start`、可選成本。
現在才指定的保留區間與現今還原價版本，只是回顧診斷，不能標為全新前瞻樣本外績效。
所有輸出 `empirical_edge_proven=false`；少於30事件加小樣本提示，30不是顯著性門檻。

法人淨額可除以同窗口未還原成交股數。流通股數需獨立核對 actual free float，
缺少時不能用資產負債表股本或稀釋加權股數替代；缺日、缺分類不補零。

## 真實辯論與三位正文

Codex 協調者以新context分別分派多空，先看原件而不看基準估值；保存兩份 opening，
再各回應全部對方論點一次，最後交獨立風險審查。不能用主Agent模擬多角色冒充執行。
`research_debate.validate` 檢查兩個不同ID、fresh metadata、原opening bytes/hash、
reply對手hash、全部論點各回一次、反駁前後立場、反證/信心/改判條件與證據路徑。
ID及旗標是稽核，不是獨立執行證明；仍須保留實際派工與原稿。

`debate-input` 含 `bull_opening`、`bear_opening`、`bull_reply`、`bear_reply`、`risk`、
`judgment`、`opening_file_sha256`、`opening_file_bytes_base64`、`execution_attestation`。
每個階段以 companies 陣列綁定 stock_id，主張用 claim id 對應回應。
JSON path/範圍選擇與PDF sha頁碼需存在，排除的 raw rows 不能被引用復活。
裁判保留 before/after、較強論點、改判證據、未定事項與新資料下可判定條件。

正文仍僅基本面、技術面、籌碼面。`analysts.schema_version=3` 在既有欄位之外，
每位新增 `primary_claim`、`quantified_evidence`、`strongest_counterevidence`、`confidence`。
未校準信心以文字明示，情境範圍不得稱統計信心區間。開場、逐項反駁與裁決保留附錄。
首頁儀表板不重複所有正文，PDF由同JSON獨立排版，含營收與行情圖。
桌面/手機/HTML列印仍須實際驗收，PDF視覺檢查不能代替。

## 本輪驗證

台股離線測試183項通過（原154項加29項模型／證據回歸），Ruff及diff空白檢查通過。
測試包含事實／假設混淆、負稀釋分子調整、NCI、毛利盈虧門檻、現金版本與餘額期間、
未來公布／捕獲排除、估值分母來源、開場不可改寫、反駁完整性與完整ZIP校驗。
實際三家公司仍只有回顧機械情境；不宣稱有校準EPS預測、合理價或前瞻交易優勢。
