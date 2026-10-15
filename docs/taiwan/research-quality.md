# 三分析師研究品質升級

## 執行

維持原有 `prepare`、`archive`、`replay`、`evaluate` 與美股流程。新增離線入口：

```sh
python -m tradingagents.research upgrade --snapshot data/3017/snapshot.json --output-dir reports/3017
python -m tradingagents.research upgrade --snapshot data/3017/snapshot.json --model scenario-input.json --industry industry.json --analysts analysts.json --output-dir reports/3017
python -m tradingagents.research validate-forecasts --input forecast-outcomes.json --output errors.json
python -m tradingagents.research validate-signals --input signal-outcomes.json --horizon 20 --output performance.json
```

不呼叫模型、不讀 OpenAI Key、不下單。Windows 建議 `python -X utf8`。正式 Token 沿用 Actions Secret，這些離線指令不需要 Token。

輸出 `report.html`（摘要＋可展開的三位分析師）、`research.json`（完整輸入／計算／日期稽核）、`manifest.json`（雜湊）。HTML 自包含；本機路徑、主動腳本、遺失錨點、重複 ID 會阻擋輸出。公開 HTTPS 的即時可達性不等於本機錨點檢查，後者不宣稱前者已通過。

## P0：財務基礎

`quality.source_audit` 重新從 snapshot 建立 bundle，不信任外部衍生數字。每列保留 period、觀測日、公布日、數值可用日、取得時間、單位、來源、raw hash、正式公布證據 hash。

FinMind 季底日期本身不足以證明單季。原供應商的 single-quarter 解讀列為未複核，現金流列為可能累計。預測必須額外提供原件核對記錄；不能把原有供應商假設當作新核實結果。

`normalize_quarter` 只容許同公司、同單位、同報表範圍、同版本集之累計「金額」差分；禁止累計 EPS、比例及股數差分。第一季可使用當期累計。不能拼接更正前後版本。

## P1：情境模型輸入契約

`scenarios.forecast(bundle, specification)` 無 specification 時明確回傳 unavailable，不補預測。specification：

- `as_of`、`bundle_sha256`：必須與輸入一致。
- `created_at`：實際建立時間；晚於舊資料日的情境是回顧建模，不能視為當時已發表的預測。
- `basis_review`：reviewer、原件核對狀態、季度／母公司歸屬／稀釋股數核對旗標；documents 含 HTTPS、公布／捕獲時間、原始 bytes base64 及 SHA256。時間不得晚於資料日；bytes 必須與 hash 一致。人工核對不因 hash 而自動得到語意保證。
- `reviewed_facts`：公司代碼、`unit=TWD_and_shares`、稀釋分子、加權稀釋股數、原件稀釋 EPS。EPS 恆等式誤差不能超過原件四捨五入範圍。不能由四捨五入 EPS 反推股數。
- 恰有 bear、base、bull；涵蓋本季起到下一年度年底，至少完整覆蓋未來四季與下一年度。
- 每季每個假設為 `{kind: assumption, value, rationale, evidence_ids}`，包括營收、毛利率、營業利益率、業外、稅率、非控制淨利、稀釋分子調整及加權稀釋股數。所有引用必須是可用證據。
- 各情境 `valuation_pe` 亦為明示假設，不能以歷史分位直接認證合理倍數。

公式：`稅前=營收×營業利益率+業外`；`母公司淨利=稅前×(1-稅率)-非控制淨利`；`稀釋EPS=(母公司淨利+稀釋分子調整)/加權稀釋股數`。對虧損或複雜反稀釋測試，須另作專業會計假設，不能把固定股數模型當作 IFRS 全面計算。

四季 EPS 加總及下一年度季度 EPS 合計均標明近似，並非審計年度加權稀釋 EPS；部分年度不冒充全年。PE 只乘正 EPS；虧損不給 PE 價格。來源 PE 分母未核對時不標 TTM PE。

`challenge` 可點名對方情境、季度、欄位與替代假設，輸出待重算／回覆記錄。使用相同 bundle 重算可取得 EPS 差異。這不是執行獨立 Agent 的證明；不得標示為新多空辯論。

## P2：產業、技術、籌碼及條件式決策

`industry_review` 將公司公告、管理層說法、外部估計及分析假設分開，要求來源、捕獲 hash、日期、對收入／利潤率的傳導與失效條件。六個面向（需求、組合、價格、產能、競爭、客戶認證）未取得證據即顯示缺口。`compare_peers` 只比較同期間／單位／口徑且有選股理由的資料，不把混合期間表格稱為同業比較。

`dynamics.rolling` 使用已核實官方日曆與同一供應商還原價格，計算 5/10/20/60 交易日報酬、MA 單日斜率、簡單平均 ATR、前視窗高點突破及下一日失敗規則。需要足夠歷史且不能缺個股日；不推定缺日為停牌或零成交。規則與成功率分開。

各視窗分開計算三大法人累積，缺類別保持 null。未取得的融資券、借券及集保資料不可補造。同期方向不代表因果。

`conditional_decision` 預設 observe。具備情境模型、價格確認及完整法人視窗才列 research_candidate，仍無下單。`reward_risk` 必須由明示的進場、條件目標與失效價格計算；不由均線自動假造停損。

## P3：預測與訊號驗證

`forecast_errors` 按公司、期間、metric、basis、模型版本計算 MAE，公告未到或未捕獲者 pending。預測晚於實際公布會拒絕。更正版本不自動擇優。

`signal_performance` 使用下一交易日開盤至第 N 日收盤，須有完整調整價與日曆。可分別跑五及二十日。觀察狀態不強迫進場，缺未來資料保持 pending。成本、稅與滑價是可調假設，不是現行費率認證；不假造券商成交或借券。提供事件報酬、成本後命中率、平均盈虧比、每筆收盤標記最大回撤；**不是可重疊投組的資金帳本回測**。

`paired_comparison` 僅對同股票同截止日的多 Agent、單 Agent 與簡單基準做配對比較；缺對照資料即 unavailable，不填零或宣稱辯論已勝出。使用者輸入的時間戳不等於獨立公證，正式研究應搭配既有 VintageStore 與原始封存。

## 驗證與未完結條件

## 獨立數字審查與實際發布封存

`numeric_review.validate_opening` 對同一凍結 bundle 驗證多空開場與引用；
`recalculate` 只容許營收、營益率、稀釋股數及 PE 的明示替代假設。
coherent 重算保留原少數股權獲利比例，isolated 固定其金額；兩者不可相加。
獨立開場、交叉反駁、風險裁決必須由實際分開的 Agent 執行，保存身分、輸入與輸出 hash。
程式提供計算契約，不會自行證明曾執行獨立 Agent。正文可維持三位分析師，辯論放附錄。

`publication.publish(root, bundle, specification, debate, model_version)` 使用實際 UTC 時間，
拒絕已結束季度、未完成辯論及未來截止日；以 exclusive create 保存含原輸入的 release。
`read_release` 驗證 hash。發布日之前的回顧報告不會因此變成事前預測。
本機時間與 hash 不是第三方時間公證；model_version 應固定 Git commit。

`tracking.track(release_path, actuals, output_root)` 先驗證封存，結果另建檔、不覆寫預測。
actuals 須提供公司、季度、metric、basis、value、公布/捕獲時間、來源 URL、來源檔路徑、
SHA256 與明選 revision_id。來源 bytes 必須匹配，缺實績為 pending；基本/稀釋及累計/單季
分開評分。首次公布與重編應各選一版另存，不能事後擇優。季度模型誤差不等於策略報酬。

- 測試覆蓋口徑差分、future publication/capture、原件 hash、稀釋分子／股數、NaN、缺日、費用、下一日進場、MAE、對照缺漏及 HTML 安全／可攜性。
- 實際報告的歷史資料捕獲、模型建立與未來驗證時間分開。回顧壓力情境不計入歷史預測準確率。
- 新獨立 Agent 數字辯論、完整同業原始證據、投資策略績效及勝率，不會因本功能存在就自動完成。驗收時必須分別列出已執行與待累積，不把「支援」寫成「已證實」。
