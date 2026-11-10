"""Dinero siempre como enteros en centavos; nunca float."""
import re
from decimal import Decimal, InvalidOperation

from .errors import ValidationError

MILES_RE = re.compile(r"\d{1,3}(\.\d{3})+")
MAX_BS = 100_000_000


def parse_bs(value, allow_negative=False) -> int:
    if isinstance(value, bool):
        raise ValidationError("Importe no válido.")
    text = str(value).strip().lower().replace("bs", "").replace(" ", "")
    if "," in text and "." in text:  # 1.250,50 -> 1250.50
        text = text.replace(".", "").replace(",", ".")
    elif MILES_RE.fullmatch(text):   # 1.250 -> 1250: los miles se muestran con punto
        text = text.replace(".", "")
    else:
        text = text.replace(",", ".")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValidationError("Importe no válido.") from None
    if not amount.is_finite():
        raise ValidationError("Importe no válido.")
    if abs(amount) > MAX_BS:
        raise ValidationError("El importe es demasiado grande.")
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise ValidationError("El importe admite como máximo dos decimales.")
    if cents < 0 and not allow_negative:
        raise ValidationError("El importe no puede ser negativo.")
    return int(cents)


def format_bs(cents) -> str:
    cents = int(cents or 0)
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    whole_txt = f"{whole:,}".replace(",", ".")
    return f"{sign}Bs {whole_txt}" + (f",{frac:02d}" if frac else "")


def to_input(cents) -> str:
    """Texto para campos de formulario: 800 -> '8', 850 -> '8,50'."""
    whole, frac = divmod(int(cents or 0), 100)
    return f"{whole},{frac:02d}" if frac else str(whole)
