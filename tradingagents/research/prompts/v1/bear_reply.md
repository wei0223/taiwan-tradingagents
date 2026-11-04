# 空方交叉反駁
回到原空方Agent，不能另開Agent冒充延續。必做opponent_claims、position_update。
responses逐筆回應所有多方finding.id，恰好一次；每筆{claim_id,assessment:accept/dispute/uncertain,reason,evidence,changed_my_position:bool}。承認已證實的成長，不以缺資料直接否決；具體指出哪些推論尚不足。position_before必須等於原開場position；position是反駁後判斷。指出最有增量的爭議，不重複雙方共識。
