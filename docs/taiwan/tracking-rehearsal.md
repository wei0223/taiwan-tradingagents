# 實績評分演練與正式追蹤

演練使用既有 release，但實績、來源及未來檢查時間全部虛構。禁止當成預測績效或官方公告證明。`tracking-drill` 不連網、不發布、不修改原封存；只允許建立全新的輸出目錄。

```powershell
python -m tradingagents.research tracking-drill --release <既有release完整路徑> --output-dir <全新的Mock目錄>
```

可重複提供 `--release`。每份發布測試 11 種情境：正常首次公告、更正、期間錯誤、基本EPS替代稀釋EPS、數值未審、公布時間缺失、捕獲晚於檢查、實績早於發布、來源缺失、原件遭竄改、尚無實績。首次公告數值設定為原預測的90%，只供驗算誤差；不代表任何公司未來實績。無EPS發布的公司也不能由基本EPS補造績效。

`MOCK_ONLY.txt`、`manifest.json`、每案的Mock原件與輸入、追蹤JSON、HTML/JSON報告皆留在演練目錄。任何含 `evidence_kind=mock` 的追蹤都標成 `mock_rehearsal_not_performance`；報告入口拒絕將它顯示為正式績效。這是離線流程測試，不能證明初次公告歷史真的齊全，也不能取代人工核對原件。

正式公告取得後，保存官方原件bytes、URL、來源定位、公布時間、捕獲時間、SHA256及版本；逐項核對公司、期間、合併範圍、單位、首次公告歷史及數值。不得只勾審查旗標。合格後使用原release、選版實績與新的追蹤目錄：

```powershell
python -m tradingagents.research track-release --release <原release> --actuals <已核對的真實實績JSON> --output-dir <新的正式追蹤目錄> --report
```

沒有合格實績就省略 `--actuals`。報告只以 `primary_initial_vintage_results` 顯示主評分，誤差＝預測－實績；更正結果另列診斷。期間或口徑不符、證據不足則等待，不填零、不算誤差。舊發布時間與預測值不變，只有追蹤檢查時間新增。

報告保留原封存的 `observable_revision_conditions`；尚未取得逐項同口徑財務橋接時，假設偏差與條件驗證仍不可用，不由總誤差自動推論毛利率、回款或股數原因。模型若需調整，另建版本並另經發布審查。

本功能開發不自動更換既有監測的固定main版本；需經PR審查、main驗收後另行切換。原監測繼續等待實績，Mock輸出不得接入正式評分。
