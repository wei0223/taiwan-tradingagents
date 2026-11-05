# STEP 4 核心證據萃取／補查後更新
payload.faces恰好三筆：{dimension,claim_id,reason,items,unavailable_reason}。
每面向選一個已接受的主要finding；items最多3筆，恰好1筆primary，其餘可support/counter，每筆{role,claim_id,reference:{input_id,pointer,quote},correlation_group}。reference必須原封引用該已接受claim的evidence。未取得證據用claim_id=null、items空並說明原因，不為湊9項填資料。
最多九項是摘要上限，完整證據不可刪除。correlation_group需指出同來源/同財報/同價格序列/同動能家族的相關性，不把MA、KD、RSI、MACD各當獨立支持。
補查後只從三位最新分析師挑選新的核心主張，不把舊revision的原件數字偷渡為新證據。
