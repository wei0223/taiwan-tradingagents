# 在 Codex 研究台股：FinMind Sponsor + 原生多 Agent

這是独立於上游 TradingAgentsGraph 的入口。Python 取得資料、計算數值及驗證引用；
Codex 的多方、空方、風險子 Agent 做解釋與辯論，主 Agent 做結論。原版美股 Graph 保留。
不使用 OpenAI API Key、不调用原版 LLM client、不下單；仍會使用你的 ChatGPT 方案額度。

## FinMind Secret 與 GitHub 資料準備

Secret 名稱固定為 `FINMIND_TOKEN`，位於 repository Settings → Secrets and variables → Actions。
使用者已確認建立此 Secret。程式無法讀回 Secret 的值，本版也不會要求貼出 Token。
GitHub Secret 只在受信任 main 分支手動資料準備步驟中注入，安裝相依套件及 Codex 分析不帶 Token。
沒有新增 OpenAI/ChatGPT 登入 Secret、排程或付費 LLM fallback。

新增工作流程 `.github/workflows/taiwan-prepare.yml`，名稱 **Prepare Taiwan Sponsor research data**。
目前仍在 PR，必須先由使用者審查合併到 main，GitHub 才會在預設分支顯示 Run workflow。
為避免將 Secret 交給未審查的 feature/PR 程式，工作流程只准 main 執行。

操作：

1. 合併后，Actions 選上述工作流程，再按 Run workflow。
2. 填入股票（如 `2330,3017,3231`）、起日及分析日期；不可填未來日期。
3. 從該次 run 的 Artifacts 下載 `taiwan-research-data-<run-id>`；保存期一天。
4. 解壓到本機 repository 的 `research-runs/`。每個股票資料夾有 bundle.json、state.json、snapshot.json。
5. 在 Codex 開啟本機儲存庫，輸入下方操作範例。只連 GitHub 而未有本機資料包，無法直接執行本機研究。

Actions 只準備資料，不執行 Codex。不存在把 GitHub Secret 自动同步回本機的機制。
Artifact 的下載權限依 GitHub repository 設定；Sponsor 原始資料不 commit 到公開程式碼。
Token 缺失時停止；單檔失敗會記錄 unavailable，其他已成功資料包保留，但 job 顯示失敗。
Sponsor 方案不保證財報具有可信公布日，未知公布日照樣排除；不買新方案或自動升級。

## Codex App 操作

用 ChatGPT 登入，確認工作根目錄是這個 repository；開啟現有聊天即可，不需要開多個聊天。
例如資料包在 `research-runs/2330-2026-10-14/`：

```text
使用 $taiwan-stock-research，研究 research-runs/2330-2026-10-14。
請啟動多方、空方及風險三個獨立子 Agent，完成獨立分析、交叉回應和主管結論。
沿用本次 ChatGPT 登入及模型；不要使用 API Key、抓新資料或下單。
```

技能在 `.agents/skills/taiwan-stock-research/SKILL.md`。
自訂角色在 `.codex/agents/taiwan-{bull,bear,risk}.toml`；介面未支援選擇自訂角色時，
主 Agent 將角色指令傳給原生子 Agent。子 Agent 機制不可用時停止，不能用單一模型扮演三角冒充獨立研究。

## 可選本機一鍵入口

Python 3.11+；data-only 相依套件為 python-dotenv、requests。Codex CLI 必須已安装並以 ChatGPT 登入。
先自行执行 `codex login status` 確認，API-key login 不適用此入口。

```shell
python -m tradingagents.research run --run-dir research-runs/2330-2026-10-14
```

wrapper 先確認 login status，拒絕 API-key auth；呼叫官方 codex exec 时強制
`forced_login_method="chatgpt"` 和 OpenAI provider。移除子程序環境中的 API keys、FinMind Token
與 GitHub Token。登入檢查僅辨識 CLI 當前輸出的 ChatGPT 文字；其他版本／語言不識別會保守拒絕。
CLI 最多執行十五分鐘；quota/auth/timeout/failure 保存狀態並停止，不自動重試或換供應商。
已配置的第三方 MCP／外部工具仍是使用者環境的一部分；研究技能只准使用本地資料及狀態工具。

## 辯論與數值驗證

- 主 Agent 管理状态，只有它可以 submit；子 Agent 各自寫 pending 檔，避免狀態競寫。
- 多空 opening 使用各自的新 context，仅讀同一個 bundle，先不讀對方報告。
- 接受兩份 opening 后，回到原多空子 Agent 各交叉回應一次，必須覆蓋對方每个論點。
- 風險 Agent 核查雙方論點與回應。主管最終裁決，不能越過「資料不足」的風險 veto。
- claim/response 引用 evidence IDs。未知／null 的資料不能當證據；所有精確數值由 Python
  在報表插入。敘述只寫質性文字，validator 拒絕数字字面值；文字數量及語意推論仍需 Agent 自律與人工審查。
- 登記不同 Agent ID 供稽核，但不代表 Python 能證明是獨立 context；真正的分派由 Codex 記錄。
- bundle 與已接受 stage 保存 SHA256，輸入或階段被修改時拒絕續行。已接受結果不可覆寫。

完成时資料夾内產生 `report.md`、`report.html`。精確價格／指標有證據欄位、單位、日期和來源，
完整原始列在 snapshot.json 作稽核，模型不使用其中被 PIT 排除的列。

## 續行與限制

同一 Codex 聊天可續行未完成階段，保留原子 Agent。跨聊天无法恢復子 Agent 时，
舊 run 保留作稽核，新 run 重做研究；目前不承諾跨程序子 Agent context checkpoint。
可以先檢查：`python -m tradingagents.research status --run-dir <run>`。
程式管理固定階段，但 Codex 的推論及自然語言仍非确定性，也不能保證投資結論正確。

基本面 publication archive／MOPS、官方交易曆、台股原版 LangGraph 接線仍未加入。
資料不完整時仍可進行有限辯論，但最終只能輸出 insufficient_data，不會以缺資料推論股價應跌。

## 官方參考

- [Codex 子 Agent](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [ChatGPT／API 登入](https://learn.chatgpt.com/docs/auth)
- [Codex 非互動執行](https://learn.chatgpt.com/docs/non-interactive-mode)
- [GitHub Actions Secret](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)

查核日期 2026-10-14。這些文件支援登入／工具用法；金融研究流程是本專案設計。
