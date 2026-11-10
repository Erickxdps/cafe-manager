"""Inventario: todo se deriva de inventory_movements (existencias = suma de movimientos)."""
import math
from datetime import date

from ..db import audit, get_db, transaction
from ..errors import BusinessRuleError, ValidationError
from . import workdays
from .catalog import VARIANT_SQL, _decorate
from .common import clean, normalize

KINDS = {"initial": "Inventario inicial", "receipt": "Recepción", "sale": "Venta", "adjust_pos": "Ajuste (+)",
         "adjust_neg": "Ajuste (−)", "sale_reversal": "Reversión de venta"}
MAX_QTY = 100000


def _int(raw, label, minimum=0):
    if isinstance(raw, str):
        raw = raw.strip()
    if raw in ("", None):
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"{label}: usa un número entero.") from None
    if not minimum <= n <= MAX_QTY:
        raise ValidationError(f"{label}: debe estar entre {minimum} y {MAX_QTY}.")
    return n


def tracked_variants(conn=None):
    rows = (conn or get_db()).execute(VARIANT_SQL + " WHERE v.tracks_stock=1 AND v.active=1 AND p.active=1 "
                                                    "ORDER BY c.sort_order, p.name, v.id")
    return [_decorate(r) for r in rows]


DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def untracked_variants(conn=None):
    """Productos activos que todavía no controlan existencias (no aparecen en recepción ni en el inventario)."""
    rows = (conn or get_db()).execute(VARIANT_SQL + " WHERE v.tracks_stock=0 AND v.active=1 AND p.active=1 "
                                                    "ORDER BY c.sort_order, p.name, v.id")
    return [_decorate(r) for r in rows]


def enable_tracking(variant_ids):
    """Activa el control de existencias. Quedan en 0 hasta registrar su recepción o sus existencias iniciales."""
    ids = {int(v) for v in variant_ids if str(v).isdigit()}
    if not ids:
        raise ValidationError("Marca al menos un producto.")
    conn = get_db()
    with transaction(conn):
        valid = {v["variant_id"] for v in untracked_variants(conn)}
        if not ids <= valid:
            raise ValidationError("Un producto de la lista no existe o ya controla existencias.")
        for vid in ids:
            conn.execute("UPDATE product_variants SET tracks_stock=1 WHERE id=?", (vid,))
        audit(conn, "activar_control", "inventario", None, f"{len(ids)} productos")
    return len(ids)


def _require_open(conn, action):
    wd = workdays.get_open(conn)
    if not wd:
        raise BusinessRuleError(f"Abre la jornada para {action}.")
    return wd


def _tracked_map(conn):
    return {v["variant_id"]: v for v in tracked_variants(conn)}


def _move(conn, vid, workday_id, kind, qty, note=""):
    conn.execute("INSERT INTO inventory_movements(variant_id,workday_id,kind,quantity,note) VALUES (?,?,?,?,?)",
                 (vid, workday_id, kind, qty, note))


# ---------- lectura ----------
def overview(filtro="todos", category_id=None, q=""):
    conn = get_db()
    wd = workdays.get_open(conn)
    wid = wd["id"] if wd else None            # sin jornada abierta, las columnas del día quedan en 0
    day = {r["variant_id"]: r for r in conn.execute(
        "SELECT variant_id, COUNT(*) AS n, "
        "SUM(CASE WHEN workday_id=? AND kind='receipt' THEN quantity ELSE 0 END) AS entradas, "
        "SUM(CASE WHEN workday_id=? AND kind IN ('sale','sale_reversal') THEN -quantity ELSE 0 END) AS vendidas, "
        "SUM(CASE WHEN workday_id=? AND kind IN ('adjust_pos','adjust_neg') THEN quantity ELSE 0 END) AS ajustes "
        "FROM inventory_movements GROUP BY variant_id", (wid, wid, wid))}
    nq, rows = normalize(q), []
    for v in tracked_variants(conn):
        d = day.get(v["variant_id"])
        v.update(has_movements=bool(d), entradas=d["entradas"] if d else 0, vendidas=d["vendidas"] if d else 0,
                 ajustes=d["ajustes"] if d else 0)
        v["apertura"] = v["stock"] - v["entradas"] + v["vendidas"] - v["ajustes"]
        if category_id and v["category_id"] != category_id:
            continue
        if nq and nq not in normalize(v["name"]):
            continue
        if filtro == "bajo" and v["availability"] != "bajo" or filtro == "agotado" and v["availability"] != "agotado":
            continue
        rows.append(v)
    return rows


def alerts():
    low = [v for v in tracked_variants() if v["availability"] == "bajo"]
    out = [v for v in tracked_variants() if v["availability"] == "agotado"]
    return low, out


def movements(variant_id=None, kind=None, limit=300):
    sql = ("SELECT m.id, m.created_at, m.kind, m.quantity, m.note, m.order_id, m.variant_id, p.name AS product, "
           "v.name AS variant FROM inventory_movements m JOIN product_variants v ON v.id=m.variant_id "
           "JOIN products p ON p.id=v.product_id WHERE 1=1")
    args = []
    if variant_id:
        sql += " AND m.variant_id=?"; args.append(variant_id)
    if kind in KINDS:
        sql += " AND m.kind=?"; args.append(kind)
    return get_db().execute(sql + " ORDER BY m.id DESC LIMIT ?", args + [limit]).fetchall()


def sales_weekdays():
    """Días de la semana (0=lunes) que ya tienen jornadas cerradas con ventas."""
    rows = get_db().execute("SELECT w.work_date FROM workdays w WHERE w.status='closed' "
                            "AND EXISTS (SELECT 1 FROM payments p WHERE p.workday_id=w.id)")
    return sorted({date.fromisoformat(r["work_date"]).weekday() for r in rows})


def suggestions(n_days=6, weekday=None):
    """Reposición = max(promedio vendido por jornada redondeado hacia arriba, stock mínimo) - existencias actuales.
    El promedio sale de las últimas jornadas CERRADAS con ventas; con `weekday` (0=lunes) solo cuentan jornadas de
    ese día de la semana (sábados con sábados). Sin jornadas no se sugiere nada."""
    conn = get_db()
    closed = [(r["id"], date.fromisoformat(r["work_date"])) for r in conn.execute(
        "SELECT w.id, w.work_date FROM workdays w WHERE w.status='closed' "
        "AND EXISTS (SELECT 1 FROM payments p WHERE p.workday_id=w.id) ORDER BY w.work_date DESC")]
    chosen = [c for c in closed if weekday is None or c[1].weekday() == weekday][:n_days]
    if not chosen:
        return {"days": 0, "dates": [], "rows": []}
    ids = [c[0] for c in chosen]
    sold = {(r["variant_id"], r["workday_id"]): r["n"] for r in conn.execute(
        f"SELECT variant_id, workday_id, SUM(-quantity) AS n FROM inventory_movements WHERE kind IN ('sale','sale_reversal') "
        f"AND workday_id IN ({','.join('?' * len(ids))}) GROUP BY variant_id, workday_id", ids)}
    rows = []
    for v in tracked_variants(conn):
        daily = [sold.get((v["variant_id"], wid), 0) for wid in ids]
        avg = sum(daily) / len(ids)
        target = max(math.ceil(avg), v["min_stock"])
        v.update(daily=daily, avg=avg, target=target, suggestion=max(0, target - v["stock"]))
        rows.append(v)
    rows.sort(key=lambda v: (-v["suggestion"], v["name"]))
    return {"days": len(ids), "rows": rows,
            "dates": [f"{DIAS[d.weekday()][:3]} {d.day:02d}/{d.month:02d}" for _, d in chosen]}


def day_rows(workday_id):
    """Inventario de una jornada: apertura, entradas, vendidas, ajustes y existencias al final de ESE día."""
    conn = get_db()
    day = {r["variant_id"]: r for r in conn.execute(
        "SELECT variant_id, "
        "SUM(CASE WHEN workday_id IS NULL OR workday_id<=:w THEN quantity ELSE 0 END) AS final, "
        "SUM(CASE WHEN workday_id=:w AND kind='receipt' THEN quantity ELSE 0 END) AS entradas, "
        "SUM(CASE WHEN workday_id=:w AND kind IN ('sale','sale_reversal') THEN -quantity ELSE 0 END) AS vendidas, "
        "SUM(CASE WHEN workday_id=:w AND kind IN ('adjust_pos','adjust_neg') THEN quantity ELSE 0 END) AS ajustes "
        "FROM inventory_movements GROUP BY variant_id", {"w": workday_id})}
    rows = []
    for r in conn.execute(VARIANT_SQL + " WHERE v.tracks_stock=1 ORDER BY c.sort_order, p.name, v.id"):
        d = day.get(r["variant_id"])
        if not d:
            continue
        name = _decorate(r)["name"]
        rows.append({"name": name, "min_stock": r["min_stock"], "final": d["final"], "entradas": d["entradas"],
                     "vendidas": d["vendidas"], "ajustes": d["ajustes"],
                     "apertura": d["final"] - d["entradas"] + d["vendidas"] - d["ajustes"]})
    return rows


# ---------- escritura ----------
def register_receipt(rows):
    """rows: [{variant_id, counted, expected}]. Cada unidad contada entra como movimiento 'receipt'."""
    conn = get_db()
    with transaction(conn):
        wd = _require_open(conn, "registrar recepciones")
        tracked, units, products, diffs = _tracked_map(conn), 0, 0, []
        for r in rows:
            counted, expected = _int(r.get("counted"), "Contado"), _int(r.get("expected"), "Esperado")
            if counted is None:
                continue
            v = tracked.get(r["variant_id"])
            if not v:
                raise ValidationError("Un producto de la lista no existe o no controla existencias.")
            note = "Recepción"
            if expected is not None:
                note += f" (esperado {expected}, contado {counted})"
                if expected != counted:
                    diffs.append(f"{v['name']}: esperado {expected}, contado {counted}")
            if counted > 0:
                _move(conn, v["variant_id"], wd["id"], "receipt", counted, note)
                products += 1
                units += counted
        if not products:
            raise ValidationError("No ingresaste ninguna cantidad recibida.")
        audit(conn, "recepcion", "inventario", None, f"{products} productos, {units} unidades")
    return {"products": products, "units": units, "differences": diffs}


def apply_count(rows, reason=""):
    """Conteo físico. rows: [{variant_id, counted, system}]. Se registra solo la diferencia contra el sistema;
    si el sistema cambió mientras se contaba (hubo ventas), se rechaza todo el lote."""
    reason = clean(reason, "El motivo", max_len=120)
    conn = get_db()
    with transaction(conn):
        wd = _require_open(conn, "ajustar existencias")
        tracked, changed = _tracked_map(conn), 0
        for r in rows:
            counted = _int(r.get("counted"), "Contado")
            if counted is None:
                continue
            v = tracked.get(r["variant_id"])
            if not v:
                raise ValidationError("Un producto de la lista no existe o no controla existencias.")
            system = _int(r.get("system"), "Sistema", minimum=-MAX_QTY) if r.get("system") not in (None, "") else None
            if system is not None and system != v["stock"]:
                raise BusinessRuleError(f"«{v['name']}» cambió de {system} a {v['stock']} mientras contabas. "
                                        f"Revisa el conteo y vuelve a enviarlo.")
            delta = counted - v["stock"]
            if delta:
                note = f"Conteo físico: sistema {v['stock']}, contado {counted}" + (f". {reason}" if reason else "")
                _move(conn, v["variant_id"], wd["id"], "adjust_pos" if delta > 0 else "adjust_neg", delta, note)
                changed += 1
        audit(conn, "conteo", "inventario", None, f"{changed} ajustes")
    return changed


def adjust(variant_id, delta_raw, note):
    delta = _int(delta_raw, "El ajuste", minimum=-MAX_QTY)
    if not delta:
        raise ValidationError("Indica cuántas unidades sumar (+) o restar (−).")
    note = clean(note, "El motivo", required=True, max_len=120)
    conn = get_db()
    with transaction(conn):
        wd = _require_open(conn, "ajustar existencias")
        v = _tracked_map(conn).get(variant_id)
        if not v:
            raise ValidationError("El producto no existe o no controla existencias.")
        if v["stock"] + delta < 0:
            raise BusinessRuleError(f"«{v['name']}» solo tiene {v['stock']} unidades; no se pueden restar {-delta}.")
        _move(conn, variant_id, wd["id"], "adjust_pos" if delta > 0 else "adjust_neg", delta, f"Ajuste manual: {note}")
        audit(conn, "ajuste", "inventario", variant_id, f"{delta:+d} {note}")


def set_initial(rows):
    """Existencias iniciales: solo para productos sin ningún movimiento. rows: [{variant_id, qty}]."""
    conn = get_db()
    with transaction(conn):
        wd = workdays.get_open(conn)
        tracked, count = _tracked_map(conn), 0
        for r in rows:
            qty = _int(r.get("qty"), "Existencias iniciales")
            if not qty:
                continue
            v = tracked.get(r["variant_id"])
            if not v:
                raise ValidationError("Un producto de la lista no existe o no controla existencias.")
            if conn.execute("SELECT 1 FROM inventory_movements WHERE variant_id=?", (v["variant_id"],)).fetchone():
                raise BusinessRuleError(f"«{v['name']}» ya tiene movimientos; corrige con un ajuste.")
            _move(conn, v["variant_id"], wd["id"] if wd else None, "initial", qty, "Inventario inicial")
            count += 1
        if not count:
            raise ValidationError("No ingresaste ninguna cantidad inicial.")
        audit(conn, "inicial", "inventario", None, f"{count} productos")
    return count


def consistency_problems(conn):
    return [f"Variante {r['variant_id']}: existencias negativas ({r['stock']})"
            for r in conn.execute("SELECT s.variant_id, s.stock FROM variant_stock s JOIN product_variants v "
                                  "ON v.id=s.variant_id WHERE v.tracks_stock=1 AND s.stock<0")]
