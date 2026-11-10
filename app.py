"""Punto de entrada: python app.py  (escucha en la red local, solo HTTP)."""
import sys

from cafe import config, create_app


def main():
    app = create_app()
    port = app.config["PORT"]
    print(f"Café Personal en http://localhost:{port}  (Ctrl+C para detener)")
    try:
        app.run(host=config.HOST, port=port, debug=False, threaded=True)
    except OSError as exc:
        print(f"No se pudo usar el puerto {port}: {exc}\n"
              f"Cierra la otra instancia o define CAFE_PORT con otro número.")
        sys.exit(1)


if __name__ == "__main__":
    main()
