import sqlite3

from ..db import audit, get_db, transaction
from ..errors import NotFoundError, ValidationError
from ..money import parse_bs, to_input
from .common import clean, delete_photo, normalize, save_photo

VARIANT_SQL = """
SELECT v.id AS variant_id, c.id AS category_id, v.name AS variant_name, v.price_cents, v.tracks_stock, v.min_stock,
       v.active AS variant_active, p.id AS product_id, p.name AS product_name, p.photo,
       p.description, p.active AS product_active, c.name AS category, s.stock,
       (SELECT MIN(r.id) FROM recipes r WHERE r.product_id = p.id) AS recipe_id
FROM product_variants v JOIN products p ON p.id = v.product_id
JOIN categories c ON c.id = p.category_id JOIN variant_stock s ON s.variant_id = v.id
"""


def display_name(product_name, variant_name):
    return f"{product_name} — {variant_name}" if variant_name else product_name


def availability(row):
    if not row["tracks_stock"]:
        return "sin_control"
    if row["stock"] <= 0:
        return "agotado"
    if row["min_stock"] and row["stock"] <= row["min_stock"]:
        return "bajo"
    return "ok"


def _decorate(row):
    d = dict(row)
    d["name"] = display_name(d["product_name"], d["variant_name"])
    d["availability"] = availability(d)
    return d


# ---------- categorías ----------
def list_categories(active_only=False):
    sql = "SELECT c.*, (SELECT COUNT(*) FROM products p WHERE p.category_id = c.id) AS n_products FROM categories c"
    if active_only:
        sql += " WHERE c.active = 1"
    return get_db().execute(sql + " ORDER BY c.sort_order, c.name").fetchall()


def save_category(cid, name):
    name = clean(name, "El nombre de la categoría", required=True, max_len=40)
    conn = get_db()
    try:
        with transaction(conn):
            if cid is None:
                order = conn.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM categories").fetchone()[0]
                cid = conn.execute("INSERT INTO categories(name, sort_order) VALUES (?,?)", (name, order)).lastrowid
            else:
                if not conn.execute("UPDATE categories SET name=? WHERE id=?", (name, cid)).rowcount:
                    raise NotFoundError("La categoría no existe.")
            audit(conn, "guardar", "categoria", cid, name)
    except sqlite3.IntegrityError:
        raise ValidationError("Ya existe una categoría con ese nombre.") from None
    return cid


def set_category_active(cid, active):
    conn = get_db()
    with transaction(conn):
        if not conn.execute("UPDATE categories SET active=? WHERE id=?", (int(active), cid)).rowcount:
            raise NotFoundError("La categoría no existe.")
        audit(conn, "activar" if active else "desactivar", "categoria", cid)


# ---------- productos ----------
def list_products(category_id=None, include_inactive=False):
    sql, args = ("SELECT p.id, p.name, p.photo, p.description, p.active, c.name AS category "
                 "FROM products p JOIN categories c ON c.id = p.category_id WHERE 1=1"), []
    if category_id:
        sql += " AND p.category_id = ?"
        args.append(category_id)
    if not include_inactive:
        sql += " AND p.active = 1"
    products = [dict(r) for r in get_db().execute(sql + " ORDER BY c.sort_order, p.name", args)]
    by_id = {p["id"]: p for p in products}
    for p in products:
        p["variants"] = []
    for row in get_db().execute(VARIANT_SQL + " ORDER BY v.id"):
        if row["product_id"] in by_id:
            by_id[row["product_id"]]["variants"].append(_decorate(row))
    return products


def get_product(pid):
    row = get_db().execute("SELECT p.*, c.name AS category FROM products p JOIN categories c "
                           "ON c.id = p.category_id WHERE p.id = ?", (pid,)).fetchone()
    if not row:
        raise NotFoundError("El producto no existe.")
    product = dict(row)
    product["variants"] = [_decorate(r) for r in get_db().execute(VARIANT_SQL + " WHERE p.id=? ORDER BY v.id", (pid,))]
    return product


def product_to_form(pid):
    p = get_product(pid)
    return {"name": p["name"], "category_id": p["category_id"], "description": p["description"], "photo": p["photo"],
            "variants": [{"id": v["variant_id"], "name": v["variant_name"], "price": to_input(v["price_cents"]),
                          "tracks": v["tracks_stock"], "min": v["min_stock"], "active": v["variant_active"]}
                         for v in p["variants"]]}


def form_to_data(form):
    ids = form.getlist("var_id")
    return {"name": form.get("name", ""), "category_id": form.get("category_id", type=int),
            "description": form.get("description", ""), "photo": None,
            "variants": [{"id": ids[i], "name": n, "price": form.getlist("var_price")[i],
                          "tracks": int(form.getlist("var_tracks")[i] == "1"),
                          "min": form.getlist("var_min")[i], "active": int(form.getlist("var_active")[i] != "0")}
                         for i, n in enumerate(form.getlist("var_name"))]}


def _parse_variants(form):
    out = []
    for v in form_to_data(form)["variants"]:
        name, price = v["name"].strip(), v["price"].strip()
        if not v["id"] and not name and not price:
            continue  # fila nueva vacía
        label = f"El precio de «{name}»" if name else "El precio"
        if not price:
            raise ValidationError(f"{label} es obligatorio.")
        try:
            minimum = int(v["min"] or 0)
        except ValueError:
            raise ValidationError("El stock mínimo debe ser un número entero.") from None
        if minimum < 0:
            raise ValidationError("El stock mínimo no puede ser negativo.")
        out.append({"id": int(v["id"]) if v["id"] else None, "name": clean(name, "La presentación", max_len=40),
                    "price": parse_bs(price), "tracks": v["tracks"], "min": minimum, "active": v["active"]})
    if not out:
        raise ValidationError("Agrega al menos una presentación con su precio.")
    if len({normalize(v["name"]) for v in out}) != len(out):
        raise ValidationError("Dos presentaciones tienen el mismo nombre (deja vacío el de la única o principal).")
    return out


def save_product(pid, form, photo_file):
    name = clean(form.get("name"), "El nombre", required=True, max_len=80)
    description = clean(form.get("description"), "La descripción", max_len=300)
    variants = _parse_variants(form)
    conn = get_db()
    category_id = form.get("category_id", type=int)
    if not category_id or not conn.execute("SELECT 1 FROM categories WHERE id=?", (category_id,)).fetchone():
        raise ValidationError("Elige una categoría.")
    new_photo, old_photo = save_photo(photo_file, "productos"), None
    try:
        with transaction(conn):
            if pid is None:
                pid = conn.execute("INSERT INTO products(category_id,name,description,photo) VALUES (?,?,?,?)",
                                   (category_id, name, description, new_photo)).lastrowid
            else:
                row = conn.execute("SELECT photo FROM products WHERE id=?", (pid,)).fetchone()
                if not row:
                    raise NotFoundError("El producto no existe.")
                old_photo = row["photo"] if new_photo else None
                conn.execute("UPDATE products SET category_id=?, name=?, description=?, photo=COALESCE(?, photo), "
                             "updated_at=datetime('now','localtime') WHERE id=?",
                             (category_id, name, description, new_photo, pid))
            for v in variants:
                if v["id"]:
                    if not conn.execute("UPDATE product_variants SET name=?, price_cents=?, tracks_stock=?, min_stock=?, "
                                        "active=? WHERE id=? AND product_id=?",
                                        (v["name"], v["price"], v["tracks"], v["min"], v["active"], v["id"], pid)).rowcount:
                        raise ValidationError("Una presentación no pertenece a este producto.")
                else:
                    conn.execute("INSERT INTO product_variants(product_id,name,price_cents,tracks_stock,min_stock) "
                                 "VALUES (?,?,?,?,?)", (pid, v["name"], v["price"], v["tracks"], v["min"]))
            audit(conn, "guardar", "producto", pid, name)
    except sqlite3.IntegrityError as exc:
        delete_photo(new_photo)
        msg = str(exc)
        raise ValidationError("Ya existe un producto con ese nombre." if "products.name" in msg
                              else "Hay una presentación repetida." if "product_variants" in msg
                              else "No se pudo guardar por un dato no válido.") from None
    except BaseException:
        delete_photo(new_photo)
        raise
    delete_photo(old_photo)
    return pid


def set_product_active(pid, active):
    """Desactivar nunca borra: el historial de pedidos e inventario se conserva."""
    conn = get_db()
    with transaction(conn):
        if not conn.execute("UPDATE products SET active=? WHERE id=?", (int(active), pid)).rowcount:
            raise NotFoundError("El producto no existe.")
        audit(conn, "activar" if active else "desactivar", "producto", pid)


# ---------- búsqueda ----------
def search(q, limit=8):
    nq = normalize(q)
    if not nq:
        return []
    hits = []
    for row in get_db().execute(VARIANT_SQL + " WHERE p.active=1 AND v.active=1"):
        d = _decorate(row)
        norm = normalize(d["name"])
        if nq in norm:
            words = norm.replace("—", " ").split()
            hits.append((0 if norm.startswith(nq) else 1 if any(w.startswith(nq) for w in words) else 2, norm, d))
    hits.sort(key=lambda h: (h[0], h[1]))
    return [h[2] for h in hits[:limit]]
