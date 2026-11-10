from datetime import date, datetime

from flask import Blueprint, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from ..db import check_integrity, get_db
from ..errors import AppError
from ..services import caja, inventory, notes, workdays

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    conn = get_db()
    wd = workdays.get_open(conn)
    ctx = {"wd": wd, "low": inventory.alerts()[0], "out": inventory.alerts()[1],
           "default_fund": workdays.default_fund_cents(conn), "today": date.today(),
           "now_hour": datetime.now().hour}
    ctx["notes"], ctx["notes_total"] = notes.pending()
    if wd:
        tasks = workdays.get_tasks(wd["id"])
        ctx.update(t=caja.totals(conn, wd["id"]), top=caja.top_sold(conn, wd["id"]),
                   pending_tasks=sum(1 for k in tasks.values() for x in k if not x["done"]),
                   done_tasks=sum(1 for k in tasks.values() for x in k if x["done"]))
    return render_template("index.html", **ctx)


@bp.get("/salud")
def health():
    conn = get_db()
    problems = check_integrity(conn)
    return jsonify(ok=not problems, schema_version=conn.execute("PRAGMA user_version").fetchone()[0],
                   problemas=problems), (200 if not problems else 500)


def _wants_json():
    return request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json"


def _respond(status, message):
    if _wants_json():
        return jsonify(error=message), status
    return render_template("error.html", status=status, message=message), status


def register_error_handlers(app):
    @app.errorhandler(AppError)
    def app_error(exc):
        return _respond(exc.status, exc.message)

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return _respond(exc.code, "No se encontró la página." if exc.code == 404 else exc.description)

    @app.errorhandler(Exception)
    def unexpected(exc):
        app.logger.exception("Error no controlado")
        return _respond(500, "Ocurrió un error interno. Tus datos no se modificaron; revisa data/logs/cafe.log.")
