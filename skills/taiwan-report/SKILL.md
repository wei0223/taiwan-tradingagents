---
name: taiwan-report
description: 在 Codex 輸入「研究台股 2330」、「台股報告 3017」或股票代碼，取得當次資料並產出單次繁體中文研究報告。用於台股基本面、技術面、籌碼面、EPS與估值；不建立持續追蹤、不下單，不用於程式開發或既有預測評分。
---

# 單次台股研究報告

使用者只需股票代碼。完成本次資料準備、研究與交付後結束；不要把資料包下載、操作GitHub、選模型或建立追蹤當成使用者必經步驟。

## 開始

執行本技能的 `scripts/request_report.py --symbol <代碼>`，讀取輸出的 request.json。安裝設定在 `scripts/project.json`；若尚未設定，用 `--repository <本機TradingAgents儲存庫>`，不要猜另一個專案。沒有指定日期時使用台北今天；目前交易資料可能僅到最近完成交易日，必須分開標示。

這個輔助程式只建立請求與資料準備參數，不會自行完成分析。不要把「請求已建立」當成「報告已產生」。接著完成以下資料、研究與交付。

## 資料準備

讀取 [資料準備與執行入口](references/workflow.md)。優先使用使用者現有、已授權的 FinMind Sponsor `main` 工作流程，Secret只留在GitHub；Codex代為啟動、等待、下載與解壓資料包，不要求使用者每次手動準備。建立請求後，立即執行本技能的 `scripts/prepare_data.py --request <request.json>`，以已授權GitHub CLI準備資料。設定中的gh路徑及登入身分須在目前執行帳號可用；登入成功不代表資料已完成。不用瀏覽器代按工作流程，也不拿缺少dispatch能力的連接工具反覆嘗試。不開通新服務、不索取Token。

僅在實際登入或權限阻擋時請使用者處理；不要為正常股票、日期或例行資料擷取重複確認。若無法取得當次資料，交付明確的阻礙與已能驗證的部分，不拿舊資料假裝最新。既有資料只可作歷史比較；使用者指定沿用時，明示資料日。


## 正式八階段（必經）

新單次請求採用 taiwan-eight-stage-v2：資料準備 → 三面向獨立分析 → 各面向多空辯論 → 最多九點核心證據 → 跨面向研究 → 受控補查與必要更新 → 整體多空及風險 → 綜合裁決/HTML/PDF/完整證據包。執行前讀 references/fixed-agents.md，照workflow-status的next_jobs與程式生成contract逐項驗收；不可用階段仍需明列。舊v1僅按保存的版本續讀，不就地重寫。

## 固定計算與公司資料庫（必經）

資料下載完成後執行 `scripts/prepare_research.py --request <request.json>`，依輔助程式輸出的 `fixed_input_dir` 讀取 `fixed-state.json`、`technical-package.json`、`company-plan.json`、`company-instructions.md` 及 `company-input-template.json`。這一步不重新啟動 Sponsor，也不產生研究結論。依賴缺少時安裝儲存庫 `requirements-fixed-research.txt` 的固定版本；不要臨時換公式。

技術面只引用固定計算包：1000個完整官方交易日、明確的參數／暖機期、原始價與還原價分開、完整週K。不得用舊snapshot的technical欄位取代、讓LLM補算或另選參數。驗收失敗仍可交付受限報告，但停止技術指標與推論。technical_package_sha256由程式綁定到最終分析師輸出；AI只依本次contract填欄位，不另增欄位，程式會拒用未綁定的技術論述。

先按固定planner指令完成並驗收研究規劃，再按其公司專屬問題與固定公司查閱計畫檢查近3年年報、8季財報與EPS／現金流附註、60個月營收、近550日法說與股本／募資／更正清單。計畫涵蓋尚未公布期間，不能視為已公告。先用company-latest找已保存版本，只重用核對過的原件；舊快照不代表當期完整。每份公司／同業原件獨立保存來源、公布與捕獲時間、雜湊及版本；每筆事實綁原句、實體頁碼、欄位、期間、單位與審查人。模型假設放assumptions，不進facts。產品組合、產能與客戶認證缺證就分類列缺口，不編造。

使用 `company-import` 保存不可變版本，再以 `company-audit` 驗證；同一欄位多版本必須明選。即使資料不足，也要匯入明列缺口的公司快照，不跳過SOP。不能只填旗標就宣稱官方初版、語意已核對或符合歷史回測。公司資料庫不會自動放寬既有財務／EPS資格。

正式報告必須使用 `upgrade --fixed-pipeline --company-snapshot <company-reference.json>`；公司與分析截止日須一致。財務審查與模型輸入仍沿用原資格規則；三分析師與辯論由固定agent-run提供，不另外寫參數覆蓋。最後確認報告附件包含固定計算包、K線圖、公司計畫、已選事實、原件及版本，而不是只交PDF。

## 研究

股票上市／上櫃與資料後綴由Provider metadata確認，不依代碼猜測。單季、累計、合併／個體、幣別、單位、公布與捕獲時間、版本逐項核對。未知期間停止相關計算，缺列不補零，EPS與加權股數不差分。遵守官方來源冷卻，不繞過拒絕。

程式計算財務、技術與籌碼數字；Codex解釋。正文維持基本面、技術面、籌碼面三位分析師。每位提出完整論述、主要主張、量化證據、最強反證、關鍵分歧及可檢驗的改判條件。公司研究規則可以共用，數值假設不得套用別家公司。

新AI研究與辯論採用taiwan-eight-stage-v2，按[八階段執行入口](references/fixed-agents.md)及workflow-status逐項完成。只使用workflow-prompt產生的指令，結果走workflow-submit及workflow-audit，保留版本、渲染指令、來源、真實派工收據與原始回覆。舊v1按其保存協定與fixed-agents-v1.md執行。主預估EPS仍只用公開FactSet轉引中位數。

正式單次報告必須傳--agent-run及request內agent_protocol。程式直接使用已驗收三分析師與裁決，不在報告階段另寫AI正文；不要另傳analysts/debate-input或reader_research覆蓋結論。多空開場各用fork_turns=none，反駁回原Agent，risk使用第三個新Agent。只有真實工具執行後才能聲稱獨立辯論，收據不是派工的自動證明。能力不足明列受限或停止，不編造。

預估EPS的主欄位採公開可查的FactSet轉引「年度中位數」，目前支援鉅亨news.cnyes.com、m.cnyes.com、gfemobile.cnyes.com的/news/id/文章。只查公開報導，未授權直接FactSet API；不新增付費服務。先按股票代碼及FactSet/EPS搜尋，開啟原文確認公司、年度、報導時間及中位數表，再保存UTF-8 HTML原始bytes、URL、captured_at、sha256、content_base64至extension-input.public_eps={stock_id,as_of,sources:{id:source}}。模板見fixed-inputs/public-eps-input-template.json。程式直接解析EPS表並保留原件；搜尋摘要、新聞目標價、平均數不能替代中位數。取得不到就明列不可用，不以自建EPS補位。

報導日期不是FactSet更新日，未揭露的共識資料日、各年度人數、幣別及基本/稀釋/調整口徑保留未知。年度EPS不能當NTM、不能拆四季；不把公開轉引冒充直接FactSet授權資料。平均值/高低值/營收/目標價均不作本次主欄位。自建四季模型若有必要，只作有標示的敏感度挑戰，不能寫成FactSet數字。此版未自動用未確認口徑的EPS計算PE。完整規則見docs/taiwan/public-consensus-eps.md。

長期價值、現金持續性與5–20日交易觀察分開。未觸發條件的R值只是情境測量，不是預期報酬；沒有交易優勢證據就如實說明，不為此啟動長期回測專案。

## 研究深度與閱讀層

將已核對公司/同業原件匯入company資料庫，按固定fundamental模板在本次contract的answers、findings與summary（舊v1依其原欄位）說明競爭命題、財務傳導與失效條件；不另填reader_research的AI正文。沒有獨立產品毛利或認證時，明示公司自述與假設，不能把伺服器應用占比當液冷占比。同業期間、範圍與單位一致，鄰近業務不作直接PE錨。

用現有四季瀑布求現價在明示PE下所需收入、毛利率、股數，逐項只變一個因素；指出達成難度，不能稱市場共識。單季辯論壓力橋不乘四冒充四季。多空摘要只留具體EPS差、來源及改判條件，完整重複原稿入證據包。

市場使用同時間軸的價格、成交量與法人淨額；缺日/分類保留未知。解釋承接/賣壓共現，不推測動機或因果。閱讀版首頁先給投資判斷，空白產業表移到缺口；工程紀錄在audit.html。沿用舊行情做內容改版時，明示最後日及原捕獲時間，不偽裝更新。

## 交付後結束

用正式 `upgrade` 模板另存報告：首頁研究結論與資料日期，三位分析師正文、多空核心分歧與裁決、EPS與估值、來源／限制。提供 report.html、可生成時的report.pdf，以及完整evidence.zip；證據包包含資料、原件、計算、假設與實際辯論紀錄。驗證引用、數值與可開啟檔案，不以測試通過冒充研究準確。

不執行 publish-forward、track-release、建立／恢復automation或定期掃描，不要求發布後評分才完成報告。只記錄這份報告的實際產生時間與來源，不能覆寫舊報告／舊預測。所有原有監測保持使用者設定的暫停狀態。

沿用目前Codex登入與模型，不呼叫付費LLM API，不新增付費服務、不下單。資料／驗證或Agent失敗時合理停止並交付狀態，不無限重試。

最後給出可點擊的報告與附件連結、一段結論、重要缺口，然後結束；不要自動提出下一輪持續追蹤任務。

## 官方交易日曆自動復原

prepare_research會自動核對共用calendar-cache中的官方月份原件，只查缺少的月份；403、429或過頻頁面即停止該主機並保存冷卻。不以星期或個股K線推算交易日。舊blocked_history會另建fixed-inputs-recovery版本，不覆寫已保存計算與研究。後續company-intake、workflow與upgrade一律使用輸出的effective_snapshot_path及fixed_input_dir；原始preparation.snapshot只作取得紀錄。fixed-inputs-current.json是目前版本定位，不是修改舊報告。復原不足仍停止受影響技術計算，繼續已驗證的研究，不重送Sponsor或要求使用者手工補日曆。
