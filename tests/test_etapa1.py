import sqlite3
import tempfile
import unittest
from pathlib import Path

from cafe import config, create_app, db
from cafe.errors import ValidationError
from cafe.money import format_bs, parse_bs


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.data = Path(self.tmp.name)
        self.app = create_app({"DATA_DIR": self.data, "TESTING": True})
        self.conn = db.connect(self.app.config["DB_PATH"])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def q(self, sql, *args):
        return self.conn.execute(sql, args)

    def variant(self, name="Prueba", price=800):
        cur = self.q("INSERT INTO products(category_id,name,is_sample) VALUES (1,?,1)", name)
        return self.q("INSERT INTO product_variants(product_id,price_cents,tracks_stock) VALUES (?,?,1)",
                      cur.lastrowid, price).lastrowid

    def workday(self, date="2026-11-07"):
        return self.q("INSERT INTO workdays(work_date) VALUES (?)", date).lastrowid

    def order(self, wd, subtotal=1000, discount=0):
        return self.q("INSERT INTO orders(workday_id,subtotal_cents,discount_cents,total_cents) VALUES (?,?,?,?)",
                      wd, subtotal, discount, subtotal - discount).lastrowid


class TestEsquema(Base):
    def test_migraciones_y_semillas(self):
        self.assertEqual(self.q("PRAGMA user_version").fetchone()[0], len(db.discover_migrations()))
        self.assertGreaterEqual(self.q("SELECT COUNT(*) FROM categories").fetchone()[0], 9)
        self.assertEqual(self.q("SELECT price_cents FROM promotions").fetchone()[0], 1700)
        self.assertEqual(self.q("SELECT COUNT(*) FROM products").fetchone()[0], 0)  # sin menú inventado

    def test_reiniciar_es_idempotente(self):
        create_app({"DATA_DIR": self.data, "TESTING": True})
        self.assertEqual(self.q("SELECT COUNT(*) FROM categories").fetchone()[0], 9)
        self.assertEqual(self.q("SELECT COUNT(*) FROM promotions").fetchone()[0], 1)

    def test_integridad_limpia(self):
        self.assertEqual(db.check_integrity(self.conn), [])

    def test_claves_foraneas_activas(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.q("INSERT INTO products(category_id,name) VALUES (999,'X')")

    def test_precio_negativo_rechazado(self):
        cur = self.q("INSERT INTO products(category_id,name) VALUES (1,'X')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.q("INSERT INTO product_variants(product_id,price_cents) VALUES (?,-1)", cur.lastrowid)

    def test_una_sola_jornada_abierta(self):
        self.workday("2026-11-07")
        with self.assertRaises(sqlite3.IntegrityError):
            self.workday("2026-11-08")
        self.q("UPDATE workdays SET status='closed'")
        self.workday("2026-11-08")  # ahora sí

    def test_pedidos_totales_coherentes(self):
        wd = self.workday()
        with self.assertRaises(sqlite3.IntegrityError):
            self.q("INSERT INTO orders(workday_id,subtotal_cents,discount_cents,total_cents) VALUES (?,1000,0,900)", wd)
        with self.assertRaises(sqlite3.IntegrityError):
            self.q("INSERT INTO orders(workday_id,subtotal_cents,discount_cents,total_cents) VALUES (?,1000,2000,-1000)", wd)

    def test_un_pago_por_pedido(self):
        wd = self.workday()
        o = self.order(wd)
        self.q("INSERT INTO payments(order_id,workday_id,method,amount_cents) VALUES (?,?, 'cash',1000)", o, wd)
        with self.assertRaises(sqlite3.IntegrityError):
            self.q("INSERT INTO payments(order_id,workday_id,method,amount_cents) VALUES (?,?, 'qr',1000)", o, wd)
        with self.assertRaises(sqlite3.IntegrityError):
            o2 = self.order(wd)
            self.q("INSERT INTO payments(order_id,workday_id,method,amount_cents) VALUES (?,?, 'tarjeta',1)", o2, wd)

    def test_linea_es_producto_o_promocion(self):
        o = self.order(self.workday())
        with self.assertRaises(sqlite3.IntegrityError):
            self.q("INSERT INTO order_items(order_id,name_snapshot,quantity,unit_price_cents) VALUES (?,'x',1,100)", o)
        with self.assertRaises(sqlite3.IntegrityError):
            self.q("INSERT INTO order_items(order_id,variant_id,promotion_id,name_snapshot,quantity,unit_price_cents) "
                   "VALUES (?,?,1,'x',1,100)", o, self.variant())

    def test_stock_calculado_desde_movimientos(self):
        v, wd = self.variant(), self.workday()
        o = self.order(wd)
        ins = "INSERT INTO inventory_movements(variant_id,kind,quantity,order_id) VALUES (?,?,?,?)"
        self.q(ins, v, "initial", 5, None)
        self.q(ins, v, "receipt", 10, None)
        self.q(ins, v, "sale", -3, o)
        self.q(ins, v, "adjust_neg", -1, None)
        self.q(ins, v, "sale_reversal", 2, o)
        self.assertEqual(self.q("SELECT stock FROM variant_stock WHERE variant_id=?", v).fetchone()[0], 13)

    def test_movimientos_signo_y_pedido_obligatorio(self):
        v, o = self.variant(), self.order(self.workday())
        ins = "INSERT INTO inventory_movements(variant_id,kind,quantity,order_id) VALUES (?,?,?,?)"
        for kind, qty, order in [("sale", 2, o), ("receipt", -2, None), ("sale", -1, None), ("adjust_pos", 0, None)]:
            with self.assertRaises(sqlite3.IntegrityError):
                self.q(ins, v, kind, qty, order)

    def test_transaccion_hace_rollback(self):
        try:
            with db.transaction(self.conn):
                self.q("INSERT INTO settings(key,value) VALUES ('t','1')")
                raise RuntimeError("falla simulada")
        except RuntimeError:
            pass
        self.assertIsNone(self.q("SELECT 1 FROM settings WHERE key='t'").fetchone())
        with db.transaction(self.conn):
            self.q("INSERT INTO settings(key,value) VALUES ('t','1')")
        self.assertIsNotNone(self.q("SELECT 1 FROM settings WHERE key='t'").fetchone())


class TestDinero(unittest.TestCase):
    def test_parse(self):
        for text, cents in [("8", 800), ("17", 1700), ("8,50", 850), ("8.5", 850), ("Bs 12", 1200),
                            ("1.250,50", 125050), ("0,10", 10), ("1.250", 125000), ("1.250.000", 125000000),
                            ("12.50", 1250), (5, 500)]:
            self.assertEqual(parse_bs(text), cents, text)
        self.assertEqual(parse_bs("0.1") + parse_bs("0.2"), parse_bs("0.3"))  # sin error de float

    def test_parse_invalidos(self):
        for bad in ["", "abc", "-5", -5, "1,234", "nan", "inf", "99999999999999999999", "1e999999"]:
            with self.assertRaises(ValidationError, msg=bad):
                parse_bs(bad)

    def test_format(self):
        self.assertEqual(format_bs(800), "Bs 8")
        self.assertEqual(format_bs(850), "Bs 8,50")
        self.assertEqual(format_bs(125050), "Bs 1.250,50")
        self.assertEqual(format_bs(-300), "-Bs 3")
        self.assertEqual(format_bs(None), "Bs 0")


class TestApp(Base):
    def test_escucha_en_red_local(self):
        self.assertEqual(config.HOST, "0.0.0.0")

    def test_rutas_y_errores(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/").status_code, 200)
        health = client.get("/salud")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.get_json()["ok"])
        missing = client.get("/no-existe")
        self.assertEqual(missing.status_code, 404)
        self.assertIn("Error 404", missing.get_data(as_text=True))
        self.assertEqual(client.get("/api/x").get_json()["error"], "No se encontró la página.")

    def test_backup_previo_a_migrar(self):
        db_path = self.data / "otra.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE x(a)")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        with self.assertRaises(Exception):  # v1 falsa: 002 falla porque no existen las tablas
            db.migrate(db_path, self.data / "bk")
        self.assertEqual(len(list((self.data / "bk").glob("pre_migracion_v1_*.db"))), 1)
        self.assertEqual(sqlite3.connect(db_path).execute("PRAGMA user_version").fetchone()[0], 1)  # rollback


if __name__ == "__main__":
    unittest.main()
