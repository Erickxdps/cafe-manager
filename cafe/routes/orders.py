from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from ..db import get_db
from ..errors import AppError, ValidationError
from ..services import orders, promotions, recipes, workdays

bp = Blueprint("orders", __name__)


def _payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Solicitud no válida.")
    return data


def _editor(order):
    items = orders.sale_catalog()
    recipe_map = {}
    for v in items:
        if v["recipe_id"] and v["recipe_id"] not in recipe_map:
            r = recipes.get_recipe(v["recipe_id"])
            recipe_map[v["recipe_id"]] = {"name": r["name"], "steps": r["steps"]}
    from . import catalog as _  # noqa: F401  (asegura el endpoint de fotos)
    boot = {"order": {**order, "discount_text": _discount_text(order)},
            "catalog": [{k: v[k] for k in ("variant_id", "name", "category", "price_cents", "photo", "availability",
                                           "stock", "recipe_id")} for v in items],
            "promotions": promotions.sale_promotions(), "recipes": recipe_map,
            "methods": orders.METHODS}
    return render_template("pedidos/editor.html", boot=boot, order=order)


def _discount_text(order):
    from ..money import to_input
    return to_input(order["discount_cents"]) if order["discount_cents"] else ""


@bp.get("/pedidos")
def order_list():
    wd = workdays.get_open()
    return render_template("pedidos/lista.html", wd=wd, orders=orders.list_orders(wd["id"]) if wd else [],
                           default_fund=workdays.default_fund_cents(), methods=orders.METHODS)


@bp.get("/pedidos/nuevo")
def order_new():
    if not workdays.get_open():
        flash("Abre la jornada para registrar pedidos.", "error")
        return redirect(url_for("orders.order_list"))
    blank = {"id": None, "status": "new", "notes": "", "subtotal_cents": 0, "discount_cents": 0, "total_cents": 0,
             "method": None, "items": [], "workday_open": True, "created_at": None}
    return _editor(blank)


@bp.get("/pedidos/<int:oid>")
def order_detail(oid):
    return _editor(orders.get_order(oid))


@bp.post("/api/pedidos")
def api_create():
    data = _payload()
    oid = orders.create_order(data.get("lines"), data.get("discount", ""), data.get("notes", ""))
    return jsonify(_order_json(oid)), 201


@bp.put("/api/pedidos/<int:oid>")
def api_update(oid):
    data = _payload()
    orders.update_order(oid, data.get("lines"), data.get("discount", ""), data.get("notes", ""))
    return jsonify(_order_json(oid))


@bp.post("/api/pedidos/<int:oid>/pagar")
def api_pay(oid):
    orders.pay_order(oid, _payload().get("method"))
    return jsonify(_order_json(oid))


@bp.post("/api/pedidos/<int:oid>/metodo")
def api_method(oid):
    orders.set_payment_method(oid, _payload().get("method"))
    return jsonify(_order_json(oid))


@bp.post("/api/pedidos/<int:oid>/cancelar")
def api_cancel(oid):
    orders.cancel_order(oid)
    return jsonify(_order_json(oid))


def _order_json(oid):
    order = orders.get_order(oid)
    order["discount_text"] = _discount_text(order)
    return order
