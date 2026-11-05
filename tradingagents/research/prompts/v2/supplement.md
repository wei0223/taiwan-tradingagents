# STEP 6 受控補查計畫
針對cross中material/unknown且missing_data非空的問題，最多3筆查詢、一次批次，不能自己執行shell、改模型或自由拼URL。payload={queries,skip_reason}。
queries每筆{id,issue_id,operation,period,edition,rationale,affected_dimensions}。
operation僅financial_listing(period=YYYY,edition=null)、financial_original(period=YYYY-Qn,edition=AI1或AIA)、monthly_revenue(period=YYYY-MM,edition=null)、capital_current(period=null,edition=null)。公司固定本次股票，期間已結束且不超過截止日。查詢目前股數不證明歷史或稀釋分母。
沒有合理查詢就queries空，skip_reason說明；不能為完成流程而硬查。程式負責HTTP/冷卻/9次HTTP上限及原件封存，遭拒不換域名繞過。原件捕獲後仍須company-import/audit，才能resolve為reviewed並建立新revision；只有捕獲或未知初版一律不當已核實數字。超過範圍的IR、公開EPS或行情需求保留缺口，下一次研究另處理。
