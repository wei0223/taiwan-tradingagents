# V6 來源修復與模型資格

正式 `upgrade` 在具備完整研究計畫時自動輸出：

- `resolution-queue.json`：公告、索引範圍及歷史版本缺口分開；每筆具有識別、時間、來源、修訂鏈、影響、優先級、原因、動作及狀態。完整識別才去重，共用清單 URL 不合併不同公告。
- `source-repair/resolution-run.json`、`attempts/`：逐筆處理紀錄，預設只查快取。原件取得後仍為 `ACQUIRED_PENDING_REVIEW`；數值影響與版本未審查前不解除 P0。
- `current-output-registry.json`：當前數值、單位、資格、model_version、calculation_run_id、input_snapshot_id、source_as_of 與 status。HTML／PDF 的條件模型從同表讀取，舊現股商數不再充當當前模型狀態。
- `v6-review.json`：分層 Gate、逐參數五種驗證、估值資格、逐期資料隔離結果與辯論觸發判定。

## 原件補抓

```console
python -m tradingagents.research resolve-events --research OUTPUT/research.json --review-input OUTPUT/review-input.json --output-dir OUTPUT/source-repair --live --max-documents 1
```

只擷取已驗證索引中的官方 URL，不使用 Token，不跟隨重導，不替換網域繞過限制。
每次預算上限 10 份；403／429 停止同站後續請求，24 小時內保持冷卻。原始回應及雜湊另存，既有嘗試不可修改；預算延後不是成功，也不造成網路冷卻。
`resolution.execute` 接受注入 client，測試不需要外部服務。

P0 影響當前財務或分母時阻擋正式估值，指定事實摘要可以 PARTIAL；歷史版本缺口單獨限制嚴格回測。交易資料 Gate 與交易策略統計優勢分開。
只有直接使用者授權、列明識別、理由、核准者和有效期的豁免才能被可信呼叫端傳入；模型自行填的旗標無效。豁免仍不能建立合理退出倍數或交易優勢。

## 驗證不能混稱 PASS

每個情境／季度／參數分列 `schema_valid`、`source_supported`、`assumption_reviewed`、`historical_calibrated`、`forward_validated`。
分類限 observed、company_guidance、statistical_estimate、judgment_assumption、stress_only，保存 as_of 與信賴依據。來源錨點可用不表示未來參數已成實績；未來季度不得標 observed。「基準條件」沒有最可能或機率校準的含義。

目前沒有來源綁定的校準或前瞻實績，後兩項維持 FAIL。稀釋股數的條件試算、工具認列資格、實際已發股數互相獨立。
`pit_validation.attribution` 可在可比實績經來源審查後分解營收、毛利、費用、稅、歸母、稀釋分子與股數的差異，另列 EPS 分子／分母精確橋及四捨五入殘差；已先公布的實績拒絕列績效。此函式本身不證明來源審查完成。

## 估值口徑

分開保存供應商報列未驗證歷史 PE、目前四季直接稀釋 EPS 和的價格參考、嚴格 PIT 歷史 PE、條件 Forward PE 和條件價格矩陣。
目前資料核對成功不能回填當年可用時間。歷史 PE 的逐點價格／EPS／股本事件核對沿用 V5 驗證；缺直接季 EPS 或當時版本的點保留拒絕原因，不以供應商倍數填補。
反向 EPS／營收／毛利率門檻及單因子敏感度保留；未驗證 PE 軸不是公平退出倍數，不產生合理價或低估排名。

## 固定時間隔離

`pit_evaluation` 包含原始營收列、數值雜湊、修訂識別、公布與捕獲時間，以及帶雜湊的固定 plan／folds。
每個起點只允許公布與捕獲均不晚於起點的版本。訓練期凍結季節、對數趨勢、月動能的反 MAE 權重；測試實績不回饋權重。保存每個起點輸入快照、模型、目標、預測及選定初版實績，輸出 MAE、MAPE、sMAPE、偏誤、絕對誤差分位數與 n。
實績也必須與原始數值列一致，初版證據不足保持 pending。模型今天才定義，即使歷史輸入齊全也不是歷史正式發布預測。

三家公司現有 130 個月資料均為 2026 年回補捕獲，2018–2021、2022–2025 兩段因此沒有可用當時版本。兩段執行檢查不等於兩段真實 PIT 回測通過，不產生 forecast_hit_rate。合成資料只驗證程式隔離，不能充當金融績效。

交易回顧與財務模型分開；費用後收益、收盤標記回撤及每日低價的保守最大不利界限分列。日線不能證明出場日的盤中先後，保守界限可能包含出場後低點，不能冒充精確盤中 MAE。樣本不足與還原價回補標籤保留。

新原件必須公司／版本／時間與 bytes 綁定、具名數值複核與定位，且與既有事實有重大差異，才回傳辯論待觸發訊號；相同 change_id 不重複處理。此機制不自動呼叫付費 LLM，也不改寫既有發布紀錄。
