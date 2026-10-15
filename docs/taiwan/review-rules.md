# 日常研究審查與原預測追蹤

`upgrade` 與 `acceptance` 均自動產生四項獨立裁決及 `review.json`。
Sponsor 工作流程原本的 acceptance 步驟直接沿用，不需要增加 Token 或 LLM。
正文維持基本面、技術面、籌碼面；沒有經核對的原件不補造分析。

| 問題 | 計算與結論邊界 |
|---|---|
| 會計獲利 | 同公司、同季同比歸母與附註精算稀釋 EPS：皆增加、皆未增加、分歧、不可用。不是股票總報酬。 |
| 現金持續性 | 同年相鄰累計金額、同範圍／單位／核對版本才差分。另列原累計及同期間同比 CFO 絕對額／營收比；不能推論已回款或可持續。 |
| 估值 | 未建模型不可用；已有模型僅列條件價格，不將假設 PE 或情境價格稱合理價，也不從負 CFO 推論高估。 |
| 短期交易 | 使用既有 `trade_rules.setup` 的固定均線／突破／量／暫定 RR 規則，獨立於長期 PE 模型。候選仍待下一實際開盤重算，沒有自動下單或勝率保證。 |

不使用 3231 的利潤率、股數、少數股權比率或估值倍數作為其他公司的門檻。
`research.json.decision` 保留舊介面相容性；正式分項裁決讀 `review.axes`，
不能把舊的組合候選旗標當短期交易訊號。

## 每次另建輸出目錄

```shell
python -m tradingagents.research upgrade --snapshot private/snapshot.json --review-input private/review-input.json --analysts private/analysts.json --output-dir research-runs/new-edition
python -m tradingagents.research acceptance --snapshot private/snapshot.json --review-input private/review-input.json --output-dir research-runs/new-acceptance
```

`--review-input`、`--analysts` 可省略。省略審查原件時，會計／現金裁決明列不可用，
不能將供應商假定的單季 EPS 或未知現金流口徑升格為已核對原件。
`analysts.json` 仍是 fundamental、technical、institutional 三個段落陣列；
分析師須針對分項裁決提出論述，不能混成單一多空勝負。
輸出目錄已含 report.html、research.json、snapshot.json 或 release-pointer.json 時拒絕覆寫。

生成的 `review-input.json` 保存完整原件 bytes、原始公布與捕獲時間、版本與頁碼；
`review.json` 保存計算、缺口、條件與輸入雜湊。保留來源而非只留 LLM 摘要。
這是額外的語意驗收，原本來源稽核／MOPS／行事曆檢查仍執行。
運作驗收通過不代表財報資料齊全、預測準確或策略有超額報酬。

## 審查輸入契約

頂層為 `stock_id`、有時區的 `cutoff_at`、`documents` 來源字典，及下列選用 packet。
`cutoff_at` 須位於報告資料日內，且不得晚於實際建立時間。缺項不可用，不當成零。

每個來源需 `stock_id`、HTTPS `source_url`、`revision_id`、`sha256`、
`content_base64`、`published_at`、有時區且精確到時間的 `captured_at`。
程式驗證 bytes 雜湊，公司與可用時間。日期精度的公布日保守於該日結束才使用。
公布／取得任一晚於截止時間即排除。

若官方目前版本的原始公布時刻未知，只有在審查者核實公開下載且明示
`publication_time_status=unknown_current_revision`、`public_capture_verified=true`
時，才以本次捕獲時間作「目前已知」的下界。公布欄仍保留 null。
這不是初次公告日證明，不能回填至捕獲前，也不能憑此評分初版預測績效。
沒有公開取得證明的未知公布日仍不可用。

每個 packet 共用：

- `stock_id`、`scope=consolidated`、`unit=TWD_and_shares`（原件仟元／仟股先各乘一千）。
- `document_ids`、原始表格 `locator`、`values_reviewed=true`、具名 `reviewed_by`。
- `version_set`：審查者核對的可比版本組，不是把兩個下載檔名改相同。
- 財務 packet 另含 `period_start`、`period_end`，必須正式季末；不得用月營收合計當財報營收。

### 會計獲利

`earnings` 和 `prior_earnings` 需同季、相隔一年、相同可比版本組，
`basis=single_quarter_diluted`、`diluted_note_verified=true`。
`values` 包含 `revenue_twd`、`operating_profit_twd`、`parent_profit_twd`、
`diluted_numerator_twd`、`numerator_adjustment_twd`、`basic_weighted_shares`、`diluted_weighted_shares`。

`dilution_components` 是 `{event_id, weighted_shares}` 陣列；確無潛在稀釋也須明列空陣列。
程式核對基本加權股數加潛在效果等於稀釋加權股數，歸母加分子調整等於稀釋分子。
事件 ID 重複、或同一事件又列入 `incremental_event_ids` 會拒絕。
不能用期末股數、四捨五入 EPS 反推股數、差分半年 EPS 或固定稀釋折扣替代附註。

### 累計現金流轉單季

`cash_ytd`、`previous_cash_ytd`：`basis=ytd`，期間起日同年一月一日，
結束日必須相鄰季末，`values` 至少有 `cfo_twd`、`revenue_twd`。
Q1 本身即單季；其餘季別需 `bridge_verified=true`、`bridge_locator`，
且前後公司、範圍、單位、`version_set` 全相同才相減。
如果本期已更正前期，先核對新版前期金額及版本橋接，不拿舊值硬減。

缺前期時單季不可用，已核對的累計值仍另列 `ytd_observation`，不丟掉已知事實。
選用 `prior_year_cash_ytd` 須同月數、前一年及同可比版本組，
同時列絕對 CFO 變化與 CFO／營收比變化，避免把規模增加當全面惡化。

`collection_confirmed=false`，`collection_evidence.status=unavailable`：
目前不自動認證回款。需另外保存逐項回款、現金流、非現金、減損及重分類橋接；
餘額下降或 CFO 改善不足。也不自動對持續性作升評。

### 募資、實際股數與附註

`capital_events` 使用相同來源契約。`event_status=authorized` 只顯示授權，不加股數。
`issued` 必須具備 `event_id`、`issue_date`、正整數 `actual_new_shares`、
`completion_locator`，生效日不得在截止後。
發行日在新財報期間後，顯示等待後續 EPS 附註；不將整季都視為已稀釋。

若發行日在已核對季度內，還需 `eps_note_reconciled=true`、`eps_note_locator`、
財報 packet 的 `basic_share_event_ids` 對應同一事件，及
`share_register_bridge={opening_shares, closing_shares, other_net_shares, locator}`。
程式核對期初加實際發行加其他淨變動等於期末。
實發普通股與潛在可轉債效果不能以同一事件重複列在兩邊。
期初已存在的舊發行需另做期初股冊橋接，目前不自動判定。
這些是已發生資料核對；本版不自動新增 EPS 預測或推算融資未來報酬。

**信任邊界：** 具名審查者仍須負責原件公司映射、數字抄錄、事件歸屬與版本可比性。
布林值及雜湊不能取代這些核對。程式檢查算式、日期與契約，不宣稱已自動理解所有 PDF。
可執行合成範例見 `tests/taiwan/test_review_rules.py`；其中數字不是公司研究資料。

## 追加原預測追蹤

```shell
python -m tradingagents.research track-release --release private/releases/STOCK/HASH.json --actuals private/selected-actuals.json --output-dir research-runs/new-tracking
python -m tradingagents.research upgrade --snapshot private/new-snapshot.json --review-input private/new-review.json --release private/releases/STOCK/HASH.json --actuals private/selected-actuals.json --output-dir research-runs/new-review
```

沒有新實績可省略 `--actuals`，明確保留 pending。
`read_release` 先驗證封存內容；不以新資料重跑模型覆蓋原預測或發布時間。
指定原 release 又傳不同模型會拒絕；改模型須另建模型版本及新發布紀錄。
評分只讀原 `predictions`，追加不可覆寫 tracking 檔；相同內容重試可重用回執。

實績列須明選 `revision_id`，含 `stock_id`、`period`、`metric`、`basis`、`value`、
`published_at`、`captured_at`、`source_url`、`source_file`、`source_sha256`。
原始 bytes 必須匹配。指標／期間／口徑完全匹配才評分；公布早於或等於原發布時間則排除，
公布／取得晚於檢查時間則待確認。

初次公告主評分另外要求：`vintage_kind=initial`、`initial_publication_verified=true`、
`source_value_reviewed=true`、`initial_publication_history_verified=true`、
`revision_policy=first_official_release`、`official_source_reviewed=true` 及 `source_locator`。
這些是審查者對原件與初版歷史的明確聲明；缺一則只可列所選版本診斷，不能混入主評分。
更正版獨立追加另一份選版清單及 tracking；同一指標重複版本不擇優、不默選。

MAE／WAPE 分模型、指標及口徑；另列所有模型共同公司／期間的配對比較。
WAPE 分母為零時不可用。月營收加總與正式季報營收不同；月份與其季合計重疊，
不能把三法乘四筆稱十二個獨立樣本。沒有新公告就沒有準確度結論。

## 驗收

```shell
python -X utf8 -m unittest discover -s tests/taiwan -v
```

`-X utf8` 避免 Windows 系統預設編碼造成既有測試讀取中文回執失敗。
新增測試含 2330／3017／3231 的差異、未來資料、跨季與版本不符、稀釋重扣、
授權與實發、股冊不符、初版與更正、月季口徑、封存不變及禁止報告覆寫。
真實 Sponsor 資料不提交公開 Git，離線已捕獲資料驗收亦不冒充重新擷取成功。
