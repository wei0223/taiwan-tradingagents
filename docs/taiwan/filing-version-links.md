# 文件身分、歷史事件版本與索引範圍

三種證明不可混用：

| 驗證 | 可證明 | 不能證明 |
| --- | --- | --- |
| listing → 官方下載表單 → PDF bytes | 特定公司、期間、文件類型的目前檔名、列示大小、下載URL與SHA256；實際捕獲時間 | 該檔案是某次歷史公告當時發布的版本 |
| 索引事件 → 檔名／版本識別 | 需官方可核對的事件識別、檔名與版本對照 | 同季、同日、依時間排序、兩個事件剛好有中英文兩份、人工勾選都不足 |
| 查詢回傳集合掃盡 | 有綁定總數時，只能證明指定服務回傳集合的窮盡 | 公告快易查涵蓋所有MOPS公告來源或所有歷次更正 |

證交所[臺灣投資指南](https://www.twse.com.tw/zh/about/company/guide.html)將公告快易查描述為精選投資人關注的公告項目。因此 `source_coverage_scope` 明示只限該精選索引；即使有服務端筆數，也不會放行 `all_mops_announcements_proven`、最新完整研究或正式估值。仍需針對財報、月營收、股本與更補正等所需來源各自取得並核對完整來源範圍。不存在可用的逐事件版本介面時保持pending，不以caller旗標代替證據。

## 目前文件鏈的離線驗收

```text
python -m tradingagents.research audit-filing-links --input filing-link-input.json --output-dir new-link-audit
```

輸入包含：

- `events`：`row`是原始索引列、`listing_id`指向listings、`document_ids`指向documents。
- `listings`：保留索引完整URL、HTTP狀態、捕獲時間、原始base64及SHA256的receipt。
- `documents`：每份含`filename`、`download_response`、`pdf_response`；兩種回應同樣保留URL、原始bytes、HTTP、實際捕獲時間及SHA256，下載表單另保留精確`query`。

目前支援財報A與公開說明書B。檔名、表格公司／年月季、文件類型、下載表單、PDF連結、大小與時間鏈必須一致。PDF內容的會計數值、數位簽章及初次公布版本均不因這條鏈通過便算核准。未知公布時間的現今下載版本只從捕獲起可用。

輸出`current_period_document_set_captured`與`event_version_link_verified`分開。現有原始索引沒有檔名／版本識別，因此本驗收不產生歷史事件對應成功。不可擅自用英文檔當較晚事件、中文檔當較早事件。

日常`audit-announcements`／`upgrade`的inventory項目可帶`filing_documents`（上述完整document物件陣列），套用相同文件鏈檢查。未過截止前捕獲的PDF會被排除；目前文件集合齊全也不會把`listing_only`變成已證實歷史事件版本。

## 更補正查詢

以官方表單指定市場、年度、季別，保存回應及完整查詢參數，逐列核對公司。不在目前回應中出現只能記為「本次查詢未列出」，不能聲稱從未有更正或該PDF就是初版。文件PDF的建立／修改日期、簽署日期及財報通過日也不是公開發布時間。

新回應與審查結果另存新目錄。舊release、營收預測與發布時間不變，不新增付費服務、不使用LLM API、不下單。
