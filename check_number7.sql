SELECT cn.number, cn.status, cn.owner_id, cn.user_card_id, cn.list_price,
       uc.custom_name, uc.number_override, uc.card_id
FROM card_numbers cn
LEFT JOIN user_cards uc ON uc.id = cn.user_card_id
WHERE cn.number = 7;
