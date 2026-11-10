"""Notas, recordatorios y tareas personales."""
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from ..errors import AppError, ValidationError
from ..services import notes

bp = Blueprint("notes", __name__)


def _filters():
    return {"tipo": request.args.get("tipo", ""), "estado": request.args.get("estado", "pendientes"), "q": request.args.get("q", "")}


@bp.get("/notas")
def note_list():
    f = _filters()
    return render_template("notas/lista.html", items=notes.list_notes(f["tipo"] or None, f["estado"], f["q"]), f=f,
                           kinds=notes.KINDS, plural=notes.KINDS_PLURAL, editing=None, form={})


@bp.route("/notas/<int:nid>", methods=["GET"])
def note_edit(nid):
    f = _filters()
    return render_template("notas/lista.html", items=notes.list_notes(f["tipo"] or None, f["estado"], f["q"]), f=f,
                           kinds=notes.KINDS, plural=notes.KINDS_PLURAL, editing=notes.get(nid), form={})


@bp.post("/notas/guardar")
def note_save():
    nid = request.form.get("id", type=int)
    try:
        notes.save(nid, request.form.get("kind"), request.form.get("title"), request.form.get("body"))
        flash("Guardado.")
        return redirect(url_for("notes.note_list", tipo=request.form.get("tipo_filtro", "")))
    except AppError as exc:
        flash(exc.message, "error")
        f = _filters()
        return render_template("notas/lista.html", items=notes.list_notes(None, "pendientes"), f=f, kinds=notes.KINDS,
                               plural=notes.KINDS_PLURAL, editing=notes.get(nid) if nid else None,
                               form=request.form), 400


@bp.post("/notas/<int:nid>/eliminar")
def note_delete(nid):
    try:
        notes.delete(nid)
        flash("Eliminado.")
    except AppError as exc:
        flash(exc.message, "error")
    return redirect(url_for("notes.note_list"))


@bp.post("/api/notas/<int:nid>/hecha")
def api_done(nid):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Solicitud no válida.")
    notes.set_done(nid, data.get("done"))
    return jsonify(ok=True)
