import io
import unicodedata
import uuid

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

from ..errors import ValidationError


def normalize(text):
    """Minúsculas y sin acentos, para búsquedas tolerantes."""
    text = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in text if unicodedata.category(c) != "Mn").lower().strip()


def clean(value, label, required=False, max_len=200):
    value = (value or "").strip()
    if required and not value:
        raise ValidationError(f"{label} es obligatorio.")
    if len(value) > max_len:
        raise ValidationError(f"{label} admite como máximo {max_len} caracteres.")
    return value


def save_photo(file_storage, subdir):
    """Valida que sea una imagen real, la reduce a 480 px y la guarda como JPEG dentro de data/uploads."""
    if not file_storage or not file_storage.filename:
        return None
    raw = file_storage.read(current_app.config.get("MAX_PHOTO_BYTES", 5 * 1024 * 1024) + 1)
    if len(raw) > current_app.config.get("MAX_PHOTO_BYTES", 5 * 1024 * 1024):
        raise ValidationError("La foto supera los 5 MB; elige una más liviana.")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValidationError("El archivo no es una imagen válida (usa JPG, PNG o WEBP).") from None
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((480, 480))
    name = f"{subdir}/{uuid.uuid4().hex}.jpg"
    path = current_app.config["UPLOAD_DIR"] / name
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=85)
    return name


def delete_photo(name):
    if not name:
        return
    root = current_app.config["UPLOAD_DIR"].resolve()
    path = (root / name).resolve()
    if root in path.parents and path.is_file():
        path.unlink()
