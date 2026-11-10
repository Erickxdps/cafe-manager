import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
HOST = "0.0.0.0"  # accesible en la red local; cafe.create_app rechaza lo que no venga de una red privada
PORT = 31847


def _ubicaciones(path=None):
    """Lee ubicacion_datos.txt (líneas clave=ruta) para que la app funcione desde cualquier consola."""
    path = path or BASE_DIR / "ubicacion_datos.txt"
    if not path.exists():
        return {}
    pares = (line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines()
             if "=" in line and not line.startswith("#"))
    return {clave.strip(): Path(ruta.strip()) for clave, ruta in pares}


def load():
    lugares = _ubicaciones()
    return {
        "DATA_DIR": Path(os.environ.get("CAFE_DATA_DIR") or lugares.get("datos") or BASE_DIR / "data"),
        "BACKUP_COPY_DIR": lugares.get("copias"),  # segundo lugar para las copias .zip (p. ej. OneDrive)
        "PORT": int(os.environ.get("CAFE_PORT", PORT)),
        "MAX_CONTENT_LENGTH": 210 * 1024 * 1024,  # permite restaurar copias grandes; las fotos se limitan aparte
        "MAX_PHOTO_BYTES": 5 * 1024 * 1024,
        "DEFAULT_FUND_CENTS": 10000,
    }


def load_secret(data_dir: Path) -> str:
    """Clave de sesión persistente (necesaria para mensajes flash)."""
    path = data_dir / "secret.key"
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()
