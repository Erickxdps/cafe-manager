-- Historial de pagos anulados. `payments` conserva SOLO los pagos vigentes (las sumas de caja no
-- necesitan filtros); al cancelar un pedido pagado, su pago se copia aquí antes de quitarlo.
CREATE TABLE voided_payments (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id),
  workday_id INTEGER NOT NULL REFERENCES workdays(id),
  method TEXT NOT NULL CHECK (method IN ('cash','qr')),
  amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
  paid_at TEXT,
  voided_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX ix_voided_workday ON voided_payments(workday_id);

-- Recupera los pagos anulados antes de esta versión a partir del registro (detalle "cash 1200").
INSERT INTO voided_payments(order_id, workday_id, method, amount_cents, paid_at, voided_at)
SELECT o.id, o.workday_id,
       substr(a.detail, 1, instr(a.detail, ' ') - 1),
       CAST(substr(a.detail, instr(a.detail, ' ') + 1) AS INTEGER),
       (SELECT MAX(p.at) FROM audit_log p WHERE p.entity='pedido' AND p.entity_id=o.id AND p.action='pagar' AND p.id < a.id),
       a.at
FROM orders o
JOIN audit_log a ON a.entity='pedido' AND a.entity_id=o.id AND a.action='anular_pago'
WHERE o.status='cancelled'
  AND a.id = (SELECT MAX(id) FROM audit_log WHERE entity='pedido' AND entity_id=o.id AND action='anular_pago')
  AND substr(a.detail, 1, instr(a.detail, ' ') - 1) IN ('cash','qr')
  AND NOT EXISTS (SELECT 1 FROM payments WHERE order_id=o.id);
