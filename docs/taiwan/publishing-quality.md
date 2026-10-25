# 報告發布資格（v4）

`upgrade` 一律產生 `publishing-quality.json`，首頁與 PDF 列出發布狀態。
`source_audit.status=passed` 僅代表供應商結構檢查；不能蓋過官方公告、
版本、股本、財報或行情缺件。頂層 `publishing_status` 為 PASS／PARTIAL／BLOCKED；
PARTIAL 只准交付有日期限制的研究，完整估值及當前交易另行阻擋。

## 日常執行

1. 使用既有 Sponsor prepare 取得完整資料，保留還原價與官方交易日曆。
2. 同月落後不超過五個交易日的 TWSE 原始行情與法人，可選用：

   ```sh
   python -m tradingagents.research refresh-market --snapshot OLD/snapshot.json --output-dir NEW-CAPTURE
   ```

   此入口不讀 Token、不下單；保存官方回應、時間、SHA256，遇拒絕立即停止。
   僅更新未還原 OHLCV 及法人，不會把原始價冒充還原價。TPEx 仍走其既有資料
   provider，不能使用 TWSE 補件入口。較久缺件須完整 prepare。
3. `refresh-sources` 更新官方來源台帳。未核對公告逐筆保留 ID、公布時刻、
   類別、影響判斷、處置與排除原因；指定三筆月營收核對不等於公告母集合完整。
4. `upgrade` 使用新目錄、更新後 snapshot 與新 cutoff。`--release` 保留原發布紀錄。
5. 行情任一口徑／法人類別落後，或官方日曆未涵蓋檢查日，當前交易輸出 BLOCKED。
   16:30 前不把當日當完整市場資料；週末、國定／臨時休市只依官方捕獲日曆。

## 前瞻參數契約

extension input 的 `forward_parameter_metadata[scenario][quarter][parameter]`：

```json
{
  "source": ["source_id_in_review_documents"],
  "observed_at": "2026-10-20T08:00:00+08:00",
  "assumption_type": "inferred",
  "validity_status": "reviewed",
  "reviewed_by": "具名審查者",
  "rationale": "本公司原件錨點、預測方法及調整理由",
  "uncertainty": "明示上下界、敏感度或無校準機率的限制"
}
```

類型為 official／inferred／stress_only。前瞻可以是有依據的 inferred 假設，
無須等待未來實績；但沒有來源、審查或不確定性不能升格正式預測。
每個參數的 value、來源數字 evidence_ids、observed_at 及型別另存 register。
純業外零假設強制 stress_only；未填值不補零。缺業外／稀釋股數時下游 EPS 阻擋。

實際稀釋 EPS、條件 EPS、現股歸母商數與未知分開。
`scenario_implied_multiple` 不得代替 `forward_pe`。四季分母、版本、股本或官方
覆蓋不合格，Forward PE、合理價與低估排名保持 null；反向等式門檻仍可列，
使用明示價格日期及假設 PE，不稱市場共識。既有 TTM 驗證入口與 PE 同口徑
檢查保留，沒有通過不能拿來源報列 PE 充當已驗證 TTM／Forward PE。

## 交付與績效

HTML 不內嵌完整稽核 JSON；原件、計算、原 Agent 內容另存相對路徑附件及
evidence.zip。delivery-manifest.json 對包內各檔案保存 SHA256。複製整個目錄或
解壓證據包即可離線閱讀；單獨 HTML 不包含證據原件。

小樣本 `strategy_performance.status=insufficient_sample`。逐期 Walk-forward、
開盤可行性、成本與可得時間阻斷沿用既有引擎；不得因已有規則就宣稱有優勢。
行情口徑缺件時不延長舊回測，舊事件另作封存診斷。
