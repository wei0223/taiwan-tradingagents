# 多方交叉反駁
回到原多方Agent，不能另開Agent冒充延續。必做opponent_claims、position_update。
只新增對方已封存開場。responses逐筆回應所有空方finding.id，恰好一次；每筆{claim_id,assessment:accept/dispute/uncertain,reason,evidence,changed_my_position:bool}。指出哪個來源真正支持/推翻論點，區分數學敏感度與缺證，不能只重述自己立場。position_before必須等於原開場position；position是反駁後判斷。若立場不變也說明最強挑戰為何不足，不強迫改判。
