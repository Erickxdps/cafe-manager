from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..errors import AppError
from ..services import catalog, inventory, workdays

bp = Blueprint("inventory", __name__)


def _rows(form, *fields):
    ids = form.getlist("v_id")
    cols = {f: form.getlist(f) for f in fields}
    return [{"variant_id": int(ids[i]), **{f: (cols[f][i] if i < len(cols[f]) else "") for f in fields}}
            for i in range(len(ids)) if ids[i].isdigit()]


def _back(endpoint="inventory.summary", **kw):
    return redirect(url_for(endpoint, **kw))


@bp.get("/inventario")
def summary():
    cat = request.args.get("categoria", type=int)
    filtro = request.args.get("filtro", "todos")
    rows = inventory.overview(filtro, cat, request.args.get("q", ""))
    low, out = inventory.alerts()
    return render_template("inventario/resumen.html", rows=rows, categories=catalog.list_categories(True), cat=cat,
                           filtro=filtro, q=request.args.get("q", ""), n_low=len(low), n_out=len(out),
                           untracked=inventory.untracked_variants(),
                           wd=workdays.get_open(), without_initial=[r for r in inventory.overview() if not r["has_movements"]])


@bp.post("/inventario/inicial")
def initial():
    try:
        n = inventory.set_initial(_rows(request.form, "qty"))
        flash(f"Existencias iniciales registradas para {n} producto(s).")
    except AppError as exc:
        flash(exc.message, "error")
    return _back()


@bp.route("/inventario/recepcion", methods=["GET", "POST"])
def receipt():
    if request.method == "POST":
        try:
            res = inventory.register_receipt(_rows(request.form, "expected", "counted"))
            msg = f"Recepción registrada: {res['units']} unidades en {res['products']} producto(s)."
            if res["differences"]:
                msg += " Diferencias con lo esperado: " + "; ".join(res["differences"]) + "."
            flash(msg)
            return _back()
        except AppError as exc:
            flash(exc.message, "error")
    return render_template("inventario/recepcion.html", rows=inventory.overview(), wd=workdays.get_open(),
                           untracked=inventory.untracked_variants())


@bp.route("/inventario/conteo", methods=["GET", "POST"])
def count():
    if request.method == "POST":
        try:
            n = inventory.apply_count(_rows(request.form, "system", "counted"), request.form.get("reason", ""))
            flash(f"Conteo aplicado: {n} producto(s) ajustado(s)." if n else "El conteo coincide con el sistema; no hubo diferencias.")
            return _back()
        except AppError as exc:
            flash(exc.message, "error")
    return render_template("inventario/conteo.html", rows=inventory.overview(), wd=workdays.get_open())


@bp.post("/inventario/ajustar")
def adjust():
    vid = request.form.get("variant_id", type=int)
    try:
        inventory.adjust(vid, request.form.get("delta"), request.form.get("note"))
        flash("Ajuste registrado.")
    except AppError as exc:
        flash(exc.message, "error")
    return _back("inventory.history", variante=vid)


@bp.get("/inventario/movimientos")
def history():
    vid = request.args.get("variante", type=int)
    kind = request.args.get("tipo")
    variant = next((r for r in inventory.overview() if r["variant_id"] == vid), None) if vid else None
    return render_template("inventario/movimientos.html", moves=inventory.movements(vid, kind), kinds=inventory.KINDS,
                           kind=kind, vid=vid, variant=variant, tracked=inventory.tracked_variants())


@bp.post("/inventario/control")
def enable_tracking():
    try:
        n = inventory.enable_tracking(request.form.getlist("v_id"))
        flash(f"Control de existencias activado en {n} producto(s). Empiezan en 0: registra su recepción o sus existencias iniciales.")
    except AppError as exc:
        flash(exc.message, "error")
    return redirect(request.referrer or url_for("inventory.receipt"))


@bp.get("/inventario/reposicion")
def restock():
    available = inventory.sales_weekdays()
    arg = request.args.get("dia")
    if arg is None:                    # por defecto: jornadas del mismo día de la semana que hoy, si hay historial
        weekday = date.today().weekday() if date.today().weekday() in available else None
    else:
        weekday = int(arg) if arg.isdigit() and int(arg) in range(7) else None
    return render_template("inventario/reposicion.html", data=inventory.suggestions(weekday=weekday), weekday=weekday,
                           available=available, dias=inventory.DIAS)
