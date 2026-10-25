# 四季條件 EPS 與估值

日常 `upgrade --extension-input` 可接受 `forward_estimate`，由正式報告流程同時
輸出 `forward-estimate.json`、HTML 及 PDF。沿用 `research_plan.forward_scenarios`
的三情境四季損益與 `forward_parameter_metadata`，不另建一套數字。

```json
{
  "forward_estimate": {
    "stock_id": "2330",
    "currency": "TWD",
    "model_version": "issuer-reviewed-conditional-v1",
    "reviewed_by": "具名的模型審查者",
    "methodology": "本公司收入、成本、稅率、歸母與未來股數的推估方法",
    "share_bridge": {"method": "明列原稀釋股數、真正新增股數及假設權重；不重扣既有可轉債"},
    "pe_sensitivity": [16, 20, 24, 28],
    "pe_rationale": "明示條件格點，不能冒充合理退出倍數",
    "ttm_quarters": [],
    "risks": ["參數假設失效條件及資料缺口"]
  }
}
```

每個前瞻參數仍需來源、觀測時刻、推估理由、不確定性與審查者。
可以是 inferred 假設，不必等待未來實績或未來 EPS 附註。文件原始 bytes、
公司、SHA256 與可得時間均驗證；損益及四季加總會重算，不能只改快取 EPS。
研究者對來源語意與推估適用性負責；程式不把具名勾選當外部審計。

三個不同判定：

- 條件 EPS 可算：自己的來源及全部參數合格即可；官方公告母集合的其他
  歷史缺件不一律阻擋這份明示模型。但缺業外或稀釋股數就不給 EPS。
- 條件 Forward PE：還須行情 gate 通過，使用未還原現價除四季稀釋估計 EPS 和。
  它不等於已驗證低估，亦不是以現股歸母商數改名。四季和不稱法定年度加權 EPS。
- 合理價／低估排名：還需獨立估值倍數與完整研究驗收，本入口不建立這個資格。

TTM 僅接受四筆連續、直接披露的單季稀釋 EPS，逐筆綁定官方原件、公司、期間、
幣別、定位、審查者及版本。標為「報列四季稀釋 EPS 加總」，不是會計年度加權
EPS。不得以年 EPS 減前三季、基本 EPS、現股商數或股價/vendor PE 反推替代。
缺原件維持 unavailable；不阻擋另有完整假設的未來模型。

EPS×PE 矩陣只列條件價格；逆推是其他參數固定時的獲利／營收要求，非市場共識。
另列四季同向收入、毛利、費用、稅率及股數敏感度，沒有校準機率。
舊封存預測保留原值與發布時間，新模型另存版本與實際發布紀錄。
