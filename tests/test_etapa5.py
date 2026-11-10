import re
import unittest
from datetime import date, timedelta

from cafe import db
from cafe.services import caja, closing, inventory, orders, workdays
from tests.test_etapa4 import InvBase


class CajaBase(InvBase):
    def api_caja(self):
        return self.client.get("/api/caja").get_json()

    def expected_sql(self):
        """Efectivo esperado recalculado directamente de las tablas, sin pasar por el servicio."""
        fund = self.q("SELECT opening_fund_cents f FROM workdays WHERE status='open'").fetchone()["f"]
        cash = self.q("SELECT COALESCE(SUM(p.amount_cents),0) s FROM payments p JOIN orders o ON o.id=p.order_id "
                      "WHERE p.method='cash' AND o.status='paid'").fetchone()["s"]
        spent = self.q("SELECT COALESCE(SUM(amount_cents),0) s FROM expenses").fetchone()["s"]
        return fund + cash - spent

    def expense(self, concept="Taxi", amount="5", note=""):
        return self.client.post("/caja/gasto", data={"concept": concept, "amount": amount, "note": note}, follow_redirects=True)

    def close(self, counted="", reserve="100", notes=""):
        return self.client.post("/jornada/cerrar", data={"counted": counted, "reserve": reserve, "notes": notes})

    def in_app(self, fn, *a, **kw):
        with self.app.app_context():
            return fn(*a, **kw)

    def new_day(self, day="2099-01-01", fund="100"):
        return self.in_app(workdays.open_workday, fund, day)


class TestCaja(CajaBase):
    def test_escenario_conocido(self):
        a, b, c = self.v("A", 1700, tracks=0), self.v("B", 800, tracks=0), self.v("C", 1000, tracks=0)
        self.order((a, 1), pay="cash"); self.order((b, 1), pay="cash"); self.order((c, 1), pay="qr")
        self.expense("Taxi", "5")
        d = self.api_caja()
        self.assertEqual((d["fund"], d["cash"], d["qr"], d["expenses"], d["expected"]), (10000, 2500, 1000, 500, 12000))
        self.assertEqual(d["expected"], 10000 + 2500 - 500)           # 100 + 25 - 5
        self.assertEqual(d["expected"], self.expected_sql())
        self.assertEqual(d["expected_text"], "Bs 120")
        with self.app.app_context():
            t = caja.totals(db.get_db(), self.wd)
        self.assertEqual((t["sales"], t["paid"], t["pending"], t["registered"]), (3500, 3, 0, 3))

    def test_qr_no_suma_al_efectivo_y_pendiente_no_cuenta(self):
        a = self.v("A", 1000, tracks=0)
        self.order((a, 1), pay="qr")
        self.assertEqual(self.api_caja()["expected"], 10000)
        self.order((a, 3))                                              # pendiente
        d = self.api_caja()
        self.assertEqual((d["cash"], d["qr"], d["expected"]), (0, 1000, 10000))
        with self.app.app_context():
            t = caja.totals(db.get_db(), self.wd)
        self.assertEqual((t["pending"], t["registered"], t["paid"]), (1, 2, 1))

    def test_cancelar_editar_y_cambiar_metodo_se_reflejan(self):
        a = self.v("A", 1000, tracks=0)
        o1 = self.order((a, 2), pay="cash")                              # +20
        self.assertEqual(self.api_caja()["expected"], 12000)
        self.edit(o1, (a, 3))                                            # +30
        self.assertEqual(self.api_caja()["expected"], 13000)
        self.api("POST", f"/api/pedidos/{o1}/metodo", {"method": "qr"})  # pasa a QR
        d = self.api_caja()
        self.assertEqual((d["cash"], d["qr"], d["expected"]), (0, 3000, 10000))
        self.api("POST", f"/api/pedidos/{o1}/metodo", {"method": "cash"})
        self.api("POST", f"/api/pedidos/{o1}/cancelar")
        d = self.api_caja()
        self.assertEqual((d["cash"], d["expected"]), (0, 10000))
        self.assertEqual(d["expected"], self.expected_sql())

    def test_descuento_se_refleja_en_el_efectivo(self):
        a = self.v("A", 1000, tracks=0)
        self.order((a, 1), discount="2,50", pay="cash")
        self.assertEqual(self.api_caja()["cash"], 750)

    def test_gastos_validacion(self):
        for concept, amount in [("", "5"), ("   ", "5"), ("Taxi", ""), ("Taxi", "0"), ("Taxi", "-3"), ("Taxi", "abc"),
                                ("Taxi", "1,234"), ("x" * 81, "5"), ("Taxi", "999999999")]:
            self.assertIn("aviso error", self.expense(concept, amount).get_data(as_text=True), (concept[:5], amount))
        self.assertEqual(self.q("SELECT COUNT(*) c FROM expenses").fetchone()["c"], 0)
        self.expense("Taxi", "5,50", "ida y vuelta")
        row = self.q("SELECT concept, amount_cents, note FROM expenses").fetchone()
        self.assertEqual((row["concept"], row["amount_cents"], row["note"]), ("Taxi", 550, "ida y vuelta"))
        self.assertEqual(self.api_caja()["expected"], 10000 - 550)

    def test_editar_y_eliminar_gasto(self):
        self.expense("Taxi", "5")
        eid = self.q("SELECT id FROM expenses").fetchone()["id"]
        self.client.post(f"/caja/gasto/{eid}/editar", data={"concept": "Taxi entrega", "amount": "8", "note": ""})
        self.assertEqual(self.api_caja()["expected"], 9200)
        bad = self.client.post(f"/caja/gasto/{eid}/editar", data={"concept": "", "amount": "8"}, follow_redirects=True)
        self.assertIn("aviso error", bad.get_data(as_text=True))
        self.assertEqual(self.q("SELECT concept FROM expenses").fetchone()["concept"], "Taxi entrega")
        self.client.post(f"/caja/gasto/{eid}/eliminar")
        self.assertEqual(self.api_caja()["expected"], 10000)
        self.assertEqual(self.client.post("/caja/gasto/9999/eliminar", follow_redirects=True).status_code, 200)   # no existe: aviso
        acciones = [r["action"] for r in self.q("SELECT action FROM audit_log WHERE entity='gasto' ORDER BY id")]
        self.assertEqual(acciones, ["crear", "modificar", "eliminar"])

    def test_efectivo_esperado_puede_ser_negativo(self):
        self.expense("Insumo", "150")
        self.assertEqual(self.api_caja()["expected"], -5000)
        html = self.client.get("/caja").get_data(as_text=True)
        self.assertIn("-Bs 50", html)
        self.assertIn('class="pizarra neg"', html)

    def test_chip_en_todas_las_pantallas_y_caja_lista_todo(self):
        a = self.v("A", 1000, tracks=0)
        oid = self.order((a, 1), pay="cash"); self.expense("Taxi", "5")
        for url in ["/", "/pedidos", f"/pedidos/{oid}", "/inventario", "/catalogo", "/recetario", "/promociones", "/jornada", "/caja", "/no-existe"]:
            html = self.client.get(url).get_data(as_text=True)
            self.assertIn('id="caja-chip"', html, url)
            self.assertIn("Bs 105", html, url)
        caja_html = self.client.get("/caja").get_data(as_text=True)
        self.assertIn(f"#{oid}", caja_html); self.assertIn("Taxi", caja_html)

    def test_caja_sin_jornada_abierta(self):
        self.close()
        r = self.client.get("/caja", follow_redirects=True)
        self.assertIn("Abre la jornada", r.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/caja").get_json(), {"open": False})
        self.assertIn("Sin jornada abierta", self.client.get("/").get_data(as_text=True))

    def test_texto_de_gasto_se_escapa(self):
        self.expense("<script>alert(1)</script>", "1")
        for url in ("/caja",):
            self.assertNotIn("<script>alert(1)</script>", self.client.get(url).get_data(as_text=True))

    def test_secuencia_mixta_coincide_con_el_recalculo_independiente(self):
        a, b = self.v("A", 700, stock=30), self.v("B", 450, tracks=0)
        o1 = self.order((a, 3), (b, 2), pay="cash"); o2 = self.order((a, 1), pay="qr"); o3 = self.order((b, 4), discount="3", pay="cash")
        self.expense("Taxi", "12,50"); self.expense("Insumo", "7")
        self.edit(o1, (a, 1), (b, 5)); self.api("POST", f"/api/pedidos/{o2}/metodo", {"method": "cash"})
        self.api("POST", f"/api/pedidos/{o3}/cancelar")
        eid = self.q("SELECT id FROM expenses WHERE concept='Insumo'").fetchone()["id"]
        self.client.post(f"/caja/gasto/{eid}/eliminar")
        self.assertEqual(self.api_caja()["expected"], self.expected_sql())
        self.assertEqual(orders.consistency_problems(self.conn), [])


class TestJornada(CajaBase):
    def test_abrir_con_fondo_editable_y_validado(self):
        self.close()
        for fund, cents in (("150", 15000), ("80,50", 8050), ("0", 0)):
            wid = self.new_day(f"2099-02-0{len(str(cents))}", fund)
            self.assertEqual(self.q("SELECT opening_fund_cents f FROM workdays WHERE id=?", wid).fetchone()["f"], cents)
            self.in_app(closing.close_workday, "", "100")
        for bad in ("abc", "-5", "1,234"):
            r = self.client.post("/jornada/abrir", data={"fund": bad}, follow_redirects=True)
            self.assertIn("aviso error", r.get_data(as_text=True), bad)
        self.assertIsNone(self.q("SELECT 1 FROM workdays WHERE status='open'").fetchone())

    def test_solo_una_abierta_y_no_repite_fecha(self):
        r = self.client.post("/jornada/abrir", data={"fund": "100"}, follow_redirects=True)
        self.assertIn("Ya hay una jornada abierta", r.get_data(as_text=True))
        self.close()
        r = self.client.post("/jornada/abrir", data={"fund": "100"}, follow_redirects=True)     # hoy ya existe (cerrada)
        self.assertIn("reabrirla", r.get_data(as_text=True))
        self.assertEqual(self.q("SELECT COUNT(*) c FROM workdays").fetchone()["c"], 1)

    def test_abrir_crea_las_tareas_de_apertura_y_cierre(self):
        t = self.q("SELECT kind, COUNT(*) c FROM workday_tasks WHERE workday_id=? GROUP BY kind", self.wd).fetchall()
        self.assertEqual({r["kind"]: r["c"] for r in t}, {"open": 9, "close": 9})
        self.assertEqual(self.q("SELECT text FROM workday_tasks WHERE kind='open' ORDER BY position").fetchone()["text"], "Iniciar jornada")

    def test_fondo_sugerido_es_la_reserva_anterior(self):
        with self.app.app_context():
            self.assertEqual(workdays.default_fund_cents(), 10000)
        self.close(reserve="80")
        with self.app.app_context():
            self.assertEqual(workdays.default_fund_cents(), 8000)
        self.assertIn('value="80"', self.client.get("/jornada").get_data(as_text=True))
        wid = self.in_app(workdays.open_workday, None, "2099-03-01")                # sin escribir fondo: usa 80
        self.assertEqual(self.q("SELECT opening_fund_cents f FROM workdays WHERE id=?", wid).fetchone()["f"], 8000)

    def test_marcar_tareas_se_guarda(self):
        tid = self.q("SELECT id FROM workday_tasks WHERE workday_id=? AND kind='open' ORDER BY position", self.wd).fetchone()["id"]
        self.assertEqual(self.api("POST", f"/api/tareas/{tid}", {"done": True}).status_code, 200)
        row = self.q("SELECT done, done_at FROM workday_tasks WHERE id=?", tid).fetchone()
        self.assertEqual(row["done"], 1); self.assertIsNotNone(row["done_at"])
        html = self.client.get("/jornada").get_data(as_text=True)
        self.assertIn("1 de 9", html)
        self.api("POST", f"/api/tareas/{tid}", {"done": False})
        row = self.q("SELECT done, done_at FROM workday_tasks WHERE id=?", tid).fetchone()
        self.assertEqual((row["done"], row["done_at"]), (0, None))
        self.assertEqual(self.api("POST", "/api/tareas/99999", {"done": True}).status_code, 404)
        self.assertEqual(self.client.post(f"/api/tareas/{tid}", data="x", content_type="text/plain").status_code, 400)

    def test_tareas_bloqueadas_con_la_jornada_cerrada(self):
        tid = self.q("SELECT id FROM workday_tasks WHERE workday_id=?", self.wd).fetchone()["id"]
        self.close()
        self.assertEqual(self.api("POST", f"/api/tareas/{tid}", {"done": True}).status_code, 409)

    def test_listas_configurables_solo_afectan_jornadas_futuras(self):
        post = lambda **d: self.client.post("/jornada/listas", data=d, follow_redirects=True)
        self.assertIn("aviso error", post(action="add", kind="open", text="  ").get_data(as_text=True))
        post(action="add", kind="open", text="Prender la máquina de café")
        first = self.q("SELECT id FROM checklist_items WHERE text='Iniciar jornada'").fetchone()["id"]
        post(action="rename", item_id=first, text="Abrir la puerta")
        limpieza = self.q("SELECT id FROM checklist_items WHERE text='Realizar limpieza'").fetchone()["id"]
        post(action="hide", item_id=limpieza)
        post(action="down", item_id=first); post(action="down", item_id=first)     # pasa la oculta y una visible
        self.assertEqual(self.q("SELECT COUNT(*) c FROM workday_tasks WHERE workday_id=? AND kind='open'", self.wd).fetchone()["c"], 9)   # la actual no cambia
        self.assertEqual(self.q("SELECT text FROM workday_tasks WHERE workday_id=? AND kind='open' ORDER BY position", self.wd).fetchone()["text"], "Iniciar jornada")
        self.close()
        nuevo = self.new_day()
        textos = [r["text"] for r in self.q("SELECT text FROM workday_tasks WHERE workday_id=? AND kind='open' ORDER BY position", nuevo)]
        self.assertEqual(len(textos), 9)                              # 9 + 1 nueva - 1 oculta
        self.assertIn("Prender la máquina de café", textos); self.assertIn("Abrir la puerta", textos)
        self.assertNotIn("Realizar limpieza", textos)
        self.assertEqual(textos.index("Abrir la puerta"), 1)           # quedó detrás de "Revisar lista de productos esperados"
        post(action="show", item_id=limpieza)
        self.assertEqual(self.q("SELECT active a FROM checklist_items WHERE id=?", limpieza).fetchone()["a"], 1)
        self.assertEqual(self.client.get("/jornada/listas").status_code, 200)

    def test_abrir_desde_inicio_y_redireccion_segura(self):
        self.close()
        self.assertIn("La jornada no está abierta", self.client.get("/").get_data(as_text=True))
        for externo in ("//evil.example", "https://evil.example", "/\\evil.example"):
            r = self.client.post("/jornada/abrir", data={"fund": "100", "next": externo})
            self.assertNotIn("evil.example", r.headers["Location"], externo)
        r = self.client.post("/jornada/abrir", data={"fund": "100", "next": "/pedidos"})
        self.assertTrue(r.headers["Location"].endswith("/pedidos"))                    # rutas propias sí


class TestCierre(CajaBase):
    def setUp(self):
        super().setUp()
        a, b = self.v("A", 1700, tracks=0), self.v("B", 800, tracks=0)
        self.order((a, 1), pay="cash"); self.order((b, 1), pay="cash"); self.order((a, 1), pay="qr")
        self.expense("Taxi", "5")                                         # esperado: 100 + 25 - 5 = 120

    def summary(self, **kw):
        with self.app.app_context():
            return closing.summary(db.get_db(), self.wd, **kw)

    def test_diferencia_y_bolsa(self):
        casos = [("118", -200, 1800, 2000), ("125", 500, 2500, 2000), ("120", 0, 2000, 2000)]
        for counted, diff, bag_counted, bag in casos:
            s = self.summary(counted=int(counted) * 100, reserve=10000)
            self.assertEqual((s["expected"], s["difference"], s["to_bag_counted"], s["to_bag"]), (12000, diff, bag_counted, bag), counted)
        s = self.summary()
        self.assertIsNone(s["difference"]); self.assertIsNone(s["to_bag_counted"])
        self.assertEqual(self.summary(reserve=15000)["to_bag"], -3000)    # no alcanza para reservar

    def test_no_se_cierra_con_pedidos_pendientes(self):
        a = self.q("SELECT id FROM product_variants LIMIT 1").fetchone()["id"]
        pend = self.order((a, 1))
        r = self.close()
        self.assertEqual(r.status_code, 400)
        self.assertIn("1 pedido(s) pendiente(s)", r.get_data(as_text=True))
        self.assertEqual(self.q("SELECT status FROM workdays WHERE id=?", self.wd).fetchone()["status"], "open")
        self.assertIn("disabled", self.client.get("/jornada/cerrar").get_data(as_text=True))
        self.api("POST", f"/api/pedidos/{pend}/cancelar")
        self.assertEqual(self.close().status_code, 302)

    def test_cierre_guarda_todo_y_no_borra_nada(self):
        antes = {t: self.q(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] for t in ("orders", "order_items", "payments", "expenses", "inventory_movements", "workday_tasks")}
        r = self.close(counted="118", reserve="100", notes="todo bien")
        self.assertEqual(r.status_code, 302); self.assertIn(f"/jornada/{self.wd}/informe", r.headers["Location"])
        w = self.q("SELECT * FROM workdays WHERE id=?", self.wd).fetchone()
        self.assertEqual((w["status"], w["counted_cash_cents"], w["reserve_cents"], w["notes"]), ("closed", 11800, 10000, "todo bien"))
        self.assertIsNotNone(w["closed_at"])
        despues = {t: self.q(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] for t in antes}
        self.assertEqual(antes, despues)
        otra = self.client.post("/jornada/cerrar", data={}, follow_redirects=True)     # ya no hay jornada abierta
        self.assertIn("No hay una jornada abierta", otra.get_data(as_text=True))
        self.assertEqual(self.q("SELECT counted_cash_cents c FROM workdays WHERE id=?", self.wd).fetchone()["c"], 11800)   # no se pisó

    def test_conteo_opcional_y_validacion(self):
        for counted, reserve in (("abc", "100"), ("-5", "100"), ("100", "abc"), ("1,234", "100"), ("100", "-1")):
            r = self.close(counted=counted, reserve=reserve)
            self.assertEqual(r.status_code, 400, (counted, reserve))
            self.assertIn("aviso error", r.get_data(as_text=True))
        self.assertEqual(self.q("SELECT status FROM workdays WHERE id=?", self.wd).fetchone()["status"], "open")
        self.assertEqual(self.close(counted="").status_code, 302)
        self.assertIsNone(self.q("SELECT counted_cash_cents c FROM workdays WHERE id=?", self.wd).fetchone()["c"])

    def test_despues_de_cerrar_nada_se_puede_modificar(self):
        a = self.q("SELECT id FROM product_variants LIMIT 1").fetchone()["id"]
        oid = self.q("SELECT id FROM orders LIMIT 1").fetchone()["id"]
        eid = self.q("SELECT id FROM expenses").fetchone()["id"]
        self.close()
        self.assertEqual(self.api("POST", f"/api/pedidos/{oid}/cancelar").status_code, 409)
        self.assertEqual(self.edit(oid, (a, 9)).status_code, 409)
        self.assertEqual(self.api("POST", "/api/pedidos", {"lines": [{"variant_id": a, "quantity": 1}]}).status_code, 409)
        self.assertIn("aviso error", self.expense("Otro", "1").get_data(as_text=True))
        self.assertIn("aviso error", self.client.post(f"/caja/gasto/{eid}/eliminar", follow_redirects=True).get_data(as_text=True))
        self.assertIn("Abre la jornada", self.receipt([(a, None, 1)]).get_data(as_text=True))
        self.assertEqual(self.q("SELECT COUNT(*) c FROM expenses").fetchone()["c"], 1)
        self.assertEqual(self.client.get(f"/jornada/{self.wd}/informe").status_code, 200)

    def test_reabrir_solo_la_mas_reciente(self):
        self.close(counted="120")
        self.assertEqual(self.client.post(f"/jornada/{self.wd}/reabrir").status_code, 302)
        self.assertEqual(self.q("SELECT status FROM workdays WHERE id=?", self.wd).fetchone()["status"], "open")
        self.assertIsNone(self.q("SELECT closed_at c FROM workdays WHERE id=?", self.wd).fetchone()["c"])
        self.expense("Extra", "20")                                       # vuelve a ser editable
        self.assertEqual(self.api_caja()["expected"], 10000 + 2500 - 500 - 2000)
        self.assertIn("ya está abierta", self.client.post(f"/jornada/{self.wd}/reabrir", follow_redirects=True).get_data(as_text=True))
        self.close()
        nuevo = self.new_day()                                            # hay otra abierta
        self.in_app(closing.close_workday, "", "100")
        r = self.client.post(f"/jornada/{self.wd}/reabrir", follow_redirects=True)   # la vieja ya no es la más reciente
        self.assertIn("más reciente", r.get_data(as_text=True))
        self.assertEqual(self.q("SELECT status FROM workdays WHERE id=?", self.wd).fetchone()["status"], "closed")
        self.assertEqual(self.client.post(f"/jornada/{nuevo}/reabrir").status_code, 302)
        self.assertEqual(self.in_app(lambda: workdays.get_open()["id"]), nuevo)
        self.assertIn("Hay otra jornada abierta", self.client.post(f"/jornada/{self.wd}/reabrir", follow_redirects=True).get_data(as_text=True))

    def test_origen_externo_rechazado(self):
        hdr = {"Origin": "http://evil.example"}
        self.assertEqual(self.client.post("/jornada/cerrar", data={}, headers=hdr).status_code, 403)
        self.assertEqual(self.client.post("/caja/gasto", data={"concept": "x", "amount": "1"}, headers=hdr).status_code, 403)
        self.assertEqual(self.q("SELECT status FROM workdays WHERE id=?", self.wd).fetchone()["status"], "open")


class TestInforme(CajaBase):
    def test_informe_completo(self):
        pan = self.v("Pan", 400, stock=10, category=4); te = self.v("Té", 800, tracks=0)
        self.receipt([(pan, None, 5)])
        self.order((pan, 3), (te, 1), discount="2", pay="cash"); self.order((te, 2), pay="qr")
        self.expense("<b>Taxi</b>", "5"); self.client.post("/inventario/ajustar", data={"variant_id": pan, "delta": "-1", "note": "se cayó"})
        self.close(counted="119", reserve="100", notes="día tranquilo")
        html = self.client.get(f"/jornada/{self.wd}/informe").get_data(as_text=True)
        for texto in ("Informe de cierre", "Bs 100", "Bs 119", "+6,00", "Bs 113", "Pan", "se cayó", "día tranquilo", "orientativo", "☐", "&lt;b&gt;Taxi&lt;/b&gt;"):
            self.assertIn(texto, html, texto)
        self.assertNotIn("<b>Taxi</b>", html)
        sold = {r["name"]: (r["units"], r["amount"]) for r in self.in_app(lambda: closing.report(self.wd))["sold"]}
        self.assertEqual(sold, {"Pan": (3, 1200), "Té": (3, 2400)})
        rep = self.in_app(lambda: closing.report(self.wd))
        # pedido 1: 3 panes (12) + 1 té (8) - descuento 2 = 18 en efectivo; pedido 2: 2 tés = 16 por QR
        self.assertEqual((rep["discounts"], rep["s"]["cash"], rep["s"]["qr"], rep["s"]["expected"]), (200, 1800, 1600, 10000 + 1800 - 500))
        self.assertEqual(rep["s"]["difference"], 11900 - 11300)
        inv = {i["name"]: i for i in rep["inventory"]}["Pan"]
        self.assertEqual((inv["apertura"], inv["entradas"], inv["vendidas"], inv["ajustes"], inv["final"]), (10, 5, 3, -1, 11))
        self.assertEqual(inv["apertura"] + inv["entradas"] - inv["vendidas"] + inv["ajustes"], inv["final"])

    def test_inventario_final_es_el_de_ese_dia_no_el_actual(self):
        pan = self.v("Pan", 400, stock=10)
        self.order((pan, 4), pay="cash")
        self.close()
        self.new_day()
        self.order((pan, 2), pay="cash")                                   # el día siguiente vende 2 más
        ayer = {i["name"]: i for i in self.in_app(lambda: closing.report(self.wd))["inventory"]}["Pan"]
        self.assertEqual((ayer["final"], ayer["vendidas"]), (6, 4))        # no 4: el informe de ayer no cambia
        self.assertEqual(self.stock(pan), 4)

    def test_combo_aparece_con_su_nombre_y_un_solo_ingreso(self):
        cafe = self.v("Café", 1000, tracks=0, category=1); galleta = self.v("Galleta", 500, stock=5, category=7)
        r = self.api("POST", "/api/pedidos", {"lines": [{"promotion_id": 1, "quantity": 1, "selections": {"1": [cafe], "2": [galleta]}}]})
        self.api("POST", f"/api/pedidos/{r.get_json()['id']}/pagar", {"method": "cash"})
        rep = self.in_app(lambda: closing.report(self.wd))
        self.assertEqual([(x["name"], x["units"], x["amount"]) for x in rep["sold"]], [("Combo Bs 17", 1, 1700)])
        self.assertEqual(rep["s"]["sales"], 1700)

    def test_informe_de_jornada_abierta_y_tareas_marcadas(self):
        tid = self.q("SELECT id FROM workday_tasks WHERE workday_id=? AND kind='open' ORDER BY position", self.wd).fetchone()["id"]
        self.api("POST", f"/api/tareas/{tid}", {"done": True})
        html = self.client.get(f"/jornada/{self.wd}/informe").get_data(as_text=True)
        self.assertIn("aún abierta", html); self.assertIn("☑ Iniciar jornada", html)
        self.assertEqual(self.client.get("/jornada/9999/informe").status_code, 404)

    def test_caja_que_cuadra_se_lee_cuadra(self):
        self.close(counted="100")                                          # sin ventas ni gastos: esperado 100
        self.assertIn("<td>Cuadra</td>", self.client.get(f"/jornada/{self.wd}/informe").get_data(as_text=True))

    def test_falta_reponer_caja_chica(self):
        self.close(counted="40", reserve="100")
        html = self.client.get(f"/jornada/{self.wd}/informe").get_data(as_text=True)
        self.assertIn("Falta reponer Bs 60 en caja chica", html)

    def test_boton_reabrir_solo_en_la_ultima(self):
        self.close()
        self.assertIn("Reabrir jornada", self.client.get(f"/jornada/{self.wd}/informe").get_data(as_text=True))
        self.new_day(); self.in_app(closing.close_workday, "", "100")
        self.assertNotIn("Reabrir jornada", self.client.get(f"/jornada/{self.wd}/informe").get_data(as_text=True))


class TestInicio(CajaBase):
    def test_panel_con_datos_reales(self):
        a, b = self.v("A", 1000, tracks=0), self.v("B", 500, stock=0)
        self.order((a, 2), pay="cash"); self.order((a, 1), pay="qr"); self.order((a, 1))
        self.expense("Taxi", "5")
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Bs 115", html)                                        # 100 + 20 - 5
        for texto in ("Pedidos", "3</strong><small>2 pagados", "1 sin pagar", "Ventas registradas", "Bs 30", "Por QR", "Bs 10", "Gastos", "Bs 5", "Lo más vendido hoy", "tareas pendientes", "Agotados"):
            self.assertIn(texto, html, texto)
        self.assertRegex(html, r"18</strong><b>tareas pendientes")

    def test_tareas_pendientes_bajan_al_marcar(self):
        for tid in [r["id"] for r in self.q("SELECT id FROM workday_tasks WHERE workday_id=? ORDER BY id LIMIT 3", self.wd)]:
            self.api("POST", f"/api/tareas/{tid}", {"done": True})
        self.assertRegex(self.client.get("/").get_data(as_text=True), r"15</strong><b>tareas pendientes")

    def test_con_jornada_cerrada_ofrece_abrir(self):
        self.close(reserve="75")
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("La jornada no está abierta", html); self.assertIn('value="75"', html)


class TestReposicionConJornadasCerradas(CajaBase):
    def test_la_reposicion_ya_tiene_datos_tras_cerrar(self):
        a = self.v("A", 500, stock=20)
        self.order((a, 6), pay="cash")
        self.assertEqual(self.in_app(lambda: inventory.suggestions())["days"], 0)     # abierta: no cuenta
        self.close()
        d = self.in_app(lambda: inventory.suggestions())
        self.assertEqual(d["days"], 1)
        fila = {r["name"]: r for r in d["rows"]}["A"]
        self.assertEqual((fila["daily"], fila["avg"], fila["target"], fila["suggestion"]), ([6], 6.0, 6, 0))   # quedan 14: no hace falta


if __name__ == "__main__":
    unittest.main()
