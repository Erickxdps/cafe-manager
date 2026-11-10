from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..errors import ValidationError
from ..services import catalog, recipes

bp = Blueprint("recipes", __name__)


@bp.get("/recetario")
def recipe_list():
    return render_template("recetario/lista.html", recipes=recipes.list_recipes())


@bp.get("/recetario/<int:rid>")
def recipe_detail(rid):
    return render_template("recetario/detalle.html", r=recipes.get_recipe(rid))


def _form(rid, data):
    products = [p for p in catalog.list_products()]
    return render_template("recetario/form.html", rid=rid, data=data, products=products)


@bp.route("/recetario/nueva", methods=["GET", "POST"])
def recipe_new():
    if request.method == "POST":
        try:
            rid = recipes.save_recipe(None, request.form, request.files.get("photo"))
        except ValidationError as exc:
            flash(exc.message, "error")
            return _form(None, dict(request.form)), 400
        flash("Receta guardada.")
        return redirect(url_for("recipes.recipe_detail", rid=rid))
    return _form(None, {"product_id": request.args.get("producto", type=int)})


@bp.route("/recetario/<int:rid>/editar", methods=["GET", "POST"])
def recipe_edit(rid):
    if request.method == "POST":
        try:
            recipes.save_recipe(rid, request.form, request.files.get("photo"))
        except ValidationError as exc:
            flash(exc.message, "error")
            return _form(rid, dict(request.form)), 400
        flash("Cambios guardados.")
        return redirect(url_for("recipes.recipe_detail", rid=rid))
    r = recipes.get_recipe(rid)
    r["steps"] = "\n".join(r["steps"])
    return _form(rid, r)


@bp.post("/recetario/<int:rid>/eliminar")
def recipe_delete(rid):
    recipes.delete_recipe(rid)
    flash("Receta eliminada.")
    return redirect(url_for("recipes.recipe_list"))
