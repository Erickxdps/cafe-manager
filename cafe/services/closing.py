"""Cierre de jornada e informe de cierre."""
from ..db import audit, get_db, transaction
from ..errors import BusinessRuleError, ValidationError
from ..money import parse_bs
from . import caja, inventory, workdays


def summary(conn, workday_id, counted=None, reserve=None):
    wd = workdays.get(workday_id, conn)
    t = caja.totals(conn, workday_id)
    reserve = wd["reserve_cents"] if reserve is None else reserve
    counted = wd["counted_cash_cents"] if counted is None else counted
    return {**t, "reserve": reserve, "counted": counted,
            "difference": None if counted is None else counted - t["expected"],
            "to_bag": t["expected"] - reserve, "to_bag_counted": None if counted is None else counted - reserve}


def _optional_money(text, label):
    if text in (None, ""):
        return None
    try:
        return parse_bs(text)
    except ValidationError as exc:
        raise ValidationError(f"{label}: {exc.message}") from None


def close_workday(counted_text=None, reserve_text=None, notes=""):
    conn = get_db()
    counted = _optional_money((counted_text or "").strip(), "Efectivo contado")
    reserve = _optional_money((reserve_text or "").strip(), "Reserva")
    notes = (notes or "").strip()[:500]
    with transaction(conn):
        wd = workdays.get_open(conn)
        if not wd:
            raise BusinessRuleError("No hay una jornada abierta.")
        pending = caja.totals(conn, wd["id"])["pending"]
        if pending:
            raise BusinessRuleError(f"Hay {pending} pedido(s) pendiente(s) de pago. Cóbralos o cancélalos antes de cerrar la jornada.")
        reserve = wd["reserve_cents"] if reserve is None else reserve
        conn.execute("UPDATE workdays SET status='closed', closed_at=datetime('now','localtime'), counted_cash_cents=?, "
                     "reserve_cents=?, notes=? WHERE id=?", (counted, reserve, notes, wd["id"]))
        audit(conn, "cerrar", "jornada", wd["id"], f"contado {counted} reserva {reserve}")
    return wd["id"]


def report(workday_id):
    conn = get_db()
    wd = workdays.get(workday_id, conn)
    s = summary(conn, workday_id)
    sold = [dict(r) for r in conn.execute(
        "SELECT oi.name_snapshot AS name, SUM(oi.quantity) AS units, SUM(oi.quantity*oi.unit_price_cents) AS amount "
        "FROM order_items oi JOIN orders o ON o.id=oi.order_id WHERE o.workday_id=? AND o.status='paid' "
        "GROUP BY oi.name_snapshot ORDER BY units DESC, name", (workday_id,))]
    discounts = conn.execute("SELECT COALESCE(SUM(discount_cents),0) FROM orders WHERE workday_id=? AND status='paid'", (workday_id,)).fetchone()[0]
    adjustments = [dict(r) for r in conn.execute(
        "SELECT m.created_at, m.kind, m.quantity, m.note, p.name AS product, v.name AS variant FROM inventory_movements m "
        "JOIN product_variants v ON v.id=m.variant_id JOIN products p ON p.id=v.product_id "
        "WHERE m.workday_id=? AND m.kind IN ('adjust_pos','adjust_neg') ORDER BY m.id", (workday_id,))]
    tasks = workdays.get_tasks(workday_id)
    cancelled = [dict(r) for r in conn.execute(
        "SELECT o.id, o.total_cents, o.cancelled_at, vp.method AS voided_method, vp.amount_cents AS voided_amount, "
        "(SELECT GROUP_CONCAT(CASE WHEN quantity>1 THEN name_snapshot||' ×'||quantity ELSE name_snapshot END, ', ') "
        " FROM order_items WHERE order_id=o.id) AS resumen "
        "FROM orders o LEFT JOIN voided_payments vp ON vp.order_id=o.id "
        "WHERE o.workday_id=? AND o.status='cancelled' ORDER BY o.id", (workday_id,))]
    return {"wd": dict(wd), "s": s, "sold": sold, "discounts": discounts, "adjustments": adjustments,
            "expenses": [dict(r) for r in caja.list_expenses(workday_id)][::-1], "inventory": inventory.day_rows(workday_id),
            "tasks": tasks, "payments": [dict(r) for r in caja.list_payments(workday_id)], "cancelled": cancelled}
