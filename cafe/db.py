import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from flask import current_app, g

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(path):
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10)  # autocommit; transacciones explícitas
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DB_PATH"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@contextmanager
def transaction(conn=None):
    """BEGIN IMMEDIATE ... COMMIT; ROLLBACK ante cualquier excepción. No admite anidamiento."""
    conn = conn or get_db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def discover_migrations():
    found = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        found.append((int(path.name.split("_")[0]), path))
    numbers = [n for n, _ in found]
    if numbers != list(range(1, len(numbers) + 1)):
        raise RuntimeError(f"Migraciones no consecutivas: {numbers}")
    return found


def migrate(db_path, backup_dir):
    """Aplica migraciones pendientes (control con PRAGMA user_version).
    Cada una corre en su propia transacción; antes de actualizar una BD con datos se guarda copia."""
    conn = connect(db_path)
    try:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        migrations = discover_migrations()
        if current > len(migrations):
            raise RuntimeError("La base de datos es más nueva que esta versión de la aplicación.")
        pending = [(n, p) for n, p in migrations if n > current]
        if pending and current > 0:
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = sqlite3.connect(str(backup_dir / f"pre_migracion_v{current}_{stamp}.db"))
            conn.backup(dest)
            dest.close()
        for number, path in pending:
            sql = path.read_text(encoding="utf-8")
            try:
                conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
    finally:
        conn.close()


def check_integrity(conn):
    """Devuelve lista de problemas (vacía si todo está bien)."""
    problems = [r[0] for r in conn.execute("PRAGMA integrity_check") if r[0] != "ok"]
    problems += [f"FK rota en {r[0]}" for r in conn.execute("PRAGMA foreign_key_check")]
    return problems


def audit(conn, action, entity, entity_id=None, detail=""):
    """Registro de movimientos relevantes (se llama dentro de la misma transacción)."""
    conn.execute("INSERT INTO audit_log(action, entity, entity_id, detail) VALUES (?,?,?,?)",
                 (action, entity, entity_id, detail))
