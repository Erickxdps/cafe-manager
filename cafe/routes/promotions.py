from collections import defaultdict

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..errors import AppError, ValidationError
from ..money import to_input
from ..services import catalog, promotions

bp = Blueprint("promotions", __name__)


@bp.get("/promociones")
def promo_list():
    variants = {v["variant_id"]: v["name"] for v in promotions.sellable_variants()}
    return render_template("promociones/lista.html", promos=promotions.list_promotions(), variants=variants,
                           archived=promotions.list_archived(), cat_names={c["id"]: c["name"] for c in catalog.list_categories()})


def _form(pid, data, used=False):
    by_category = defaultdict(list)
    for v in promotions.sellable_variants():
        by_category[v["category_id"]].append(v)
    groups = [(c, by_category.get(c["id"], [])) for c in catalog.list_categories(True)]
    return render_template("promociones/form.html", pid=pid, data=data, groups=groups, used=used)


def _form_data(form):
    slots = [{"idx": i, "id": form.get(f"slot_{i}_id", ""), "name": form.get(f"slot_{i}_name", ""),
              "qty": form.get(f"slot_{i}_qty", "1"), "options": [int(v) for v in form.getlist(f"slot_{i}_options") if v.isdigit()],
              "cats": [int(v) for v in form.getlist(f"slot_{i}_cats") if v.isdigit()]}
             for i in form.getlist("slot_idx")]
    return {"name": form.get("name", ""), "description": form.get("description", ""), "price": form.get("price", ""),
            "active": form.get("active") == "1", "slots": slots}


@bp.route("/promociones/nueva", methods=["GET", "POST"])
def promo_new():
    if request.method == "POST":
        try:
            pid = promotions.save_promotion(None, request.form)
        except ValidationError as exc:
            flash(exc.message, "error")
            return _form(None, _form_data(request.form)), 400
        flash("Promoción guardada.")
        return redirect(url_for("promotions.promo_edit", pid=pid))
    return _form(None, {"name": "", "description": "", "price": "", "active": True,
                        "slots": [{"idx": "0", "id": "", "name": "", "qty": 1, "options": [], "cats": []}]})


@bp.route("/promociones/<int:pid>", methods=["GET", "POST"])
def promo_edit(pid):
    if request.method == "POST":
        try:
            promotions.save_promotion(pid, request.form)
        except ValidationError as exc:
            flash(exc.message, "error")
            return _form(pid, _form_data(request.form)), 400
        flash("Promoción guardada.")
        return redirect(url_for("promotions.promo_edit", pid=pid))
    p = promotions.get_promotion(pid)
    return _form(pid, used=p["used"], data={"name": p["name"], "description": p["description"], "price": to_input(p["price_cents"]),
                       "active": bool(p["active"]),
                       "slots": [{"idx": str(i), "id": s["id"], "name": s["name"], "qty": s["quantity"], "options": s["options"], "cats": s["categories"]}
                                 for i, s in enumerate(p["slots"])]})


@bp.post("/promociones/<int:pid>/eliminar")
def promo_delete(pid):
    try:
        result = promotions.delete_promotion(pid)
        flash("Promoción eliminada." if result == "eliminada" else
              "La promoción ya se había vendido, así que se archivó: ya no aparece para vender, pero los pedidos e informes "
              "anteriores la conservan.")
    except AppError as exc:
        flash(exc.message, "error")
    return redirect(url_for("promotions.promo_list"))
