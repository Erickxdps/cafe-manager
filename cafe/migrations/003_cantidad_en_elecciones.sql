-- Cada elección de una promoción puede pedir varias unidades (p. ej. "2 galletas", cada una a elegir entre las opciones).
ALTER TABLE promotion_slots ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 1 AND quantity <= 10);
