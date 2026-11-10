"""Caja de la jornada: efectivo esperado = fondo + ventas en efectivo - gastos. El QR nunca entra al efectivo."""
from ..db import audit, get_db, transaction
from ..errors import BusinessRuleError, NotFoundError, ValidationError
from ..money import parse_bs
from . import workdays
from .common import clean


def totals(conn, workday_id):
    wd = workdays.get(workday_id, conn)
    pay = {r["method"]: r["s"] for r in conn.execute(
        "SELECT p.method, SUM(p.amount_cents) s FROM payments p JOIN orders o ON o.id=p.order_id "
        "WHERE p.workday_id=? AND o.status='paid' GROUP BY p.method", (workday_id,))}
    cash, qr = pay.get("cash", 0), pay.get("qr", 0)
    spent = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM expenses WHERE workday_id=?", (workday_id,)).fetchone()[0]
    counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM orders WHERE workday_id=? GROUP BY status", (workday_id,))}
    return {"fund": wd["opening_fund_cents"], "cash": cash, "qr": qr, "sales": cash + qr, "expenses": spent,
            "expected": wd["opening_fund_cents"] + cash - spent, "paid": counts.get("paid", 0),
            "pending": counts.get("pending", 0), "cancelled": counts.get("cancelled", 0),
            "registered": counts.get("paid", 0) + counts.get("pending", 0)}


def top_sold(conn, workday_id, limit=5):
    return [dict(r) for r in conn.execute(
        "SELECT oi.name_snapshot AS name, SUM(oi.quantity) AS units FROM order_items oi JOIN orders o ON o.id=oi.order_id "
        "WHERE o.workday_id=? AND o.status='paid' GROUP BY oi.name_snapshot ORDER BY units DESC, name LIMIT ?", (workday_id, limit))]


def list_expenses(workday_id):
    return get_db().execute("SELECT * FROM expenses WHERE workday_id=? ORDER BY id DESC", (workday_id,)).fetchall()


def list_payments(workday_id):
    return get_db().execute(
        "SELECT o.id AS order_id, p.method, p.amount_cents, p.paid_at, "
        "(SELECT GROUP_CONCAT(CASE WHEN quantity>1 THEN name_snapshot||' ×'||quantity ELSE name_snapshot END, ', ') "
        " FROM order_items WHERE order_id=o.id) AS resumen "
        "FROM payments p JOIN orders o ON o.id=p.order_id "
        "WHERE p.workday_id=? AND o.status='paid' ORDER BY p.id DESC", (workday_id,)).fetchall()


def _validated(concept, amount_text, note):
    concept = clean(concept, "El concepto", required=True, max_len=80)
    cents = parse_bs(amount_text)
    if cents <= 0:
        raise ValidationError("El importe debe ser mayor que cero.")
    if cents > 100_000_00:
        raise ValidationError("El importe es demasiado grande.")
    return concept, cents, clean(note, "La observación", max_len=200)


def add_expense(concept, amount_text, note=""):
    concept, cents, note = _validated(concept, amount_text, note)
    conn = get_db()
    with transaction(conn):
        wd = workdays.get_open(conn)
        if not wd:
            raise BusinessRuleError("Abre la jornada para registrar gastos.")
        eid = conn.execute("INSERT INTO expenses(workday_id,concept,amount_cents,note) VALUES (?,?,?,?)",
                           (wd["id"], concept, cents, note)).lastrowid
        audit(conn, "crear", "gasto", eid, f"{concept} {cents}")
    return eid


def _open_expense(conn, expense_id):
    row = conn.execute("SELECT e.*, w.status AS wstatus FROM expenses e JOIN workdays w ON w.id=e.workday_id WHERE e.id=?",
                       (expense_id,)).fetchone()
    if not row:
        raise NotFoundError("El gasto no existe.")
    if row["wstatus"] != "open":
        raise BusinessRuleError("La jornada de este gasto está cerrada; reábrela para modificarlo.")
    return row


def update_expense(expense_id, concept, amount_text, note=""):
    concept, cents, note = _validated(concept, amount_text, note)
    conn = get_db()
    with transaction(conn):
        old = _open_expense(conn, expense_id)
        conn.execute("UPDATE expenses SET concept=?, amount_cents=?, note=? WHERE id=?", (concept, cents, note, expense_id))
        audit(conn, "modificar", "gasto", expense_id, f"{old['concept']} {old['amount_cents']} -> {concept} {cents}")


def delete_expense(expense_id):
    conn = get_db()
    with transaction(conn):
        old = _open_expense(conn, expense_id)
        conn.execute("DELETE FROM expenses WHERE id=?", (expense_id,))
        audit(conn, "eliminar", "gasto", expense_id, f"{old['concept']} {old['amount_cents']}")
