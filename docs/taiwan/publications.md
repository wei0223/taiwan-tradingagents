# 正式公布日期與數值版本

## 來源檢查

FinMind Sponsor 實際回傳的損益表只有 `date, stock_id, type, value, origin_name`。
月營收包含 `create_time`，但沒有正式公告時間。[FinMind 官方欄位說明](https://finmind.github.io/tutor/TaiwanMarket/Fundamental/)
明確區分季末日期與公告日，且說明 `create_time` 是資料庫建立日，舊列可能為空。
Sponsor 的批量存取能力不能使這些欄位成為正式公布時間。

MOPS [公告快易查](https://mopsov.twse.com.tw/mops/web/ezsearch)的官方前端
[查詢程式](https://mopsov.twse.com.tw/mops/web/js/mop_search.js)使用
`POST https://mopsov.twse.com.tw/mops/web/ezsearch_query`。本次檢查實際回傳 JSON 後
接入 `F22`（月營業收入）與 `F27`（綜合損益表），不是從新聞或法定期限推估日期。
此為網站查詢介面，未視為永久穩定、具 SLA 的公共 API；網站變更、封鎖或未知 schema
時回報缺資料。新版 MOPS 首頁曾回傳安全性阻擋頁；不繞過防護，使用可正常存取的官方舊站查詢。

## 日期與版本政策

- `data_date`：保留 FinMind 原始日期；月營收所屬年月另外由 revenue_year/month 識別。
- `published_at`：MOPS `CDATE` 的民國日期轉西元，僅適用台北日終分析。
- `published_timestamp`：MOPS `CDATE + CTIME`，明確保留 `+08:00`。
- `publication_evidence`：保存公司、報表期別、項目、原始公告、公告 SHA256、官方來源及
  文件網址、取得時間、所綁定 FinMind 原始列 SHA256；雜湊用於完整性比對，不是官方數位簽章。
- `available_at`：至少為 FinMind 數值取得時間與 MOPS 公告取得時間較晚者的台北日期。
  若兩來源今天取得，昨天的分析不能使用；今天的日終分析可使用，但不宣稱數值為最初申報版。
- `publication_audit`：保存實際查詢參數、所有原始公告（包含被排除列）、原始 UTF-8 回應及 SHA256、
  更新時間及錯誤。不得把含未來列的 raw audit 交給 LLM。

資料公司、F22/F27、公告主旨的期別、官方文件連結內 co_id/year/month/season 必須一致。
不將董事會通過日期、季度結束日、法定截止日、批次出表日或資料庫建立日當公布日。
同一期間有多次申報時採最新公告；相同时间有不同內容則視為不明，排除。
公告晚於分析日、版本取得晚於分析日、無公告、日期／時區／期別錯誤都不能納入分析。

MOPS 公告列表證明申報時間，不證明目前 FinMind 數值與最初申報內容完全相同；
因此本版是**保守的取得日版本政策**，不是完整歷史財報版本資料庫。
未抓取／解析所有原始財報文件，也不拿 MOPS 累計 EPS 直接替換 FinMind 單季 EPS。
歷史回測要使用當時保存的 snapshot，並在匯入時重新驗證日期、雜湊與標準化數值。

## 使用與 Mock

```shell
python -m tradingagents.research prepare --symbols 2330,3017,3231 --start 2025-01-01 --as-of 2026-10-14 --mops-publications --output-dir research-runs
python -m unittest discover -s tests/taiwan -v
```

資料 Provider 預設不新增網路請求；旗標開啟後，每檔每種基本面資料最多一個查詢並快取，
HTTP timeout 20 秒，不自動重試。查詢區間最早為 2013 年，回傳達 1000 列即拒用以免截斷。
`MopsAnnouncementClient(transport=..., clock=...)` 可完全 Mock，不傳送 FinMind Token。
既有注入字串日期 resolver 維持相容，但只適合呼叫端已自行驗證歷史版本的 archive；
不應用 lambda 把單純日期欄位當成可信驗證。

GitHub Actions 加入布林選項，預設取得 MOPS 日期；修改需合併 main 後才會用到 Sponsor Secret。
Secret 僅提供 FinMind 準備步驟，不提供 PR、模型或 MOPS。原始 Sponsor 資料留在本機／短期 artifact，
不提交公開 repository。失敗仍保存原始金融列並標記 unavailable，不編造公布日。
