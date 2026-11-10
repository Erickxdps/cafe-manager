from flask import (Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)

from ..errors import ValidationError
from ..services import catalog, recipes

bp = Blueprint("catalog", __name__)


@bp.get("/catalogo")
def product_list():
    cat = request.args.get("categoria", type=int)
    show_inactive = bool(request.args.get("inactivos"))
    return render_template("catalogo/lista.html", products=catalog.list_products(cat, show_inactive),
                           categories=catalog.list_categories(), cat=cat, show_inactive=show_inactive)


@bp.get("/catalogo/<int:pid>")
def product_detail(pid):
    product = catalog.get_product(pid)
    recs = [recipes.get_recipe(r["id"]) for r in recipes.recipes_for_product(pid)]
    return render_template("catalogo/detalle.html", p=product, recipes=recs)


def _form(pid, data):
    return render_template("catalogo/form.html", pid=pid, data=data, categories=catalog.list_categories(True))


@bp.route("/catalogo/nuevo", methods=["GET", "POST"])
def product_new():
    if request.method == "POST":
        try:
            pid = catalog.save_product(None, request.form, request.files.get("photo"))
        except ValidationError as exc:
            flash(exc.message, "error")
            return _form(None, catalog.form_to_data(request.form)), 400
        flash("Producto guardado.")
        return redirect(url_for("catalog.product_detail", pid=pid))
    return _form(None, {"name": "", "category_id": None, "description": "", "photo": None,
                        "variants": [{"id": "", "name": "", "price": "", "tracks": 0, "min": 0, "active": 1}]})


@bp.route("/catalogo/<int:pid>/editar", methods=["GET", "POST"])
def product_edit(pid):
    if request.method == "POST":
        try:
            catalog.save_product(pid, request.form, request.files.get("photo"))
        except ValidationError as exc:
            flash(exc.message, "error")
            data = catalog.form_to_data(request.form)
            data["photo"] = catalog.get_product(pid)["photo"]
            return _form(pid, data), 400
        flash("Cambios guardados.")
        return redirect(url_for("catalog.product_detail", pid=pid))
    return _form(pid, catalog.product_to_form(pid))


@bp.post("/catalogo/<int:pid>/estado")
def product_state(pid):
    active = request.form.get("active") == "1"
    catalog.set_product_active(pid, active)
    flash("Producto activado." if active else "Producto desactivado; su historial se conserva.")
    return redirect(request.referrer or url_for("catalog.product_detail", pid=pid))


@bp.route("/categorias", methods=["GET", "POST"])
def categories():
    if request.method == "POST":
        try:
            catalog.save_category(request.form.get("id", type=int), request.form.get("name"))
            flash("Categoría guardada.")
        except ValidationError as exc:
            flash(exc.message, "error")
        return redirect(url_for("catalog.categories"))
    return render_template("catalogo/categorias.html", categories=catalog.list_categories())


@bp.post("/categorias/<int:cid>/estado")
def category_state(cid):
    catalog.set_category_active(cid, request.form.get("active") == "1")
    return redirect(url_for("catalog.categories"))


@bp.get("/api/buscar")
def api_search():
    results = catalog.search(request.args.get("q", ""))
    return jsonify(resultados=[{k: r[k] for k in ("product_id", "variant_id", "name", "category", "price_cents",
                                                  "photo", "availability", "stock", "recipe_id")} for r in results])


@bp.get("/media/<path:filename>")
def media(filename):
    root = current_app.config["UPLOAD_DIR"]
    if not (root / filename).resolve().is_relative_to(root.resolve()):
        abort(404)
    return send_from_directory(root, filename, max_age=3600)
