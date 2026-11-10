-- Unidades de un pedido pagado que NO descontaron existencias porque el producto no controlaba
-- existencias al venderse. Regla exacta para pedidos pagados, por variante:
--   descontado (movimientos del pedido) + sin descontar (esta tabla) = consumo de sus líneas.
-- Así activar o quitar el control de existencias después no descuadra el inventario.
CREATE TABLE order_stock_exempt (
  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  variant_id INTEGER NOT NULL REFERENCES product_variants(id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  PRIMARY KEY (order_id, variant_id)
);

-- Pedidos pagados existentes: lo que consumieron y no descontaron queda registrado como "sin descontar".
INSERT INTO order_stock_exempt(order_id, variant_id, quantity)
SELECT c.order_id, c.variant_id, c.qty - COALESCE(d.qty, 0)
FROM (
  SELECT order_id, variant_id, SUM(qty) AS qty FROM (
    SELECT i.order_id, i.variant_id, i.quantity AS qty FROM order_items i WHERE i.variant_id IS NOT NULL
    UNION ALL
    SELECT i.order_id, oc.variant_id, i.quantity FROM order_item_components oc JOIN order_items i ON i.id = oc.order_item_id
  ) GROUP BY order_id, variant_id
) c
JOIN orders o ON o.id = c.order_id AND o.status = 'paid'
LEFT JOIN (
  SELECT order_id, variant_id, -SUM(quantity) AS qty FROM inventory_movements
  WHERE kind IN ('sale','sale_reversal') GROUP BY order_id, variant_id
) d ON d.order_id = c.order_id AND d.variant_id = c.variant_id
WHERE c.qty - COALESCE(d.qty, 0) > 0;
