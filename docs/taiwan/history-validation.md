# 多年來源、Q3 nowcast 與分軌驗證

歷史回補不改寫舊捕獲時間。MOPS公告與數值版本是不同證據。

## 日常整合驗收

Sponsor workflow 的 extended 模式會依序 prepare、audit、acceptance，再封存 artifact。
acceptance 在沒有 FINMIND_TOKEN 的步驟執行，使用同一個 snapshot 產出三分析師
`integrated/report.html`、`research.json`、`manifest.json` 及 `acceptance.json`。
核對官方日曆的逐月原始回應、年度 MOPS 查詢回執及來源稽核；缺件回傳非零，
不能把本機補救的結果當作 main 已通過。所有股票共用程式，不嵌入奇鋐的毛利率或產品假設。

```bash
python -m tradingagents.research acceptance --snapshot snapshot.json --output-dir integrated
```

這是整合與資料驗收，不是新獨立 Agent 辯論或預測績效驗收。缺人工審核的單季財報、
股數或產業假設時，EPS 模型維持 unavailable。既有 predictive_cli、validate-forecasts、
validate-signals 使用明確輸入另行執行，不以目前資料自動宣稱歷史 PIT 或產業模型有效。
PDF 歷次更正及尚未公布的 Q3 實績仍另行追蹤，不阻擋來源完整的每日資料準備。

## 來源保存

`MopsPublicationResolver` 按年度查詢，對每個觀察期間查該年及次年（處理跨年公布）。
每年原始回應單獨保留，不捏造合併HTTP回應；同時點多版本歧義仍拒絕。
`OfficialCalendarProvider.fetch` 超過60個月時自動拆成三年窗口，各月原始交易所回應仍可驗證；
失敗部分另列，不能以平日或個股K線反推市場交易日。

`history_archive.capture_year(root, code, year)` 保存目前官方清單、上傳時間與更正欄位，
優先下載中文合併、年度個體及有更正標示版本，其他語言保留清單。每份下載失敗分開列出，
不因一份壞檔失去整年清單。刪除／替換前的舊PDF未必仍可取得，不能宣稱復原全部更正歷史。

`statements.parse_income` 解析已保存的官方合併損益表HTML，檢查單位與明確當季欄。
Q1一月一日至三月底累計即同一單季；Q2/Q3必須有明確單季欄。
Q4年度表保留原件但不差分EPS，股數也不由四捨五入EPS反推。
官方HTML表不等於完整財報附註，PDF與EPS附註缺件須另列。

## 長期比較

`predictive.walk_forward` 仍要求公布與捕獲均在研究日前；缺版本則 unavailable。
`historical_validation.retrospective_revenue` 是另外命名的**現今修訂版本回補診斷**，
不改原捕獲時間，只以公布時點切分，用來找模型問題；其結果不可提供正式PIT權重。
比較log趨勢、月動能與同月去年naive。產業法缺來源就不補假數。
收入驅動EPS診斷以去年同季明確稀釋EPS乘收入增長，是固定EPS／收入轉換率假設，
不冒充完整成本與股數模型。Q4因單季稀釋口徑缺失而排除。

2020–2022探索、2023–2024事先鎖定的歷史保留診斷、2025以後已看過區間另列。
所有歷史數值均為現在捕獲，保留區間也不能改稱當年實盤或嚴格PIT。
訓練權重與保留誤差分開，三法資料不完整時正式權重維持不可用。

## Q3

`historical_validation.nowcast_error` 缺正式實績回傳pending。
實績公布早於模型時，回傳ineligible而非計入事前績效；公司、季度與單季稀釋口徑須一致。
須核對重大訊息、自結與正式財報的最早獲利公開時間，不以較晚正式財報或更正時間取代。
`earliest_profit_public_at`及`profit_release_history_reviewed`缺失時不計分。
EPS附註加權股數未核對就不拆解；公布／捕獲時間晚於評估時點不能使用。
通過後以`earnings_bridge`拆收入、營益率、業外、稅、非控制權益與股數，
保留EPS四捨五入殘差。分解是數學歸因，不是因果證明。

## 短期

`historical_validation.short_horizon` 沿用凍結突破規則，次日開盤檢查RR，五或二十交易日出場。
開盤跌穿停損按開盤模擬；日內同時觸及停損與目標時先停損；目標跳空保守按目標價。
停損／目標可提前出場，持有期限不足且未觸發邊界時保持pending，不補造未來行情。
成本為預先指定假設：單邊費14.25bps、賣方稅30bps、單邊滑價5bps，非券商費率認證。
每個期限不重疊持倉事件；不是完整資金、張數、流動性、跳動單位或撮合帳本。
同進場持有至期限的比較只評估出場規則，不證明選股alpha；極少訊號不報統計優勢。
