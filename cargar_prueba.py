"""Datos de PRUEBA, todos marcados con '(Prueba)'. No son el menú ni los precios reales.
Uso:  python cargar_prueba.py            carga
      python cargar_prueba.py --quitar   elimina los datos de prueba (o los desactiva si ya tienen pedidos)
"""
import sys

from cafe import create_app
from cafe.db import get_db, transaction

# (nombre, categoría, [(presentación, precio en centavos, control stock, stock inicial, mínimo)])
PRODUCTS = [
    ("(Prueba) Café A", "Cafés calientes", [("", 1000, 0, 0, 0)]),
    ("(Prueba) Café B", "Cafés calientes", [("", 1200, 0, 0, 0)]),
    ("(Prueba) Té", "Tés", [("", 800, 0, 0, 0)]),
    ("(Prueba) Éclair", "Pastelería", [("", 800, 1, 6, 2)]),
    ("(Prueba) Éclair pequeño", "Pastelería", [("", 500, 1, 3, 2)]),
    ("(Prueba) Galleta 1", "Galletas", [("", 500, 1, 8, 3)]),
    ("(Prueba) Galleta 2", "Galletas", [("", 500, 1, 0, 3)]),
    ("(Prueba) Galleta 3", "Galletas", [("", 500, 1, 5, 3)]),
]


def load(conn):
    for name, category, variants in PRODUCTS:
        if conn.execute("SELECT 1 FROM products WHERE name=?", (name,)).fetchone():
            continue
        cid = conn.execute("SELECT id FROM categories WHERE name=?", (category,)).fetchone()["id"]
        pid = conn.execute("INSERT INTO products(category_id,name,is_sample) VALUES (?,?,1)", (cid, name)).lastrowid
        for vname, price, tracks, stock, minimum in variants:
            vid = conn.execute("INSERT INTO product_variants(product_id,name,price_cents,tracks_stock,min_stock) "
                               "VALUES (?,?,?,?,?)", (pid, vname, price, tracks, minimum)).lastrowid
            if stock:
                conn.execute("INSERT INTO inventory_movements(variant_id,kind,quantity,note) VALUES (?,'initial',?,'(Prueba)')",
                             (vid, stock))
    if not conn.execute("SELECT 1 FROM recipes WHERE name='(Prueba) Receta de café'").fetchone():
        rid = conn.execute("INSERT INTO recipes(name,drink_type,description,product_id) VALUES "
                           "('(Prueba) Receta de café','Café','Receta de ejemplo',"
                           "(SELECT id FROM products WHERE name='(Prueba) Café A'))").lastrowid
        for i, text in enumerate(["Moler el café", "Preparar el espresso", "Servir caliente"], 1):
            conn.execute("INSERT INTO recipe_steps(recipe_id,position,text) VALUES (?,?,?)", (rid, i, text))


def remove(conn):
    conn.execute("DELETE FROM recipes WHERE name LIKE '(Prueba)%'")
    for p in conn.execute("SELECT id FROM products WHERE is_sample=1").fetchall():
        used = conn.execute("SELECT 1 FROM order_items oi JOIN product_variants v ON v.id=oi.variant_id "
                            "WHERE v.product_id=?", (p["id"],)).fetchone()
        if used:
            conn.execute("UPDATE products SET active=0 WHERE id=?", (p["id"],))
        else:
            conn.execute("DELETE FROM promotion_slot_options WHERE variant_id IN (SELECT id FROM product_variants WHERE product_id=?)", (p["id"],))
            conn.execute("DELETE FROM inventory_movements WHERE variant_id IN (SELECT id FROM product_variants WHERE product_id=?)", (p["id"],))
            conn.execute("DELETE FROM product_variants WHERE product_id=?", (p["id"],))
            conn.execute("DELETE FROM products WHERE id=?", (p["id"],))


if __name__ == "__main__":
    app = create_app()
    with app.app_context(), transaction(get_db()) as conn:
        (remove if "--quitar" in sys.argv else load)(conn)
    print("Datos de prueba eliminados." if "--quitar" in sys.argv else "Datos de prueba cargados.")
