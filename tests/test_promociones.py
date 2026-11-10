import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cafe import db
from tests.test_etapa3 import Base


class TestPromocionesGenerales(Base):
    """Una promoción puede mezclar cualquier producto y pedir varias unidades por elección."""

    def setUp(self):
        super().setUp()
        self.jugo = self.v("Jugo", 900, tracks=0, category=3)
        self.eclair = self.v("Éclair", 800, stock=6, category=5)
        self.choco = self.v("Chocolate", 700, stock=3, category=6)
        self.g = [self.v(f"Galleta {i}", 500, stock=4, category=7) for i in (1, 2, 3)]

    def promo(self, name, price, slots):
        """slots: [(nombre, cantidad, [variantes])]"""
        data = {"name": name, "description": "", "price": price, "active": "1", "slot_idx": []}
        for i, (sname, qty, opts) in enumerate(slots):
            data["slot_idx"].append(str(i))
            data.update({f"slot_{i}_id": "", f"slot_{i}_name": sname, f"slot_{i}_qty": str(qty),
                         f"slot_{i}_options": [str(o) for o in opts]})
        r = self.client.post("/promociones/nueva", data=data)
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        return self.q("SELECT id FROM promotions WHERE name=?", name).fetchone()["id"]

    def slot_ids(self, pid):
        return [r["id"] for r in self.q("SELECT id FROM promotion_slots WHERE promotion_id=? ORDER BY position", pid)]

    def sell(self, pid, selections, pay="cash"):
        r = self.api("POST", "/api/pedidos", {"lines": [{"promotion_id": pid, "quantity": 1, "selections": selections}]})
        if r.status_code != 201:
            return r
        oid = r.get_json()["id"]
        self.api("POST", f"/api/pedidos/{oid}/pagar", {"method": pay})
        return oid

    def test_dos_galletas_a_elegir(self):
        pid = self.promo("2 galletas + jugo", "20", [("Jugo", 1, [self.jugo]), ("Galleta", 2, self.g)])
        s_j, s_g = self.slot_ids(pid)
        oid = self.sell(pid, {str(s_j): [self.jugo], str(s_g): [self.g[0], self.g[0]]})   # la misma dos veces
        self.assertEqual(self.stock(self.g[0]), 2)
        self.assertEqual(self.moves(oid), [("sale", -2)])
        self.assertEqual(self.q("SELECT amount_cents a FROM payments").fetchone()["a"], 2000)
        oid2 = self.sell(pid, {str(s_j): self.jugo, str(s_g): [self.g[1], self.g[2]]})    # distintas; jugo en formato simple
        self.assertEqual((self.stock(self.g[1]), self.stock(self.g[2])), (3, 3))
        self.assertEqual(self.q("SELECT COUNT(*) c FROM order_item_components WHERE order_item_id IN (SELECT id FROM order_items WHERE order_id=?)", oid2).fetchone()["c"], 3)
        self.ok()

    def test_cantidad_de_elecciones_exacta(self):
        pid = self.promo("2 galletas", "9", [("Galleta", 2, self.g)])
        (s,) = self.slot_ids(pid)
        for malo in ([self.g[0]], [self.g[0], self.g[1], self.g[2]], [], None):
            r = self.sell(pid, {str(s): malo})
            self.assertEqual(r.status_code, 400, malo)
        self.assertEqual(self.q("SELECT COUNT(*) c FROM orders").fetchone()["c"], 0)

    def test_stock_cuenta_las_dos_unidades(self):
        pid = self.promo("2 galletas", "9", [("Galleta", 2, self.g)])
        (s,) = self.slot_ids(pid)
        self.sell(pid, {str(s): [self.g[0], self.g[0]]}); self.sell(pid, {str(s): [self.g[0], self.g[0]]})
        r = self.sell(pid, {str(s): [self.g[0], self.g[1]]})              # g0 ya está en 0
        self.assertEqual(r.status_code, 409)
        self.assertIn("agotado", r.get_json()["error"])
        self.assertEqual(self.stock(self.g[0]), 0)
        self.ok()

    def test_cualquier_producto_de_cualquier_categoria(self):
        pid = self.promo("Dulce + jugo", "25", [("Postre", 1, [self.eclair, self.choco, self.g[0]]), ("Jugo", 1, [self.jugo])])
        s_p, s_j = self.slot_ids(pid)
        self.sell(pid, {str(s_p): [self.eclair], str(s_j): [self.jugo]})
        self.sell(pid, {str(s_p): [self.choco], str(s_j): [self.jugo]}, pay="qr")
        self.assertEqual((self.stock(self.eclair), self.stock(self.choco)), (5, 2))
        self.assertEqual(self.q("SELECT SUM(amount_cents) t FROM payments").fetchone()["t"], 5000)
        self.ok()

    def test_cancelar_promocion_de_varias_unidades_revierte_todo(self):
        pid = self.promo("2 galletas", "9", [("Galleta", 2, self.g)])
        (s,) = self.slot_ids(pid)
        oid = self.sell(pid, {str(s): [self.g[0], self.g[1]]})
        self.api("POST", f"/api/pedidos/{oid}/cancelar")
        self.assertEqual([self.stock(g) for g in self.g], [4, 4, 4])
        self.ok()

    def test_editar_pedido_pagado_con_promocion_de_varias_unidades(self):
        pid = self.promo("2 galletas", "9", [("Galleta", 2, self.g)])
        (s,) = self.slot_ids(pid)
        oid = self.sell(pid, {str(s): [self.g[0], self.g[1]]})
        item = self.q("SELECT id FROM order_items").fetchone()["id"]
        sel = self.api("GET", f"/api/pedidos/{oid}") if False else None
        r = self.api("PUT", f"/api/pedidos/{oid}", {"lines": [{"item_id": item, "promotion_id": pid, "quantity": 1,
                                                               "selections": {str(s): [self.g[1], self.g[2]]}}]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual([self.stock(g) for g in self.g], [4, 3, 3])
        self.assertEqual(r.get_json()["items"][0]["selections"], {str(s): [self.g[1], self.g[2]]})   # el editor recibe listas
        self.ok()

    def test_eleccion_con_una_sola_opcion_es_fija(self):
        pid = self.promo("Éclair + té", "12", [("Éclair", 1, [self.eclair]), ("Bebida", 1, [self.jugo])])
        s_e, s_b = self.slot_ids(pid)
        oid = self.sell(pid, {str(s_e): [self.eclair], str(s_b): [self.jugo]})
        self.assertEqual(self.moves(oid), [("sale", -1)])
        r = self.sell(pid, {str(s_e): [self.choco], str(s_b): [self.jugo]})        # otra opción no vale
        self.assertEqual(r.status_code, 400)

    def test_cantidad_invalida_en_el_formulario(self):
        for qty in ("0", "11", "-1", "abc", "1.5"):
            r = self.client.post("/promociones/nueva", data={"name": "X" + qty, "price": "5", "slot_idx": ["0"], "slot_0_id": "",
                                                              "slot_0_name": "E", "slot_0_qty": qty, "slot_0_options": [str(self.jugo)]})
            self.assertEqual(r.status_code, 400, qty)
        self.assertEqual(self.q("SELECT COUNT(*) c FROM promotions").fetchone()["c"], 1)   # solo la sembrada

    def test_formulario_conserva_cantidad_y_opciones(self):
        pid = self.promo("2 galletas", "9", [("Galleta", 2, self.g)])
        html = self.client.get(f"/promociones/{pid}").get_data(as_text=True)
        self.assertIn('value="2"', html)
        self.assertEqual(len(re.findall(r"<input[^>]*\schecked", html)), 4)   # 3 galletas + "disponible para vender"
        self.assertIn("Secciones incluidas", html)
        self.assertIn('name="slot_0_qty" value="2"', html)                    # la cantidad existente no se pierde al guardar
        self.assertIn("×2", self.client.get("/promociones").get_data(as_text=True))


class TestAccesos(Base):
    def test_promociones_en_el_menu_de_todas_las_pantallas(self):
        for url in ["/", "/pedidos", "/pedidos/nuevo", "/catalogo", "/recetario", "/promociones", "/categorias"]:
            html = self.client.get(url).get_data(as_text=True)
            self.assertIn('href="/promociones"', html, url)


class TestMigracion(unittest.TestCase):
    def test_actualiza_base_v2_con_datos_sin_perder_nada(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path, bk = Path(tmp) / "viejo.db", Path(tmp) / "bk"
            conn = sqlite3.connect(path)
            for n in (1, 2):                                  # una instalación anterior (Etapa 2/3)
                conn.executescript(next(p for k, p in db.discover_migrations() if k == n).read_text(encoding="utf-8"))
            conn.execute("PRAGMA user_version = 2")
            conn.execute("INSERT INTO products(category_id,name) VALUES (1,'Café')")
            conn.execute("INSERT INTO product_variants(product_id,price_cents) VALUES (1,1000)")
            conn.execute("INSERT INTO promotion_slot_options VALUES (1,1)")
            conn.commit(); conn.close()
            db.migrate(path, bk)
            conn = db.connect(path)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(db.discover_migrations()))
            self.assertEqual([r["quantity"] for r in conn.execute("SELECT quantity FROM promotion_slots")], [1, 1])
            self.assertEqual(conn.execute("SELECT name FROM products").fetchone()["name"], "Café")
            self.assertEqual(conn.execute("SELECT COUNT(*) c FROM promotion_slot_options").fetchone()["c"], 1)
            self.assertEqual(db.check_integrity(conn), [])
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE promotion_slots SET quantity=0")
            conn.close()
            self.assertEqual(len(list(bk.glob("pre_migracion_v2_*.db"))), 1)    # copia previa automática
            db.migrate(path, bk)                                                 # reinicio: no repite nada
            self.assertEqual(len(list(bk.glob("*.db"))), 1)


if __name__ == "__main__":
    unittest.main()


class TestPromoPorCategoria(Base):
    """Combo real: una bebida (café o té de cualquier tipo) + un producto de panadería (cualquiera). Uno de uno."""

    CAFES, TES, JUGOS, PAN, PASTEL, CHOCO, GALLETAS = 1, 2, 3, 4, 5, 6, 7

    def setUp(self):
        super().setUp()
        self.cafe = self.v("Café A", 1000, tracks=0, category=self.CAFES)
        self.te = self.v("Té X", 800, tracks=0, category=self.TES)
        self.jugo = self.v("Jugo", 900, tracks=0, category=self.JUGOS)
        self.pan = self.v("Pan", 400, stock=5, category=self.PAN)
        self.galleta = self.v("Galleta", 500, stock=5, category=self.GALLETAS)
        self.eclair = self.v("Éclair", 800, stock=5, category=self.PASTEL)

    def options(self, slot_name):
        from cafe.services import promotions
        with self.app.app_context():
            promo = [p for p in promotions.sale_promotions() if p["id"] == 1][0]
        return sorted(o["name"] for s in promo["slots"] if s["name"] == slot_name for o in s["options"])

    def sell(self, bebida, panaderia, pay="cash"):
        r = self.api("POST", "/api/pedidos", {"lines": [{"promotion_id": 1, "quantity": 1,
                                                          "selections": {"1": [bebida], "2": [panaderia]}}]})
        if r.status_code == 201 and pay:
            self.api("POST", f"/api/pedidos/{r.get_json()['id']}/pagar", {"method": pay})
        return r

    def save_slots(self, bebida_cats, pan_cats, pan_options=()):
        return self.client.post("/promociones/1", data={
            "name": "Combo Bs 17", "price": "17", "active": "1", "slot_idx": ["0", "1"],
            "slot_0_id": "1", "slot_0_name": "Bebida", "slot_0_cats": [str(c) for c in bebida_cats],
            "slot_1_id": "2", "slot_1_name": "Panadería", "slot_1_cats": [str(c) for c in pan_cats],
            "slot_1_options": [str(v) for v in pan_options]})

    def test_combo_sembrado_ya_funciona_por_categorias(self):
        self.assertEqual(self.options("Bebida"), ["Café A", "Té X"])           # sin Jugo
        self.assertEqual(self.options("Panadería"), ["Galleta", "Pan"])        # sin Éclair (pastelería)
        row = self.q("SELECT name FROM promotion_slots WHERE id=2").fetchone()
        self.assertEqual(row["name"], "Panadería")

    def test_vender_cafe_mas_pan_un_solo_ingreso(self):
        r = self.sell(self.cafe, self.pan)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.q("SELECT amount_cents a FROM payments").fetchone()["a"], 1700)
        self.assertEqual((self.stock(self.pan), self.stock(self.galleta)), (4, 5))
        self.assertEqual(self.sell(self.te, self.galleta).status_code, 201)
        self.ok()

    def test_un_producto_nuevo_de_la_categoria_entra_solo(self):
        tostada = self.v("Tostada", 600, stock=3, category=self.PAN)           # creada DESPUÉS, sin tocar la promoción
        self.assertIn("Tostada", self.options("Panadería"))
        self.assertEqual(self.sell(self.cafe, tostada).status_code, 201)
        te2 = self.v("Té Y", 800, tracks=0, category=self.TES)
        self.assertEqual(self.sell(te2, self.pan).status_code, 201)
        self.assertEqual(self.stock(tostada), 2)
        self.ok()

    def test_fuera_de_las_categorias_se_rechaza(self):
        self.assertEqual(self.sell(self.jugo, self.pan).status_code, 400)       # jugo no es café ni té
        self.assertEqual(self.sell(self.cafe, self.eclair).status_code, 400)    # pastelería no está marcada
        self.assertEqual(self.sell(self.pan, self.cafe).status_code, 400)       # elecciones cruzadas
        self.assertEqual(self.q("SELECT COUNT(*) c FROM orders").fetchone()["c"], 0)

    def test_producto_desactivado_no_es_opcion(self):
        self.q("UPDATE products SET active=0 WHERE name='Pan'")
        self.assertNotIn("Pan", self.options("Panadería"))
        self.assertEqual(self.sell(self.cafe, self.pan).status_code, 400)

    def test_agotado_aparece_pero_no_se_vende(self):
        for _ in range(5):
            self.sell(self.cafe, self.pan)
        self.assertEqual(self.stock(self.pan), 0)
        self.assertIn("Pan", self.options("Panadería"))                         # sigue en la lista (el selector lo marca agotado)
        r = self.sell(self.cafe, self.pan)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.sell(self.cafe, self.galleta).status_code, 201)

    def test_categoria_mas_producto_suelto_de_otra_categoria(self):
        self.assertEqual(self.save_slots([1, 2], [4], pan_options=[self.eclair]).status_code, 302)
        self.assertEqual(self.options("Panadería"), sorted(["Pan", "Éclair"]))
        self.assertEqual(self.sell(self.cafe, self.eclair).status_code, 201)
        self.assertEqual(self.sell(self.cafe, self.galleta).status_code, 400)   # galletas ya no está marcada

    def test_quitar_una_categoria_desde_el_formulario(self):
        self.save_slots([1], [4, 7])
        self.assertEqual(self.options("Bebida"), ["Café A"])
        self.assertEqual(self.sell(self.te, self.pan).status_code, 400)
        self.assertEqual(self.sell(self.cafe, self.pan).status_code, 201)

    def test_pedido_ya_hecho_no_cambia_si_cambia_la_configuracion(self):
        r = self.sell(self.cafe, self.pan)
        oid = r.get_json()["id"]
        self.save_slots([2], [7])                                               # ahora solo té + galleta
        self.assertEqual(self.api("POST", f"/api/pedidos/{oid}/cancelar").status_code, 200)   # cancelar sigue siendo válido
        self.assertEqual(self.stock(self.pan), 5)
        self.ok()

    def test_formulario_muestra_categorias_marcadas(self):
        html = self.client.get("/promociones/1").get_data(as_text=True)
        self.assertEqual(len(re.findall(r'class="cat-toggle" name="slot_0_cats" value="[12]" data-n="\d+" checked', html)), 2)
        self.assertEqual(len(re.findall(r'class="cat-toggle" name="slot_1_cats" value="[47]" data-n="\d+" checked', html)), 2)
        self.assertRegex(html, r'value="%d"\s+disabled' % self.pan)             # el producto suelto sobra si su categoría está marcada


class TestMigracion004(unittest.TestCase):
    def build(self, tmp, version):
        path = Path(tmp) / "b.db"
        conn = sqlite3.connect(path)
        for n, p in db.discover_migrations():
            if n <= version:
                conn.executescript(p.read_text(encoding="utf-8"))
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
        return path, conn

    def test_base_sin_tocar_recibe_nombre_y_categorias_sugeridas(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path, conn = self.build(tmp, 3); conn.close()
            db.migrate(path, Path(tmp) / "bk")
            conn = db.connect(path)
            self.assertEqual(conn.execute("SELECT name FROM promotion_slots WHERE id=2").fetchone()["name"], "Panadería")
            cats = lambda s: sorted(r["name"] for r in conn.execute(
                "SELECT c.name FROM promotion_slot_categories sc JOIN categories c ON c.id=sc.category_id WHERE sc.slot_id=?", (s,)))
            self.assertEqual(cats(1), ["Cafés calientes", "Tés"])
            self.assertEqual(cats(2), ["Galletas", "Panadería francesa"])
            self.assertEqual(db.check_integrity(conn), [])
            conn.close()

    def test_no_pisa_lo_que_el_usuario_ya_configuro(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path, conn = self.build(tmp, 3)
            conn.execute("INSERT INTO products(category_id,name) VALUES (1,'Café')")
            conn.execute("INSERT INTO product_variants(product_id,price_cents) VALUES (1,1000)")
            conn.execute("INSERT INTO promotion_slot_options VALUES (1,1)")                  # ya eligió una opción en Bebida
            conn.execute("UPDATE promotion_slots SET name='Dulce' WHERE id=2")               # ya renombró la otra elección
            conn.commit(); conn.close()
            db.migrate(path, Path(tmp) / "bk")
            conn = db.connect(path)
            self.assertEqual(conn.execute("SELECT name FROM promotion_slots WHERE id=2").fetchone()["name"], "Dulce")
            self.assertEqual(conn.execute("SELECT COUNT(*) c FROM promotion_slot_categories").fetchone()["c"], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) c FROM promotion_slot_options").fetchone()["c"], 1)
            conn.close()
