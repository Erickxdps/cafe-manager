"""Pedidos, pagos y su efecto en el inventario.

Regla central: el inventario de un pedido se calcula siempre por DIFERENCIA.
  objetivo  = lo que el pedido debe tener descontado (pagado: su consumo; pendiente/cancelado: nada)
  actual    = suma neta de sus movimientos 'sale'/'sale_reversal'
  se registra solo (objetivo - actual).
Por eso pagar dos veces, editar o cancelar nunca duplican ni pierden movimientos.

Cambios en "controla existencias" de un producto con pedidos ya pagados:
  - Lo vendido mientras el producto no controlaba existencias queda registrado "sin descontar"
    (tabla order_stock_exempt). Activar el control después no lo descuenta de forma retroactiva.
  - Cancelar o quitar unidades devuelve SIEMPRE lo que realmente se descontó, controle o no hoy.
  - Regla exacta de un pedido pagado: descontado + sin descontar = consumo de sus líneas.
"""
from ..db import audit, get_db, transaction
from ..errors import BusinessRuleError, NotFoundError, ValidationError
from ..money import format_bs, parse_bs
from . import workdays
from .catalog import VARIANT_SQL, display_name

METHODS = {"cash": "Efectivo", "qr": "QR"}
MAX_QTY = 999


# ---------- lectura ----------
def _order_row(conn, order_id):
    row = conn.execute("SELECT o.*, w.status AS workday_status FROM orders o JOIN workdays w ON w.id=o.workday_id "
                       "WHERE o.id=?", (order_id,)).fetchone()
    if not row:
        raise NotFoundError("El pedido no existe.")
    return row


def _items(conn, order_id):
    items = []
    for it in conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order_id,)):
        comps = [dict(c) for c in conn.execute(
            "SELECT slot_id, variant_id, name_snapshot AS name FROM order_item_components WHERE order_item_id=? ORDER BY id",
            (it["id"],))]
        items.append({"item_id": it["id"], "variant_id": it["variant_id"], "promotion_id": it["promotion_id"],
                      "name": it["name_snapshot"], "quantity": it["quantity"], "unit_price_cents": it["unit_price_cents"],
                      "selections": _group_selections(comps),
                      "components_text": " + ".join(c["name"] for c in comps), "components": comps})
    return items


def _group_selections(comps):
    out = {}
    for c in comps:
        out.setdefault(str(c["slot_id"]), []).append(c["variant_id"])
    return out


def get_order(order_id):
    conn = get_db()
    row = _order_row(conn, order_id)
    pay = conn.execute("SELECT method, amount_cents FROM payments WHERE order_id=?", (order_id,)).fetchone()
    return {"id": row["id"], "workday_id": row["workday_id"], "status": row["status"], "notes": row["notes"],
            "subtotal_cents": row["subtotal_cents"], "discount_cents": row["discount_cents"],
            "total_cents": row["total_cents"], "created_at": row["created_at"], "paid_at": row["paid_at"],
            "workday_open": row["workday_status"] == "open", "method": pay["method"] if pay else None,
            "items": _items(conn, order_id)}


def list_orders(workday_id):
    rows = get_db().execute(
        "SELECT o.id, o.status, o.total_cents, o.created_at, p.method, "
        "(SELECT GROUP_CONCAT(CASE WHEN quantity>1 THEN name_snapshot||' ×'||quantity ELSE name_snapshot END, ', ') "
        " FROM order_items WHERE order_id=o.id) AS resumen "
        "FROM orders o LEFT JOIN payments p ON p.order_id=o.id WHERE o.workday_id=? "
        "ORDER BY CASE o.status WHEN 'pending' THEN 0 WHEN 'paid' THEN 1 ELSE 2 END, o.id DESC", (workday_id,))
    return [dict(r) for r in rows]


def sale_catalog():
    """Variantes activas para el selector de pedidos."""
    from .promotions import sellable_variants
    return sellable_variants()


# ---------- validación y armado de líneas ----------
def _build_lines(conn, order_id, lines):
    if not isinstance(lines, list) or not lines:
        raise ValidationError("Agrega al menos un producto al pedido.")
    existing = {}
    if order_id:
        existing = {i["item_id"]: i for i in _items(conn, order_id)}
    keep_ok = {i["variant_id"] for i in existing.values() if i["variant_id"]}
    keep_ok |= {c["variant_id"] for i in existing.values() for c in i["components"]}
    keep_promos = {i["promotion_id"] for i in existing.values() if i["promotion_id"]}
    built = []
    for raw in lines:
        if not isinstance(raw, dict):
            raise ValidationError("Línea de pedido no válida.")
        try:
            qty = int(raw.get("quantity", 1))
        except (TypeError, ValueError):
            raise ValidationError("La cantidad debe ser un número entero.") from None
        if not 1 <= qty <= MAX_QTY:
            raise ValidationError(f"La cantidad debe estar entre 1 y {MAX_QTY}.")
        item = None
        if raw.get("item_id"):
            item = existing.get(int(raw["item_id"]))
            if not item:
                raise ValidationError("Una línea no pertenece a este pedido.")
        if raw.get("promotion_id"):
            built.append(_build_combo(conn, raw, qty, item, keep_ok, keep_promos))
        else:
            built.append(_build_variant_line(conn, raw, qty, item, keep_ok))
    return built


def _build_variant_line(conn, raw, qty, item, keep_ok):
    try:
        vid = int(raw.get("variant_id"))
    except (TypeError, ValueError):
        raise ValidationError("Producto no válido.") from None
    if item and item["variant_id"] != vid:
        raise ValidationError("Una línea no corresponde a su producto.")
    row = conn.execute(VARIANT_SQL + " WHERE v.id=?", (vid,)).fetchone()
    if not row:
        raise ValidationError("El producto no existe.")
    name = display_name(row["product_name"], row["variant_name"])
    if not (row["variant_active"] and row["product_active"]) and vid not in keep_ok:
        raise ValidationError(f"«{name}» está desactivado.")
    return {"variant_id": vid, "promotion_id": None, "quantity": qty, "components": [],
            "name": item["name"] if item else name,  # el nombre y precio del pedido son históricos
            "unit_price_cents": item["unit_price_cents"] if item else row["price_cents"]}


def _is_option(conn, slot_id, variant_id):
    """Una variante es opción de la elección si se marcó suelta o si pertenece a una categoría marcada."""
    return conn.execute(
        "SELECT 1 FROM promotion_slot_options WHERE slot_id=? AND variant_id=? UNION ALL "
        "SELECT 1 FROM promotion_slot_categories sc JOIN products p ON p.category_id=sc.category_id "
        "JOIN product_variants v ON v.product_id=p.id WHERE sc.slot_id=? AND v.id=?",
        (slot_id, variant_id, slot_id, variant_id)).fetchone() is not None


def _build_combo(conn, raw, qty, item, keep_ok, keep_promos):
    if qty != 1:
        raise ValidationError("Cada combo es una línea con cantidad 1; agrega otra línea para más combos.")
    promo = conn.execute("SELECT * FROM promotions WHERE id=?", (raw["promotion_id"],)).fetchone()
    if not promo:
        raise ValidationError("La promoción no existe.")
    if item and item["promotion_id"] != promo["id"]:
        raise ValidationError("Una línea no corresponde a su promoción.")
    if (not promo["active"] or promo["archived"]) and promo["id"] not in keep_promos:
        raise ValidationError(f"La promoción «{promo['name']}» está desactivada.")
    selections = raw.get("selections") or {}
    unchanged = item is not None and _normalized(selections) == _normalized(item["selections"])
    # lo ya vendido se respeta aunque la promoción haya cambiado después; validar solo elecciones nuevas
    comps = [dict(c) for c in item["components"]] if unchanged else _chosen_components(conn, promo, selections, keep_ok)
    return {"variant_id": None, "promotion_id": promo["id"], "quantity": 1, "components": comps,
            "name": item["name"] if item else promo["name"],
            "unit_price_cents": item["unit_price_cents"] if item else promo["price_cents"]}


def _normalized(selections):
    """{'3': [7]} y {'3': 7} significan lo mismo; None si el formato no es válido."""
    try:
        return {str(k): sorted(int(v) for v in (c if isinstance(c, list) else [c])) for k, c in selections.items()}
    except (AttributeError, TypeError, ValueError):
        return None


def _chosen_components(conn, promo, selections, keep_ok):
    comps = []
    for slot in conn.execute("SELECT * FROM promotion_slots WHERE promotion_id=? ORDER BY position, id", (promo["id"],)):
        chosen = selections.get(str(slot["id"]))
        chosen = chosen if isinstance(chosen, list) else [chosen]
        try:
            vids = [int(v) for v in chosen]
        except (TypeError, ValueError):
            vids = []
        if len(vids) != slot["quantity"]:
            raise ValidationError(f"Elige {slot['quantity']} opción(es) para «{slot['name']}».")
        for vid in vids:
            if not _is_option(conn, slot["id"], vid):
                raise ValidationError(f"Esa opción no está disponible en «{slot['name']}».")
            row = conn.execute(VARIANT_SQL + " WHERE v.id=?", (vid,)).fetchone()
            cname = display_name(row["product_name"], row["variant_name"])
            if not (row["variant_active"] and row["product_active"]) and vid not in keep_ok:
                raise ValidationError(f"«{cname}» está desactivado.")
            comps.append({"slot_id": slot["id"], "variant_id": vid, "name": cname})
    return comps


def _consumption(built):
    """Unidades de cada variante que el pedido consume. Un combo consume sus componentes, no se cuenta aparte."""
    out = {}
    for b in built:
        if b["variant_id"]:
            out[b["variant_id"]] = out.get(b["variant_id"], 0) + b["quantity"]
        for c in b["components"]:
            out[c["variant_id"]] = out.get(c["variant_id"], 0) + b["quantity"]
    return out


# ---------- inventario ----------
def _deducted(conn, order_id):
    rows = conn.execute("SELECT variant_id, -SUM(quantity) AS n FROM inventory_movements WHERE order_id=? "
                        "AND kind IN ('sale','sale_reversal') GROUP BY variant_id", (order_id,))
    return {r["variant_id"]: r["n"] for r in rows if r["n"]}


def _variants(conn, ids):
    ids = list(ids)
    if not ids:
        return {}
    rows = conn.execute(VARIANT_SQL + f" WHERE v.id IN ({','.join('?' * len(ids))})", ids)
    return {r["variant_id"]: r for r in rows}


def _targets(conn, wanted, current, exempt=None):
    """Unidades que el pedido debe tener descontadas por variante (ver reglas al inicio del módulo).
    wanted: consumo de sus líneas; current: lo ya descontado; exempt: lo registrado como sin descontar."""
    exempt = exempt or {}
    ids = set(wanted) | set(current)
    info = _variants(conn, ids)
    targets = {}
    for vid in ids:
        want = wanted.get(vid, 0)
        if info[vid]["tracks_stock"]:
            targets[vid] = max(0, want - exempt.get(vid, 0))
        else:  # ya no controla: nada nuevo se descuenta, pero lo quitado se devuelve
            targets[vid] = min(current.get(vid, 0), want)
    return targets, info


def _exempt(conn, order_id):
    return {r["variant_id"]: r["quantity"] for r in
            conn.execute("SELECT variant_id, quantity FROM order_stock_exempt WHERE order_id=?", (order_id,))}


def _save_exempt(conn, order_id, wanted, targets):
    conn.execute("DELETE FROM order_stock_exempt WHERE order_id=?", (order_id,))
    for vid, want in wanted.items():
        if want > targets.get(vid, 0):
            conn.execute("INSERT INTO order_stock_exempt(order_id,variant_id,quantity) VALUES (?,?,?)",
                         (order_id, vid, want - targets.get(vid, 0)))


def _check_available(targets, info, current):
    """Solo se exige stock para lo que hay que descontar ahora (objetivo - ya descontado)."""
    for vid, target in targets.items():
        v = info[vid]
        need = target - current.get(vid, 0)
        if not v["tracks_stock"] or need <= 0:
            continue
        if need > v["stock"]:
            name = display_name(v["product_name"], v["variant_name"])
            available = v["stock"] + current.get(vid, 0)
            raise BusinessRuleError(f"«{name}» está agotado." if available <= 0
                                    else f"No hay existencias suficientes de «{name}» (disponibles: {available}).")


def _sync_stock(conn, order_id, workday_id, targets, current):
    for vid in set(current) | set(targets):
        diff = targets.get(vid, 0) - current.get(vid, 0)
        if diff:
            conn.execute("INSERT INTO inventory_movements(variant_id,workday_id,kind,quantity,order_id,note) "
                         "VALUES (?,?,?,?,?,?)", (vid, workday_id, "sale" if diff > 0 else "sale_reversal",
                                                  -diff, order_id, f"Pedido #{order_id}"))


def _items_consumption(conn, order_id):
    return _consumption([{"variant_id": i["variant_id"], "quantity": i["quantity"], "components": i["components"]}
                         for i in _items(conn, order_id)])


# ---------- escritura ----------
def _open_workday_or_fail(conn):
    wd = workdays.get_open(conn)
    if not wd:
        raise BusinessRuleError("Abre la jornada para registrar pedidos.")
    return wd


def _editable(order):
    if order["status"] == "cancelled":
        raise BusinessRuleError("El pedido está cancelado y ya no se puede modificar.")
    if order["workday_status"] != "open":
        raise BusinessRuleError("La jornada de este pedido está cerrada; hay que reabrirla para modificarlo.")


def _totals(built, discount_text):
    subtotal = sum(b["quantity"] * b["unit_price_cents"] for b in built)
    discount = parse_bs(discount_text) if discount_text not in (None, "") else 0
    if discount > subtotal:
        raise ValidationError(f"El descuento no puede superar el subtotal ({format_bs(subtotal)}).")
    return subtotal, discount


def _write_items(conn, order_id, built):
    conn.execute("DELETE FROM order_items WHERE order_id=?", (order_id,))  # componentes caen por cascada
    for b in built:
        item_id = conn.execute("INSERT INTO order_items(order_id,variant_id,promotion_id,name_snapshot,quantity,unit_price_cents) "
                               "VALUES (?,?,?,?,?,?)", (order_id, b["variant_id"], b["promotion_id"], b["name"],
                                                        b["quantity"], b["unit_price_cents"])).lastrowid
        for c in b["components"]:
            conn.execute("INSERT INTO order_item_components(order_item_id,slot_id,variant_id,name_snapshot) VALUES (?,?,?,?)",
                         (item_id, c["slot_id"], c["variant_id"], c["name"]))


def create_order(lines, discount_text="", notes=""):
    conn = get_db()
    notes = (notes or "").strip()[:500]
    with transaction(conn):
        wd = _open_workday_or_fail(conn)
        built = _build_lines(conn, None, lines)
        subtotal, discount = _totals(built, discount_text)
        targets, info = _targets(conn, _consumption(built), {})
        _check_available(targets, info, {})  # un producto agotado no se puede vender
        oid = conn.execute("INSERT INTO orders(workday_id,subtotal_cents,discount_cents,total_cents,notes) VALUES (?,?,?,?,?)",
                           (wd["id"], subtotal, discount, subtotal - discount, notes)).lastrowid
        _write_items(conn, oid, built)
        audit(conn, "crear", "pedido", oid, f"total {subtotal - discount}")
    return oid


def update_order(order_id, lines, discount_text="", notes=""):
    conn = get_db()
    notes = (notes or "").strip()[:500]
    with transaction(conn):
        order = _order_row(conn, order_id)
        _editable(order)
        built = _build_lines(conn, order_id, lines)
        subtotal, discount = _totals(built, discount_text)
        current = _deducted(conn, order_id)
        wanted = _consumption(built)
        exempt = _exempt(conn, order_id) if order["status"] == "paid" else {}
        targets, info = _targets(conn, wanted, current, exempt)
        _check_available(targets, info, current)
        _write_items(conn, order_id, built)
        total = subtotal - discount
        conn.execute("UPDATE orders SET subtotal_cents=?, discount_cents=?, total_cents=?, notes=?, "
                     "updated_at=datetime('now','localtime') WHERE id=?", (subtotal, discount, total, notes, order_id))
        if order["status"] == "paid":
            _sync_stock(conn, order_id, order["workday_id"], targets, current)
            _save_exempt(conn, order_id, wanted, targets)
            conn.execute("UPDATE payments SET amount_cents=? WHERE order_id=?", (total, order_id))
        audit(conn, "modificar", "pedido", order_id, f"total {order['total_cents']} -> {total}")


def pay_order(order_id, method):
    if method not in METHODS:
        raise ValidationError("Elige Efectivo o QR.")
    conn = get_db()
    with transaction(conn):
        order = _order_row(conn, order_id)
        _editable(order)
        if order["status"] != "pending":
            raise BusinessRuleError("Este pedido ya está pagado.")
        if not _items(conn, order_id):
            raise BusinessRuleError("El pedido no tiene productos.")
        consumption = _items_consumption(conn, order_id)
        current = _deducted(conn, order_id)
        targets, info = _targets(conn, consumption, current)
        _check_available(targets, info, current)
        _sync_stock(conn, order_id, order["workday_id"], targets, current)
        _save_exempt(conn, order_id, consumption, targets)
        conn.execute("UPDATE orders SET status='paid', paid_at=datetime('now','localtime'), "
                     "updated_at=datetime('now','localtime') WHERE id=?", (order_id,))
        conn.execute("INSERT INTO payments(order_id,workday_id,method,amount_cents) VALUES (?,?,?,?)",
                     (order_id, order["workday_id"], method, order["total_cents"]))
        audit(conn, "pagar", "pedido", order_id, f"{method} {order['total_cents']}")


def set_payment_method(order_id, method):
    if method not in METHODS:
        raise ValidationError("Elige Efectivo o QR.")
    conn = get_db()
    with transaction(conn):
        order = _order_row(conn, order_id)
        _editable(order)
        if order["status"] != "paid":
            raise BusinessRuleError("Solo se puede cambiar el método de un pedido pagado.")
        conn.execute("UPDATE payments SET method=? WHERE order_id=?", (method, order_id))
        audit(conn, "cambiar_metodo", "pedido", order_id, method)


def cancel_order(order_id):
    conn = get_db()
    with transaction(conn):
        order = _order_row(conn, order_id)
        _editable(order)
        if order["status"] == "paid":
            # revierte todo lo realmente descontado, aunque el producto ya no controle existencias
            _sync_stock(conn, order_id, order["workday_id"], {}, _deducted(conn, order_id))
            conn.execute("DELETE FROM order_stock_exempt WHERE order_id=?", (order_id,))
            pay = conn.execute("SELECT method, amount_cents FROM payments WHERE order_id=?", (order_id,)).fetchone()
            # el pago sale de la caja pero queda en el historial de pagos anulados
            conn.execute("INSERT INTO voided_payments(order_id,workday_id,method,amount_cents,paid_at) "
                         "SELECT order_id, workday_id, method, amount_cents, paid_at FROM payments WHERE order_id=?",
                         (order_id,))
            conn.execute("DELETE FROM payments WHERE order_id=?", (order_id,))
            audit(conn, "anular_pago", "pedido", order_id, f"{pay['method']} {pay['amount_cents']}")
        conn.execute("UPDATE orders SET status='cancelled', cancelled_at=datetime('now','localtime'), "
                     "updated_at=datetime('now','localtime') WHERE id=?", (order_id,))
        audit(conn, "cancelar", "pedido", order_id)


def consistency_problems(conn):
    """Comprobaciones de integridad entre pedidos, pagos e inventario (usadas en pruebas y diagnóstico)."""
    problems = []
    for o in conn.execute("SELECT id, status, total_cents, subtotal_cents, discount_cents FROM orders"):
        pay = conn.execute("SELECT amount_cents FROM payments WHERE order_id=?", (o["id"],)).fetchone()
        if o["status"] == "paid" and (not pay or pay["amount_cents"] != o["total_cents"]):
            problems.append(f"Pedido {o['id']}: pago ausente o distinto del total")
        if o["status"] != "paid" and pay:
            problems.append(f"Pedido {o['id']}: tiene pago sin estar pagado")
        items = conn.execute("SELECT SUM(quantity*unit_price_cents) s FROM order_items WHERE order_id=?", (o["id"],)).fetchone()["s"] or 0
        if items != o["subtotal_cents"]:
            problems.append(f"Pedido {o['id']}: subtotal no coincide con sus líneas")
        deducted = _deducted(conn, o["id"])
        exempt = _exempt(conn, o["id"])
        if o["status"] == "paid":
            want = _items_consumption(conn, o["id"])
            for vid in set(want) | set(deducted) | set(exempt):
                if deducted.get(vid, 0) < 0 or deducted.get(vid, 0) + exempt.get(vid, 0) != want.get(vid, 0):
                    problems.append(f"Pedido {o['id']}: inventario descontado distinto del consumo (variante {vid})")
        else:
            if deducted:
                problems.append(f"Pedido {o['id']}: {o['status']} con inventario descontado")
            if exempt:
                problems.append(f"Pedido {o['id']}: {o['status']} con unidades registradas sin descontar")
    return problems
