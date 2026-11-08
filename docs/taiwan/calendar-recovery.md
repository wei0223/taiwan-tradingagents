# 官方交易日曆自動復原

日常輸入股票代碼時，Sponsor 資料準備及 Codex 的 `prepare_research.py` 都使用官方月份原件快取。上市用 TWSE FMTQIK，上櫃用 TPEx 指數交易日清單。同一市場的原件可跨股票使用，兩個市場不能混用。

## 復原順序

1. 逐份核對官方 URL、查詢月份、原始 UTF-8 bytes 的 SHA256、捕獲時刻、月份欄位、重複日期及市場。
2. 從本次快照和共用快取選擇符合分析截止日的原件。可用 `--calendar-seed-snapshot` 匯入以前保存的官方原件，一次匯入後後續請求自動重用。
3. 只有缺少或涵蓋不足的月份才查官方 API。單次上限 200 次，拒絕後停止該主機，不換網域或用代理。403、429及 HTTP 200 過頻頁面保存原始回應，跨程序保留 24 小時冷卻。冷卻紀錄損壞時停止網路，仍可讀合格原件。
4. 原始捕獲時間晚於分析截止的版本不採用。捕獲時間必須晚於所需截止日，或原件已明列該截止日的交易，才足以覆蓋當月。不能從星期、舊月份未列某日或個股 K 線推論休市。
5. 足夠時另存 `effective-snapshot.json`，重新計算固定技術包與八階段輸入。保留原取得快照 SHA256 與有效快照 SHA256、重用／缺失月份及網路次數。

不足時保持受限，不填零或放寬 1,000 個完整交易日規則。當月請求失敗仍保留其他已驗證月份及可繼續的研究。這項功能處理官方日曆取得失敗，不能補出缺漏 OHLCV、還原價、公司財報或 AI 論述。

## Codex 日常入口

```bash
python skills/taiwan-report/scripts/prepare_research.py --request /path/to/request.json
```

本機設定 `project.json` 的 `calendar_cache_dir` 可指定共用庫，預設為儲存庫同層 `calendar-cache`。既有 `blocked_history` 另建 `fixed-inputs-recovery-<UTC>`，原計算與原 Agent run 不覆寫。`fixed-inputs-current.json` 只定位新版本，輔助程式輸出的 `fixed_input_dir`、`effective_snapshot_path` 應貫穿公司原件查核、研究與最終報告，不能與舊輸入混用。若技術狀態已 ready，重入只核對雜湊，不重算或重送 Sponsor。

GitHub main 的 Sponsor 流程會還原與保存 `.calendar-cache`。只有合格原件及來源拒絕紀錄進快取，不含 Secret。即使後續來源稽核失敗也保存本輪已取得的日曆原件。初次空快取仍需成功取得官方月份；來源拒絕且沒有合格原件時保留缺口。

## 明確復原入口

```bash
python -m tradingagents.research calendar-recover \
  --snapshot failed/snapshot.json --archive-dir calendar-cache \
  --seed-snapshot previous/snapshot.json --output-dir new-recovery

python -m tradingagents.research fixed-inputs \
  --snapshot failed/snapshot.json --calendar-cache-dir calendar-cache \
  --calendar-recovery-live --output-dir new-fixed-inputs
```

`calendar-recover` 預設離線；`--live` 才可查缺月。`fixed-inputs` 的復原也需明示 `--calendar-recovery-live`。Codex 日常輔助程式已自動帶入。兩者均須新輸出目錄，且不修改舊報告、封存預測或發布時間。

## 驗收

離線測試覆蓋 TWSE／TPEx、市場錯配、損壞 bytes／快取、未來捕獲、過期月份、缺月單查、部分月份失敗、拒絕跨次冷卻、請求上限、正常 fixed-inputs 與 Codex 續跑定位。真實 1216 歷史失敗批次以保存原件重播，不動用 FinMind Token，結果應記錄零網路請求、1,000 日技術狀態及舊來源雜湊核對。
