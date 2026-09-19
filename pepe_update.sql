DELETE FROM user_cards WHERE card_id IN (18, 62);
DELETE FROM cards WHERE id IN (18, 62);

INSERT INTO cards (filename, name, is_active, created_at, rarity) VALUES
('pepe_diamond.jpg','Pepe Diamond',1,datetime('now'),'diamond'),
('pepe_gold.jpg','Pepe Gold',1,datetime('now'),'gold'),
('pepe_platina.jpg','Pepe Platina',1,datetime('now'),'platina'),
('pepe_silver.jpg','Pepe Silver',1,datetime('now'),'silver'),
('pepe_bronze.jpg','Pepe Bronze',1,datetime('now'),'bronze');
