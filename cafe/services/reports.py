"""Historial de jornadas, comparaciones e informes por rango de fechas.

Todo se calcula desde las tablas, con los mismos criterios que la caja y el cierre:
  - Una venta es un pedido PAGADO (los pendientes y cancelados no cuentan). Cuenta en su jornada.
  - Efectivo esperado = fondo + ventas en efectivo - gastos. El QR nunca entra al efectivo.
  - Un combo es una sola venta con su propio nombre (no se reparte entre sus componentes);
    sus componentes sí descuentan existencias, por eso aparecen en el informe de inventario.
  - Los descuentos son por pedido: importe bruto por producto - descuentos = total de ventas.
"""
from datetime import date, timedelta

from ..db import get_db
from ..errors import ValidationError
from .catalog import VARIANT_SQL, display_name

COMBO_CATEGORY = "Combos y promociones"
MAX_COMPARE = 7


# ---------- fechas ----------
def parse_range(desde=None, hasta=None, default_days=30):
    """Devuelve (desde, hasta) como date. Por defecto, los últimos `default_days` días hasta hoy."""
    def _d(text, label):
        try:
            return date.fromisoformat(text)
        except (TypeError, ValueError):
            raise ValidationError(f"{label}: fecha no válida (usa AAAA-MM-DD).") from None
    end = _d(hasta, "Hasta") if hasta else date.today()
    start = _d(desde, "Desde") if desde else end - timedelta(days=default_days - 1)
    if start > end:
        raise ValidationError("La fecha «desde» no puede ser posterior a «hasta».")
    if (end - start).days > 3660:
        raise ValidationError("El rango máximo es de 10 años.")
    return start, end


def _workday_ids(conn, start, end):
    return [r["id"] for r in conn.execute(
        "SELECT id FROM workdays WHERE work_date BETWEEN ? AND ? ORDER BY work_date", (start.isoformat(), end.isoformat()))]


def _in(ids):
    return ",".join("?" * len(ids))


# ---------- historial ----------
def workday_summaries(conn, ids=None, start=None, end=None):
    """Una fila por jornada con sus totales (para historial, comparación y exportaciones)."""
    sql = ("SELECT w.*, "
           "(SELECT COUNT(*) FROM orders o WHERE o.workday_id=w.id AND o.status='paid') AS paid, "
           "(SELECT COUNT(*) FROM orders o WHERE o.workday_id=w.id AND o.status='pending') AS pending, "
           "(SELECT COUNT(*) FROM orders o WHERE o.workday_id=w.id AND o.status='cancelled') AS cancelled, "
           "(SELECT COALESCE(SUM(p.amount_cents),0) FROM payments p WHERE p.workday_id=w.id AND p.method='cash') AS cash, "
           "(SELECT COALESCE(SUM(p.amount_cents),0) FROM payments p WHERE p.workday_id=w.id AND p.method='qr') AS qr, "
           "(SELECT COALESCE(SUM(e.amount_cents),0) FROM expenses e WHERE e.workday_id=w.id) AS expenses, "
           "(SELECT COALESCE(SUM(o.discount_cents),0) FROM orders o WHERE o.workday_id=w.id AND o.status='paid') AS discounts, "
           "(SELECT COUNT(*) FROM voided_payments vp WHERE vp.workday_id=w.id) AS voided_n, "
           "(SELECT COALESCE(SUM(vp.amount_cents),0) FROM voided_payments vp WHERE vp.workday_id=w.id) AS voided, "
           "(SELECT COALESCE(SUM(i.quantity),0) FROM order_items i JOIN orders o ON o.id=i.order_id "
           " WHERE o.workday_id=w.id AND o.status='paid') AS units "
           "FROM workdays w WHERE 1=1")
    args = []
    if ids is not None:
        if not ids:
            return []
        sql += f" AND w.id IN ({_in(ids)})"; args += list(ids)
    if start:
        sql += " AND w.work_date >= ?"; args.append(start.isoformat())
    if end:
        sql += " AND w.work_date <= ?"; args.append(end.isoformat())
    rows = []
    for r in conn.execute(sql + " ORDER BY w.work_date DESC, w.id DESC", args):
        d = dict(r)
        d["sales"] = d["cash"] + d["qr"]
        d["expected"] = d["opening_fund_cents"] + d["cash"] - d["expenses"]
        d["difference"] = None if d["counted_cash_cents"] is None else d["counted_cash_cents"] - d["expected"]
        rows.append(d)
    return rows


def totals_of(rows):
    keys = ("paid", "pending", "cancelled", "cash", "qr", "sales", "expenses", "discounts", "voided_n", "voided", "units")
    out = {k: sum(r[k] for r in rows) for k in keys}
    diffs = [r["difference"] for r in rows if r["difference"] is not None]
    out["difference"] = sum(diffs) if diffs else None
    out["days"] = len(rows)
    out["avg_sales"] = out["sales"] // len(rows) if rows else 0   # centavos enteros (se trunca)
    return out


def history(start, end):
    conn = get_db()
    rows = workday_summaries(conn, start=start, end=end)
    return {"rows": rows, "totals": totals_of(rows)}


# ---------- comparación ----------
def compare(ids):
    try:
        ids = sorted({int(i) for i in ids})
    except (TypeError, ValueError):
        raise ValidationError("Selección de jornadas no válida.") from None
    if len(ids) < 2:
        raise ValidationError("Marca al menos dos jornadas para compararlas.")
    if len(ids) > MAX_COMPARE:
        raise ValidationError(f"Se pueden comparar hasta {MAX_COMPARE} jornadas a la vez.")
    conn = get_db()
    days = workday_summaries(conn, ids=ids)
    if len(days) != len(ids):
        raise ValidationError("Alguna de las jornadas elegidas no existe.")
    days.sort(key=lambda d: d["work_date"])
    order = [d["id"] for d in days]
    units = {}
    for r in conn.execute(
            f"SELECT o.workday_id, i.name_snapshot AS name, SUM(i.quantity) AS n, SUM(i.quantity*i.unit_price_cents) AS amount "
            f"FROM order_items i JOIN orders o ON o.id=i.order_id WHERE o.status='paid' AND o.workday_id IN ({_in(order)}) "
            f"GROUP BY o.workday_id, i.name_snapshot", order):
        units.setdefault(r["name"], {})[r["workday_id"]] = (r["n"], r["amount"])
    products = []
    for name, per_day in units.items():
        values = [per_day.get(w, (0, 0)) for w in order]
        products.append({"name": name, "units": [v[0] for v in values], "amounts": [v[1] for v in values],
                         "total_units": sum(v[0] for v in values)})
    products.sort(key=lambda p: (-p["total_units"], p["name"].lower()))
    return {"days": days, "products": products}


# ---------- informe por rango ----------
def report(start, end):
    """Informe completo de un rango de fechas (por jornada). Lo usan la pantalla, Excel y PDF."""
    conn = get_db()
    ids = _workday_ids(conn, start, end)
    days = workday_summaries(conn, ids=ids)
    days.sort(key=lambda d: d["work_date"])
    totals = totals_of(days)
    data = {"start": start, "end": end, "days": days, "totals": totals,
            "by_product": [], "by_category": [], "methods": [], "expenses": [], "expenses_by_concept": [],
            "inventory": [], "stock": [], "cancelled": [], "top": [], "gross": 0, "reconciles": True}
    if ids:
        data.update(_sales(conn, ids, totals))
        data.update(_expenses(conn, ids))
        data["cancelled"] = _cancelled(conn, ids)
    data["inventory"] = _inventory(conn, ids)
    data["stock"] = _stock_at(conn, end)
    data["top"] = data["by_product"][:10]
    return data


def _sales(conn, ids, totals):
    by_product = [dict(r) for r in conn.execute(
        f"SELECT i.name_snapshot AS name, CASE WHEN i.promotion_id IS NULL THEN 0 ELSE 1 END AS is_combo, "
        f"SUM(i.quantity) AS units, SUM(i.quantity*i.unit_price_cents) AS amount, COUNT(DISTINCT o.id) AS orders "
        f"FROM order_items i JOIN orders o ON o.id=i.order_id "
        f"WHERE o.status='paid' AND o.workday_id IN ({_in(ids)}) GROUP BY i.name_snapshot, is_combo "
        f"ORDER BY units DESC, amount DESC, name", ids)]
    # Categoría actual del producto (el pedido guarda nombre y precio históricos, no la categoría).
    by_category = [dict(r) for r in conn.execute(
        f"SELECT CASE WHEN i.promotion_id IS NOT NULL THEN ? ELSE COALESCE(c.name, 'Sin categoría') END AS name, "
        f"SUM(i.quantity) AS units, SUM(i.quantity*i.unit_price_cents) AS amount "
        f"FROM order_items i JOIN orders o ON o.id=i.order_id "
        f"LEFT JOIN product_variants v ON v.id=i.variant_id LEFT JOIN products p ON p.id=v.product_id "
        f"LEFT JOIN categories c ON c.id=p.category_id "
        f"WHERE o.status='paid' AND o.workday_id IN ({_in(ids)}) GROUP BY 1 ORDER BY amount DESC, name",
        [COMBO_CATEGORY] + ids)]
    gross = sum(p["amount"] for p in by_product)
    for group in (by_product, by_category):
        for row in group:
            row["share"] = (row["amount"] * 1000 // gross) / 10 if gross else 0   # % con un decimal, sin float en el cálculo
    methods = [dict(r) for r in conn.execute(
        f"SELECT method, COUNT(*) AS orders, SUM(amount_cents) AS amount FROM payments "
        f"WHERE workday_id IN ({_in(ids)}) GROUP BY method ORDER BY method", ids)]
    names = {"cash": "Efectivo", "qr": "QR"}
    for m in methods:
        m["label"] = names.get(m["method"], m["method"])
    return {"by_product": by_product, "by_category": by_category, "methods": methods, "gross": gross,
            "reconciles": gross - totals["discounts"] == totals["sales"]}


def _expenses(conn, ids):
    rows = [dict(r) for r in conn.execute(
        f"SELECT e.*, w.work_date FROM expenses e JOIN workdays w ON w.id=e.workday_id "
        f"WHERE e.workday_id IN ({_in(ids)}) ORDER BY w.work_date, e.id", ids)]
    grouped = {}
    for e in rows:
        key = e["concept"].strip().lower()
        g = grouped.setdefault(key, {"concept": e["concept"].strip(), "count": 0, "amount": 0})
        g["count"] += 1; g["amount"] += e["amount_cents"]
    return {"expenses": rows, "expenses_by_concept": sorted(grouped.values(), key=lambda g: (-g["amount"], g["concept"]))}


def _cancelled(conn, ids):
    return [dict(r) for r in conn.execute(
        f"SELECT o.id, o.total_cents, o.created_at, o.cancelled_at, w.work_date, vp.method AS voided_method, "
        f"vp.amount_cents AS voided_amount, "
        f"(SELECT GROUP_CONCAT(CASE WHEN quantity>1 THEN name_snapshot||' ×'||quantity ELSE name_snapshot END, ', ') "
        f" FROM order_items WHERE order_id=o.id) AS resumen "
        f"FROM orders o JOIN workdays w ON w.id=o.workday_id LEFT JOIN voided_payments vp ON vp.order_id=o.id "
        f"WHERE o.status='cancelled' AND o.workday_id IN ({_in(ids)}) ORDER BY w.work_date, o.id", ids)]


def _inventory(conn, ids):
    """Movimientos del rango por producto controlado (incluye las unidades de los combos)."""
    if not ids:
        return []
    rows = []
    for r in conn.execute(
            f"SELECT m.variant_id, p.name AS product, v.name AS variant, "
            f"SUM(CASE WHEN m.kind='initial' THEN m.quantity ELSE 0 END) AS initial, "
            f"SUM(CASE WHEN m.kind='receipt' THEN m.quantity ELSE 0 END) AS receipts, "
            f"SUM(CASE WHEN m.kind IN ('sale','sale_reversal') THEN -m.quantity ELSE 0 END) AS sold, "
            f"SUM(CASE WHEN m.kind='adjust_pos' THEN m.quantity ELSE 0 END) AS adj_pos, "
            f"SUM(CASE WHEN m.kind='adjust_neg' THEN -m.quantity ELSE 0 END) AS adj_neg, "
            f"COUNT(*) AS moves "
            f"FROM inventory_movements m JOIN product_variants v ON v.id=m.variant_id JOIN products p ON p.id=v.product_id "
            f"WHERE m.workday_id IN ({_in(ids)}) GROUP BY m.variant_id ORDER BY p.name, v.name", ids):
        d = dict(r)
        d["name"] = display_name(d["product"], d["variant"])
        rows.append(d)
    return rows


def _stock_at(conn, end):
    """Existencias de cada producto controlado al final del día `end` (movimientos de jornadas hasta esa fecha;
    los movimientos sin jornada —inventario inicial cargado sin jornada abierta— cuentan por su fecha de registro)."""
    stock = {r["variant_id"]: r["n"] for r in conn.execute(
        "SELECT m.variant_id, SUM(m.quantity) AS n FROM inventory_movements m LEFT JOIN workdays w ON w.id=m.workday_id "
        "WHERE (w.id IS NOT NULL AND w.work_date <= :d) OR (w.id IS NULL AND date(m.created_at) <= :d) "
        "GROUP BY m.variant_id", {"d": end.isoformat()})}
    rows = []
    for r in conn.execute(VARIANT_SQL + " WHERE v.tracks_stock=1 ORDER BY c.sort_order, p.name, v.id"):
        if r["variant_id"] not in stock and not (r["variant_active"] and r["product_active"]):
            continue
        n = stock.get(r["variant_id"], 0)
        rows.append({"name": display_name(r["product_name"], r["variant_name"]), "category": r["category"],
                     "stock": n, "min_stock": r["min_stock"],
                     "state": "agotado" if n <= 0 else ("bajo" if r["min_stock"] and n <= r["min_stock"] else "ok")})
    return rows
