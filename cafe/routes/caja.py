from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for 

from ..db import get_db
from ..errors import AppError
from ..money import format_bs, to_input
from ..services import caja, workdays

bp = Blueprint("caja", __name__)


@bp.get("/caja")
def index():
    conn = get_db()
    wd = workdays.get_open(conn)
    if not wd:
        flash("Abre la jornada para ver la caja.", "error")
        return redirect(url_for("workday.index"))
    pending_orders = conn.execute(
        "SELECT o.id, o.total_cents, o.created_at, "
        "(SELECT GROUP_CONCAT(CASE WHEN quantity>1 THEN name_snapshot||' ×'||quantity ELSE name_snapshot END, ', ') "
        " FROM order_items WHERE order_id=o.id) AS resumen "
        "FROM orders o WHERE o.workday_id=? AND o.status='pending' ORDER BY o.id DESC", (wd["id"],)).fetchall()
    return render_template("caja/index.html", 
            wd=wd, t=caja.totals(conn, wd["id"]), 
            expenses=caja.list_expenses(wd["id"]),
            payments=caja.list_payments(wd["id"]), 
            pending_orders=pending_orders,
            to_input=to_input)


@bp.post("/caja/gasto")
def add_expense():
    try:
        caja.add_expense(request.form.get("concept"), request.form.get("amount"), request.form.get("note", ""))
        flash("Gasto registrado.")
    except AppError as exc:
        flash(exc.message, "error")
    return redirect(url_for("caja.index"))


@bp.post("/caja/gasto/<int:eid>/editar")
def edit_expense(eid):
    try:
        caja.update_expense(eid, request.form.get("concept"), request.form.get("amount"), request.form.get("note", ""))
        flash("Gasto actualizado.")
    except AppError as exc:
        flash(exc.message, "error")
    return redirect(url_for("caja.index"))


@bp.post("/caja/gasto/<int:eid>/eliminar")
def delete_expense(eid):
    try:
        caja.delete_expense(eid)
        flash("Gasto eliminado.")
    except AppError as exc:
        flash(exc.message, "error")
    return redirect(url_for("caja.index"))


@bp.get("/api/caja")
def api():
    conn = get_db()
    wd = workdays.get_open(conn)
    if not wd:
        return jsonify(open=False)
    t = caja.totals(conn, wd["id"])
    return jsonify(open=True, expected=t["expected"], expected_text=format_bs(t["expected"]), cash=t["cash"], qr=t["qr"],
            expenses=t["expenses"], fund=t["fund"])
