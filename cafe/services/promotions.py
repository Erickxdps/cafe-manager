import sqlite3

from ..db import audit, get_db, transaction
from ..errors import NotFoundError, ValidationError
from ..money import parse_bs
from .catalog import VARIANT_SQL, _decorate
from .common import clean


def sellable_variants():
    rows = get_db().execute(VARIANT_SQL + " WHERE p.active=1 AND v.active=1 ORDER BY c.sort_order, p.name, v.id")
    return [_decorate(r) for r in rows]


def _slots(conn, promotion_id):
    slots = []
    for s in conn.execute("SELECT * FROM promotion_slots WHERE promotion_id=? ORDER BY position, id", (promotion_id,)):
        opts = [r["variant_id"] for r in conn.execute("SELECT variant_id FROM promotion_slot_options WHERE slot_id=?", (s["id"],))]
        cats = [r["category_id"] for r in conn.execute("SELECT category_id FROM promotion_slot_categories WHERE slot_id=?", (s["id"],))]
        slots.append({"id": s["id"], "name": s["name"], "quantity": s["quantity"], "options": opts, "categories": cats})
    return slots


def list_promotions():
    conn = get_db()
    return [{**dict(p), "slots": _slots(conn, p["id"]), "used": _used(conn, p["id"])}
            for p in conn.execute("SELECT * FROM promotions WHERE archived=0 ORDER BY name")]


def list_archived():
    return [dict(r) for r in get_db().execute(
        "SELECT p.*, (SELECT COUNT(*) FROM order_items i WHERE i.promotion_id=p.id) AS sold FROM promotions p "
        "WHERE p.archived=1 ORDER BY p.name")]


def _used(conn, pid):
    return conn.execute("SELECT 1 FROM order_items WHERE promotion_id=? LIMIT 1", (pid,)).fetchone() is not None


def delete_promotion(pid):
    """Borra la promoción si nunca se vendió; si ya está en pedidos, la archiva (el historial no se toca).
    Devuelve "eliminada" o "archivada"."""
    conn = get_db()
    with transaction(conn):
        row = conn.execute("SELECT * FROM promotions WHERE id=? AND archived=0", (pid,)).fetchone()
        if not row:
            raise NotFoundError("La promoción no existe.")
        if _used(conn, pid):
            # se libera el nombre para poder crear otra igual; los pedidos guardan su propio nombre histórico
            conn.execute("UPDATE promotions SET archived=1, active=0, name=? WHERE id=?",
                         (f"{row['name']} (archivada #{pid})"[:80], pid))
            audit(conn, "archivar", "promocion", pid, row["name"])
            return "archivada"
        conn.execute("DELETE FROM promotions WHERE id=?", (pid,))   # elecciones y opciones caen por cascada
        audit(conn, "eliminar", "promocion", pid, row["name"])
        return "eliminada"


def get_promotion(pid):
    conn = get_db()
    row = conn.execute("SELECT * FROM promotions WHERE id=? AND archived=0", (pid,)).fetchone()
    if not row:
        raise NotFoundError("La promoción no existe.")
    return {**dict(row), "slots": _slots(conn, pid), "used": _used(conn, pid)}


def save_promotion(pid, form):
    name = clean(form.get("name"), "El nombre", required=True, max_len=60)
    description = clean(form.get("description"), "La descripción", max_len=200)
    price = parse_bs(form.get("price", ""))
    active = int(form.get("active") == "1")
    slots = []
    for idx in form.getlist("slot_idx"):
        sid, sname = form.get(f"slot_{idx}_id", type=int), form.get(f"slot_{idx}_name", "").strip()
        opts = {int(v) for v in form.getlist(f"slot_{idx}_options") if v.isdigit()}
        raw_qty = (form.get(f"slot_{idx}_qty") or "1").strip()
        qty = int(raw_qty) if raw_qty.lstrip("-").isdigit() else 0
        if not 1 <= qty <= 10:
            raise ValidationError("La cantidad de una elección debe estar entre 1 y 10.")
        cats = {int(v) for v in form.getlist(f"slot_{idx}_cats") if v.isdigit()}
        if sname and not opts and not cats:
            raise ValidationError(f"«{sname}» no tiene nada para elegir: activa al menos una sección o agrega productos sueltos.")
        if sname or sid:
            slots.append((sid, sname, opts, qty, cats))
    if not slots:
        raise ValidationError("La promoción necesita al menos una elección (por ejemplo: Bebida).")
    conn = get_db()
    try:
        with transaction(conn):
            if pid is None:
                pid = conn.execute("INSERT INTO promotions(name,description,price_cents,active) VALUES (?,?,?,?)",
                                   (name, description, price, active)).lastrowid
            elif not conn.execute("UPDATE promotions SET name=?, description=?, price_cents=?, active=? WHERE id=? AND archived=0",
                                  (name, description, price, active, pid)).rowcount:
                raise NotFoundError("La promoción no existe.")
            kept = set()
            for pos, (sid, sname, opts, qty, cats) in enumerate(slots, 1):
                if sid and not conn.execute("SELECT 1 FROM promotion_slots WHERE id=? AND promotion_id=?", (sid, pid)).fetchone():
                    raise ValidationError("Una elección no pertenece a esta promoción.")
                if not sname:
                    continue  # nombre vacío en una existente = quitarla (abajo)
                if sid:
                    conn.execute("UPDATE promotion_slots SET name=?, position=?, quantity=? WHERE id=?", (sname, pos, qty, sid))
                else:
                    sid = conn.execute("INSERT INTO promotion_slots(promotion_id,name,position,quantity) VALUES (?,?,?,?)",
                                       (pid, sname, pos, qty)).lastrowid
                kept.add(sid)
                conn.execute("DELETE FROM promotion_slot_options WHERE slot_id=?", (sid,))
                for vid in opts:
                    conn.execute("INSERT INTO promotion_slot_options(slot_id,variant_id) VALUES (?,?)", (sid, vid))
                conn.execute("DELETE FROM promotion_slot_categories WHERE slot_id=?", (sid,))
                for cat in cats:
                    conn.execute("INSERT INTO promotion_slot_categories(slot_id,category_id) VALUES (?,?)", (sid, cat))
            for old in conn.execute("SELECT id FROM promotion_slots WHERE promotion_id=?", (pid,)).fetchall():
                if old["id"] not in kept:
                    if conn.execute("SELECT 1 FROM order_item_components WHERE slot_id=?", (old["id"],)).fetchone():
                        raise ValidationError("No se puede quitar una elección que ya se usó en pedidos.")
                    conn.execute("DELETE FROM promotion_slots WHERE id=?", (old["id"],))
            audit(conn, "guardar", "promocion", pid, f"{name} {price}")
    except sqlite3.IntegrityError as exc:
        raise ValidationError("Ya existe una promoción con ese nombre." if "promotions.name" in str(exc)
                              else "Alguna opción elegida no existe.") from None
    return pid


def sale_promotions():
    """Promociones activas con sus opciones vendibles (para el selector de pedidos)."""
    conn = get_db()
    variants = {v["variant_id"]: v for v in sellable_variants()}
    out = []
    for p in conn.execute("SELECT * FROM promotions WHERE active=1 AND archived=0 ORDER BY name"):
        slots = []
        for s in _slots(conn, p["id"]):
            effective = [v for v in variants.values() if v["variant_id"] in s["options"] or v["category_id"] in s["categories"]]
            slots.append({"id": s["id"], "name": s["name"], "quantity": s["quantity"],
                          "options": [{"variant_id": v["variant_id"], "name": v["name"], "category": v["category"],
                                       "availability": v["availability"], "stock": v["stock"], "photo": v["photo"]}
                                      for v in effective]})
        out.append({"id": p["id"], "name": p["name"], "price_cents": p["price_cents"], "slots": slots})
    return out
