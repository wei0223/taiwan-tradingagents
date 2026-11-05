# Taiwan TradingAgents

台股專用的 Codex 研究工具。輸入股票代碼，取得當次資料，產出基本面、技術面與籌碼面分析師報告，以及獨立多空交叉反駁與風險裁決。

## 日常使用

在已設定此專案的 Codex 輸入：

> 研究台股 3017

流程依序完成：代碼與上市櫃辨識 → Sponsor 資料擷取 → 固定公司研究計畫 → 官方原件查證 → 1,000 個完整交易日的程式指標 → 公開可核對的 FactSet EPS 轉引 → 固定 AI 研究與辯論 → HTML、可生成時的 PDF、完整證據 ZIP。

AI 研究使用目前登入的 Codex；Python 本身不代替 AI 研究。只有資料包、離線驗證或 Mock 通過，不代表已完成真實分析。資料不足時交付受限報告，不補值或強行估價。

完成一份即結束，不建立持續追蹤、不下單、不呼叫付費 LLM API。

## 安裝與命令

需要 Python 3.11 以上及已登入的 Codex。首次使用單次報告需設定技能的本機儲存庫與輸出路徑；詳見 [單次報告設定](docs/taiwan/on-demand-report.md) 及 [技能工作流程](skills/taiwan-report/references/workflow.md)。

```bash
# 完整技術指標、PDF 原件讀取與報告輸出
python -m pip install ".[fixed-research,research-pdf]"

# 命令說明；兩個安裝入口相同
taiwan-research --help
tradingagents --help
python -m tradingagents.research --help
python main.py --help
```

基本安裝只需要 requests 與 python-dotenv。固定技術引擎使用鎖定版本的 TA-Lib、NumPy 及 pypdf；PDF 報告需 reportlab 與可用中文字型。程式不再安裝 LangChain、LangGraph、模型 SDK、Yahoo Finance 或舊互動 CLI。

FinMind Sponsor Token 通常只放在 GitHub Actions Secret `FINMIND_TOKEN`，由可信任的 main 分支執行 [資料準備流程](.github/workflows/taiwan-prepare.yml)。GitHub CLI 使用既有授權，不把 Secret 交給 Agent。只有明確選擇本機準備資料時才自行設定本機環境；不要提交或分享 Token。

## 資料與研究規則

- 保留原始資料、來源、公布時間、捕獲時間、SHA256 與版本；禁止使用分析截止後才可得的資料。
- 財報欄位逐項辨識單季／累計、合併／個體、單位與版本。符合版本橋接才差分期間金額；EPS、加權股數及期末餘額不差分。
- 技術指標由程式計算；行情不合格時停止相關計算。公開年度 FactSet EPS 不自動視為 NTM、已確認稀釋口徑或合理估值。
- 正文只有三位分析師；已實現獲利、現金持續性、估值與短期交易分開裁決。
- 舊預測、原件與發布時間不改寫。歷史研究／封存讀取命令保留相容用途，不代表預設追蹤或已有交易優勢。

## 文件

| 主題 | 文件 |
|---|---|
| 固定公司資料與技術 SOP | [固定流程](docs/taiwan/fixed-research-pipeline.md) |
| AI 研究及辯論契約 | [固定 AI 指令](docs/taiwan/fixed-agent-protocol.md) |
| FactSet EPS 轉引 | [公開共識 EPS](docs/taiwan/public-consensus-eps.md) |
| 財報期間與版本 | [財務期間](docs/taiwan/financial-periods.md) |
| 官方來源與缺口 | [來源清單](docs/taiwan/official-source-registry.md)、[缺口結案](docs/taiwan/gap-closure.md) |
| 報告與原件資格 | [審查規則](docs/taiwan/review-rules.md)、[發布資格](docs/taiwan/publishing-quality.md) |
| 台股專用重構 | [範圍與遷移](docs/taiwan/taiwan-only.md) |

## 開發驗證

```bash
python -m pip install ".[dev,fixed-research,research-pdf]"
python -m unittest discover -s tests/taiwan -v
pytest -q
ruff check .
```

測試預設使用 Mock／離線資料，不消耗 Sponsor 額度。CI 驗證 Python 3.11–3.14、不同時區、乾淨安裝、提示詞封裝與台股合約。測試成功不等同研究準確或策略有效。

## 上游與授權

本專案衍生自 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)，保留原 Apache-2.0 [LICENSE](LICENSE) 與上游歷史。美股／跨市場 LangGraph、模型連線、CLI、供應商、記憶與回測已自目前程式樹移除；舊版本仍可在 Git 歷史查閱。`CHANGELOG.md` 的舊發布與 `architecture-audit.md` 是歷史紀錄，非目前支援清單。
