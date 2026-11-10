-- Esquema base. Dinero en centavos (INTEGER). El stock NO se guarda: se calcula
-- sumando inventory_movements (vista variant_stock). Lo vendible (precio/stock) vive en product_variants.

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE categories (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  sort_order INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

CREATE TABLE products (
  id INTEGER PRIMARY KEY,
  category_id INTEGER NOT NULL REFERENCES categories(id),
  name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  photo TEXT,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  is_sample INTEGER NOT NULL DEFAULT 0 CHECK (is_sample IN (0,1)),
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- Todo producto tiene al menos una variante (la "por defecto" con name = '').
CREATE TABLE product_variants (
  id INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL REFERENCES products(id),
  name TEXT NOT NULL DEFAULT '' COLLATE NOCASE,
  price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
  tracks_stock INTEGER NOT NULL DEFAULT 0 CHECK (tracks_stock IN (0,1)),
  min_stock INTEGER NOT NULL DEFAULT 0 CHECK (min_stock >= 0),
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  UNIQUE (product_id, name)
);
CREATE INDEX ix_variants_product ON product_variants(product_id);

CREATE TABLE recipes (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  drink_type TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  photo TEXT,
  product_id INTEGER REFERENCES products(id) ON DELETE SET NULL
);
CREATE TABLE recipe_steps (
  id INTEGER PRIMARY KEY,
  recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  text TEXT NOT NULL,
  UNIQUE (recipe_id, position)
);

-- Promociones: cada "slot" (bebida, galleta...) tiene opciones elegibles entre variantes.
CREATE TABLE promotions (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);
CREATE TABLE promotion_slots (
  id INTEGER PRIMARY KEY,
  promotion_id INTEGER NOT NULL REFERENCES promotions(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE promotion_slot_options (
  slot_id INTEGER NOT NULL REFERENCES promotion_slots(id) ON DELETE CASCADE,
  variant_id INTEGER NOT NULL REFERENCES product_variants(id),
  PRIMARY KEY (slot_id, variant_id)
);

CREATE TABLE workdays (
  id INTEGER PRIMARY KEY,
  work_date TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
  opening_fund_cents INTEGER NOT NULL DEFAULT 10000 CHECK (opening_fund_cents >= 0),
  reserve_cents INTEGER NOT NULL DEFAULT 10000 CHECK (reserve_cents >= 0),
  counted_cash_cents INTEGER CHECK (counted_cash_cents IS NULL OR counted_cash_cents >= 0),
  opened_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  closed_at TEXT,
  notes TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX ux_one_open_workday ON workdays(status) WHERE status = 'open';

CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  workday_id INTEGER NOT NULL REFERENCES workdays(id),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','paid','cancelled')),
  subtotal_cents INTEGER NOT NULL DEFAULT 0 CHECK (subtotal_cents >= 0),
  discount_cents INTEGER NOT NULL DEFAULT 0 CHECK (discount_cents >= 0),
  total_cents INTEGER NOT NULL DEFAULT 0,
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  paid_at TEXT,
  cancelled_at TEXT,
  CHECK (discount_cents <= subtotal_cents),
  CHECK (total_cents = subtotal_cents - discount_cents)
);
CREATE INDEX ix_orders_workday ON orders(workday_id, status);

-- Precio y nombre quedan congelados en la línea (histórico). Un combo = una línea con quantity 1.
CREATE TABLE order_items (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  variant_id INTEGER REFERENCES product_variants(id),
  promotion_id INTEGER REFERENCES promotions(id),
  name_snapshot TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
  CHECK ((variant_id IS NULL) <> (promotion_id IS NULL))
);
CREATE INDEX ix_items_order ON order_items(order_id);

CREATE TABLE order_item_components (
  id INTEGER PRIMARY KEY,
  order_item_id INTEGER NOT NULL REFERENCES order_items(id) ON DELETE CASCADE,
  slot_id INTEGER NOT NULL REFERENCES promotion_slots(id),
  variant_id INTEGER NOT NULL REFERENCES product_variants(id),
  name_snapshot TEXT NOT NULL
);

-- Un solo pago por pedido (se actualiza si el pedido pagado cambia).
CREATE TABLE payments (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id),
  workday_id INTEGER NOT NULL REFERENCES workdays(id),
  method TEXT NOT NULL CHECK (method IN ('cash','qr')),
  amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
  paid_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX ix_payments_workday ON payments(workday_id, method);

-- quantity con signo: entradas > 0, salidas < 0. Las ventas siempre referencian su pedido.
CREATE TABLE inventory_movements (
  id INTEGER PRIMARY KEY,
  variant_id INTEGER NOT NULL REFERENCES product_variants(id),
  workday_id INTEGER REFERENCES workdays(id),
  kind TEXT NOT NULL CHECK (kind IN ('initial','receipt','sale','adjust_pos','adjust_neg','sale_reversal')),
  quantity INTEGER NOT NULL CHECK (quantity <> 0),
  order_id INTEGER REFERENCES orders(id),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  CHECK (kind IN ('sale','adjust_neg') AND quantity < 0 OR kind IN ('initial','receipt','adjust_pos','sale_reversal') AND quantity > 0),
  CHECK (kind NOT IN ('sale','sale_reversal') OR order_id IS NOT NULL)
);
CREATE INDEX ix_movements_variant ON inventory_movements(variant_id);
CREATE INDEX ix_movements_order ON inventory_movements(order_id);

CREATE VIEW variant_stock AS
  SELECT v.id AS variant_id, COALESCE(SUM(m.quantity), 0) AS stock
  FROM product_variants v LEFT JOIN inventory_movements m ON m.variant_id = v.id
  GROUP BY v.id;

CREATE TABLE expenses (
  id INTEGER PRIMARY KEY,
  workday_id INTEGER NOT NULL REFERENCES workdays(id),
  concept TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX ix_expenses_workday ON expenses(workday_id);

-- Plantillas de checklist (editables) y progreso por jornada.
CREATE TABLE checklists (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL UNIQUE CHECK (kind IN ('open','close')),
  name TEXT NOT NULL
);
CREATE TABLE checklist_items (
  id INTEGER PRIMARY KEY,
  checklist_id INTEGER NOT NULL REFERENCES checklists(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  text TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);
CREATE TABLE workday_tasks (
  id INTEGER PRIMARY KEY,
  workday_id INTEGER NOT NULL REFERENCES workdays(id),
  kind TEXT NOT NULL CHECK (kind IN ('open','close')),
  position INTEGER NOT NULL,
  text TEXT NOT NULL,
  done INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0,1)),
  done_at TEXT
);
CREATE INDEX ix_tasks_workday ON workday_tasks(workday_id, kind);

CREATE TABLE personal_notes (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL DEFAULT 'note' CHECK (kind IN ('note','reminder','task')),
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  done INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0,1)),
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  action TEXT NOT NULL,
  entity TEXT NOT NULL,
  entity_id INTEGER,
  detail TEXT NOT NULL DEFAULT ''
);
