import ipaddress
import logging
from datetime import date, timedelta
from logging.handlers import RotatingFileHandler

from urllib.parse import urlparse

from flask import Flask, abort, request, url_for
from markupsafe import Markup, escape

from . import config, db
from .money import format_bs


DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def _as_date(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _fecha(value):
    """2026-11-07 -> 07/11/2026"""
    if not value:
        return ""
    return _as_date(value).strftime("%d/%m/%Y")


def _dia(value):
    return DIAS[_as_date(value).weekday()]


def _ip_privada(valor):
    try:
        return ipaddress.ip_address(valor).is_private
    except ValueError:
        return False


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(config.load())
    if test_config and test_config.get("TESTING"):
        app.config["BACKUP_COPY_DIR"] = None   # las pruebas nunca escriben en la carpeta real de copias
    if test_config:
        app.config.update(test_config)

    data_dir = app.config["DATA_DIR"]
    app.config.update(
        DB_PATH=data_dir / "cafe.db",
        UPLOAD_DIR=data_dir / "uploads",
        BACKUP_DIR=data_dir / "backups",
        LOG_DIR=data_dir / "logs",
    )
    for key in ("DATA_DIR", "UPLOAD_DIR", "BACKUP_DIR", "LOG_DIR"):
        app.config[key].mkdir(parents=True, exist_ok=True)
    app.secret_key = config.load_secret(data_dir)

    if not app.config.get("TESTING"):
        handler = RotatingFileHandler(app.config["LOG_DIR"] / "cafe.log",
                                      maxBytes=512_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        app.logger.addHandler(handler)
        app.logger.setLevel(logging.INFO)

    db.migrate(app.config["DB_PATH"], app.config["BACKUP_DIR"])
    app.teardown_appcontext(db.close_db)
    app.jinja_env.filters["bs"] = format_bs
    app.jinja_env.filters["fecha"] = _fecha
    app.jinja_env.globals.update(dia=_dia, pct=lambda v: f"{v:.1f} %".replace(".", ","), timedelta=timedelta)

    def icono(name, cls=""):
        """Ícono del sprite local (static/img/iconos.svg). Decorativo: el texto de al lado da el significado."""
        href = url_for("static", filename="img/iconos.svg") + "#i-" + escape(name)
        return Markup(f'<svg class="ico {escape(cls)}" aria-hidden="true" focusable="false"><use href="{href}"></use></svg>')
    app.jinja_env.globals["icono"] = icono

    @app.context_processor
    def hoy():
        return {"today": date.today()}
    from .services import workdays as _workdays
    app.jinja_env.globals["progress"] = _workdays.progress   # visible también desde macros importados
    app.jinja_env.filters["inicial"] = lambda text: next((c.upper() for c in text if c.isalnum()), "·")

    @app.context_processor
    def caja_en_la_barra():
        """El efectivo esperado se muestra en todas las pantallas mientras haya jornada abierta."""
        try:
            from .services import caja as caja_service, workdays
            conn = db.get_db()
            wd = workdays.get_open(conn)
            return {"caja_total": caja_service.totals(conn, wd["id"]) if wd else None, "jornada_abierta": wd,
                    "jornada_vencida": bool(wd) and wd["work_date"] < date.today().isoformat()}
        except Exception:  # una pantalla de error no debe fallar por esto
            return {"caja_total": None, "jornada_abierta": None, "jornada_vencida": False}

    @app.before_request
    def local_requests_only():
        """Solo red privada, y evita que otra página web envíe formularios a esta app (CSRF / DNS rebinding)."""
        host = request.host.split(":")[0]
        if not _ip_privada(request.remote_addr) or not (host == "localhost" or _ip_privada(host)):
            abort(403)
        origin = request.headers.get("Origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and urlparse(origin).netloc != request.host:
            abort(403)

    from .routes import caja, catalog, config as config_routes, inventory, main, notes, orders, promotions, recipes, reports, workday
    for module in (main, catalog, recipes, orders, promotions, inventory, caja, workday, reports, notes, config_routes):
        app.register_blueprint(module.bp)
    main.register_error_handlers(app)
    return app
