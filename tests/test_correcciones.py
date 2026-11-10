"""Correcciones de la revisión: cambio de "controla existencias" con pedidos pagados,
pagos anulados en historial (migración 005), ventas sin descontar (migración 006) y aviso no oficial."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cafe import create_app, db
from cafe.services import orders
from test_etapa3 import Base


class TestCambioDeControl(Base):
    def set_tracks(self, vid, on):
        self.q("UPDATE product_variants SET tracks_stock=? WHERE id=?", 1 if on else 0, vid)

    def receive(self, vid, n):
        self.q("INSERT INTO inventory_movements(variant_id,kind,quantity) VALUES (?,'receipt',?)", vid, n)

    def test_activar_despues_de_vender_no_descuenta_retroactivo(self):
        vid = self.v("Croissant", tracks=0)
        oid = self.order((vid, 2), pay="cash")
        self.set_tracks(vid, True); self.receive(vid, 10)
        self.assertEqual(self.edit(oid, (vid, 2)).status_code, 200)
        self.assertEqual(self.stock(vid), 10)          # antes quedaba en 8
        self.ok()

    def test_activar_y_luego_aumentar_descuenta_solo_lo_nuevo(self):
        vid = self.v("Croissant", tracks=0)
        oid = self.order((vid, 2), pay="cash")
        self.set_tracks(vid, True); self.receive(vid, 10)
        self.edit(oid, (vid, 3)); self.assertEqual(self.stock(vid), 9)
        self.edit(oid, (vid, 1)); self.assertEqual(self.stock(vid), 10)  # vuelve la unidad nueva
        self.edit(oid, (vid, 4)); self.assertEqual(self.stock(vid), 7)
        self.ok()
        self.api("POST", f"/api/pedidos/{oid}/cancelar")
        self.assertEqual(self.stock(vid), 10)
        self.ok()

    def test_quitar_control_y_cancelar_devuelve_lo_descontado(self):
        vid = self.v("Pan", tracks=1, stock=5)
        oid = self.order((vid, 2), pay="cash")
        self.set_tracks(vid, False)
        self.assertEqual(self.api("POST", f"/api/pedidos/{oid}/cancelar").status_code, 200)
        self.assertEqual(self.stock(vid), 5)           # antes quedaba en 3
        self.ok()

    def test_quitar_control_reducir_devuelve_y_aumentar_no_descuenta(self):
        vid = self.v("Pan", tracks=1, stock=5)
        oid = self.order((vid, 3), pay="cash")
        self.set_tracks(vid, False)
        self.edit(oid, (vid, 1)); self.assertEqual(self.stock(vid), 4)
        self.edit(oid, (vid, 6)); self.assertEqual(self.stock(vid), 4)
        self.ok()
        self.set_tracks(vid, True)                      # vuelve a controlar: las 5 sin descontar no se cobran
        self.edit(oid, (vid, 6)); self.assertEqual(self.stock(vid), 4)
        self.edit(oid, (vid, 7)); self.assertEqual(self.stock(vid), 3)
        self.ok()

    def test_combo_con_componente_que_cambia_de_control(self):
        drink = self.v("Café", tracks=0, category=1)
        cookie = self.v("Galleta", tracks=0, category=7)
        promo = self.q("SELECT id FROM promotions").fetchone()["id"]
        slots = [r["id"] for r in self.q("SELECT id FROM promotion_slots WHERE promotion_id=? ORDER BY position", promo)]
        sel = {str(slots[0]): [drink], str(slots[1]): [cookie]}
        r = self.api("POST", "/api/pedidos", {"lines": [{"promotion_id": promo, "selections": sel}]})
        oid = r.get_json()["id"]
        self.api("POST", f"/api/pedidos/{oid}/pagar", {"method": "cash"})
        self.set_tracks(cookie, True); self.receive(cookie, 4)
        item = self.q("SELECT id FROM order_items WHERE order_id=?", oid).fetchone()["id"]
        self.api("PUT", f"/api/pedidos/{oid}", {"lines": [{"item_id": item, "promotion_id": promo, "selections": sel}]})
        self.assertEqual(self.stock(cookie), 4)
        self.ok()

    def test_detecta_unidades_sin_descontar_manipuladas(self):
        vid = self.v("Croissant", tracks=0)
        oid = self.order((vid, 2), pay="cash")
        self.q("DELETE FROM order_stock_exempt WHERE order_id=?", oid)
        self.assertTrue(orders.consistency_problems(self.conn))


class TestPagosAnulados(Base):
    def test_cancelar_pagado_guarda_el_pago_en_historial(self):
        vid = self.v("A", 1200, stock=3)
        oid = self.order((vid, 1), pay="qr")
        self.api("POST", f"/api/pedidos/{oid}/cancelar")
        self.assertIsNone(self.q("SELECT 1 FROM payments WHERE order_id=?", oid).fetchone())
        row = self.q("SELECT method, amount_cents, paid_at FROM voided_payments WHERE order_id=?", oid).fetchone()
        self.assertEqual((row["method"], row["amount_cents"]), ("qr", 1200))
        self.assertIsNotNone(row["paid_at"])
        self.ok()

    def test_cancelar_pendiente_no_crea_historial(self):
        vid = self.v("A", 1200, stock=3)
        oid = self.order((vid, 1))
        self.api("POST", f"/api/pedidos/{oid}/cancelar")
        self.assertIsNone(self.q("SELECT 1 FROM voided_payments WHERE order_id=?", oid).fetchone())

    def test_aviso_no_oficial_visible(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("no sustituyen el sistema oficial", html)


class TestMigraciones005y006(unittest.TestCase):
    """Una base en versión 4 (como la del usuario) se actualiza y conserva su historia."""

    def test_backfill_desde_version_4(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        path = Path(tmp.name) / "cafe.db"
        conn = db.connect(path)
        for number, file in db.discover_migrations():
            if number <= 4:
                conn.executescript(file.read_text(encoding="utf-8"))
        conn.executescript("""
            PRAGMA user_version = 4;
            INSERT INTO workdays(id, work_date) VALUES (1, '2026-11-07');
            INSERT INTO products(id, category_id, name) VALUES (1, 1, 'Con control'), (2, 2, 'Sin control');
            INSERT INTO product_variants(id, product_id, price_cents, tracks_stock) VALUES (1, 1, 1000, 1), (2, 2, 500, 0);
            INSERT INTO inventory_movements(variant_id, kind, quantity) VALUES (1, 'initial', 10);
            -- pedido 1 pagado: 2 con control (descontados) + 3 sin control
            INSERT INTO orders(id, workday_id, status, subtotal_cents, total_cents) VALUES (1, 1, 'paid', 3500, 3500);
            INSERT INTO order_items(order_id, variant_id, name_snapshot, quantity, unit_price_cents) VALUES (1,1,'Con control',2,1000),(1,2,'Sin control',3,500);
            INSERT INTO inventory_movements(variant_id, workday_id, kind, quantity, order_id) VALUES (1, 1, 'sale', -2, 1);
            INSERT INTO payments(order_id, workday_id, method, amount_cents) VALUES (1, 1, 'cash', 3500);
            -- pedido 2 pagado y cancelado con la versión anterior (el pago se borró)
            INSERT INTO orders(id, workday_id, status, subtotal_cents, total_cents) VALUES (2, 1, 'cancelled', 1000, 1000);
            INSERT INTO order_items(order_id, variant_id, name_snapshot, quantity, unit_price_cents) VALUES (2,1,'Con control',1,1000);
            INSERT INTO audit_log(at, action, entity, entity_id, detail) VALUES
              ('2026-11-07 14:06:53', 'pagar', 'pedido', 2, 'cash 1200'),
              ('2026-11-07 14:08:57', 'anular_pago', 'pedido', 2, 'cash 1200');
        """)
        conn.close()

        app = create_app({"DATA_DIR": Path(tmp.name), "TESTING": True})
        conn = db.connect(app.config["DB_PATH"])
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(db.discover_migrations()))
        v = conn.execute("SELECT * FROM voided_payments").fetchall()
        self.assertEqual([(r["order_id"], r["method"], r["amount_cents"], r["paid_at"], r["voided_at"]) for r in v],
                         [(2, "cash", 1200, "2026-11-07 14:06:53", "2026-11-07 14:08:57")])
        e = conn.execute("SELECT order_id, variant_id, quantity FROM order_stock_exempt").fetchall()
        self.assertEqual([tuple(r) for r in e], [(1, 2, 3)])
        self.assertEqual(orders.consistency_problems(conn), [])
        self.assertTrue(list(Path(tmp.name, "backups").glob("pre_migracion_v4_*.db")))
        conn.close()
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
