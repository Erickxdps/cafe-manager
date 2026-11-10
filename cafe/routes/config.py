"""Configuración y copias de seguridad."""
from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for

from ..db import get_db
from ..errors import AppError
from ..money import to_input
from ..services import backup, settings

bp = Blueprint("config", __name__)


@bp.get("/configuracion")
def index():
    conn = get_db()
    return render_template("config/index.html", s=settings.get_all(), to_input=to_input, backups=backup.list_backups(),
                           schema=conn.execute("PRAGMA user_version").fetchone()[0],
                           data_dir=current_app.config["DATA_DIR"], labels=settings.EDITABLE)


@bp.post("/configuracion")
def save():
    try:
        settings.update(request.form)
        flash("Configuración guardada.")
    except AppError as exc:
        flash(exc.message, "error")
    return redirect(url_for("config.index"))


@bp.post("/copias/crear")
def backup_create():
    try:
        name = backup.create("manual")
        flash(f"Copia de seguridad creada: {name}")
    except AppError as exc:
        flash(exc.message, "error")
    except OSError as exc:
        current_app.logger.exception("No se pudo crear la copia")
        flash(f"No se pudo crear la copia: {exc}", "error")
    return redirect(url_for("config.index") + "#copias")


@bp.get("/copias/<name>")
def backup_download(name):
    return send_file(backup.path_of(name), as_attachment=True, download_name=name)


@bp.post("/copias/<name>/eliminar")
def backup_delete(name):
    try:
        backup.delete(name)
        flash("Copia eliminada.")
    except AppError as exc:
        flash(exc.message, "error")
    return redirect(url_for("config.index") + "#copias")


def _do_restore(**kw):
    if request.form.get("entiendo") != "1":
        flash("Marca la casilla de confirmación para restaurar.", "error")
        return redirect(url_for("config.index") + "#copias")
    try:
        safety = backup.restore(**kw)
        flash(f"Datos restaurados. El estado anterior quedó guardado en la copia {safety}.")
        return redirect(url_for("main.index"))
    except AppError as exc:
        flash(exc.message, "error")
    except Exception as exc:  # noqa: BLE001 - se informa y se registra; los datos vuelven al estado previo
        current_app.logger.exception("Falló la restauración")
        flash(f"No se pudo restaurar; tus datos no cambiaron ({exc.__class__.__name__}).", "error")
    return redirect(url_for("config.index") + "#copias")


@bp.post("/copias/<name>/restaurar")
def backup_restore(name):
    return _do_restore(name=name)


@bp.post("/copias/restaurar-archivo")
def backup_restore_upload():
    file = request.files.get("archivo")
    if not file or not file.filename:
        flash("Elige el archivo de la copia.", "error")
        return redirect(url_for("config.index") + "#copias")
    return _do_restore(uploaded=file)
