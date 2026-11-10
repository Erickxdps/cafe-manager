"""Notas personales: indicaciones recibidas, recordatorios y tareas (sin calendario)."""
from ..db import audit, get_db, transaction
from ..errors import NotFoundError, ValidationError
from .common import clean, normalize

KINDS = {"note": "Nota / indicación", "reminder": "Recordatorio", "task": "Tarea"}
KINDS_PLURAL = {"note": "Notas", "reminder": "Recordatorios", "task": "Tareas"}


def list_notes(kind=None, estado="pendientes", q=""):
    sql, args = "SELECT * FROM personal_notes WHERE 1=1", []
    if kind in KINDS:
        sql += " AND kind=?"; args.append(kind)
    if estado == "pendientes":
        sql += " AND done=0"
    elif estado == "hechas":
        sql += " AND done=1"
    rows = [dict(r) for r in get_db().execute(
        sql + " ORDER BY done, CASE kind WHEN 'reminder' THEN 0 WHEN 'task' THEN 1 ELSE 2 END, id DESC", args)]
    nq = normalize(q)
    if nq:
        rows = [r for r in rows if nq in normalize(r["title"] + " " + r["body"])]
    return rows


def pending(limit=5):
    """Recordatorios y tareas sin completar (para el panel principal)."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM personal_notes WHERE done=0 AND kind IN ('reminder','task') "
                        "ORDER BY CASE kind WHEN 'reminder' THEN 0 ELSE 1 END, id DESC LIMIT ?", (limit,)).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM personal_notes WHERE done=0 AND kind IN ('reminder','task')").fetchone()[0]
    return [dict(r) for r in rows], total


def get(note_id):
    row = get_db().execute("SELECT * FROM personal_notes WHERE id=?", (note_id,)).fetchone()
    if not row:
        raise NotFoundError("La nota no existe.")
    return dict(row)


def _validated(kind, title, body):
    if kind not in KINDS:
        raise ValidationError("Elige si es una nota, un recordatorio o una tarea.")
    return kind, clean(title, "El título", required=True, max_len=120), clean(body, "El detalle", max_len=4000)


def save(note_id, kind, title, body=""):
    kind, title, body = _validated(kind, title, body)
    conn = get_db()
    with transaction(conn):
        if note_id:
            if not conn.execute("SELECT 1 FROM personal_notes WHERE id=?", (note_id,)).fetchone():
                raise NotFoundError("La nota no existe.")
            conn.execute("UPDATE personal_notes SET kind=?, title=?, body=? WHERE id=?", (kind, title, body, note_id))
            audit(conn, "modificar", "nota", note_id, title)
        else:
            note_id = conn.execute("INSERT INTO personal_notes(kind,title,body) VALUES (?,?,?)", (kind, title, body)).lastrowid
            audit(conn, "crear", "nota", note_id, title)
    return note_id


def set_done(note_id, done):
    conn = get_db()
    with transaction(conn):
        if not conn.execute("SELECT 1 FROM personal_notes WHERE id=?", (note_id,)).fetchone():
            raise NotFoundError("La nota no existe.")
        conn.execute("UPDATE personal_notes SET done=? WHERE id=?", (int(bool(done)), note_id))


def delete(note_id):
    conn = get_db()
    with transaction(conn):
        row = conn.execute("SELECT title FROM personal_notes WHERE id=?", (note_id,)).fetchone()
        if not row:
            raise NotFoundError("La nota no existe.")
        conn.execute("DELETE FROM personal_notes WHERE id=?", (note_id,))
        audit(conn, "eliminar", "nota", note_id, row["title"])
