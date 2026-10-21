# 公告原件擷取與驗收

研究流程共用 `announcement_coverage.reconcile`。原件通過條件包含公司、索引所指 URL、原始 bytes 的 SHA256、HTTP 狀態、本文身分及期間／主旨；不能只勾 `reviewed`，也不能把 HTTP 200 空白頁當原件。這個步驟核對文件內容，並不自動核准財務數值影響。

## 日常步驟

1. Sponsor extended 流程繼續保存各類 MOPS 公告索引。不讀取或轉傳 FinMind Token 給 MOPS。
2. 將索引 receipt（含 `query`、`source_url`、`content_base64`、`sha256`、HTTP 與捕獲時間）另存為 `index.json`，自願啟用公開原件擷取：

```text
python -m tradingagents.research capture-originals --index index.json --max-documents 50 --output-dir new-capture
```

預算為 1–100 份、同一 URL 只擷取一次。HTTP 403／429 或來源明示查詢過頻時停止新增擷取，保存失敗回應與未處理事件；不重試繞過限制。新目錄必須不存在，既有原件不覆寫。公開 transport 可注入 Mock，無付費 LLM 呼叫。

3. 用 `capture.json` 的 `documents`、`inventory` 合併已核對的舊原件，明示 `context` 與 `coverage` 形成 `audit-input.json`：

```json
{
  "context": {"stock_id": "3017", "mode": "latest_research", "cutoff_at": "帶時區的實際截止時間"},
  "documents": {"index": "完整索引 receipt；此處不是字串或檔案路徑", "original-000": "完整原件 receipt"},
  "inventory": [{"id": "索引事件 identity", "document_id": "original-000", "original_url": "索引完整 HYPERLINK", "published_at": "索引公告時間"}],
  "coverage": [{"category": "earnings", "document_id": "index", "from_at": "帶時區起點", "through_at": "等於 cutoff_at"}]
}
```

上例只說明欄位形狀；documents 必須填入實際物件，不能用說明文字替代。coverage 須分別列 earnings、capital、monthly_revenue、corrections。公告時間是事件索引時間，不代表現今下載原件的最初版本公布時間。

```text
python -m tradingagents.research audit-announcements --input audit-input.json --output-dir new-audit
```

輸出 `announcement-audit.json` 與完整輸入。離線驗收不呼叫網路、不改模型、release 或舊報告。`upgrade --review-input ... --research-plan ...` 會套用同一組內容檢查，不需要另一份放寬規則。

## 可用與不可用

- F22：核對公司、民國年月、營業收入淨額與新台幣仟元，程式將「本月」轉 TWD。比對既有來源時逐月同口徑；不因一致便證明歷史初版。
- F26–F29：核對報表、期間與單位；權益變動表的上半年度是累計，不當單季。損益、現金流與 EPS 的數值、版本橋接仍走既有財務審查，不能因內容可讀便直接採用。
- F03：核對變更執照年月與實收股本欄；登記股數不等同加權稀釋股數，不能直接取代 EPS 分母。
- E01／E02：年度電子書或公開說明書清單是 `listing_only`。仍需特定檔名、上傳版本與原件的可驗證橋接；一個年度 URL 不會自動補齊多次公告。
- 相同 URL 若有多份候選文件，須用事件 identity 明確選版；不能由輸入順序決定版本。
- 回應沒有綁定的服務端總數時，維持 `index_exhaustion_unverified`。月份分段查詢與全窗一致，只是相互印證，並不證明來源已掃盡。客戶端的千筆顯示上限也不是服務端完整性保證。
- 原件公布時刻未知時，只從公開捕獲時間起可用；不能用索引日期把今天的版本回填到舊封存。數值影響、初版證明、財務／稀釋審查與來源掃盡分開記錄。
