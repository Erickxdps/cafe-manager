"""Correcciones de la revisión general: jornada de otro día y combos vendidos tras cambiar la promoción."""
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from cafe import create_app, db
from cafe.errors import AppError
from cafe.services import orders, workdays


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.app = create_app({"DATA_DIR": Path(self.tmp.name), "TESTING": True})
        self.client = self.app.test_client()
        ctx = self.app.test_request_context()
        ctx.push()
        self.addCleanup(ctx.pop)
        self.conn = db.get_db()

    def variante(self, nombre):
        cat = self.conn.execute("SELECT id FROM categories LIMIT 1").fetchone()[0]
        pid = self.conn.execute("INSERT INTO products(category_id,name) VALUES (?,?)", (cat, nombre)).lastrowid
        return self.conn.execute("INSERT INTO product_variants(product_id,price_cents) VALUES (?,1000)", (pid,)).lastrowid


class TestJornadaVencida(Base):
    def test_avisa_si_la_jornada_abierta_no_es_de_hoy(self):
        ayer = date.today() - timedelta(days=1)
        workdays.open_workday("100", day=ayer.isoformat())
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn(f"La jornada abierta es del {ayer.strftime('%d/%m/%Y')}", html)
        self.assertIn("Cerrar esa jornada", html)

    def test_no_avisa_con_la_jornada_de_hoy(self):
        workdays.open_workday("100")
        self.assertNotIn("La jornada abierta es del", self.client.get("/").get_data(as_text=True))


class TestComboVendido(Base):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.variante("Cafe A"), self.variante("Cafe B")
        self.promo = self.conn.execute("INSERT INTO promotions(name,price_cents) VALUES ('Promo',1500)").lastrowid
        self.slot = self.conn.execute("INSERT INTO promotion_slots(promotion_id,name,position,quantity) "
                                      "VALUES (?,'Bebida',1,1)", (self.promo,)).lastrowid
        self.opcion(self.a)
        workdays.open_workday("100")
        self.oid = orders.create_order([{"promotion_id": self.promo, "selections": {str(self.slot): [self.a]}}])
        orders.pay_order(self.oid, "cash")
        self.item = orders.get_order(self.oid)["items"][0]

    def opcion(self, vid):
        self.conn.execute("DELETE FROM promotion_slot_options WHERE slot_id=?", (self.slot,))
        self.conn.execute("INSERT INTO promotion_slot_options(slot_id,variant_id) VALUES (?,?)", (self.slot, vid))

    def linea(self, selections):
        return [{"item_id": self.item["item_id"], "promotion_id": self.promo, "selections": selections}]

    def test_se_puede_editar_aunque_cambien_las_opciones(self):
        self.opcion(self.b)
        orders.update_order(self.oid, self.linea(self.item["selections"]), "1")
        order = orders.get_order(self.oid)
        self.assertEqual(order["total_cents"], 1400)
        self.assertEqual(order["items"][0]["components"][0]["variant_id"], self.a)
        self.assertEqual(orders.consistency_problems(self.conn), [])

    def test_una_eleccion_nueva_si_se_valida(self):
        otra = self.variante("Cafe C")
        self.opcion(self.b)
        with self.assertRaises(AppError):
            orders.update_order(self.oid, self.linea({str(self.slot): [otra]}))


if __name__ == "__main__":
    unittest.main()
