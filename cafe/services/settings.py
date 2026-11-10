"""Configuración editable (tabla settings)."""
from ..db import audit, get_db, transaction
from ..errors import ValidationError
from ..money import parse_bs

EDITABLE = {"default_fund_cents": "Fondo inicial por defecto", "default_reserve_cents": "Reserva para la siguiente jornada"}
DEFAULTS = {"default_fund_cents": 10000, "default_reserve_cents": 10000}
MAX_CENTS = 10_000_00   # Bs 10.000


def get_all():
    rows = {r["key"]: r["value"] for r in get_db().execute("SELECT key, value FROM settings")}
    return {k: int(rows.get(k, DEFAULTS[k])) for k in EDITABLE}


def update(values):
    """values: {clave: texto en Bs}. Valida todo antes de guardar."""
    parsed = {}
    for key, label in EDITABLE.items():
        if key not in values:
            continue
        text = (values.get(key) or "").strip()
        if not text:
            raise ValidationError(f"{label}: escribe un importe.")
        try:
            cents = parse_bs(text)
        except ValidationError as exc:
            raise ValidationError(f"{label}: {exc.message}") from None
        if cents > MAX_CENTS:
            raise ValidationError(f"{label}: el importe es demasiado grande.")
        parsed[key] = cents
    conn = get_db()
    with transaction(conn):
        for key, cents in parsed.items():
            conn.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, str(cents)))
        if parsed:
            audit(conn, "modificar", "configuracion", None, ", ".join(f"{k}={v}" for k, v in parsed.items()))
    return parsed
