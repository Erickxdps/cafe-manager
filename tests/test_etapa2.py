import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cafe import create_app, db
from cafe.services import catalog


def png_bytes(size=(900, 600)):
    buf = io.BytesIO()
    Image.new("RGB", size, (120, 80, 40)).save(buf, "PNG")
    return buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.data = Path(self.tmp.name)
        self.app = create_app({"DATA_DIR": self.data, "TESTING": True})
        self.client = self.app.test_client()
        self.conn = db.connect(self.app.config["DB_PATH"])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def product(self, name="Café americano", variants=((("", "10", "0", "0")),), category=1, photo=None, **extra):
        data = {"name": name, "category_id": str(category), "description": "",
                "var_id": ["" for _ in variants], "var_name": [v[0] for v in variants],
                "var_price": [v[1] for v in variants], "var_tracks": [v[2] for v in variants],
                "var_min": [v[3] for v in variants], "var_active": ["1" for _ in variants]}
        data.update(extra)
        if photo:
            data["photo"] = (io.BytesIO(photo), "foto.png")
        return self.client.post("/catalogo/nuevo", data=data, content_type="multipart/form-data")

    def pid(self, name):
        return self.conn.execute("SELECT id FROM products WHERE name=?", (name,)).fetchone()["id"]

    def q(self, sql, *a):
        return self.conn.execute(sql, a)


class TestCatalogo(Base):
    def test_paginas_cargan(self):
        for url in ["/", "/catalogo", "/catalogo/nuevo", "/categorias", "/recetario", "/recetario/nueva"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_crear_producto_con_variantes(self):
        r = self.product("Éclair", (("grande", "12", "1", "2"), ("chico", "8,50", "1", "2")))
        self.assertEqual(r.status_code, 302)
        rows = self.q("SELECT name, price_cents, tracks_stock, min_stock FROM product_variants "
                      "WHERE product_id=? ORDER BY id", self.pid("Éclair")).fetchall()
        self.assertEqual([tuple(r) for r in rows], [("grande", 1200, 1, 2), ("chico", 850, 1, 2)])

    def test_validaciones(self):
        casos = [dict(name=""), dict(name="X", variants=(("", "abc", "0", "0"),)),
                 dict(name="X", variants=(("", "-3", "0", "0"),)), dict(name="X", variants=(("", "1,234", "0", "0"),)),
                 dict(name="X", variants=(("a", "5", "0", "0"), ("A", "6", "0", "0"))),
                 dict(name="X", variants=(("", "5", "0", "-1"),)), dict(name="X", category=999)]
        for caso in casos:
            r = self.product(**caso)
            self.assertEqual(r.status_code, 400, caso)
            self.assertIn("aviso error", r.get_data(as_text=True))
        self.assertEqual(self.q("SELECT COUNT(*) c FROM products").fetchone()["c"], 0)  # nada se guardó a medias

    def test_nombre_duplicado(self):
        self.product("Té")
        r = self.product("té")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Ya existe un producto", r.get_data(as_text=True))
        self.assertEqual(self.q("SELECT COUNT(*) c FROM products").fetchone()["c"], 1)

    def test_editar_precio_y_agregar_variante(self):
        self.product("Galleta", (("", "5", "1", "3"),))
        pid, vid = self.pid("Galleta"), self.q("SELECT id FROM product_variants").fetchone()["id"]
        r = self.client.post(f"/catalogo/{pid}/editar", data={
            "name": "Galleta", "category_id": "7", "description": "x", "var_id": [str(vid), ""],
            "var_name": ["", "doble"], "var_price": ["6", "10"], "var_tracks": ["1", "1"],
            "var_min": ["3", "1"], "var_active": ["1", "1"]})
        self.assertEqual(r.status_code, 302)
        prices = [r["price_cents"] for r in self.q("SELECT price_cents FROM product_variants ORDER BY id")]
        self.assertEqual(prices, [600, 1000])

    def test_variante_de_otro_producto_rechazada(self):
        self.product("A"); self.product("B")
        vid_a = self.q("SELECT id FROM product_variants WHERE product_id=?", self.pid("A")).fetchone()["id"]
        r = self.client.post(f"/catalogo/{self.pid('B')}/editar", data={
            "name": "B", "category_id": "1", "var_id": [str(vid_a)], "var_name": [""], "var_price": ["99"],
            "var_tracks": ["0"], "var_min": ["0"], "var_active": ["1"]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.q("SELECT price_cents p FROM product_variants WHERE id=?", vid_a).fetchone()["p"], 1000)

    def test_desactivar_conserva_historial(self):
        self.product("Té")
        pid = self.pid("Té")
        self.client.post(f"/catalogo/{pid}/estado", data={"active": "0"})
        self.assertEqual(self.q("SELECT active FROM products WHERE id=?", pid).fetchone()["active"], 0)
        self.assertNotIn("Té</a>", self.client.get("/catalogo").get_data(as_text=True))
        self.assertIn("Té</a>", self.client.get("/catalogo?inactivos=1").get_data(as_text=True))
        self.assertEqual(self.client.get("/api/buscar?q=te").get_json()["resultados"], [])
        self.client.post(f"/catalogo/{pid}/estado", data={"active": "1"})
        self.assertEqual(len(self.client.get("/api/buscar?q=te").get_json()["resultados"]), 1)

    def test_filtro_por_categoria(self):
        self.product("Té", category=2); self.product("Café", category=1)
        html = self.client.get("/catalogo?categoria=2").get_data(as_text=True)
        self.assertIn("Té</a>", html); self.assertNotIn("Café</a>", html)

    def test_categorias(self):
        self.client.post("/categorias", data={"name": "Sándwiches"})
        self.assertIsNotNone(self.q("SELECT 1 FROM categories WHERE name='Sándwiches'").fetchone())
        r = self.client.post("/categorias", data={"name": "sándwiches"}, follow_redirects=True)
        self.assertIn("Ya existe una categoría", r.get_data(as_text=True))
        self.client.post("/categorias", data={"id": "1", "name": "Cafés"})
        self.assertEqual(self.q("SELECT name FROM categories WHERE id=1").fetchone()["name"], "Cafés")
        self.client.post("/categorias/9/estado", data={"active": "0"})
        self.assertEqual(self.q("SELECT active FROM categories WHERE id=9").fetchone()["active"], 0)


class TestBuscador(Base):
    def setUp(self):
        super().setUp()
        self.product("Café americano", (("", "10", "0", "0"),))
        self.product("Cappuccino", (("", "14", "0", "0"),))
        self.product("Té verde", (("", "8", "0", "0"),))
        self.product("Éclair", (("grande", "12", "1", "2"), ("chico", "8", "1", "2")), category=5)
        v = self.q("SELECT id FROM product_variants WHERE name='chico'").fetchone()["id"]
        self.q("INSERT INTO inventory_movements(variant_id,kind,quantity) VALUES (?,'initial',2)", v)

    def search(self, q):
        return [r["name"] for r in self.client.get("/api/buscar", query_string={"q": q}).get_json()["resultados"]]

    def test_autocompletado(self):
        self.assertEqual(self.search("ca"), ["Café americano", "Cappuccino"])
        self.assertEqual(self.search("CA"), ["Café americano", "Cappuccino"])
        self.assertIn("Café americano", self.search("cafe"))      # sin acento
        self.assertEqual(self.search("eclair"), ["Éclair — chico", "Éclair — grande"])
        self.assertEqual(self.search("   "), [])
        self.assertEqual(self.search("zzz"), [])

    def test_disponibilidad_y_precio(self):
        res = {r["name"]: r for r in self.client.get("/api/buscar?q=eclair").get_json()["resultados"]}
        self.assertEqual(res["Éclair — chico"]["availability"], "bajo")      # 2 <= mínimo 2
        self.assertEqual(res["Éclair — chico"]["stock"], 2)
        self.assertEqual(res["Éclair — grande"]["availability"], "agotado")
        self.assertEqual(res["Éclair — grande"]["price_cents"], 1200)
        self.assertEqual(self.client.get("/api/buscar?q=te").get_json()["resultados"][0]["availability"], "sin_control")

    def test_prefijo_antes_que_contiene(self):
        self.assertEqual(self.search("e")[0], "Éclair — chico")


class TestFotos(Base):
    def test_foto_se_reduce_y_se_sirve(self):
        self.assertEqual(self.product("Té", photo=png_bytes()).status_code, 302)
        photo = self.q("SELECT photo FROM products").fetchone()["photo"]
        path = self.data / "uploads" / photo
        self.assertTrue(path.exists())
        with Image.open(path) as img:
            self.assertLessEqual(max(img.size), 480)
        resp = self.client.get(f"/media/{photo}")
        self.assertEqual(resp.status_code, 200)
        resp.close()

    def test_archivo_no_imagen_rechazado(self):
        r = self.product("Té", photo=b"esto no es una imagen")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.q("SELECT COUNT(*) c FROM products").fetchone()["c"], 0)

    def test_foto_huerfana_se_borra_si_falla_el_guardado(self):
        self.product("Té")
        self.product("Té", photo=png_bytes())  # duplicado -> falla
        files = [p for p in (self.data / "uploads").rglob("*.jpg")]
        self.assertEqual(files, [])

    def test_reemplazar_foto_borra_la_anterior(self):
        self.product("Té", photo=png_bytes())
        pid = self.pid("Té"); old = self.q("SELECT photo FROM products").fetchone()["photo"]
        vid = self.q("SELECT id FROM product_variants").fetchone()["id"]
        self.client.post(f"/catalogo/{pid}/editar", content_type="multipart/form-data", data={
            "name": "Té", "category_id": "2", "var_id": [str(vid)], "var_name": [""], "var_price": ["10"],
            "var_tracks": ["0"], "var_min": ["0"], "var_active": ["1"], "photo": (io.BytesIO(png_bytes((50, 50))), "n.png")})
        new = self.q("SELECT photo FROM products").fetchone()["photo"]
        self.assertNotEqual(new, old)
        self.assertFalse((self.data / "uploads" / old).exists())
        self.assertTrue((self.data / "uploads" / new).exists())

    def test_no_se_sale_de_uploads(self):
        self.assertEqual(self.client.get("/media/../cafe.db").status_code, 404)
        self.assertEqual(self.client.get("/media/%2e%2e/cafe.db").status_code, 404)


class TestRecetas(Base):
    def test_crud(self):
        self.product("Latte")
        form = {"name": "Latte", "drink_type": "Café", "description": "d", "notes": "n",
                "steps": "Espresso\n\nVaporizar leche\n  Servir ", "product_id": str(self.pid("Latte"))}
        r = self.client.post("/recetario/nueva", data=form)
        self.assertEqual(r.status_code, 302)
        rid = self.q("SELECT id FROM recipes").fetchone()["id"]
        self.assertEqual([s["text"] for s in self.q("SELECT text FROM recipe_steps ORDER BY position")],
                         ["Espresso", "Vaporizar leche", "Servir"])
        self.assertIn("Vaporizar leche", self.client.get(f"/catalogo/{self.pid('Latte')}").get_data(as_text=True))
        self.client.post(f"/recetario/{rid}/editar", data={**form, "steps": "Solo un paso"})
        self.assertEqual(self.q("SELECT COUNT(*) c FROM recipe_steps").fetchone()["c"], 1)
        r = self.client.post("/recetario/nueva", data={**form, "steps": ""})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/recetario/nueva", data=form)
        self.assertEqual(r.status_code, 400)  # nombre duplicado
        self.assertEqual(self.client.post(f"/recetario/{rid}/eliminar").status_code, 302)
        self.assertEqual(self.q("SELECT COUNT(*) c FROM recipes").fetchone()["c"], 0)
        self.assertEqual(self.q("SELECT COUNT(*) c FROM recipe_steps").fetchone()["c"], 0)  # cascada
        self.assertEqual(self.client.get(f"/recetario/{rid}").status_code, 404)

    def test_buscador_incluye_receta(self):
        self.product("Latte")
        self.client.post("/recetario/nueva", data={"name": "Latte", "steps": "a", "product_id": str(self.pid("Latte"))})
        self.assertIsNotNone(self.client.get("/api/buscar?q=lat").get_json()["resultados"][0]["recipe_id"])


class TestSeguridad(Base):
    def test_origen_externo_rechazado(self):
        r = self.client.post("/categorias", data={"name": "Mala"}, headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertIsNone(self.q("SELECT 1 FROM categories WHERE name='Mala'").fetchone())

    def test_host_no_local_rechazado(self):
        self.assertEqual(self.client.get("/", headers={"Host": "evil.example"}).status_code, 403)

    def test_red_local_aceptada(self):
        r = self.client.get("/", headers={"Host": "192.168.0.8:31847"}, environ_base={"REMOTE_ADDR": "192.168.0.20"})
        self.assertEqual(r.status_code, 200)

    def test_ip_publica_rechazada(self):
        self.assertEqual(self.client.get("/", headers={"Host": "8.8.8.8"}).status_code, 403)
        self.assertEqual(self.client.get("/", environ_base={"REMOTE_ADDR": "8.8.8.8"}).status_code, 403)

    def test_formulario_de_otro_origen_en_red_local_rechazado(self):
        r = self.client.post("/categorias", data={"name": "Mala"}, headers={
            "Host": "192.168.0.8:31847", "Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_origen_local_ok(self):
        r = self.client.post("/categorias", data={"name": "Buena"}, headers={"Origin": "http://localhost"})
        self.assertEqual(r.status_code, 302)

    def test_xss_escapado(self):
        self.product("<script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)</script>", self.client.get("/catalogo").get_data(as_text=True))


class TestDatosPrueba(Base):
    def test_cargar_y_quitar(self):
        import cargar_prueba as cp
        with self.app.app_context(), db.transaction(db.get_db()) as conn:
            cp.load(conn)
            cp.load(conn)  # idempotente
        self.assertEqual(self.q("SELECT COUNT(*) c FROM products WHERE is_sample=1").fetchone()["c"], len(cp.PRODUCTS))
        self.assertEqual(self.q("SELECT COUNT(*) c FROM recipes").fetchone()["c"], 1)
        self.assertEqual(self.client.get("/api/buscar?q=galleta").get_json()["resultados"][0]["name"][:8], "(Prueba)")
        self.assertEqual(db.check_integrity(self.conn), [])
        with self.app.app_context(), db.transaction(db.get_db()) as conn:
            cp.remove(conn)
        self.assertEqual(self.q("SELECT COUNT(*) c FROM products").fetchone()["c"], 0)
        self.assertEqual(db.check_integrity(self.conn), [])


if __name__ == "__main__":
    unittest.main()
