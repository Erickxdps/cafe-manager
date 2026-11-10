import unittest

from cafe import db
from cafe.services import inventory, orders
from tests.test_etapa3 import Base


class InvBase(Base):
    def setUp(self):
        super().setUp()
        self.wd = self.q("SELECT id FROM workdays WHERE status='open'").fetchone()["id"]

    def form(self, url, **cols):
        return self.client.post(url, data=cols, follow_redirects=True)

    def receipt(self, items):
        """items: [(variant_id, esperado|None, contado|None)]"""
        return self.form("/inventario/recepcion", v_id=[str(i[0]) for i in items],
                         expected=["" if i[1] is None else str(i[1]) for i in items],
                         counted=["" if i[2] is None else str(i[2]) for i in items])

    def count(self, items, reason=""):
        """items: [(variant_id, sistema, contado)]"""
        return self.form("/inventario/conteo", v_id=[str(i[0]) for i in items], system=[str(i[1]) for i in items],
                         counted=["" if i[2] is None else str(i[2]) for i in items], reason=reason)

    def kinds(self, vid):
        return [(m["kind"], m["quantity"]) for m in self.q("SELECT kind, quantity FROM inventory_movements WHERE variant_id=? ORDER BY id", vid)]

    def row(self, vid):
        with self.app.app_context():
            return next(r for r in inventory.overview() if r["variant_id"] == vid)

    @staticmethod
    def tbody(html):
        return html.split("<tbody>")[1].split("</tbody>")[0]

    def set_stock(self, vid, target):
        """Deja las existencias en 'target' (las jornadas de prueba descuentan ventas sin stock previo)."""
        delta = target - self.stock(vid)
        if delta:
            self.q("INSERT INTO inventory_movements(variant_id,kind,quantity) VALUES (?,?,?)",
                   vid, "adjust_pos" if delta > 0 else "adjust_neg", delta)

    def closed_day(self, date, sales):
        """Jornada cerrada con ventas: sales = {variant_id: unidades}."""
        wid = self.q("INSERT INTO workdays(work_date,status) VALUES (?, 'closed')", date).lastrowid
        oid = self.q("INSERT INTO orders(workday_id,status,subtotal_cents,total_cents) VALUES (?, 'paid', 100, 100)", wid).lastrowid
        self.q("INSERT INTO payments(order_id,workday_id,method,amount_cents) VALUES (?,?,'cash',100)", oid, wid)
        for vid, n in sales.items():
            if n:
                self.q("INSERT INTO inventory_movements(variant_id,workday_id,kind,quantity,order_id) VALUES (?,?,'sale',?,?)", vid, wid, -n, oid)
        return wid


class TestResumen(InvBase):
    def test_columnas_cuadran_con_los_movimientos(self):
        a = self.v("A", 500, stock=0)
        d0 = self.q("INSERT INTO workdays(work_date,status) VALUES ('2026-01-01','closed')").lastrowid
        o0 = self.q("INSERT INTO orders(workday_id,status,subtotal_cents,total_cents) VALUES (?, 'paid', 100, 100)", d0).lastrowid
        ins = "INSERT INTO inventory_movements(variant_id,workday_id,kind,quantity,order_id) VALUES (?,?,?,?,?)"
        self.q(ins, a, None, "initial", 10, None)
        self.q(ins, a, d0, "sale", -2, o0)                      # ayer: 8 al cerrar
        self.receipt([(a, 5, 5)])                                 # hoy +5
        oid = self.order((a, 3), pay="cash")                      # hoy -3
        self.client.post("/inventario/ajustar", data={"variant_id": a, "delta": "-1", "note": "se cayó"})
        r = self.row(a)
        self.assertEqual((r["apertura"], r["entradas"], r["vendidas"], r["ajustes"], r["stock"]), (8, 5, 3, -1, 9))
        self.assertEqual(r["apertura"] + r["entradas"] - r["vendidas"] + r["ajustes"], r["stock"])
        self.assertEqual(self.stock(a), r["stock"])
        independiente = self.q("SELECT SUM(quantity) s FROM inventory_movements WHERE variant_id=? AND (workday_id IS NULL OR workday_id!=?)", a, self.wd).fetchone()["s"]
        self.assertEqual(independiente, r["apertura"])           # la apertura es lo anterior a la jornada

    def test_cancelar_restaura_vendidas(self):
        a = self.v("A", 500, stock=5)
        oid = self.order((a, 2), pay="cash")
        self.assertEqual(self.row(a)["vendidas"], 2)
        self.api("POST", f"/api/pedidos/{oid}/cancelar")
        r = self.row(a)
        self.assertEqual((r["vendidas"], r["stock"], r["apertura"]), (0, 5, 5))

    def test_sin_jornada_abierta_todo_es_apertura(self):
        a = self.v("A", 500, stock=7)
        self.q("UPDATE workdays SET status='closed'")
        r = self.row(a)
        self.assertEqual((r["apertura"], r["entradas"], r["vendidas"], r["ajustes"], r["stock"]), (7, 0, 0, 0, 7))
        self.assertEqual(self.client.get("/inventario").status_code, 200)

    def test_filtros_y_busqueda(self):
        ok = self.v("Pan francés", 500, stock=9)
        bajo = self.v("Éclair", 800, stock=2, category=5)
        self.q("UPDATE product_variants SET min_stock=3 WHERE id=?", bajo)
        out = self.v("Galleta", 500, stock=0, category=7)
        sin_control = self.v("Té", 800, tracks=0)
        html = lambda qs: self.tbody(self.client.get("/inventario" + qs).get_data(as_text=True))
        todos = html("")
        self.assertNotIn("Té", todos)                                           # sin control: no aparece
        for nombre in ("Pan francés", "Éclair", "Galleta"):
            self.assertIn(nombre, todos)
        self.assertEqual([n in html("?filtro=bajo") for n in ("Pan francés", "Éclair", "Galleta")], [False, True, False])
        self.assertEqual([n in html("?filtro=agotado") for n in ("Pan francés", "Éclair", "Galleta")], [False, False, True])
        self.assertEqual([n in html("?categoria=5") for n in ("Pan francés", "Éclair")], [False, True])
        self.assertIn("Pan francés", html("?q=PAN%20FRANCES"))                 # sin mayúsculas ni acentos
        self.assertNotIn("Éclair", html("?q=pan"))


class TestRecepcion(InvBase):
    def test_registra_entradas_y_diferencias(self):
        a, b = self.v("A", 500, stock=0), self.v("B", 300, stock=2)
        r = self.receipt([(a, 12, 10), (b, 6, 6)])
        html = r.get_data(as_text=True)
        self.assertIn("Recepción registrada: 16 unidades en 2 producto(s)", html)
        self.assertIn("esperado 12, contado 10", html)
        self.assertEqual((self.stock(a), self.stock(b)), (10, 8))
        note = self.q("SELECT note FROM inventory_movements WHERE variant_id=?", a).fetchone()["note"]
        self.assertEqual(note, "Recepción (esperado 12, contado 10)")
        self.assertEqual(self.q("SELECT workday_id w FROM inventory_movements WHERE variant_id=?", a).fetchone()["w"], self.wd)

    def test_producto_agotado_se_puede_vender_tras_recibir(self):
        a = self.v("A", 500, stock=0)
        self.assertEqual(self.api("POST", "/api/pedidos", {"lines": [{"variant_id": a, "quantity": 1}]}).status_code, 409)
        self.receipt([(a, None, 3)])
        self.order((a, 3), pay="cash")
        self.assertEqual(self.stock(a), 0)
        self.assertEqual(orders.consistency_problems(self.conn), [])

    def test_exige_jornada_abierta(self):
        a = self.v("A", 500, stock=0)
        self.q("UPDATE workdays SET status='closed'")
        self.assertIn("Abre la jornada", self.receipt([(a, None, 5)]).get_data(as_text=True))
        self.assertEqual(self.kinds(a), [])

    def test_un_dato_malo_no_guarda_nada(self):
        a, b = self.v("A", 500, stock=0), self.v("B", 500, stock=0)
        for contado in ("abc", "-3", "1.5", "99999999"):
            r = self.form("/inventario/recepcion", v_id=[str(a), str(b)], expected=["", ""], counted=["5", contado])
            self.assertIn("aviso error", r.get_data(as_text=True), contado)
        self.assertEqual((self.kinds(a), self.kinds(b)), ([], []))

    def test_vacio_y_ceros(self):
        a = self.v("A", 500, stock=0)
        self.assertIn("ninguna cantidad", self.receipt([(a, None, None)]).get_data(as_text=True))
        self.assertIn("ninguna cantidad", self.receipt([(a, 5, 0)]).get_data(as_text=True))      # llegó 0: sin movimiento
        self.assertEqual(self.kinds(a), [])

    def test_producto_sin_control_rechazado(self):
        te = self.v("Té", 800, tracks=0)
        self.assertIn("aviso error", self.receipt([(te, None, 5)]).get_data(as_text=True))
        self.assertEqual(self.kinds(te), [])

    def test_origen_externo_rechazado(self):
        a = self.v("A", 500, stock=0)
        r = self.client.post("/inventario/recepcion", data={"v_id": [str(a)], "counted": ["5"]}, headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)


class TestConteo(InvBase):
    def test_solo_registra_diferencias_con_signo_correcto(self):
        a, b, c, d = (self.v(n, 500, stock=10) for n in "ABCD")
        self.count([(a, 10, 8), (b, 10, 12), (c, 10, 10), (d, 10, None)], reason="conteo de cierre")
        self.assertEqual(self.kinds(a), [("initial", 10), ("adjust_neg", -2)])
        self.assertEqual(self.kinds(b), [("initial", 10), ("adjust_pos", 2)])
        self.assertEqual(self.kinds(c), [("initial", 10)])                      # coincide: nada
        self.assertEqual(self.kinds(d), [("initial", 10)])                      # en blanco: no se toca
        self.assertEqual((self.stock(a), self.stock(b)), (8, 12))
        note = self.q("SELECT note FROM inventory_movements WHERE variant_id=? AND kind='adjust_neg'", a).fetchone()["note"]
        self.assertEqual(note, "Conteo físico: sistema 10, contado 8. conteo de cierre")

    def test_si_hubo_ventas_mientras_se_contaba_rechaza_todo(self):
        a, b = self.v("A", 500, stock=10), self.v("B", 500, stock=5)
        self.order((a, 1), pay="cash")                                          # el sistema pasa de 10 a 9
        r = self.count([(b, 5, 4), (a, 10, 8)])                                 # el formulario se mostró con 10
        self.assertIn("cambió de 10 a 9", r.get_data(as_text=True))
        self.assertEqual(self.kinds(b), [("initial", 5)])                       # ni siquiera B se ajustó
        self.assertEqual(self.stock(a), 9)

    def test_valores_invalidos(self):
        a = self.v("A", 500, stock=3)
        for malo in ("-1", "abc", "2.5"):
            r = self.form("/inventario/conteo", v_id=[str(a)], system=["3"], counted=[malo])
            self.assertIn("aviso error", r.get_data(as_text=True), malo)
        self.assertEqual(self.kinds(a), [("initial", 3)])

    def test_contar_cero_agota_el_producto(self):
        a = self.v("A", 500, stock=3)
        self.count([(a, 3, 0)])
        self.assertEqual(self.stock(a), 0)
        self.assertEqual(self.row(a)["availability"], "agotado")

    def test_exige_jornada_abierta(self):
        a = self.v("A", 500, stock=3)
        self.q("UPDATE workdays SET status='closed'")
        self.assertIn("Abre la jornada", self.count([(a, 3, 1)]).get_data(as_text=True))
        self.assertEqual(self.stock(a), 3)


class TestAjusteEInicial(InvBase):
    def adjust(self, vid, delta, note="motivo"):
        return self.client.post("/inventario/ajustar", data={"variant_id": vid, "delta": delta, "note": note}, follow_redirects=True)

    def test_ajustes_manuales(self):
        a = self.v("A", 500, stock=5)
        self.adjust(a, "3"); self.adjust(a, "-2", "se rompió")
        self.assertEqual(self.kinds(a), [("initial", 5), ("adjust_pos", 3), ("adjust_neg", -2)])
        self.assertEqual(self.stock(a), 6)
        self.assertIn("Ajuste manual: se rompió", self.client.get(f"/inventario/movimientos?variante={a}").get_data(as_text=True))

    def test_validaciones_de_ajuste(self):
        a = self.v("A", 500, stock=2)
        casos = [("0", "x"), ("", "x"), ("abc", "x"), ("1", ""), ("-3", "x"), ("1.5", "x")]
        for delta, note in casos:
            self.assertIn("aviso error", self.adjust(a, delta, note).get_data(as_text=True), (delta, note))
        self.assertEqual(self.stock(a), 2)                                      # nunca queda negativo

    def test_inicial_solo_para_productos_sin_movimientos(self):
        a, b = self.v("A", 500, stock=0), self.v("B", 500, stock=4)
        self.q("UPDATE workdays SET status='closed'")                           # se permite sin jornada abierta
        self.form("/inventario/inicial", v_id=[str(a)], qty=["6"])
        self.assertEqual(self.kinds(a), [("initial", 6)])
        self.assertIsNone(self.q("SELECT workday_id w FROM inventory_movements WHERE variant_id=?", a).fetchone()["w"])
        r = self.form("/inventario/inicial", v_id=[str(a), str(b)], qty=["9", "9"])   # a ya tiene movimientos
        self.assertIn("ya tiene movimientos", r.get_data(as_text=True))
        self.assertEqual((self.stock(a), self.stock(b)), (6, 4))                # lote atómico
        self.assertIn("ninguna cantidad", self.form("/inventario/inicial", v_id=[str(a)], qty=[""]).get_data(as_text=True))

    def test_resumen_ofrece_cargar_iniciales_solo_a_quien_no_tiene(self):
        a = self.v("Sin inicial", 500, stock=0)
        b = self.v("Con inicial", 500, stock=3)
        html = self.client.get("/inventario").get_data(as_text=True)
        self.assertIn("Productos sin existencias iniciales", html)
        self.assertIn(f'value="{a}"', html.split("sin existencias iniciales")[1].split("</section>")[0])
        self.assertNotIn(f'value="{b}"', html.split("sin existencias iniciales")[1].split("</section>")[0])


class TestMovimientos(InvBase):
    def test_ventas_y_reversiones_aparecen_con_enlace_al_pedido(self):
        a = self.v("A", 500, stock=5)
        oid = self.order((a, 2), pay="cash")
        html = self.client.get(f"/inventario/movimientos?variante={a}").get_data(as_text=True)
        self.assertIn("Venta", html); self.assertIn(f"Pedido #{oid}", html)
        self.api("POST", f"/api/pedidos/{oid}/cancelar")
        html = self.client.get(f"/inventario/movimientos?variante={a}").get_data(as_text=True)
        self.assertIn("Reversión de venta", html)

    def test_filtros(self):
        a, b = self.v("A", 500, stock=5), self.v("B", 500, stock=5)
        self.receipt([(a, None, 2)])
        por_variante = self.tbody(self.client.get(f"/inventario/movimientos?variante={b}").get_data(as_text=True))
        self.assertNotIn("Recepción", por_variante)
        por_tipo = self.tbody(self.client.get("/inventario/movimientos?tipo=receipt").get_data(as_text=True))
        self.assertIn("Recepción", por_tipo); self.assertNotIn("Inventario inicial", por_tipo)

    def test_texto_del_motivo_se_escapa(self):
        a = self.v("A", 500, stock=5)
        self.client.post("/inventario/ajustar", data={"variant_id": a, "delta": "1", "note": "<script>alert(1)</script>"})
        html = self.client.get(f"/inventario/movimientos?variante={a}").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", html)


class TestReposicion(InvBase):
    def sug(self, days=6):
        with self.app.app_context():
            data = inventory.suggestions(days)
        return data["days"], {r["name"]: r for r in data["rows"]}

    def test_sin_historial_no_inventa(self):
        self.v("A", 500, stock=1)
        self.order(((self.q("SELECT id FROM product_variants").fetchone()["id"]), 1), pay="cash")   # el día abierto no cuenta
        days, rows = self.sug()
        self.assertEqual((days, rows), (0, {}))
        self.assertIn("no hay datos reales", self.client.get("/inventario/reposicion").get_data(as_text=True))

    def test_formula(self):
        a, b, c, d = (self.v(n, 500, stock=s) for n, s in (("A", 1), ("B", 1), ("C", 10), ("D", 1)))
        self.q("UPDATE product_variants SET min_stock=2 WHERE id=?", a)         # promedio 5 > mínimo 2
        self.q("UPDATE product_variants SET min_stock=3 WHERE id=?", b)         # nunca se vendió: manda el mínimo
        self.closed_day("2026-02-01", {a: 4, c: 1, d: 1})
        self.closed_day("2026-02-02", {a: 6, c: 1, d: 2})
        self.closed_day("2026-02-03", {a: 5, c: 1})
        for vid, n in ((a, 1), (b, 1), (c, 10), (d, 1)):
            self.set_stock(vid, n)
        days, rows = self.sug()
        self.assertEqual(days, 3)
        self.assertEqual((rows["A"]["avg"], rows["A"]["target"], rows["A"]["suggestion"]), (5.0, 5, 4))
        self.assertEqual((rows["B"]["target"], rows["B"]["suggestion"]), (3, 2))
        self.assertEqual(rows["C"]["suggestion"], 0)                           # le sobra
        self.assertEqual((rows["D"]["avg"], rows["D"]["target"], rows["D"]["suggestion"]), (1.0, 1, 0))   # (1+2+0)/3 = 1 -> 1, hay 1
        self.assertEqual(self.client.get("/inventario/reposicion").status_code, 200)

    def test_redondea_hacia_arriba_y_usa_solo_las_ultimas_jornadas(self):
        a = self.v("A", 500, stock=0)
        self.closed_day("2026-01-01", {a: 100})                                 # vieja: queda fuera con n=2
        self.closed_day("2026-02-01", {a: 1})
        self.closed_day("2026-02-02", {a: 2})
        self.set_stock(a, 0)
        days, rows = self.sug(2)
        self.assertEqual((days, rows["A"]["avg"], rows["A"]["target"], rows["A"]["suggestion"]), (2, 1.5, 2, 2))

    def test_jornada_cerrada_sin_ventas_no_cuenta(self):
        a = self.v("A", 500, stock=0)
        self.q("INSERT INTO workdays(work_date,status) VALUES ('2026-03-01','closed')")
        days, _ = self.sug()
        self.assertEqual(days, 0)


class TestAlertasEIntegracion(InvBase):
    def test_inicio_muestra_agotados_y_bajos(self):
        bajo = self.v("Poco", 500, stock=3)
        self.q("UPDATE product_variants SET min_stock=3 WHERE id=?", bajo)      # igual al mínimo ya es "bajo"
        self.v("Nada", 500, stock=0)
        self.v("Bien", 500, stock=20)
        inactivo = self.v("Viejo", 500, stock=0)
        self.q("UPDATE products SET active=0 WHERE name='Viejo'")
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Agotados", html); self.assertIn("Nada", html)
        self.assertIn("Existencias bajas", html); self.assertIn("Poco", html)
        self.assertNotIn("Bien", html.split("alertas")[1].split("</section>")[0])
        self.assertNotIn("Viejo", html)

    def test_inicio_sin_alertas_no_las_muestra(self):
        self.v("Bien", 500, stock=20)
        self.assertNotIn('class="alertas"', self.client.get("/").get_data(as_text=True))

    def test_negativos_se_detectan(self):
        a = self.v("A", 500, stock=1)
        self.assertEqual(inventory.consistency_problems(self.conn), [])
        self.q("INSERT INTO inventory_movements(variant_id,kind,quantity) VALUES (?,'adjust_neg',-5)", a)
        self.assertTrue(inventory.consistency_problems(self.conn))

    def test_ciclo_completo_mantiene_todo_consistente(self):
        a, b = self.v("A", 500, stock=0), self.v("B", 300, stock=0)
        self.receipt([(a, 10, 10), (b, 8, 7)])
        o1 = self.order((a, 4), (b, 2), pay="cash")
        o2 = self.order((a, 3), pay="qr")
        self.client.put(f"/api/pedidos/{o1}", json={"lines": [
            {"item_id": r["id"], "variant_id": r["variant_id"], "quantity": 1} for r in self.q("SELECT id, variant_id FROM order_items WHERE order_id=?", o1)]})
        self.api("POST", f"/api/pedidos/{o2}/cancelar")
        self.count([(a, self.stock(a), 8), (b, self.stock(b), 6)], reason="cierre")
        self.assertEqual(orders.consistency_problems(self.conn), [])
        self.assertEqual(inventory.consistency_problems(self.conn), [])
        for vid in (a, b):
            r = self.row(vid)
            self.assertEqual(r["apertura"] + r["entradas"] - r["vendidas"] + r["ajustes"], r["stock"])
            self.assertEqual(r["stock"], self.stock(vid))


class TestControlYVisibilidad(InvBase):
    def test_recepcion_es_facil_de_encontrar(self):
        self.assertIn('href="/inventario/recepcion"', self.client.get("/").get_data(as_text=True))
        self.assertIn("Recibir productos", self.client.get("/inventario").get_data(as_text=True))

    def test_producto_sin_control_se_activa_y_luego_se_recibe_y_vende(self):
        pan = self.v("Pan", 400, tracks=0, category=4)
        self.assertNotIn("Pan", self.tbody(self.client.get("/inventario/recepcion").get_data(as_text=True)))
        html = self.client.get("/inventario/recepcion").get_data(as_text=True)
        self.assertIn("Productos sin control de existencias (1)", html)
        r = self.form("/inventario/control", v_id=[str(pan)])
        self.assertIn("Control de existencias activado", r.get_data(as_text=True))
        self.assertIn("Pan", self.tbody(self.client.get("/inventario/recepcion").get_data(as_text=True)))
        self.assertEqual(self.stock(pan), 0)                                   # empieza en 0: agotado
        self.assertEqual(self.api("POST", "/api/pedidos", {"lines": [{"variant_id": pan, "quantity": 1}]}).status_code, 409)
        self.receipt([(pan, 6, 6)])
        self.order((pan, 2), pay="cash")
        self.assertEqual(self.stock(pan), 4)
        self.assertEqual(orders.consistency_problems(self.conn), [])

    def test_sin_productos_controlados_explica_que_hacer(self):
        self.v("Pan", 400, tracks=0)
        html = self.client.get("/inventario/recepcion").get_data(as_text=True)
        self.assertIn("Todavía no hay productos con control de existencias", html)
        self.assertIn("<details", html); self.assertIn("open", html.split("<details")[1].split(">")[0])   # panel abierto

    def test_activacion_invalida_no_cambia_nada(self):
        libre, ya, off = self.v("Libre", 400, tracks=0), self.v("Ya", 400, stock=1), self.v("Off", 400, tracks=0)
        self.q("UPDATE products SET active=0 WHERE name='Off'")
        for ids in ([], [str(ya)], [str(off)], [str(libre), str(ya)], ["x"], [str(libre), "9999"]):
            r = self.form("/inventario/control", v_id=ids)
            self.assertIn("aviso error", r.get_data(as_text=True), ids)
        self.assertEqual(self.q("SELECT tracks_stock t FROM product_variants WHERE id=?", libre).fetchone()["t"], 0)

    def test_activacion_exige_origen_local(self):
        pan = self.v("Pan", 400, tracks=0)
        r = self.client.post("/inventario/control", data={"v_id": [str(pan)]}, headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)


class TestReposicionPorDia(InvBase):
    SAB = ("2026-10-24", "2026-10-31")
    DOM = ("2026-10-25", "2026-11-01")

    def sug(self, **kw):
        with self.app.app_context():
            return inventory.suggestions(**kw)

    def historial(self):
        a = self.v("A", 500, stock=0)
        for d, n in zip(self.SAB, (8, 10)):
            self.closed_day(d, {a: n})
        for d, n in zip(self.DOM, (2, 4)):
            self.closed_day(d, {a: n})
        self.set_stock(a, 0)
        return a

    def test_sabado_y_domingo_se_calculan_por_separado(self):
        self.historial()
        fila = lambda **kw: {r["name"]: r for r in self.sug(**kw)["rows"]}["A"]
        self.assertEqual((fila(weekday=5)["avg"], fila(weekday=5)["suggestion"]), (9.0, 9))      # (8+10)/2
        self.assertEqual((fila(weekday=6)["avg"], fila(weekday=6)["suggestion"]), (3.0, 3))      # (2+4)/2
        self.assertEqual(fila()["avg"], 6.0)                                                      # mezclados: 24/4
        with self.app.app_context():
            self.assertEqual(inventory.sales_weekdays(), [5, 6])

    def test_columnas_por_jornada(self):
        self.historial()
        d = self.sug(weekday=5)
        self.assertEqual(d["dates"], ["sáb 31/10", "sáb 24/10"])                                  # la más reciente primero
        self.assertEqual({r["name"]: r["daily"] for r in d["rows"]}["A"], [10, 8])

    def test_limite_de_jornadas_dentro_del_mismo_dia(self):
        a = self.v("A", 500, stock=0)
        from datetime import date, timedelta
        base = date(2026, 10, 31)
        for i in range(8):
            self.closed_day((base - timedelta(days=7 * i)).isoformat(), {a: i + 1})
        d = self.sug(n_days=6, weekday=5)
        self.assertEqual(d["days"], 6)
        self.assertEqual({r["name"]: r["daily"] for r in d["rows"]}["A"], [1, 2, 3, 4, 5, 6])    # las 6 más recientes

    def test_pantalla_filtra_por_dia(self):
        self.historial()
        sab = self.client.get("/inventario/reposicion?dia=5").get_data(as_text=True)
        self.assertIn("sáb 31/10", sab); self.assertNotIn("dom 01/11", sab)
        dom = self.client.get("/inventario/reposicion?dia=6").get_data(as_text=True)
        self.assertIn("dom 01/11", dom); self.assertNotIn("sáb 31/10", dom)
        todos = self.client.get("/inventario/reposicion?dia=todos").get_data(as_text=True)
        self.assertIn("sáb 31/10", todos); self.assertIn("dom 01/11", todos)
        self.assertEqual(self.client.get("/inventario/reposicion?dia=99").status_code, 200)

    def test_por_defecto_usa_el_mismo_dia_de_la_semana_que_hoy(self):
        from datetime import date, timedelta
        hoy = date.today()
        a = self.v("A", 500, stock=0)
        self.closed_day((hoy - timedelta(days=7)).isoformat(), {a: 7})        # mismo día de la semana
        self.closed_day((hoy - timedelta(days=1)).isoformat(), {a: 1})        # otro día
        html = self.client.get("/inventario/reposicion").get_data(as_text=True)
        self.assertIn(f"{inventory.DIAS[hoy.weekday()][:3]} {(hoy - timedelta(days=7)).day:02d}", html)
        self.assertEqual(len(__import__("re").findall(r"<th>\w{3} \d\d/\d\d</th>", html)), 1)    # una sola columna de jornada

    def test_dia_sin_historial_lo_dice(self):
        self.historial()
        html = self.client.get("/inventario/reposicion?dia=2").get_data(as_text=True)               # miércoles
        self.assertIn("no hay datos reales", html)


if __name__ == "__main__":
    unittest.main()
