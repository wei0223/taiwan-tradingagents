# Codex 研究流程驗證（2026-10-14）

開發於 feature/taiwan-market-provider，續用 PR #1，未修改或合併 main。

## 已完成

- GitHub Secret FINMIND_TOKEN 的 Sponsor 資料準備工作流程（manual dispatch、main only）。
- evidence-only bundle，排除未知／未來公布列，重算指標，不信任輸入的衍生數值。
- 原生多方／空方／風險角色設定、Codex skill 與固定六階段狀態。
- 子 Agent ID、canonical JSON SHA256、阶段先後、每個論點的引用及交叉回應完整性檢查。
- 關鍵資料缺失强制 insufficient_data；主 Agent 不能推翻 risk 的資料不足 veto。
- 報告數值由 Python 插入，Markdown／HTML 輸出；HTML escape；無 API-key fallback。
- 同聊天未完成階段續行，quota/auth/timeout/failure 中止且保存已接受輸出。

## 本機測試

- 38 項 unittest 全通過，含原台股 22 項與新增研究 16 項；網路封鎖／Mock。
- 2330、3017、3231 bundle 測試；原始未來列不暴露、衍生數值重算、PIT／來源／過期行情。
- 六階段、獨立 role ID、引用未知／null 資料、數值幻覺字面值、交叉回應漏項及重複項。
- API 登入拒絕、環境 key 移除、quota 不重試／不切供應商、timeout 状态保留。
- 全庫 Ruff 通過；TOML 解析、workflow YAML 解析、Skill Creator quick_validate 通過。
- 最初报表測試引用錯誤的預期值，修正為被引用來源的實際數值後通過；不是修改計算來迎合測試。
- 本機 pip 安裝 PyYAML 無進度，已中止；改用 PyPI 官方 wheel，校驗 SHA256 后在工作區
  隔離載入。skill validator 在 Windows 默认 cp950 失敗，使用 Python UTF-8 模式後通過。

## 真正的子 Agent forward test

使用本聊天授權的原生子 Agent，角色與窗口互相隔離：

1. /root/bull_sample 和 /root/bear_sample：獨立新 context 的 opening。
2. 回到同一多空 Agent，逐項交叉回應。
3. /root/risk_sample：新的独立 context，審查雙方 opening 及 reply。
4. 主 Agent 裁決；全部六階段通過 Python validator，結果 insufficient_data。

初次空方測試将 raw file SHA256 与 canonical JSON hash 比較而停止，沒有修改檔案。
依此觀測補上精確 hash 語意与 status 驗證方式；續行后通過。這是實際測試发现並修復的文件問題。

合成樣本在 [examples/synthetic-debate/report.md](examples/synthetic-debate/report.md)，
包含 bundle、stage JSON、state 与 HTML。所有價格完全合成，不能作為真實股票研究。
Canonical Agent ID 是本次真實工具的識別，单凭登記字段无法證明模型獨立性；工具分派紀錄是額外证据。

## 尚未驗證／限制

- 使用者已確認 Secret 建立；無法讀回、也未取得 Token，本次没有使用 Sponsor 真實 API。
- GitHub 資料準備工作流程在 PR 分支，main-only 且須合併到預設分支才可手動啟動。
  不為測試直接寫 main 或將 Secret 提供給未審查分支。因此 Sponsor live data + artifact
  下載端到端尚未驗證。
- 本機 codex-cli 0.162.0-alpha.17.2 的 login status 在沙盒報 Could not find home directory；
  CLI wrapper 使用 Mock 驗證，未在此沙盒執行已登入的 CLI 端到端。
  原生子 Agent forward test 已完成，但不等於 CLI 登入或本機 custom agent discovery 全部驗證。
- Token 化引用／數字字面值可驗證；質性論點、文字數量、投資結論正確性仍需研究與人工審查。
- 自訂 role 指令不提供独立 OS readonly sandbox；子 Agent 繼承父 sandbox/權限。
- 同聊天可續行；不同聊天無法恢復原子 Agent 時，舊 run 留存，新 run 重新研究。
- MOPS 公布日 archive、官方交易曆、台股原版 LangGraph 接線仍未實作。
- 沒有 OpenAI API Key 调用、付費服務開通、自動 credits 購買或實際下單。

## 遠端完整 CI

程式提交 `d1dacee21d9480f4a7a38481099c76ecb712e4b2` 的完整 CI 全通過：
Python 3.11、3.12、3.13、3.14、America/New_York、clean-install smoke 及全庫 Ruff。
Python 3.11 日誌：1300 passed、1 skipped、1 deselected、124 subtests passed；
skip 為未安装選用 langchain_aws，integration 預設排除。data-only CI 各版本也全通過。

- [完整 CI](https://github.com/wei0223/taiwan-tradingagents/actions/runs/38544138944)
- [台股／研究離線 CI](https://github.com/wei0223/taiwan-tradingagents/actions/runs/38544138940)

本文件最後補充上述結果，沒有變更已通過的程式行為。本機、原生子 Agent、CLI Mock
及 Sponsor live 的範圍保持分開，不以離線 CI 代替 Sponsor 端到端實測。
