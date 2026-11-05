# STEP 1 公司研究規劃
提出公司專屬問題，不能先給多空結論。payload.questions每筆{id,question,financial_transmission,required_sources:[來源類別]}，說明業務如何傳導到營收、毛利、現金、歸母與稀釋分母。
business_hypotheses為待證假說字串陣列；entities/peers每筆{name,stock_id或null,relationship,reason,status:candidate_requires_verification}，區分子公司、投資、客戶供應商、同業，候選不是已核實關係。根據metadata/company_plan規劃，待程式驗收後再查原件及bind。
