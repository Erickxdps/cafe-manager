"""Historial de jornadas, comparación, informes y exportaciones."""
from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from ..errors import AppError
from ..services import exports, reports

bp = Blueprint("reports", __name__)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _range(default_days):
    return reports.parse_range(request.args.get("desde") or None, request.args.get("hasta") or None, default_days)


@bp.get("/historial")
def history():
    try:
        start, end = _range(default_days=90)
    except AppError as exc:
        flash(exc.message, "error")
        return redirect(url_for("reports.history"))
    return render_template("historial/lista.html", h=reports.history(start, end), start=start, end=end,
                           max_compare=reports.MAX_COMPARE)


@bp.get("/historial/comparar")
def compare():
    try:
        c = reports.compare(request.args.getlist("j"))
    except AppError as exc:
        flash(exc.message, "error")
        return redirect(url_for("reports.history", desde=request.args.get("desde", ""), hasta=request.args.get("hasta", "")))
    return render_template("historial/comparar.html", c=c)


@bp.get("/informes")
def report():
    try:
        start, end = _range(default_days=30)
    except AppError as exc:
        flash(exc.message, "error")
        return redirect(url_for("reports.report"))
    return render_template("historial/informe.html", r=reports.report(start, end), period=exports.period_text)


@bp.get("/informes/<fmt>")
def export(fmt):
    if fmt not in ("excel", "pdf"):
        return redirect(url_for("reports.report"))
    try:
        start, end = _range(default_days=30)
    except AppError as exc:
        flash(exc.message, "error")
        return redirect(url_for("reports.report"))
    data = reports.report(start, end)
    if fmt == "excel":
        body, mime, name = exports.to_excel(data), XLSX, exports.filename(data, "xlsx")
    else:
        body, mime, name = exports.to_pdf(data), "application/pdf", exports.filename(data, "pdf")
    return Response(body, mimetype=mime, headers={"Content-Disposition": f'attachment; filename="{name}"',
                                                  "Cache-Control": "no-store"})
