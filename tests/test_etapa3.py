import tempfile
import unittest
from pathlib import Path

from cafe import create_app, db
from cafe.services import orders


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.app = create_app({"DATA_DIR": Path(self.tmp.name), "TESTING": True})
        self.client = self.app.test_client()
        self.conn = db.connect(self.app.config["DB_PATH"])
        self.client.post("/jornada/abrir", data={"fund": "100"})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def q(self, sql, *a):
        return self.conn.execute(sql, a)

    def v(self, name, price=800, tracks=1, stock=0, category=1):
        pid = self.q("INSERT INTO products(category_id,name) VALUES (?,?)", category, name).lastrowid
        vid = self.q("INSERT INTO product_variants(product_id,price_cents,tracks_stock) VALUES (?,?,?)", pid, price, tracks).lastrowid
        if stock:
            self.q("INSERT INTO inventory_movements(variant_id,kind,quantity) VALUES (?,'initial',?)", vid, stock)
        return vid

    def stock(self, vid):
        return self.q("SELECT stock FROM variant_stock WHERE variant_id=?", vid).fetchone()["stock"]

    def moves(self, oid):
        return [(m["kind"], m["quantity"]) for m in self.q("SELECT kind, quantity FROM inventory_movements WHERE order_id=? ORDER BY id", oid)]

    def api(self, method, url, body=None, **kw):
        return self.client.open(url, method=method, json=body if body is not None else {}, **kw)

    def order(self, *lines, discount="", pay=None):
        r = self.api("POST", "/api/pedidos", {"lines": [{"variant_id": v, "quantity": q} for v, q in lines], "discount": discount})
        self.assertEqual(r.status_code, 201, r.get_json())
        oid = r.get_json()["id"]
        if pay:
            self.assertEqual(self.api("POST", f"/api/pedidos/{oid}/pagar", {"method": pay}).status_code, 200)
        return oid

    def edit(self, oid, *lines, discount=""):
        cur = {i["variant_id"]: i["item_id"] for i in orders_items(self.conn, oid)}
        return self.api("PUT", f"/api/pedidos/{oid}", {"lines": [
            {"item_id": cur.get(v), "variant_id": v, "quantity": q} for v, q in lines], "discount": discount})

    def ok(self):
        self.assertEqual(orders.consistency_problems(self.conn), [])
        self.assertEqual(db.check_integrity(self.conn), [])


def orders_items(conn, oid):
    return [{"variant_id": r["variant_id"], "item_id": r["id"]} for r in conn.execute("SELECT id, variant_id FROM order_items WHERE order_id=?", (oid,))]


class TestPedidos(Base):
    def test_pendiente_no_descuenta_ni_cuenta_como_venta(self):
        e = self.v("Éclair", 800, stock=5)
        oid = self.order((e, 2))
        self.assertEqual(self.stock(e), 5)
        self.assertEqual(self.moves(oid), [])
        self.assertEqual(self.q("SELECT COUNT(*) c FROM payments").fetchone()["c"], 0)
        o = self.q("SELECT status, total_cents FROM orders").fetchone()
        self.assertEqual((o["status"], o["total_cents"]), ("pending", 1600))
        self.ok()

    def test_pagar_descuenta_una_sola_vez(self):
        e = self.v("Éclair", 800, stock=5)
        oid = self.order((e, 2))
        self.assertEqual(self.api("POST", f"/api/pedidos/{oid}/pagar", {"method": "cash"}).status_code, 200)
        self.assertEqual(self.moves(oid), [("sale", -2)])
        r = self.api("POST", f"/api/pedidos/{oid}/pagar", {"method": "qr"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("ya está pagado", r.get_json()["error"])
        self.assertEqual(self.moves(oid), [("sale", -2)])
        self.assertEqual(self.stock(e), 3)
        p = self.q("SELECT method, amount_cents FROM payments").fetchall()
        self.assertEqual([(x["method"], x["amount_cents"]) for x in p], [("cash", 1600)])
        self.ok()

    def test_metodo_invalido_y_cambio_de_metodo(self):
        e = self.v("Té", 800, tracks=0)
        oid = self.order((e, 1))
        self.assertEqual(self.api("POST", f"/api/pedidos/{oid}/pagar", {"method": "tarjeta"}).status_code, 400)
        self.assertEqual(self.api("POST", f"/api/pedidos/{oid}/metodo", {"method": "qr"}).status_code, 409)  # aún pendiente
        self.api("POST", f"/api/pedidos/{oid}/pagar", {"method": "cash"})
        self.api("POST", f"/api/pedidos/{oid}/metodo", {"method": "qr"})
        self.assertEqual(self.q("SELECT method FROM payments").fetchone()["method"], "qr")
        self.assertEqual(self.q("SELECT COUNT(*) c FROM payments").fetchone()["c"], 1)

    def test_producto_sin_control_no_genera_movimientos(self):
        te = self.v("Té", 800, tracks=0)
        oid = self.order((te, 3), pay="qr")
        self.assertEqual(self.moves(oid), [])
        self.ok()

    def test_agotado_no_se_puede_vender(self):
        g = self.v("Galleta", 500, stock=0)
        r = self.api("POST", "/api/pedidos", {"lines": [{"variant_id": g, "quantity": 1}]})
        self.assertEqual(r.status_code, 409)
        self.assertIn("agotado", r.get_json()["error"])
        self.assertEqual(self.q("SELECT COUNT(*) c FROM orders").fetchone()["c"], 0)
        e = self.v("Éclair", 800, stock=2)
        r = self.api("POST", "/api/pedidos", {"lines": [{"variant_id": e, "quantity": 3}]})
        self.assertIn("disponibles: 2", r.get_json()["error"])

    def test_pendientes_no_reservan_stock_el_segundo_cobro_falla(self):
        e = self.v("Éclair", 800, stock=1)
        a, b = self.order((e, 1)), self.order((e, 1))
        self.assertEqual(self.api("POST", f"/api/pedidos/{a}/pagar", {"method": "cash"}).status_code, 200)
        r = self.api("POST", f"/api/pedidos/{b}/pagar", {"method": "cash"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.q("SELECT status FROM orders WHERE id=?", b).fetchone()["status"], "pending")
        self.assertEqual(self.moves(b), [])
        self.assertEqual(self.stock(e), 0)
        self.ok()

    def test_cobro_fallido_no_deja_nada_a_medias(self):
        a, b = self.v("A", 500, stock=5), self.v("B", 500, stock=1)
        x = self.order((a, 1), (b, 1))
        self.order((b, 1), pay="cash")               # se lleva la última unidad de B
        r = self.api("POST", f"/api/pedidos/{x}/pagar", {"method": "cash"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.stock(a), 5)           # A no se descontó
        self.assertEqual(self.moves(x), [])
        self.ok()

    def test_editar_pedido_pagado_ajusta_solo_la_diferencia(self):
        e = self.v("Éclair", 800, stock=10)
        oid = self.order((e, 2), pay="cash")
        self.assertEqual(self.edit(oid, (e, 2)).status_code, 200)           # sin cambios
        self.assertEqual(self.moves(oid), [("sale", -2)])
        self.assertEqual(self.edit(oid, (e, 3)).status_code, 200)           # +1
        self.assertEqual(self.moves(oid), [("sale", -2), ("sale", -1)])
        self.assertEqual(self.stock(e), 7)
        self.assertEqual(self.q("SELECT amount_cents a FROM payments").fetchone()["a"], 2400)
        self.assertEqual(self.edit(oid, (e, 1)).status_code, 200)           # -2
        self.assertEqual(self.moves(oid)[-1], ("sale_reversal", 2))
        self.assertEqual(self.stock(e), 9)
        self.assertEqual(self.q("SELECT method FROM payments").fetchone()["method"], "cash")  # se conserva el método
        self.assertEqual(self.q("SELECT amount_cents a FROM payments").fetchone()["a"], 800)
        self.ok()

    def test_editar_pagado_validando_stock_con_lo_ya_descontado(self):
        e = self.v("Éclair", 800, stock=3)
        oid = self.order((e, 3), pay="cash")                                 # stock 0
        self.assertEqual(self.edit(oid, (e, 3)).status_code, 200)            # mismo consumo: permitido
        r = self.edit(oid, (e, 4))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.q("SELECT quantity q FROM order_items").fetchone()["q"], 3)  # nada cambió
        self.assertEqual(self.q("SELECT total_cents t FROM orders").fetchone()["t"], 2400)
        self.ok()

    def test_editar_pagado_agregando_y_quitando_productos(self):
        a, b = self.v("A", 500, stock=5), self.v("B", 300, stock=5)
        oid = self.order((a, 1), pay="qr")
        self.edit(oid, (b, 2))                                               # A fuera, B dentro
        self.assertEqual(self.stock(a), 5)
        self.assertEqual(self.stock(b), 3)
        self.assertEqual(self.q("SELECT amount_cents a FROM payments").fetchone()["a"], 600)
        self.ok()

    def test_cancelar_pendiente_no_toca_inventario(self):
        e = self.v("Éclair", 800, stock=5)
        oid = self.order((e, 2))
        self.assertEqual(self.api("POST", f"/api/pedidos/{oid}/cancelar").status_code, 200)
        self.assertEqual(self.moves(oid), [])
        self.assertEqual(self.stock(e), 5)
        self.ok()

    def test_cancelar_pagado_revierte_todo(self):
        e = self.v("Éclair", 800, stock=5)
        oid = self.order((e, 2), pay="cash")
        self.edit(oid, (e, 4))
        self.api("POST", f"/api/pedidos/{oid}/cancelar")
        self.assertEqual(self.stock(e), 5)
        self.assertEqual(self.q("SELECT COUNT(*) c FROM payments").fetchone()["c"], 0)
        self.assertEqual(self.q("SELECT status FROM orders").fetchone()["status"], "cancelled")
        self.assertEqual(sum(q for _, q in self.moves(oid)), 0)
        self.assertEqual(self.api("POST", f"/api/pedidos/{oid}/cancelar").status_code, 409)   # no se revierte dos veces
        self.assertEqual(self.edit(oid, (e, 1)).status_code, 409)
        self.assertEqual(self.api("POST", f"/api/pedidos/{oid}/pagar", {"method": "cash"}).status_code, 409)
        self.assertEqual(self.stock(e), 5)
        self.ok()

    def test_precio_historico(self):
        a, b = self.v("A", 500, stock=9), self.v("B", 300, stock=9)
        oid = self.order((a, 1), pay="cash")
        self.q("UPDATE product_variants SET price_cents=999 WHERE id=?", a)
        self.q("UPDATE product_variants SET price_cents=777 WHERE id=?", b)
        self.edit(oid, (a, 2), (b, 1))
        prices = {r["variant_id"]: r["unit_price_cents"] for r in self.q("SELECT variant_id, unit_price_cents FROM order_items")}
        self.assertEqual(prices, {a: 500, b: 777})          # A conserva su precio; B, nueva, toma el actual
        self.assertEqual(self.q("SELECT total_cents t FROM orders").fetchone()["t"], 1777)
        self.ok()

    def test_nombre_historico_si_se_renombra_el_producto(self):
        a = self.v("A", 500, tracks=0)
        oid = self.order((a, 1))
        self.q("UPDATE products SET name='Otro'")
        self.assertEqual(self.q("SELECT name_snapshot n FROM order_items WHERE order_id=?", oid).fetchone()["n"], "A")

    def test_descuento_monto_fijo(self):
        a = self.v("A", 1000, tracks=0)
        oid = self.order((a, 1), discount="2,50", pay="cash")
        o = self.q("SELECT subtotal_cents s, discount_cents d, total_cents t FROM orders").fetchone()
        self.assertEqual((o["s"], o["d"], o["t"]), (1000, 250, 750))
        self.assertEqual(self.q("SELECT amount_cents a FROM payments").fetchone()["a"], 750)
        r = self.api("POST", "/api/pedidos", {"lines": [{"variant_id": a, "quantity": 1}], "discount": "11"})
        self.assertEqual(r.status_code, 400)
        r = self.api("POST", "/api/pedidos", {"lines": [{"variant_id": a, "quantity": 1}], "discount": "-1"})
        self.assertEqual(r.status_code, 400)
        self.ok()

    def test_validaciones(self):
        a = self.v("A", 500, stock=5)
        off = self.v("Off", 500, stock=5)
        self.q("UPDATE product_variants SET active=0 WHERE id=?", off)
        bad = [[], None, [{"variant_id": a, "quantity": 0}], [{"variant_id": a, "quantity": 1000}],
               [{"variant_id": a, "quantity": "x"}], [{"variant_id": 9999, "quantity": 1}], [{"variant_id": off, "quantity": 1}],
               [{"quantity": 1}], ["texto"], [{"variant_id": a, "quantity": 1, "item_id": 5}]]
        for lines in bad:
            r = self.api("POST", "/api/pedidos", {"lines": lines})
            self.assertEqual(r.status_code, 400, lines)
            self.assertIn("error", r.get_json())
        self.assertEqual(self.q("SELECT COUNT(*) c FROM orders").fetchone()["c"], 0)
        self.assertEqual(self.client.post("/api/pedidos", data="no json", content_type="text/plain").status_code, 400)

    def test_linea_de_otro_pedido_rechazada(self):
        a = self.v("A", 500, tracks=0)
        o1, o2 = self.order((a, 1)), self.order((a, 1))
        item_de_o1 = self.q("SELECT id FROM order_items WHERE order_id=?", o1).fetchone()["id"]
        r = self.api("PUT", f"/api/pedidos/{o2}", {"lines": [{"item_id": item_de_o1, "variant_id": a, "quantity": 5}]})
        self.assertEqual(r.status_code, 400)

    def test_pedido_con_producto_desactivado_despues_se_puede_pagar(self):
        a = self.v("A", 500, stock=5)
        oid = self.order((a, 1))
        self.q("UPDATE products SET active=0")
        self.assertEqual(self.api("POST", f"/api/pedidos/{oid}/pagar", {"method": "cash"}).status_code, 200)
        self.assertEqual(self.edit(oid, (a, 2)).status_code, 200)   # ya estaba en el pedido
        self.ok()

    def test_sin_jornada_abierta(self):
        a = self.v("A", 500, tracks=0)
        oid = self.order((a, 1))
        self.q("UPDATE workdays SET status='closed'")
        for r in (self.api("POST", "/api/pedidos", {"lines": [{"variant_id": a, "quantity": 1}]}),
                  self.edit(oid, (a, 2)), self.api("POST", f"/api/pedidos/{oid}/pagar", {"method": "cash"}),
                  self.api("POST", f"/api/pedidos/{oid}/cancelar")):
            self.assertEqual(r.status_code, 409)
        self.assertEqual(self.client.get(f"/pedidos/{oid}").status_code, 200)   # se puede consultar

    def test_origen_externo_en_put_rechazado(self):
        a = self.v("A", 500, tracks=0)
        oid = self.order((a, 1))
        r = self.api("PUT", f"/api/pedidos/{oid}", {"lines": [{"variant_id": a, "quantity": 9}]}, headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_pantallas(self):
        a = self.v("</script><b>X", 500, stock=2)
        oid = self.order((a, 1))
        html = self.client.get("/pedidos").get_data(as_text=True)
        self.assertIn(f"#{oid}", html)
        page = self.client.get(f"/pedidos/{oid}").get_data(as_text=True)
        self.assertNotIn("</script><b>X", page)          # tojson escapa
        self.assertEqual(self.client.get("/pedidos/nuevo").status_code, 200)
        self.assertEqual(self.client.get("/pedidos/9999").status_code, 404)


class TestCombo(Base):
    def setUp(self):
        super().setUp()
        self.cafe = self.v("Café", 1000, tracks=0)
        self.te = self.v("Té", 800, tracks=0)
        self.g = [self.v(f"Galleta {i}", 500, stock=4) for i in (1, 2, 3)]
        r = self.client.post("/promociones/1", data={
            "name": "Combo Bs 17", "description": "", "price": "17", "active": "1", "slot_idx": ["0", "1"],
            "slot_0_id": "1", "slot_0_name": "Bebida", "slot_0_options": [str(self.cafe), str(self.te)],
            "slot_1_id": "2", "slot_1_name": "Galleta", "slot_1_options": [str(g) for g in self.g]})
        self.assertEqual(r.status_code, 302)

    def combo(self, bebida, galleta, **kw):
        return {"promotion_id": 1, "quantity": 1, "selections": {"1": bebida, "2": galleta}, **kw}

    def make(self, *lines, pay=None):
        r = self.api("POST", "/api/pedidos", {"lines": list(lines)})
        self.assertEqual(r.status_code, 201, r.get_json())
        oid = r.get_json()["id"]
        if pay:
            self.api("POST", f"/api/pedidos/{oid}/pagar", {"method": pay})
        return oid

    def test_un_solo_ingreso_y_descuenta_componentes(self):
        oid = self.make(self.combo(self.cafe, self.g[0]), pay="cash")
        self.assertEqual(self.q("SELECT amount_cents a FROM payments").fetchone()["a"], 1700)
        self.assertEqual(self.q("SELECT COUNT(*) c FROM payments").fetchone()["c"], 1)
        self.assertEqual(self.q("SELECT subtotal_cents s FROM orders").fetchone()["s"], 1700)
        self.assertEqual(self.moves(oid), [("sale", -1)])           # solo la galleta tiene control
        self.assertEqual(self.stock(self.g[0]), 3)
        comps = [r["name_snapshot"] for r in self.q("SELECT name_snapshot FROM order_item_components ORDER BY id")]
        self.assertEqual(comps, ["Café", "Galleta 1"])              # componentes registrados
        self.ok()

    def test_componentes_con_control_no_se_duplican(self):
        self.q("UPDATE product_variants SET tracks_stock=1 WHERE id=?", self.cafe)
        self.q("INSERT INTO inventory_movements(variant_id,kind,quantity) VALUES (?,'initial',3)", self.cafe)
        oid = self.make(self.combo(self.cafe, self.g[1]), pay="qr")
        self.assertEqual(sorted(self.moves(oid)), [("sale", -1), ("sale", -1)])
        self.assertEqual(self.stock(self.cafe), 2)
        self.assertEqual(self.stock(self.g[1]), 3)
        self.api("POST", f"/api/pedidos/{oid}/cancelar")
        self.assertEqual((self.stock(self.cafe), self.stock(self.g[1])), (3, 4))
        self.ok()

    def test_combo_mas_producto_suelto_de_la_misma_galleta(self):
        oid = self.make(self.combo(self.te, self.g[0]), {"variant_id": self.g[0], "quantity": 2}, pay="cash")
        self.assertEqual(self.stock(self.g[0]), 1)                  # 1 del combo + 2 sueltas
        self.assertEqual(self.q("SELECT amount_cents a FROM payments").fetchone()["a"], 1700 + 1000)
        r = self.api("POST", "/api/pedidos", {"lines": [self.combo(self.te, self.g[0]), {"variant_id": self.g[0], "quantity": 4}]})
        self.assertEqual(r.status_code, 409)                        # 5 > 1 disponible
        self.ok()

    def test_cambiar_galleta_de_un_combo_pagado(self):
        oid = self.make(self.combo(self.cafe, self.g[0]), pay="cash")
        item = self.q("SELECT id FROM order_items").fetchone()["id"]
        r = self.api("PUT", f"/api/pedidos/{oid}", {"lines": [self.combo(self.cafe, self.g[2], item_id=item)]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((self.stock(self.g[0]), self.stock(self.g[2])), (4, 3))
        self.assertEqual(self.q("SELECT amount_cents a FROM payments").fetchone()["a"], 1700)
        self.ok()

    def test_precio_del_combo_es_historico(self):
        oid = self.make(self.combo(self.cafe, self.g[0]), pay="cash")
        self.q("UPDATE promotions SET price_cents=2000")
        item = self.q("SELECT id FROM order_items").fetchone()["id"]
        self.api("PUT", f"/api/pedidos/{oid}", {"lines": [self.combo(self.cafe, self.g[1], item_id=item)]})
        self.assertEqual(self.q("SELECT total_cents t FROM orders").fetchone()["t"], 1700)
        self.make(self.combo(self.cafe, self.g[0]))
        self.assertEqual(self.q("SELECT MAX(total_cents) t FROM orders").fetchone()["t"], 2000)   # pedido nuevo, precio nuevo

    def test_validaciones_del_combo(self):
        otra_bebida = self.v("Jugo", 900, tracks=0)
        casos = [self.combo(self.cafe, self.g[0], quantity=2),
                 {"promotion_id": 1, "quantity": 1, "selections": {"1": self.cafe}},               # falta galleta
                 {"promotion_id": 1, "quantity": 1, "selections": {}},
                 self.combo(self.g[0], self.cafe),                                                  # opciones cruzadas
                 self.combo(otra_bebida, self.g[0]),                                                # no es opción
                 {"promotion_id": 99, "quantity": 1, "selections": {}}]
        for c in casos:
            self.assertEqual(self.api("POST", "/api/pedidos", {"lines": [c]}).status_code, 400, c)
        self.q("UPDATE promotions SET active=0")
        self.assertEqual(self.api("POST", "/api/pedidos", {"lines": [self.combo(self.cafe, self.g[0])]}).status_code, 400)
        self.assertEqual(self.q("SELECT COUNT(*) c FROM orders").fetchone()["c"], 0)

    def test_galleta_agotada_no_permite_combo(self):
        for _ in range(4):
            self.make(self.combo(self.cafe, self.g[0]), pay="cash")
        r = self.api("POST", "/api/pedidos", {"lines": [self.combo(self.cafe, self.g[0])]})
        self.assertEqual(r.status_code, 409)
        self.assertIn("agotado", r.get_json()["error"])
        self.ok()

    def test_dos_combos_en_un_pedido(self):
        oid = self.make(self.combo(self.cafe, self.g[0]), self.combo(self.te, self.g[0]), pay="cash")
        self.assertEqual(self.q("SELECT total_cents t FROM orders").fetchone()["t"], 3400)
        self.assertEqual(self.stock(self.g[0]), 2)
        self.ok()


class TestConfigPromocion(Base):
    def form(self, **over):
        data = {"name": "Otra", "description": "", "price": "20", "active": "1", "slot_idx": ["0"],
                "slot_0_id": "", "slot_0_name": "Elección", "slot_0_options": [], "slot_0_cats": ["1"]}
        data.update(over)
        return self.client.post("/promociones/nueva", data=data)

    def test_crear_y_validar(self):
        self.assertEqual(self.form().status_code, 302)
        self.assertEqual(self.form().status_code, 400)                         # nombre repetido
        self.assertEqual(self.form(name="X", price="abc").status_code, 400)
        self.assertEqual(self.form(name="Y", slot_idx=[], slot_0_name="").status_code, 400)
        r = self.form(name="Z", slot_0_cats=[])                                 # una elección sin nada para elegir
        self.assertEqual(r.status_code, 400)
        self.assertIn("no tiene nada para elegir", r.get_data(as_text=True))
        self.assertEqual(self.q("SELECT COUNT(*) c FROM promotions").fetchone()["c"], 2)

    def test_no_se_puede_quitar_eleccion_usada(self):
        a = self.v("Café", 500, tracks=0); g = self.v("Galleta", 500, tracks=0)
        base = {"name": "Combo Bs 17", "price": "17", "active": "1", "slot_idx": ["0", "1"], "slot_0_id": "1", "slot_0_name": "Bebida",
                "slot_0_options": [str(a)], "slot_1_id": "2", "slot_1_name": "Galleta", "slot_1_options": [str(g)]}
        self.client.post("/promociones/1", data=base)
        self.api("POST", "/api/pedidos", {"lines": [{"promotion_id": 1, "quantity": 1, "selections": {"1": a, "2": g}}]})
        r = self.client.post("/promociones/1", data={**base, "slot_1_name": ""})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.q("SELECT COUNT(*) c FROM promotion_slots").fetchone()["c"], 2)   # nada cambió


class TestSecuencia(Base):
    def test_secuencia_larga_mantiene_la_consistencia(self):
        a, b = self.v("A", 500, stock=20), self.v("B", 300, stock=20)
        o1 = self.order((a, 3), (b, 2), pay="cash")
        o2 = self.order((a, 1), pay="qr")
        o3 = self.order((b, 4))
        self.edit(o1, (a, 5), (b, 1)); self.edit(o2, (a, 2), (b, 2))
        self.api("POST", f"/api/pedidos/{o3}/pagar", {"method": "cash"})
        self.edit(o3, (b, 1)); self.api("POST", f"/api/pedidos/{o1}/cancelar")
        self.edit(o2, (a, 1)); self.edit(o3, (a, 1), (b, 1))
        self.assertEqual(self.stock(a), 20 - 1 - 1)
        self.assertEqual(self.stock(b), 20 - 1)
        self.ok()

    def test_detecta_inconsistencias_manipuladas(self):
        a = self.v("A", 500, stock=5)
        oid = self.order((a, 2), pay="cash")
        self.q("DELETE FROM inventory_movements WHERE order_id=?", oid)
        self.assertTrue(orders.consistency_problems(self.conn))


if __name__ == "__main__":
    unittest.main()
