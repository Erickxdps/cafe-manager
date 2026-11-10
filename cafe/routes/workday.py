from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from ..db import get_db
from ..errors import AppError, ValidationError
from ..services import backup, caja, closing, workdays

bp = Blueprint("workday", __name__)


@bp.get("/jornada")
def index():
    conn = get_db()
    wd = workdays.get_open(conn)
    tasks = workdays.get_tasks(wd["id"]) if wd else None
    last = workdays.latest(conn)
    return render_template("jornada/index.html", wd=wd, tasks=tasks,
                           totals=caja.totals(conn, wd["id"]) if wd else None, default_fund=workdays.default_fund_cents(conn),
                           last=last if last and last["status"] == "closed" else None,
                           past=conn.execute("SELECT * FROM workdays WHERE status='closed' ORDER BY work_date DESC LIMIT 15").fetchall())


@bp.post("/jornada/abrir")
def open_():
    try:
        workdays.open_workday(request.form.get("fund"))
        flash("Jornada abierta. Buen turno.")
    except AppError as exc:
        flash(exc.message, "error")
    nxt = request.form.get("next", "")
    local = nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt   # solo rutas de esta app
    return redirect(nxt if local else url_for("workday.index"))


@bp.post("/api/tareas/<int:tid>")
def api_task(tid):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Solicitud no válida.")
    workdays.set_task(tid, data.get("done"))
    return jsonify(ok=True)


@bp.route("/jornada/cerrar", methods=["GET", "POST"])
def close():
    conn = get_db()
    wd = workdays.get_open(conn)
    if not wd:
        flash("No hay una jornada abierta.", "error")
        return redirect(url_for("workday.index"))
    if request.method == "POST":
        try:
            wid = closing.close_workday(request.form.get("counted"), request.form.get("reserve"), request.form.get("notes"))
            flash("Jornada cerrada. Aquí está el informe de cierre.")
            try:   # copia automática; si falla, el cierre ya está hecho y solo se avisa
                backup.create("cierre")
            except Exception:  # noqa: BLE001
                current_app.logger.exception("No se pudo crear la copia automática al cerrar")
                flash("Aviso: no se pudo crear la copia automática; crea una desde Configuración.", "error")
            return redirect(url_for("workday.report", wid=wid))
        except AppError as exc:
            flash(exc.message, "error")
    pending = conn.execute("SELECT id, total_cents FROM orders WHERE workday_id=? AND status='pending' ORDER BY id", (wd["id"],)).fetchall()
    from ..money import to_input
    form = request.form if request.method == "POST" else {}
    return render_template("jornada/cerrar.html", wd=wd, s=closing.summary(conn, wd["id"]), pending=pending,
                           tasks=workdays.get_tasks(wd["id"])["close"], to_input=to_input, form=form), (400 if request.method == "POST" else 200)


@bp.get("/jornada/<int:wid>/informe")
def report(wid):
    return render_template("jornada/informe.html", r=closing.report(wid), latest=workdays.latest())


@bp.post("/jornada/<int:wid>/reabrir")
def reopen(wid):
    try:
        workdays.reopen_workday(wid)
        flash("Jornada reabierta.")
        return redirect(url_for("workday.index"))
    except AppError as exc:
        flash(exc.message, "error")
        return redirect(url_for("workday.report", wid=wid))


@bp.route("/jornada/listas", methods=["GET", "POST"])
def lists():
    if request.method == "POST":
        try:
            workdays.template_action(request.form.get("action"), request.form.get("kind"),
                                     request.form.get("item_id", type=int), request.form.get("text", ""))
        except AppError as exc:
            flash(exc.message, "error")
        return redirect(url_for("workday.lists"))
    return render_template("jornada/listas.html", open_items=workdays.template_items("open"), close_items=workdays.template_items("close"))
