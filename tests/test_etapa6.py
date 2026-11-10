"""Etapa 6: historial, comparación, informes, exportaciones, copias de seguridad, notas y configuración.
Las cifras se recalculan aparte con SQL directo para comprobar que los informes coinciden con los registros."""
import io
import json
import shutil
import sqlite3
import subprocess
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook
from PIL import Image

from cafe import db
from cafe.services import backup, orders, reports
from tests.test_etapa5 import CajaBase

TODAY = date.today()
D2 = TODAY + timedelta(days=1)
D2_TXT = D2.strftime("%d/%m/%Y")


class Datos(CajaBase):
    """Dos jornadas conocidas:
    Hoy:      A×2 (Bs 10 c/u) efectivo; B×1 (Bs 8) QR; combo Bs 17 efectivo con descuento Bs 2;
              pedido cancelado ya cobrado (A×1, efectivo); pendiente cancelado; gasto Bs 5. Contado Bs 140.
    Mañana (D2): A×3 efectivo; gasto Bs 3; sin conteo (queda abierta)."""

    def build(self):
        self.a = self.v("Alfa", 1000, stock=20, category=4)
        self.b = self.v("Beta", 800, tracks=0, category=1)
        self.c = self.v("Café", 1500, tracks=0, category=1)
        self.g = self.v("Galleta", 500, stock=10, category=7)
        self.order((self.a, 2), pay="cash")
        self.order((self.b, 1), pay="qr")
        promo = self.q("SELECT id FROM promotions").fetchone()["id"]
        slots = [r["id"] for r in self.q("SELECT id FROM promotion_slots WHERE promotion_id=? ORDER BY position", promo)]
        r = self.api("POST", "/api/pedidos", {"lines": [{"promotion_id": promo, "selections": {
            str(slots[0]): [self.c], str(slots[1]): [self.g]}}], "discount": "2"})
        self.combo_order = r.get_json()["id"]
        self.api("POST", f"/api/pedidos/{self.combo_order}/pagar", {"method": "cash"})
        self.cancelled = self.order((self.a, 1), pay="cash")
        self.api("POST", f"/api/pedidos/{self.cancelled}/cancelar")
        pend = self.order((self.b, 1))
        self.api("POST", f"/api/pedidos/{pend}/cancelar")
        self.expense("Taxi", "5")
        self.assertEqual(self.close(counted="140").status_code, 302)
        self.day1 = self.wd
        self.day2 = self.new_day(D2.isoformat())
        self.order((self.a, 3), pay="cash")
        self.expense("Bolsas", "3")
        self.ok()

    def rep(self, start=TODAY, end=D2):
        with self.app.app_context():
            return reports.report(start, end)


class TestHistorial(Datos):
    def setUp(self):
        super().setUp()
        self.build()

    def test_totales_por_jornada_coinciden_con_sql(self):
        with self.app.app_context():
            rows = {r["id"]: r for r in reports.workday_summaries(db.get_db())}
        d1, d2 = rows[self.day1], rows[self.day2]
        # hoy: efectivo 20 + 15 (combo 17 - 2) = 35; QR 8; gastos 5; esperado 100 + 35 - 5 = 130; contado 140 -> +10
        self.assertEqual((d1["cash"], d1["qr"], d1["sales"], d1["expenses"], d1["expected"]), (3500, 800, 4300, 500, 13000))
        self.assertEqual((d1["paid"], d1["cancelled"], d1["pending"], d1["difference"]), (3, 2, 0, 1000))
        self.assertEqual((d1["voided_n"], d1["voided"], d1["discounts"]), (1, 1000, 200))
        self.assertEqual((d2["cash"], d2["expenses"], d2["expected"], d2["difference"]), (3000, 300, 12700, None))
        for wid, r in rows.items():
            cash = self.q("SELECT COALESCE(SUM(amount_cents),0) s FROM payments WHERE workday_id=? AND method='cash'", wid).fetchone()["s"]
            self.assertEqual(r["cash"], cash)

    def test_pagina_historial_y_rango(self):
        html = self.client.get(f"/historial?desde={TODAY.isoformat()}&hasta={D2.isoformat()}").get_data(as_text=True)
        self.assertIn("Bs 130", html)       # esperado de hoy
        self.assertIn("+Bs 10", html)       # diferencia (sobra)
        self.assertIn(D2_TXT, html)
        only_today = self.client.get(f"/historial?desde={TODAY.isoformat()}&hasta={TODAY.isoformat()}").get_data(as_text=True)
        self.assertNotIn(D2_TXT, only_today)

    def test_rango_invertido_o_invalido(self):
        r = self.client.get("/historial?desde=2099-01-02&hasta=2099-01-01", follow_redirects=True)
        self.assertIn("no puede ser posterior", r.get_data(as_text=True))
        r = self.client.get("/informes?desde=hola", follow_redirects=True)
        self.assertIn("fecha no válida", r.get_data(as_text=True))

    def test_comparar(self):
        r = self.client.get(f"/historial/comparar?j={self.day1}&j={self.day2}")
        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            c = reports.compare([self.day2, self.day1])
        self.assertEqual([d["id"] for d in c["days"]], [self.day1, self.day2])   # ordenado por fecha
        alfa = next(p for p in c["products"] if p["name"] == "Alfa")
        self.assertEqual(alfa["units"], [2, 3])      # el cancelado no cuenta
        self.assertEqual(alfa["total_units"], 5)

    def test_comparar_valida_seleccion(self):
        for q in (f"j={self.day1}", "j=999&j=998", "j=x&j=y", "&".join(f"j={i}" for i in range(1, 10))):
            r = self.client.get("/historial/comparar?" + q, follow_redirects=True)
            self.assertIn('class="aviso error"', r.get_data(as_text=True), q)


class TestInforme(Datos):
    def setUp(self):
        super().setUp()
        self.build()

    def test_cuadra_producto_categoria_metodo_y_total(self):
        r = self.rep()
        t = r["totals"]
        self.assertEqual(t["sales"], 4300 + 3000)
        self.assertTrue(r["reconciles"])
        self.assertEqual(sum(p["amount"] for p in r["by_product"]) - t["discounts"], t["sales"])
        self.assertEqual(sum(c["amount"] for c in r["by_category"]), r["gross"])
        self.assertEqual(sum(m["amount"] for m in r["methods"]), t["sales"])
        sql = self.q("SELECT COALESCE(SUM(amount_cents),0) s FROM payments").fetchone()["s"]
        self.assertEqual(t["sales"], sql)

    def test_combo_es_una_venta_propia_y_sus_componentes_van_al_inventario(self):
        r = self.rep()
        combo = [p for p in r["by_product"] if p["is_combo"]]
        self.assertEqual([(p["name"], p["units"], p["amount"]) for p in combo], [("Combo Bs 17", 1, 1700)])
        self.assertNotIn("Galleta", [p["name"] for p in r["by_product"]])
        self.assertIn(reports.COMBO_CATEGORY, [c["name"] for c in r["by_category"]])
        inv = {i["name"]: i for i in r["inventory"]}
        self.assertEqual(inv["Galleta"]["sold"], 1)
        self.assertEqual(inv["Alfa"]["sold"], 5)        # 2 + 3; el cancelado se revirtió

    def test_precio_historico(self):
        self.q("UPDATE product_variants SET price_cents=9900 WHERE id=?", self.a)
        alfa = next(p for p in self.rep()["by_product"] if p["name"] == "Alfa")
        self.assertEqual(alfa["amount"], 5 * 1000)

    def test_cancelados_con_pago_anulado(self):
        c = {x["id"]: x for x in self.rep()["cancelled"]}
        self.assertEqual((c[self.cancelled]["voided_method"], c[self.cancelled]["voided_amount"]), ("cash", 1000))
        self.assertEqual(len(c), 2)
        self.assertEqual(sum(1 for x in c.values() if x["voided_method"] is None), 1)

    def test_gastos_agrupados(self):
        r = self.rep()
        self.assertEqual({g["concept"]: g["amount"] for g in r["expenses_by_concept"]}, {"Taxi": 500, "Bolsas": 300})

    def test_existencias_a_una_fecha_pasada(self):
        past = {s["name"]: s["stock"] for s in self.rep(TODAY, TODAY)["stock"]}
        now = {s["name"]: s["stock"] for s in self.rep()["stock"]}
        self.assertEqual(past["Alfa"], 20 - 2)
        self.assertEqual(now["Alfa"], 20 - 2 - 3)
        self.assertEqual(now["Galleta"], 9)

    def test_rango_sin_jornadas(self):
        r = self.rep(date(2000, 1, 1), date(2000, 1, 2))
        self.assertEqual((r["totals"]["days"], r["by_product"], r["methods"]), (0, [], []))
        self.assertEqual(self.client.get("/informes?desde=2000-01-01&hasta=2000-01-02").status_code, 200)

    def test_pantalla_informe(self):
        html = self.client.get(f"/informes?desde={TODAY.isoformat()}&hasta={D2.isoformat()}").get_data(as_text=True)
        self.assertIn("Bs 73", html)              # total de ventas 43 + 30
        self.assertIn("combo", html)
        self.assertNotIn("no cuadran", html)


class TestExportaciones(Datos):
    def setUp(self):
        super().setUp()
        self.build()
        self.qs = f"desde={TODAY.isoformat()}&hasta={D2.isoformat()}"

    def test_excel_coincide_con_el_informe(self):
        r = self.client.get("/informes/excel?" + self.qs)
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["Content-Disposition"])
        wb = load_workbook(io.BytesIO(r.data))
        self.assertIn("Ventas por producto", wb.sheetnames)
        resumen = {row[0]: row[1] for row in wb["Resumen"].iter_rows(min_row=6, values_only=True) if row[0]}
        self.assertEqual(float(resumen["Total de ventas registradas"]), 73.0)
        self.assertEqual(float(resumen["Ventas por QR"]), 8.0)
        self.assertEqual(float(resumen["Gastos"]), 8.0)
        prod = {row[0]: row for row in wb["Ventas por producto"].iter_rows(min_row=6, values_only=True) if row[0]}
        self.assertEqual((prod["Alfa"][1], float(prod["Alfa"][2])), (5, 50.0))
        self.assertEqual(wb["Ventas por producto"]["C6"].number_format, '"Bs" #,##0.00')

    @unittest.skipUnless(shutil.which("pdftotext"), "pdftotext no disponible")
    def test_pdf_contiene_las_cifras(self):
        r = self.client.get("/informes/pdf?" + self.qs)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data.startswith(b"%PDF"))
        text = subprocess.run(["pdftotext", "-layout", "-", "-"], input=r.data, capture_output=True).stdout.decode()
        self.assertIn("Bs 73", text)
        self.assertIn("Combo Bs 17", text)
        self.assertIn("no oficial", text)

    def test_pdf_y_excel_sin_datos(self):
        for fmt in ("excel", "pdf"):
            self.assertEqual(self.client.get(f"/informes/{fmt}?desde=2000-01-01&hasta=2000-01-01").status_code, 200)

    def test_informe_de_cierre_enlaza_exportaciones_y_muestra_cancelados(self):
        html = self.client.get(f"/jornada/{self.day1}/informe").get_data(as_text=True)
        self.assertIn("/informes/pdf?desde=", html)
        self.assertIn("Pedidos cancelados", html)
        self.assertIn("no cuenta en la caja", html)


class TestCopias(CajaBase):
    def photo(self):
        path = Path(self.app.config["UPLOAD_DIR"]) / "productos" / "foto.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (10, 10), "brown").save(path)
        return path

    def count(self, table):
        return self.q(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]

    def make(self):
        with self.app.app_context():
            return backup.create("manual")

    def test_crear_contiene_base_fotos_y_metadatos(self):
        self.photo()
        self.v("Alfa", 1000, stock=3)
        name = self.make()
        with zipfile.ZipFile(Path(self.app.config["BACKUP_DIR"]) / name) as z:
            names = set(z.namelist())
            meta = json.loads(z.read("copia.json"))
            snap = Path(self.tmp.name) / "snap.db"
            snap.write_bytes(z.read("cafe.db"))
        self.assertTrue({"cafe.db", "copia.json", "uploads/productos/foto.jpg"} <= names)
        self.assertEqual(meta["schema_version"], len(db.discover_migrations()))
        c = sqlite3.connect(snap)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM products").fetchone()[0], 1)
        c.close()

    def test_restaurar_recupera_datos_y_fotos_y_guarda_el_estado_anterior(self):
        foto = self.photo()
        a = self.v("Alfa", 1000, stock=5)
        self.order((a, 2), pay="cash")
        name = self.make()
        # cambios posteriores que la restauración debe deshacer
        self.order((a, 1), pay="qr")
        self.expense("Taxi", "4")
        foto.unlink()
        (foto.parent / "nueva.jpg").write_bytes(foto.parent.joinpath("..").resolve().name.encode())
        r = self.client.post(f"/copias/{name}/restaurar", data={"entiendo": "1"}, follow_redirects=True)
        self.assertIn("Datos restaurados", r.get_data(as_text=True))
        self.assertEqual((self.count("orders"), self.count("expenses")), (1, 0))
        self.assertEqual(self.stock(a), 3)
        self.assertTrue(foto.exists())
        self.assertFalse((foto.parent / "nueva.jpg").exists())
        self.ok()
        kinds = [b["kind"] for b in self.in_app(backup.list_backups)]
        self.assertIn("antes_de_restaurar", kinds)
        # y el estado anterior se puede recuperar
        safety = next(b["name"] for b in self.in_app(backup.list_backups) if b["kind"] == "antes_de_restaurar")
        self.client.post(f"/copias/{safety}/restaurar", data={"entiendo": "1"})
        self.assertEqual((self.count("orders"), self.count("expenses")), (2, 1))

    def test_restaurar_exige_confirmacion(self):
        name = self.make()
        self.v("Alfa", 1000)
        r = self.client.post(f"/copias/{name}/restaurar", data={}, follow_redirects=True)
        self.assertIn("Marca la casilla", r.get_data(as_text=True))
        self.assertEqual(self.count("products"), 1)

    def _upload(self, data, fname):
        return self.client.post("/copias/restaurar-archivo", data={"entiendo": "1", "archivo": (io.BytesIO(data), fname)},
                                content_type="multipart/form-data", follow_redirects=True)

    def _zip(self, files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n, content in files.items():
                z.writestr(n, content)
        return buf.getvalue()

    def test_rechaza_archivos_invalidos_sin_tocar_nada(self):
        self.v("Alfa", 1000)
        good_db = (Path(self.app.config["DB_PATH"])).read_bytes()
        cases = {
            "texto.zip": b"no soy un zip",
            "sin_db.zip": self._zip({"copia.json": "{}"}),
            "ruta.zip": self._zip({"cafe.db": good_db, "../../malo.txt": "x"}),
            "ruta_fotos.zip": self._zip({"cafe.db": good_db, "uploads/../../malo.txt": "x"}),
            "ruta_absoluta.zip": self._zip({"cafe.db": good_db, "uploads/C:/malo.txt": "x"}),
            "extra.zip": self._zip({"cafe.db": good_db, "programa.exe": "x"}),
            "basura.db": b"SQLite format 3\x00" + b"\x00" * 200,
            "foto.png": b"\x89PNG",
        }
        other = Path(self.tmp.name) / "otra.db"
        c = sqlite3.connect(other); c.execute("CREATE TABLE x(a)"); c.execute("PRAGMA user_version=1"); c.commit(); c.close()
        cases["otra_app.db"] = other.read_bytes()
        newer = Path(self.tmp.name) / "nueva.db"
        shutil.copyfile(self.app.config["DB_PATH"], newer)
        c = sqlite3.connect(newer); c.execute("PRAGMA user_version=999"); c.commit(); c.close()
        cases["nueva.db"] = newer.read_bytes()
        for fname, data in cases.items():
            r = self._upload(data, fname)
            self.assertIn('class="aviso error"', r.get_data(as_text=True), fname)
            self.assertEqual(self.count("products"), 1, fname)
        self.assertFalse((Path(self.tmp.name).parent / "malo.txt").exists())
        self.assertNotIn("antes_de_restaurar", [b["kind"] for b in self.in_app(backup.list_backups)])
        self.assertIn("más nueva", self._upload(cases["nueva.db"], "nueva.db").get_data(as_text=True))

    def test_restaurar_desde_db_suelto_conserva_fotos(self):
        foto = self.photo()
        snap = Path(self.tmp.name) / "solo.db"
        src = db.connect(self.app.config["DB_PATH"]); dst = sqlite3.connect(snap); src.backup(dst); dst.close(); src.close()
        self.v("Alfa", 1000)
        r = self._upload(snap.read_bytes(), "solo.db")
        self.assertIn("Datos restaurados", r.get_data(as_text=True))
        self.assertEqual(self.count("products"), 0)
        self.assertTrue(foto.exists())

    def test_restaurar_copia_de_version_anterior_aplica_migraciones(self):
        old = Path(self.tmp.name) / "v4.db"
        c = db.connect(old)
        for n, f in db.discover_migrations():
            if n <= 4:
                c.executescript(f.read_text(encoding="utf-8"))
        c.execute("PRAGMA user_version=4"); c.close()
        r = self._upload(old.read_bytes(), "v4.db")
        self.assertIn("Datos restaurados", r.get_data(as_text=True))
        self.assertEqual(self.q("PRAGMA user_version").fetchone()[0], len(db.discover_migrations()))

    def test_si_falla_a_mitad_vuelve_al_estado_anterior(self):
        name = self.make()
        self.v("Alfa", 1000)
        with mock.patch.object(backup, "_replace_uploads", side_effect=[OSError("disco lleno"), None]):
            r = self.client.post(f"/copias/{name}/restaurar", data={"entiendo": "1"}, follow_redirects=True)
        self.assertIn("No se pudo restaurar", r.get_data(as_text=True))
        self.assertEqual(self.count("products"), 1)        # sigue el estado previo, no el de la copia

    def test_descarga_y_nombres_seguros(self):
        name = self.make()
        r = self.client.get(f"/copias/{name}")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(r.data)))
        for bad in ("..%2Fcafe.db", "cafe.db", "secret.key", "x.zip"):
            self.assertEqual(self.client.get(f"/copias/{bad}").status_code, 404, bad)
        self.assertIn(self.client.post("/copias/..%2F..%2Fcafe.db/eliminar", follow_redirects=True).status_code, (200, 404))
        self.assertTrue(Path(self.app.config["DB_PATH"]).exists())

    def test_eliminar_copia(self):
        name = self.make()
        self.client.post(f"/copias/{name}/eliminar")
        self.assertEqual(self.in_app(backup.list_backups), [])

    def test_cerrar_jornada_crea_copia_automatica_y_conserva_30(self):
        self.close()
        self.assertEqual([b["kind"] for b in self.in_app(backup.list_backups)], ["cierre"])
        bdir = Path(self.app.config["BACKUP_DIR"])
        for i in range(35):
            (bdir / f"cafe_cierre_20000101_0000{i:02d}.zip").write_bytes(b"")
        with self.app.app_context():
            backup._prune("cierre", backup.KEEP_AUTOMATIC)
        self.assertEqual(len(list(bdir.glob("cafe_cierre_*.zip"))), 30)

    def test_fallo_de_copia_no_impide_cerrar(self):
        with mock.patch.object(backup, "create", side_effect=OSError("sin espacio")):
            r = self.close()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.q("SELECT status FROM workdays WHERE id=?", self.wd).fetchone()["status"], "closed")

    def test_pantalla_configuracion(self):
        self.make()
        html = self.client.get("/configuracion").get_data(as_text=True)
        self.assertIn("Crear copia ahora", html)
        self.assertIn("Base + fotos", html)


class TestNotas(CajaBase):
    def save(self, **data):
        return self.client.post("/notas/guardar", data=data, follow_redirects=True)

    def test_crear_editar_completar_eliminar(self):
        self.save(kind="task", title="Pedir bolsas", body="De papel")
        nid = self.q("SELECT id FROM personal_notes").fetchone()["id"]
        self.assertIn("Pedir bolsas", self.client.get("/").get_data(as_text=True))       # aparece en Inicio
        self.save(id=str(nid), kind="reminder", title="Pedir bolsas grandes", body="")
        row = self.q("SELECT * FROM personal_notes WHERE id=?", nid).fetchone()
        self.assertEqual((row["kind"], row["title"]), ("reminder", "Pedir bolsas grandes"))
        self.assertEqual(self.api("POST", f"/api/notas/{nid}/hecha", {"done": True}).status_code, 200)
        self.assertNotIn("Pedir bolsas grandes", self.client.get("/notas").get_data(as_text=True))
        self.assertIn("Pedir bolsas grandes", self.client.get("/notas?estado=hechas").get_data(as_text=True))
        self.assertNotIn("Pedir bolsas grandes", self.client.get("/").get_data(as_text=True))
        self.client.post(f"/notas/{nid}/eliminar")
        self.assertEqual(self.q("SELECT COUNT(*) n FROM personal_notes").fetchone()["n"], 0)

    def test_validacion(self):
        r = self.save(kind="task", title="   ")
        self.assertIn("El título es obligatorio", r.get_data(as_text=True))
        r = self.save(kind="cosa", title="X")
        self.assertIn("Elige si es", r.get_data(as_text=True))
        self.assertEqual(self.save(kind="note", title="x" * 121).status_code, 400)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM personal_notes").fetchone()["n"], 0)
        self.assertEqual(self.api("POST", "/api/notas/999/hecha", {"done": True}).status_code, 404)
        self.assertEqual(self.client.get("/notas/999").status_code, 404)

    def test_busqueda_sin_acentos_y_filtro(self):
        self.save(kind="note", title="Indicación de la encargada", body="Limpiar la máquina")
        self.save(kind="task", title="Otra cosa")
        html = self.client.get("/notas?estado=todas&q=maquina").get_data(as_text=True)
        self.assertIn("Indicación de la encargada", html)
        self.assertNotIn("Otra cosa", html)
        html = self.client.get("/notas?tipo=task&estado=todas").get_data(as_text=True)
        self.assertNotIn("Indicación de la encargada", html)


class TestConfiguracion(CajaBase):
    def test_guardar_y_usar_la_reserva_por_defecto(self):
        r = self.client.post("/configuracion", data={"default_fund_cents": "150", "default_reserve_cents": "80,50"},
                             follow_redirects=True)
        self.assertIn("Configuración guardada", r.get_data(as_text=True))
        vals = dict(self.q("SELECT key, value FROM settings").fetchall())
        self.assertEqual((vals["default_fund_cents"], vals["default_reserve_cents"]), ("15000", "8050"))
        self.close(reserve="")
        self.assertEqual(self.q("SELECT reserve_cents FROM workdays WHERE id=?", self.wd).fetchone()["reserve_cents"], 10000)
        wid = self.new_day("2099-03-01")
        self.assertEqual(self.q("SELECT reserve_cents FROM workdays WHERE id=?", wid).fetchone()["reserve_cents"], 8050)

    def test_validacion(self):
        for data in ({"default_fund_cents": "abc", "default_reserve_cents": "100"},
                     {"default_fund_cents": "-5", "default_reserve_cents": "100"},
                     {"default_fund_cents": "", "default_reserve_cents": "100"},
                     {"default_fund_cents": "99999999", "default_reserve_cents": "100"}):
            r = self.client.post("/configuracion", data=data, follow_redirects=True)
            self.assertIn('class="aviso error"', r.get_data(as_text=True), data)
        self.assertEqual(dict(self.q("SELECT key, value FROM settings").fetchall())["default_fund_cents"], "10000")


class TestFotoGrande(CajaBase):
    def test_foto_de_mas_de_5_mb_se_rechaza(self):
        cat = self.q("SELECT id FROM categories LIMIT 1").fetchone()["id"]
        big = io.BytesIO(b"\xff\xd8" + b"0" * (5 * 1024 * 1024 + 10))
        r = self.client.post("/catalogo/nuevo", data={"name": "Grande", "category_id": str(cat), "photo": (big, "x.jpg"),
                                                     "var_id": [""], "var_name": [""], "var_price": ["10"],
                                                     "var_tracks": ["0"], "var_min": ["0"], "var_active": ["1"]},
                             content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("supera los 5 MB", r.get_data(as_text=True))


class TestRangoMaximo(CajaBase):
    def test_rango_de_mas_de_10_anios(self):
        r = self.client.get("/informes?desde=2000-01-01&hasta=2026-01-01", follow_redirects=True)
        self.assertIn("rango máximo", r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
