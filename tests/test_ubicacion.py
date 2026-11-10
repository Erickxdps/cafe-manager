"""Ubicación de los datos y segundo lugar para las copias."""
import tempfile
import unittest
from pathlib import Path

from cafe import config, create_app
from cafe.services import backup


class TestUbicacion(unittest.TestCase):
    def test_lee_el_archivo_de_ubicacion(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "u.txt"
            f.write_text("# comentario\ndatos=C:\\CafeDatos\ncopias=D:\\Otra carpeta\n", encoding="utf-8")
            self.assertEqual(config._ubicaciones(f), {"datos": Path("C:\\CafeDatos"), "copias": Path("D:\\Otra carpeta")})
            self.assertEqual(config._ubicaciones(Path(tmp) / "no_existe.txt"), {})

    def test_las_pruebas_nunca_usan_la_carpeta_real(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            app = create_app({"DATA_DIR": Path(tmp), "TESTING": True})
            self.assertIsNone(app.config["BACKUP_COPY_DIR"])


class TestCopiaDoble(unittest.TestCase):
    def preparar(self, tmp, destino):
        app = create_app({"DATA_DIR": Path(tmp) / "d", "TESTING": True})
        app.config["BACKUP_COPY_DIR"] = destino
        return app

    def test_la_copia_llega_a_los_dos_lugares(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            segundo = Path(tmp) / "segundo"
            with self.preparar(tmp, segundo).app_context():
                nombre = backup.create("manual")
            self.assertTrue((Path(tmp) / "d" / "backups" / nombre).exists())
            self.assertTrue((segundo / nombre).exists())

    def test_si_falla_el_segundo_lugar_la_copia_sigue(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            archivo = Path(tmp) / "no_es_carpeta"
            archivo.write_text("x")
            with self.preparar(tmp, archivo).app_context():
                nombre = backup.create("manual")
            self.assertTrue((Path(tmp) / "d" / "backups" / nombre).exists())

    def test_conserva_solo_las_ultimas_en_el_segundo_lugar(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            segundo = Path(tmp) / "segundo"
            segundo.mkdir()
            for i in range(backup.KEEP_AUTOMATIC + 3):
                (segundo / f"cafe_cierre_20260101_0000{i:02d}.zip").write_text("x")
            ajena = segundo / "data_antes_de_mover_1.zip"
            ajena.write_text("x")
            with self.preparar(tmp, segundo).app_context():
                backup.create("cierre")
            self.assertEqual(len(list(segundo.glob("cafe_cierre_*.zip"))), backup.KEEP_AUTOMATIC)
            self.assertTrue(ajena.exists())


if __name__ == "__main__":
    unittest.main()
