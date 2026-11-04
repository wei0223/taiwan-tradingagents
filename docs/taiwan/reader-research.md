# 單次研究：閱讀與稽核分層

`upgrade`的`extension-input.one_shot=true`使用閱讀版`report.html`及`report.pdf`，完整既有版面另存`audit.html`；期間、來源、EPS、版本與發布資格計算仍保留，閱讀分層不放寬資格。沒有產業證據時不顯示空表，缺口列於來源段落。

`extension-input.reader_research`可包含同公司與分析日、`verdict`、`data_note`、`new_insights`、`industry_cards`、`peers`、`sources`、`price_interpretation`、`market_interpretation`、`debate_focus`與`valuation_scenarios`。sources逐份保留原bytes的base64、SHA256、公司代碼、官方HTTPS URL、實際公布/捕獲時間與PDF實體頁；匯出reader-sources/，舊捕獲不能改成今天。公司自述與第三方已證實分開。數值仍需對原頁抄錄核對，hash驗證不是數值真實性證明。

同業必須同季single_quarter、consolidated、TWD；主要同業與鄰近業務比較分開。不比較每股面額差異，也不把較高毛利自動當成高PE的依據。沒有同口徑資料時省略表格。

`reader_research.reverse_conditions`對四個連續季度的同一獲利瀑布求逆：固定其他條件，分別解收入、毛利率與共同股數倍率。三欄是三個獨立等式，不能一起套用；股數變動只是分母敏感度，不代表有可執行減資/發行方案。模型未驗證、倍數未驗證、市場共識未驗證分開標示。不由單季乘四或差分EPS造分母。

`market_alignment`以相同價格交易日與法人分類連接，驗證日曆及分類完整性。缺值不補零，不將買超日數未知顯示為零；按收盤價分組的淨額不是法人實際成交成本。報酬按窗口首日前收計算，圖中首末收盤差另標示，不能混為同一報酬。四面板共用日期，保留價格、成交股數、外資淨額、投信淨額各自單位。

本輪多空正文只留可改EPS/判斷的分歧：列雙方假設、單季/四季口徑、數字影響、裁決及可判定條件。完整逐項反駁與真實Agent原稿仍在證據包；不能為有變化而強迫改立場。單季橋不能升格四季EPS矩陣。

每次另存新目錄，分享時解壓完整evidence.zip後開report.html。HTML/PDF來源相對路徑與所有計算/原件一併封裝。不建立監測、不發布新預測，原封存及公布時間不變。測試數學反解能重現目標，亦測試缺分類、不同季別、錯公司/來源hash與閱讀版分層。
