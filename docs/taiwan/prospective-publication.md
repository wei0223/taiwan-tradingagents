# 前瞻模型審查與正式研究發布

`publish-forward` 封存裁判明確核准的指標，使用執行時實際UTC時間，不接受回填發布時間。
正式發布表示願意按固定口徑追蹤誤差，不代表公司指引、共識、校準機率或已驗證優勢。

```powershell
python -m tradingagents.research publish-forward --bundle publication-bundle.json --model selected-model.json --debate sealed-debate-input.json --selections publication-selections.json --review-input review-input.json --release-dir releases
```

模型保留四個連續未來季度、每季完整13項損益輸入。每項未來值須標為
`assumption`，有原件來源、推估理由與不確定性；不得以過去有財報為理由把未來值標成fact。
來源bytes、公司、SHA256與公布／捕獲時間均核對，截止日依台北時區。
稀釋EPS另須明示分母、反稀釋及分子政策，不差分EPS或加權股數，不重加已有潛在普通股。

每季每指標明選：

| decision | 封存／評分 |
| --- | --- |
| `publish_forecast` | 納入主要預測；EPS取兩位小數，收入取最接近百萬元 |
| `conditional_only` | 保留算式與情境，不列主要預測或命中率 |
| `unavailable` | 明確不可用，不補值 |

一份模型可只發布其中一季或一項指標。無任何核准指標時拒絕空白release，保留審查版。
未來尚未公布實績不是禁止預測的理由；缺可辯護的公司專屬假設或分母橋接則不可強行通過。
多空情境不等於上下分位數，未校準不得加上概率、信賴區間或最可能稱號。

真正獨立的開場、雙向交叉反駁與風險裁決分別保存。發布器檢驗公司、事實包、
不同Agent識別、階段時序及內容雜湊，且發布模型的數字與各指標選擇必須吻合裁決。
這些紀錄是操作證據，不是第三方模型認證或不可偽造的身分簽章。
正文仍是基本面、技術面、籌碼面三位分析師，辯論及前後立場作附錄。

schema_version=2 的release保存模型、選擇、計算、原始資料清單與獨立辯論；
原始bytes另存同公司source-bytes/。`publication.read_release` 驗證hash與原件並重算。
未驗證PE、年度替代EPS、缺失TTM、歷史版本及交易訊號資格不隨發布升級。
發行值與時間不可改；修改假設必須新版本、新發布時間。

後續沿用 `track-release`，僅對正式選定指標評分，財務季營收不能拿月合計代替，
直接單季稀釋EPS不能拿基本／累計／年減前三季EPS替代。初次公告須完成原件、數值、
時間與版本審查；重編值另列診斷，不挑最好版本。沒有同口徑實績就pending，不算誤差。
同一季營收與EPS是相關結果，不當作兩個獨立市場樣本。當季少量樣本不證明模型較準。

`schema_version=1` 舊封存繼續由原入口讀取；新增功能不回寫舊發布。
