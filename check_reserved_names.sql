SELECT cn.name, cn.status, cn.owner_id, cn.user_card_id, cn.list_price,
       uc.custom_name, uc.card_id
FROM card_names cn
LEFT JOIN user_cards uc ON uc.id = cn.user_card_id
WHERE cn.name IN ('rzabeyda','zabeyda','zzabeyda');
