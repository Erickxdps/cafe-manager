-- Una elección puede abarcar categorías completas: los productos nuevos de esa categoría entran solos.
CREATE TABLE promotion_slot_categories (
  slot_id INTEGER NOT NULL REFERENCES promotion_slots(id) ON DELETE CASCADE,
  category_id INTEGER NOT NULL REFERENCES categories(id),
  PRIMARY KEY (slot_id, category_id)
);

-- La elección sembrada "Galleta" pasa a "Panadería" (puede ser galleta, pan, tostada...), solo si nadie la editó.
UPDATE promotion_slots SET name = 'Panadería' WHERE id = 2 AND promotion_id = 1 AND name = 'Galleta';
UPDATE promotions SET description = 'Un café o té + un producto de panadería'
 WHERE id = 1 AND description = 'Un café o té + una galleta';

-- Configuración inicial sugerida del combo (editable), solo si aún no tiene opciones:
-- bebida = cafés y tés; panadería = panadería francesa y galletas.
INSERT INTO promotion_slot_categories(slot_id, category_id)
SELECT 1, id FROM categories WHERE name IN ('Cafés calientes', 'Tés')
  AND EXISTS (SELECT 1 FROM promotion_slots WHERE id = 1 AND name = 'Bebida')
  AND NOT EXISTS (SELECT 1 FROM promotion_slot_options WHERE slot_id = 1);
INSERT INTO promotion_slot_categories(slot_id, category_id)
SELECT 2, id FROM categories WHERE name IN ('Panadería francesa', 'Galletas')
  AND EXISTS (SELECT 1 FROM promotion_slots WHERE id = 2 AND name = 'Panadería')
  AND NOT EXISTS (SELECT 1 FROM promotion_slot_options WHERE slot_id = 2);
