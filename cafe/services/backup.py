"""Copias de seguridad y restauración.

Una copia es un .zip en data/backups con:
  - cafe.db       instantánea consistente de la base (API de copia de SQLite, no copia del archivo en uso)
  - uploads/...   las fotos de productos y recetas
  - copia.json    versión del esquema, fecha y conteos (para mostrar y validar)
También se listan las copias automáticas previas a una migración (pre_migracion_*.db, solo base de datos).

Restaurar:
  1. Se valida TODO antes de tocar nada (zip seguro, base íntegra, versión compatible, tablas esperadas).
  2. Se crea una copia automática "antes_de_restaurar" del estado actual.
  3. La base se reemplaza con la API de copia de SQLite (atómico para SQLite) y se aplican migraciones si
     la copia es de una versión anterior.
  4. Las fotos se reemplazan por las de la copia (si la copia es solo .db, las fotos actuales se conservan).
Si algo falla en los pasos 3-4, se vuelve automáticamente al estado del paso 2.
"""
import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from flask import current_app

from .. import db
from ..errors import BusinessRuleError, NotFoundError, ValidationError

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.(zip|db)$")
KIND_LABELS = {"manual": "Manual", "cierre": "Al cerrar jornada", "antes_de_restaurar": "Antes de restaurar",
               "pre_migracion": "Antes de actualizar la app", "subida": "Restaurada desde archivo"}
REQUIRED_TABLES = {"workdays", "orders", "order_items", "payments", "products", "product_variants", "inventory_movements"}
KEEP_AUTOMATIC = 30            # copias automáticas al cerrar jornada que se conservan
MAX_UPLOAD = 200 * 1024 * 1024  # tamaño máximo descomprimido aceptado


def _dirs():
    cfg = current_app.config
    return Path(cfg["DB_PATH"]), Path(cfg["UPLOAD_DIR"]), Path(cfg["BACKUP_DIR"])


def _counts(conn):
    out = {}
    for t in ("workdays", "orders", "products", "recipes", "expenses"):
        try:
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            out[t] = None
    return out


# ---------- crear ----------
def create(kind="manual"):
    """Crea una copia .zip y devuelve su nombre."""
    if kind not in KIND_LABELS:
        raise ValidationError("Tipo de copia no válido.")
    db_path, upload_dir, backup_dir = _dirs()
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"cafe_{kind}_{stamp}.zip"
    n = 1
    while (backup_dir / name).exists():
        n += 1
        name = f"cafe_{kind}_{stamp}_{n}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "cafe.db"
        src = db.connect(db_path)
        dst = sqlite3.connect(str(snap))
        try:
            src.backup(dst)
            version = src.execute("PRAGMA user_version").fetchone()[0]
            counts = _counts(src)
        finally:
            dst.close(); src.close()
        meta = {"app": "Café Personal", "schema_version": version, "created_at": datetime.now().isoformat(timespec="seconds"),
                "kind": kind, "counts": counts}
        partial = backup_dir / (name + ".parcial")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "cafe.db")
            z.writestr("copia.json", json.dumps(meta, ensure_ascii=False, indent=2))
            if upload_dir.exists():
                for f in sorted(upload_dir.rglob("*")):
                    if f.is_file():
                        z.write(f, "uploads/" + f.relative_to(upload_dir).as_posix())
        partial.replace(backup_dir / name)   # la copia solo aparece cuando está completa
    _mirror(backup_dir / name)
    if kind == "cierre":
        _prune("cierre", KEEP_AUTOMATIC)
    return name


def _mirror(path):
    """Guarda la copia también en el segundo lugar configurado. Si falla, la copia principal sigue siendo válida."""
    target = current_app.config.get("BACKUP_COPY_DIR")
    if not target:
        return
    try:
        target = Path(target)
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target / path.name)
    except OSError:
        current_app.logger.exception("No se pudo guardar la copia en %s", target)


def _prune(kind, keep):
    _, _, backup_dir = _dirs()
    target = current_app.config.get("BACKUP_COPY_DIR")
    for folder in filter(None, (backup_dir, Path(target) if target else None)):
        for f in sorted(folder.glob(f"cafe_{kind}_*.zip"), reverse=True)[keep:]:
            f.unlink(missing_ok=True)


# ---------- listar ----------
def _kind_of(name):
    if name.startswith("pre_migracion_"):
        return "pre_migracion"
    m = re.match(r"cafe_(manual|cierre|antes_de_restaurar|subida)_", name)
    return m.group(1) if m else "manual"


def _meta(path):
    if path.suffix == ".db":
        return {"schema_version": None, "counts": {}}
    try:
        with zipfile.ZipFile(path) as z:
            return json.loads(z.read("copia.json").decode("utf-8"))
    except (KeyError, zipfile.BadZipFile, ValueError, OSError):
        return {"schema_version": None, "counts": {}, "damaged": True}


def list_backups():
    _, _, backup_dir = _dirs()
    if not backup_dir.exists():
        return []
    out = []
    for f in backup_dir.iterdir():
        if not f.is_file() or not NAME_RE.match(f.name):
            continue
        st = f.stat()
        kind = _kind_of(f.name)
        meta = _meta(f)
        out.append({"name": f.name, "kind": kind, "label": KIND_LABELS[kind], "size": st.st_size,
                    "size_text": _size(st.st_size), "when": datetime.fromtimestamp(st.st_mtime),
                    "photos": f.suffix == ".zip", "counts": meta.get("counts") or {},
                    "schema_version": meta.get("schema_version"), "damaged": meta.get("damaged", False)})
    out.sort(key=lambda b: b["when"], reverse=True)
    return out


def _size(n):
    return f"{n / 1024 / 1024:.1f} MB".replace(".", ",") if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"


def path_of(name):
    """Ruta segura de una copia (nunca fuera de data/backups)."""
    _, _, backup_dir = _dirs()
    if not name or not NAME_RE.match(name):
        raise NotFoundError("La copia no existe.")
    path = (backup_dir / name).resolve()
    if path.parent != backup_dir.resolve() or not path.is_file():
        raise NotFoundError("La copia no existe.")
    return path


def delete(name):
    path_of(name).unlink()


# ---------- validar ----------
def _validate_db(path):
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        raise ValidationError("El archivo no es una base de datos de Café Personal.") from None
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValidationError("La base de datos de la copia está dañada.")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED_TABLES <= tables:
            raise ValidationError("El archivo no es una base de datos de Café Personal.")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        current = len(db.discover_migrations())
        if version > current:
            raise ValidationError("La copia es de una versión más nueva de la aplicación; actualiza la aplicación primero.")
        if version < 1:
            raise ValidationError("La copia no tiene una versión de esquema válida.")
        if list(conn.execute("PRAGMA foreign_key_check")):
            raise ValidationError("La copia tiene referencias rotas entre tablas.")
        return version
    except sqlite3.DatabaseError:
        raise ValidationError("El archivo no es una base de datos de Café Personal.") from None
    finally:
        conn.close()


def _extract(source, workdir):
    """Deja en `workdir` cafe.db y (si viene) uploads/. Devuelve (ruta_db, ruta_uploads_o_None)."""
    workdir = Path(workdir)
    if source.suffix == ".db":
        dest = workdir / "cafe.db"
        shutil.copyfile(source, dest)
        return dest, None
    try:
        z = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError):
        raise ValidationError("El archivo no es una copia válida (.zip dañado).") from None
    with z:
        names = z.namelist()
        if "cafe.db" not in names:
            raise ValidationError("La copia no contiene la base de datos (cafe.db).")
        total = 0
        for info in z.infolist():
            parts = Path(info.filename).parts
            if info.filename.startswith(("/", "\\")) or ".." in parts or ":" in info.filename:
                raise ValidationError("La copia contiene rutas no permitidas.")
            if info.filename != "cafe.db" and info.filename != "copia.json" and not info.filename.startswith("uploads/"):
                raise ValidationError("La copia contiene archivos inesperados.")
            total += info.file_size
            if total > MAX_UPLOAD:
                raise ValidationError("La copia es demasiado grande.")
        z.extractall(workdir)
    uploads = workdir / "uploads"
    uploads.mkdir(exist_ok=True)   # una copia completa sin fotos deja la carpeta de fotos vacía
    return workdir / "cafe.db", uploads


# ---------- restaurar ----------
def restore(name=None, uploaded=None):
    """Restaura desde una copia guardada (`name`) o desde un archivo subido (FileStorage)."""
    db_path, upload_dir, backup_dir = _dirs()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if uploaded is not None:
            fname = (uploaded.filename or "").lower()
            if not fname.endswith((".zip", ".db")):
                raise ValidationError("Elige un archivo .zip (copia completa) o .db (solo base de datos).")
            source = tmp / ("subida" + (".zip" if fname.endswith(".zip") else ".db"))
            uploaded.save(source)
        else:
            source = path_of(name)
        work = tmp / "contenido"
        work.mkdir()
        new_db, new_uploads = _extract(source, work)
        _validate_db(new_db)

        safety = create("antes_de_restaurar")
        try:
            _replace_db(new_db, db_path)
            db.migrate(db_path, backup_dir)
            check = db.connect(db_path)
            try:
                problems = db.check_integrity(check)
            finally:
                check.close()
            if problems:
                raise BusinessRuleError("La base restaurada no superó la comprobación de integridad.")
            if new_uploads is not None:
                _replace_uploads(new_uploads, upload_dir)
        except Exception:
            _rollback(safety)
            raise
    return safety


def _replace_db(new_db, db_path):
    src = sqlite3.connect(str(new_db))
    dst = db.connect(db_path)
    try:
        src.backup(dst)   # copia página a página dentro de una transacción de SQLite
    finally:
        src.close(); dst.close()


def _replace_uploads(new_uploads, upload_dir):
    upload_dir = Path(upload_dir)
    staging = upload_dir.with_name("uploads_restaurando")
    old = upload_dir.with_name("uploads_anterior")
    for p in (staging, old):
        if p.exists():
            shutil.rmtree(p)
    shutil.copytree(new_uploads, staging)
    if upload_dir.exists():
        upload_dir.rename(old)
    staging.rename(upload_dir)
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)


def _rollback(safety_name):
    db_path, upload_dir, _ = _dirs()
    with tempfile.TemporaryDirectory() as tmp:
        new_db, new_uploads = _extract(path_of(safety_name), tmp)
        _replace_db(new_db, db_path)
        if new_uploads is not None:
            _replace_uploads(new_uploads, upload_dir)
