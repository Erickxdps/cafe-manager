"""Jornadas (una por día), sus listas de tareas y plantillas de checklist."""
from datetime import date

from ..db import audit, get_db, transaction
from ..errors import BusinessRuleError, NotFoundError, ValidationError
from ..money import parse_bs
from .common import clean


def get_open(conn=None):
    return (conn or get_db()).execute("SELECT * FROM workdays WHERE status='open'").fetchone()


def get(workday_id, conn=None):
    row = (conn or get_db()).execute("SELECT * FROM workdays WHERE id=?", (workday_id,)).fetchone()
    if not row:
        raise NotFoundError("La jornada no existe.")
    return row


def latest(conn=None):
    return (conn or get_db()).execute("SELECT * FROM workdays ORDER BY work_date DESC, id DESC LIMIT 1").fetchone()


def default_fund_cents(conn=None):
    """El fondo de hoy es lo que se reservó al cerrar la jornada anterior; si no hay, el valor por defecto (Bs 100)."""
    conn = conn or get_db()
    prev = conn.execute("SELECT reserve_cents FROM workdays WHERE status='closed' ORDER BY work_date DESC, id DESC LIMIT 1").fetchone()
    if prev:
        return prev["reserve_cents"]
    row = conn.execute("SELECT value FROM settings WHERE key='default_fund_cents'").fetchone()
    return int(row["value"]) if row else 10000


def default_reserve_cents(conn=None):
    row = (conn or get_db()).execute("SELECT value FROM settings WHERE key='default_reserve_cents'").fetchone()
    return int(row["value"]) if row else 10000


def _create_tasks(conn, workday_id):
    for kind in ("open", "close"):
        conn.execute("INSERT INTO workday_tasks(workday_id,kind,position,text) "
                     "SELECT ?, c.kind, i.position, i.text FROM checklist_items i JOIN checklists c ON c.id=i.checklist_id "
                     "WHERE c.kind=? AND i.active=1 ORDER BY i.position, i.id", (workday_id, kind))


def open_workday(fund_text=None, day=None):
    conn = get_db()
    fund = parse_bs(fund_text) if fund_text not in (None, "") else default_fund_cents(conn)
    day = day or date.today().isoformat()
    with transaction(conn):
        if get_open(conn):
            raise BusinessRuleError("Ya hay una jornada abierta.")
        if conn.execute("SELECT 1 FROM workdays WHERE work_date=?", (day,)).fetchone():
            raise BusinessRuleError("Ya existe una jornada de hoy (cerrada). Puedes reabrirla desde Jornada.")
        wid = conn.execute("INSERT INTO workdays(work_date, opening_fund_cents, reserve_cents) VALUES (?,?,?)",
                           (day, fund, default_reserve_cents(conn))).lastrowid
        _create_tasks(conn, wid)
        audit(conn, "abrir", "jornada", wid, f"fondo {fund}")
    return wid


def reopen_workday(workday_id):
    """Solo la jornada más reciente se puede reabrir (para no mezclar días) y nunca habiendo otra abierta."""
    conn = get_db()
    with transaction(conn):
        wd = get(workday_id, conn)
        if wd["status"] != "closed":
            raise BusinessRuleError("La jornada ya está abierta.")
        if get_open(conn):
            raise BusinessRuleError("Hay otra jornada abierta; ciérrala primero.")
        if latest(conn)["id"] != wd["id"]:
            raise BusinessRuleError("Solo se puede reabrir la jornada más reciente.")
        conn.execute("UPDATE workdays SET status='open', closed_at=NULL WHERE id=?", (workday_id,))
        audit(conn, "reabrir", "jornada", workday_id)


# ---------- tareas de la jornada ----------
def get_tasks(workday_id):
    out = {"open": [], "close": []}
    for r in get_db().execute("SELECT * FROM workday_tasks WHERE workday_id=? ORDER BY position, id", (workday_id,)):
        out[r["kind"]].append(dict(r))
    return out


def progress(tasks):
    return sum(1 for t in tasks if t["done"]), len(tasks)


def set_task(task_id, done):
    conn = get_db()
    with transaction(conn):
        row = conn.execute("SELECT t.id, w.status FROM workday_tasks t JOIN workdays w ON w.id=t.workday_id WHERE t.id=?",
                           (task_id,)).fetchone()
        if not row:
            raise NotFoundError("La tarea no existe.")
        if row["status"] != "open":
            raise BusinessRuleError("La jornada está cerrada; reábrela para cambiar tareas.")
        conn.execute("UPDATE workday_tasks SET done=?, done_at=CASE WHEN ?=1 THEN datetime('now','localtime') END WHERE id=?",
                     (int(bool(done)), int(bool(done)), task_id))


# ---------- plantillas de checklist (afectan a las jornadas que se abran después) ----------
def template_items(kind):
    return get_db().execute("SELECT i.* FROM checklist_items i JOIN checklists c ON c.id=i.checklist_id "
                            "WHERE c.kind=? ORDER BY i.position, i.id", (kind,)).fetchall()


def template_action(action, kind=None, item_id=None, text=""):
    conn = get_db()
    with transaction(conn):
        if action == "add":
            text = clean(text, "La tarea", required=True, max_len=120)
            cl = conn.execute("SELECT id FROM checklists WHERE kind=?", (kind,)).fetchone()
            if not cl:
                raise ValidationError("Lista no válida.")
            pos = conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM checklist_items WHERE checklist_id=?", (cl["id"],)).fetchone()[0]
            conn.execute("INSERT INTO checklist_items(checklist_id,position,text) VALUES (?,?,?)", (cl["id"], pos, text))
            return
        item = conn.execute("SELECT * FROM checklist_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            raise NotFoundError("La tarea no existe.")
        if action == "rename":
            conn.execute("UPDATE checklist_items SET text=? WHERE id=?", (clean(text, "La tarea", required=True, max_len=120), item_id))
        elif action in ("hide", "show"):
            conn.execute("UPDATE checklist_items SET active=? WHERE id=?", (int(action == "show"), item_id))
        elif action in ("up", "down"):
            ids = [r["id"] for r in conn.execute("SELECT id FROM checklist_items WHERE checklist_id=? ORDER BY position, id", (item["checklist_id"],))]
            i = ids.index(item_id)
            j = i - 1 if action == "up" else i + 1
            if 0 <= j < len(ids):
                ids[i], ids[j] = ids[j], ids[i]
                for pos, iid in enumerate(ids, 1):
                    conn.execute("UPDATE checklist_items SET position=? WHERE id=?", (pos, iid))
        else:
            raise ValidationError("Acción no válida.")
