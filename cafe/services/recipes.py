import sqlite3

from ..db import audit, get_db, transaction
from ..errors import NotFoundError, ValidationError
from .common import clean, delete_photo, save_photo


def list_recipes():
    return get_db().execute(
        "SELECT r.*, p.name AS product_name FROM recipes r LEFT JOIN products p ON p.id = r.product_id "
        "ORDER BY r.name").fetchall()


def get_recipe(rid):
    conn = get_db()
    row = conn.execute("SELECT r.*, p.name AS product_name FROM recipes r LEFT JOIN products p "
                       "ON p.id = r.product_id WHERE r.id=?", (rid,)).fetchone()
    if not row:
        raise NotFoundError("La receta no existe.")
    recipe = dict(row)
    recipe["steps"] = [r["text"] for r in conn.execute(
        "SELECT text FROM recipe_steps WHERE recipe_id=? ORDER BY position", (rid,))]
    return recipe


def recipes_for_product(pid):
    return get_db().execute("SELECT id, name FROM recipes WHERE product_id=? ORDER BY id", (pid,)).fetchall()


def save_recipe(rid, form, photo_file):
    name = clean(form.get("name"), "El nombre de la bebida", required=True, max_len=80)
    drink_type = clean(form.get("drink_type"), "El tipo", max_len=40)
    description = clean(form.get("description"), "La descripción", max_len=300)
    notes = clean(form.get("notes"), "Las notas", max_len=1000)
    product_id = form.get("product_id", type=int) or None
    steps = [s.strip() for s in form.get("steps", "").splitlines() if s.strip()]
    if not steps:
        raise ValidationError("Escribe al menos un paso de preparación (uno por línea).")
    conn = get_db()
    if product_id and not conn.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone():
        raise ValidationError("El producto relacionado no existe.")
    new_photo, old_photo = save_photo(photo_file, "recetas"), None
    try:
        with transaction(conn):
            if rid is None:
                rid = conn.execute("INSERT INTO recipes(name,drink_type,description,notes,photo,product_id) "
                                   "VALUES (?,?,?,?,?,?)", (name, drink_type, description, notes, new_photo, product_id)).lastrowid
            else:
                row = conn.execute("SELECT photo FROM recipes WHERE id=?", (rid,)).fetchone()
                if not row:
                    raise NotFoundError("La receta no existe.")
                old_photo = row["photo"] if new_photo else None
                conn.execute("UPDATE recipes SET name=?, drink_type=?, description=?, notes=?, "
                             "photo=COALESCE(?, photo), product_id=? WHERE id=?",
                             (name, drink_type, description, notes, new_photo, product_id, rid))
                conn.execute("DELETE FROM recipe_steps WHERE recipe_id=?", (rid,))
            conn.executemany("INSERT INTO recipe_steps(recipe_id, position, text) VALUES (?,?,?)",
                             [(rid, i, s) for i, s in enumerate(steps, 1)])
            audit(conn, "guardar", "receta", rid, name)
    except sqlite3.IntegrityError:
        delete_photo(new_photo)
        raise ValidationError("Ya existe una receta con ese nombre.") from None
    except BaseException:
        delete_photo(new_photo)
        raise
    delete_photo(old_photo)
    return rid


def delete_recipe(rid):
    conn = get_db()
    photo = None
    with transaction(conn):
        row = conn.execute("SELECT name, photo FROM recipes WHERE id=?", (rid,)).fetchone()
        if not row:
            raise NotFoundError("La receta no existe.")
        photo = row["photo"]
        conn.execute("DELETE FROM recipes WHERE id=?", (rid,))
        audit(conn, "eliminar", "receta", rid, row["name"])
    delete_photo(photo)
