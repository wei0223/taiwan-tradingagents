# 固定研究流程 v1

使用者輸入股票代碼後，Codex代為執行一次資料準備、固定計算、公司原件查閱、分析與報告。沒有新增排程、下單或付費LLM呼叫。

## 執行順序

1. request_report建立不可變請求；prepare_data沿用已授權main Sponsor流程，核對run與artifact。
2. prepare_research讀同一artifact，執行fixed-inputs；不重新dispatch。輸出technical-package、公司計畫、空白輸入契約、查閱指令及雜湊狀態。
3. 先完成agents/stages/planner的固定研究規劃及agent-submit，再按動態公司問題、company-plan和company-instructions查官方原件；重用原件須保留原捕獲時間。公司與每家同業分開建檔。company-import驗證後保存不可變SourceStore原件與事實版本；company-audit重新驗證。company-latest僅找舊版本，不證明當期新鮮度。
4. Codex解釋程式數字，依公司規則建立假設；研究與獨立多空挑戰依fixed-agent-protocol.md的10個固定階段執行。技術論述須綁定technical_package_sha256，未綁定就只列程式結果。
5. upgrade --fixed-pipeline --company-snapshot <reference> --agent-run <agents>另存報告，產出日／週K、公司原件、固定計算包及可搬移證據包。缺公司快照直接拒絕；允許明列缺口的快照。不得修改舊發布紀錄。

## 公司資料契約

company-plan：近3個完整年度、8個已結束季度、60個完整月份；近550日法說（選最新可取得4份）、股本募資及更正查閱範圍。季度結束不代表財報已公告。查閱與補件在本次請求內有界執行，不建立長期監測。

八類來源：annual_report、financial_statement、eps_note、cashflow_statement、investor_presentation、monthly_revenue、capital_event、correction_index。2330、3017、3231分別載入晶圓代工、散熱、ODM規則；其他股票使用generic。候選同業只作查證清單，不視為已證實可比公司。

輸入使用fixed-inputs/company-input-template.json。sources以id索引，每份有stock_id/category/revision_id/source_url/published_at/captured_at/sha256/content_base64；未知公布時間為null。facts分numeric及company_statement，保存id/metric/source_id/page/quote/column_label/context_quotes/reviewer/reviewed_at；numeric另保存raw_value/multiplier/unit/period_start/period_end/basis/scope。EPS及加權股數另標basic/diluted，只接受直接原始抄錄，不差分。資產負債用point_in_time。允許欄位清單由company-plan輸出。

原文必須存在於實際PDF頁面或文字bytes，數值與倍率由Decimal核對。這能防止引用不存在，不能自動證明表格語意、來源權威或公司說法屬實；reviewer必須真的核對原件。同欄位多版本用selected_fact_ids明選；所有版本仍保存。assumptions與facts分開，每個假設保存已選事實id、理由、不確定性。gaps使用pending/not_published/unavailable/not_applicable並寫原因。

category有文件不代表歷史完整。所有新公司快照strict_pit_ready=false，不能因匯入、勾旗標或現今回補自動取得歷史績效資格；既有財務期間／版本銜接驗收繼續適用。

## 固定技術引擎

requirements-fixed-research.txt固定TA-Lib0.8.3、NumPy2.3.5、pypdf6.1.1；引擎與實作雜湊記入包內，參數、暖機期、價格口徑均逐欄輸出。相同輸入、版本與環境應產生相同數字。未知、暖機及程式自訂除零為null，不補零。TA-Lib原生除零約定沿用固定版本。

歷史必須包含截止日前最後1000個官方交易日的完整有效OHLCV，驗證日曆原件、日期覆蓋、公司、重複、OHLC及時間。超過1000筆但中間缺日仍拒絕；不替停牌日造K線。只使用最後1000筆固定初始化。若歷史起點太近，最多三段、每次向前延長兩年；來源拒絕、內部缺日或失效日曆不重試。當日未完成K排除。

原始價及供應商還原價分開；還原價缺失不冒充原始價。沒有公司行動與成交量口徑證明時，還原價的量價指標停用。完整週K從日K聚合，排除首個不完整週與尚未完成週。沒有向前看，Ichimoku可見雲只用歷史移位，不把未來收盤回填。

固定指標：SMA/EMA/WMA(5/10/20/50/60/120/200/240)、HMA/KAMA(20/60)、RSI(6/12/14/24)、MACD(12/26/9)、KD(9/3/3 SMA)、KDJ(9/3/3 Wilder RMA)、StochRSI、ADX/ADXR/DI、CCI、WilliamsR、ROC、MOM、TRIX、PPO、UltimateOscillator、Aroon、ATR/NATR、標準差、布林通道、Keltner、Donchian、SAR、Supertrend、OBV、AD/ADOSC、MFI、CMF、PVT、VWMA、成交量均線與前20日量比、BIAS、PSY、VR、AR/BR/CR、BBI、Ichimoku、均線斜率及區間報酬。現行108個輸出欄位，含不可用欄位；不是108個獨立訊號。61個TA-Lib K線型態以原分數輸出，非機率。

真實VWAP_TURNOVER目前不可用：須另核對成交金額的單位與交易範圍；不拿典型價代理冒充真VWAP。日K無法重建盤中VWAP。交叉事件是觀察，不是進場指令或統計優勢。

## 邊界與升級

目前為台股專用；未開 fixed 旗標的既有台股研究入口保留相容用途，新單次技能要求 fixed 旗標。Sponsor 與本機 fixed-inputs 都須嚴格驗收 1,000 日；是否完成真實擷取須依本次 run 與資料品質判定，不能用封存或 Mock 結果冒充當次實測。原版美股流程已移除，見 [範圍遷移](taiwan-only.md)。
