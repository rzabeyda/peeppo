UPDATE card_numbers
SET status = 'owned',
    owner_id = (SELECT user_id FROM user_cards WHERE id = 316),
    updated_at = datetime('now')
WHERE number = 7;

SELECT cn.number, cn.status, cn.owner_id, cn.user_card_id, cn.list_price,
       uc.custom_name, uc.number_override
FROM card_numbers cn
LEFT JOIN user_cards uc ON uc.id = cn.user_card_id
WHERE cn.number = 7;
