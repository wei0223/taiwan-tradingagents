# 缺口結案與資料資格分離

`upgrade --extension-input` 可載入 `gap_closure`；其 `evidence.source_id` 指向
`--review-input` 的 `documents` 原件。驗證原始 bytes、SHA256、官方來源、公司、
捕獲及審查時間後，HTML 與 `gap-closure-review.json` 呈現逐項處置。
不提供此欄位時維持原流程。來源快照 `refresh-sources --review-input-dir`
也會讀取各公司 review input 的 `gap_closure`，保存處置，繼續封存新來源。

每項必須寫明目標文件、缺失欄位、查證範圍、原件及定位、具名審查結論、
排除用途與重新開案所需證據，不能只用網路拒絕或下載失敗判定無法重建。

| disposition | 工單狀態 | 用途限制 |
| --- | --- | --- |
| `recoverable` | actionable／可補齊 | 必須指定可執行下一步，未核對前不授予資格 |
| `alternative_scope` | closed_scoped／可採替代口徑 | 本版支援直接公布的2025年度稀釋EPS作同年度比較，不能填Q4或滾動TTM |
| `unreconstructable_in_reviewed_scope` | closed_scoped／本輪範圍無法重建 | 官方目前清單缺事件／批次與版本bytes身分，排除依賴該證明的歷史用途，不宣稱永遠不存在 |

工單結案不是原件數值認證器。`qualification_promoted=false`，
`original_missing_metric_available=false`，不改 EPS inventory、估值 Gate、
歷史 PE 點及逐期回放資格，也不發布新預測。年度替代不得年減前三季EPS，
不能差分加權股數，不能將基本EPS當成稀釋EPS。
只允許已通過各自財務／期間審查的資料繼續做指定事實研究。

`historical_event_links_pending` 舊欄位仍表示整體來源缺歷史證明，
不代表已結案的指定工單仍在等重試。閱讀工單狀態請使用 `gap_closure`。
嚴格績效仍由 `vintage.knowledge` 與 `pit_validation.replay` 逐起點裁定；
零合格起點就不輸出績效，不把本輪結案當成通過。

離線獨立驗收（output-dir 必須是新目錄）：

```powershell
python -m tradingagents.research audit-gap-closure --ledger closure-ledger.json --review-input review-input.json --output-dir new-gap-audit
```

舊 ledger、預測、發布時間與原件保持不變。新合格官方證據出現時另建審查版本，
可記錄 `parent_ledger_sha256` 作前版關聯；這只是關聯，並非自動授予來源資格。
網路冷卻到期、同清單重抓或報告改寫都不足以重新開案或解除歷史排除。
原始捕獲時間與後來取得的版本證明分開；捕獲新資料不得回填為過去已知版本。

目前實作僅驗證並呈現審查處置，不會自行解析新來源並判定歷史缺口已補齊。
新版本證明仍須經正式 vintage / EPS 審查後才能用於數值或績效。
