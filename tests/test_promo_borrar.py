"""Borrar promociones: si nunca se vendió se elimina; si ya está en pedidos se archiva sin tocar el historial.
También: configuración por secciones (categorías completas) y la pantalla de promociones."""
from tests import test_promociones as tp
from tests.test_etapa3 import Base


class PromoBase(Base):
    # solo los ayudantes de la otra clase, no sus pruebas (si no, se ejecutarían dos veces)
    def setUp(self):
        super().setUp()
        self.jugo = self.v("Jugo", 900, tracks=0, category=3)
        self.eclair = self.v("Éclair", 800, stock=6, category=5)
        self.choco = self.v("Chocolate", 700, stock=3, category=6)
        self.g = [self.v(f"Galleta {i}", 500, stock=4, category=7) for i in (1, 2, 3)]

    promo = tp.TestPromocionesGenerales.promo
    slot_ids = tp.TestPromocionesGenerales.slot_ids
    sell = tp.TestPromocionesGenerales.sell

    def count(self, sql, *a):
        return self.q(sql, *a).fetchone()[0]


class TestBorrar(PromoBase):
    def test_eliminar_promocion_nunca_vendida(self):
        pid = self.promo("Dulce", "12", [("Dulce", 1, [self.eclair, self.choco])])
        slots = self.slot_ids(pid)
        r = self.client.post(f"/promociones/{pid}/eliminar", follow_redirects=True)
        self.assertIn("Promoción eliminada", r.get_data(as_text=True))
        self.assertEqual(self.count("SELECT COUNT(*) FROM promotions WHERE id=?", pid), 0)
        self.assertEqual(self.count(f"SELECT COUNT(*) FROM promotion_slots WHERE id IN ({','.join('?' * len(slots))})", *slots), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM promotion_slot_options WHERE slot_id=?", slots[0]), 0)
        self.ok()

    def test_promocion_vendida_se_archiva_y_conserva_el_historial(self):
        pid = self.promo("Dulce", "12", [("Dulce", 1, [self.eclair, self.choco])])
        sid = self.slot_ids(pid)[0]
        oid = self.sell(pid, {str(sid): [self.eclair]})
        stock_before = self.stock(self.eclair)
        r = self.client.post(f"/promociones/{pid}/eliminar", follow_redirects=True)
        self.assertIn("se archivó", r.get_data(as_text=True))
        row = self.q("SELECT * FROM promotions WHERE id=?", pid).fetchone()
        self.assertEqual((row["archived"], row["active"]), (1, 0))
        # el pedido y el inventario no cambian; el nombre del pedido es el histórico
        item = self.q("SELECT name_snapshot, unit_price_cents FROM order_items WHERE order_id=?", oid).fetchone()
        self.assertEqual((item["name_snapshot"], item["unit_price_cents"]), ("Dulce", 1200))
        self.assertEqual(self.stock(self.eclair), stock_before)
        self.ok()
        # no aparece para vender ni en la lista, sí en "archivadas"
        html = self.client.get("/promociones").get_data(as_text=True)
        self.assertIn("Promociones archivadas (1)", html)
        self.assertNotIn(f"/promociones/{pid}/eliminar", html)
        self.assertNotIn('"name": "Dulce (archivada', self.client.get("/pedidos/nuevo").get_data(as_text=True))
        self.assertEqual(self.client.get(f"/promociones/{pid}").status_code, 404)
        r = self.api("POST", "/api/pedidos", {"lines": [{"promotion_id": pid, "quantity": 1, "selections": {str(sid): [self.choco]}}]})
        self.assertEqual(r.status_code, 400)
        # el pedido con la promoción archivada se puede seguir cancelando (y devuelve existencias)
        self.assertEqual(self.api("POST", f"/api/pedidos/{oid}/cancelar").status_code, 200)
        self.assertEqual(self.stock(self.eclair), stock_before + 1)
        self.ok()
        # el nombre queda libre para una promoción nueva
        self.promo("Dulce", "13", [("Dulce", 1, [self.choco])])

    def test_no_se_puede_borrar_dos_veces_ni_una_inexistente(self):
        pid = self.promo("Dulce", "12", [("Dulce", 1, [self.eclair])])
        self.sell(pid, {str(self.slot_ids(pid)[0]): [self.eclair]})
        self.client.post(f"/promociones/{pid}/eliminar")
        for target in (pid, 9999):
            r = self.client.post(f"/promociones/{target}/eliminar", follow_redirects=True)
            self.assertIn("La promoción no existe", r.get_data(as_text=True))
        self.assertEqual(self.count("SELECT COUNT(*) FROM promotions WHERE id=?", pid), 1)

    def test_editar_una_archivada_no_es_posible(self):
        pid = self.promo("Dulce", "12", [("Dulce", 1, [self.eclair])])
        self.sell(pid, {str(self.slot_ids(pid)[0]): [self.eclair]})
        self.client.post(f"/promociones/{pid}/eliminar")
        r = self.client.post(f"/promociones/{pid}", data={"name": "Revivida", "price": "1", "slot_idx": ["0"], "slot_0_id": "",
                                                         "slot_0_name": "X", "slot_0_options": [str(self.choco)]})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.q("SELECT archived FROM promotions WHERE id=?", pid).fetchone()["archived"], 1)

    def test_botones_eliminar_y_archivar_segun_uso(self):
        used = self.promo("Vendida", "12", [("Dulce", 1, [self.eclair])])
        self.sell(used, {str(self.slot_ids(used)[0]): [self.eclair]})
        fresh = self.promo("Nueva", "12", [("Dulce", 1, [self.eclair])])
        self.assertIn("Archivar promoción", self.client.get(f"/promociones/{used}").get_data(as_text=True))
        self.assertIn("Eliminar promoción", self.client.get(f"/promociones/{fresh}").get_data(as_text=True))


class TestSecciones(PromoBase):
    def test_eleccion_por_secciones_incluye_productos_nuevos(self):
        data = {"name": "Por secciones", "price": "15", "active": "1", "slot_idx": ["0"], "slot_0_id": "",
                "slot_0_name": "Dulce", "slot_0_cats": ["5", "6"]}         # Pastelería + Chocolates
        self.assertEqual(self.client.post("/promociones/nueva", data=data).status_code, 302)
        pid = self.q("SELECT id FROM promotions WHERE name='Por secciones'").fetchone()["id"]
        nuevo = self.v("Brownie", 900, stock=2, category=6)                 # agregado después: entra solo
        with self.app.app_context():
            from cafe.services import promotions
            promo = next(p for p in promotions.sale_promotions() if p["id"] == pid)
        opts = {o["variant_id"]: o["category"] for o in promo["slots"][0]["options"]}
        self.assertEqual(set(opts), {self.eclair, self.choco, nuevo})
        self.assertNotIn(self.jugo, opts)
        html = self.client.get(f"/promociones/{pid}").get_data(as_text=True)
        self.assertIn("Secciones incluidas", html)

    def test_eleccion_vacia_se_rechaza_con_mensaje(self):
        data = {"name": "Vacía", "price": "15", "slot_idx": ["0"], "slot_0_id": "", "slot_0_name": "Bebida"}
        r = self.client.post("/promociones/nueva", data=data)
        self.assertEqual(r.status_code, 400)
        self.assertIn("«Bebida» no tiene nada para elegir", r.get_data(as_text=True))
