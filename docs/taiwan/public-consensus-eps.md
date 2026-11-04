# 公開轉引FactSet EPS

使用公開鉅亨文章，不呼叫FactSet API，不新增服務。此版只接年度EPS中位數，不接新聞目標價或其他FactSet資料。Codex負責公開搜尋並取得原文；程式負責從明確標示的市場預估EPS表抽取年度與中位數、核對原件SHA256及時間、保存版本並呈現於報告。沒有背景追蹤。

支援https://news.cnyes.com/news/id/數字、m.cnyes.com及gfemobile.cnyes.com同路徑，無query/帳密。其他來源須先增加相應解析與測試，不能偽裝成鉅亨格式。正文必須有FactSet、股票代碼-TW與EPS標題，明確年度欄及中位數列；不以摘要或目標價代入。

extension-input.public_eps格式：{stock_id,as_of,sources:{article_id:{source_url,captured_at,sha256,content_base64}}}。content_base64為完整UTF-8 HTML原始bytes，sha256須吻合。固定準備另產生空白模板。報告的public-eps.json保存抽取結果、各次報導與選版理由，public-eps-sources保存原件與收據並納入證據包。

只選已取得、截止日前已公布且已捕獲文章中各年度最新報導；不聲稱窮盡來源或為FactSet最新完整共識。同時間有相異中位數直接拒絕。未來來源排除；沒有合格數據顯示不可用。

報導發布時間不等於共識資料日，後者未知則null。分析師人數只綁摘要明示的年度，不向其他年度複製。幣別、基本/稀釋與調整口徑不推定，因此不自動接估值或績效；年度EPS也不是未來十二月EPS。原始完整文章包含其他表格，僅為驗證EPS保留，不代表程式使用其他資料或取得再散布授權；不把擷取的資料庫／文章提交Git。

公司已公布的EPS仍走財報原件流程。公開共識、公司實績與自建壓力試算分開，主預估只引用合格轉引中位數。現有自建模型作挑戰時須清楚標示，不能補入缺失共識。

## 驗收（2026-11-04）

新增12項離線測試、完整台股548項測試通過。另讀取一篇公開鉅亨原始HTML驗證解析格式；它是歷史範例，不當作最新共識。PDF用合成數據檢查，與正式研究／績效分開。未執行Sponsor或FactSet API，沒有新增服務。技能格式、Ruff及diff檢查通過。修改仍在feature/fixed-research-pipeline，未合併main。
