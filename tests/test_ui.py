"""Tests de la UI Qt (sin ventana real: plataforma `offscreen`).

No tocan el hardware ni el directorio de configuracion del usuario: `profiles_dir`
se parchea a un directorio temporal y el daemon es un doble en memoria.
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:  # pragma: no cover - PySide6 es opcional
    from PySide6.QtWidgets import QApplication

    import app.ui as ui

    HAS_QT = ui.HAS_QT
except ImportError:  # pragma: no cover
    HAS_QT = False


class FakeDaemon:
    def __init__(self, profiles: dict | None = None, active: str = "FPS"):
        from x6core.profiles import Profile

        self.profiles = profiles or {"FPS": Profile(name="FPS")}
        self.active = active if active in self.profiles else sorted(self.profiles)[0]
        self.auto_apps_enabled = True
        self.switched: list[str] = []
        self.listeners: list = []

    def subscribe(self, callback) -> None:
        self.listeners.append(callback)

    def unsubscribe(self, callback) -> None:
        if callback in self.listeners:
            self.listeners.remove(callback)

    def set_auto_apps(self, enabled: bool) -> None:
        self.auto_apps_enabled = bool(enabled)

    def switch(self, name: str) -> bool:
        self.switched.append(name)
        self.active = name
        return True

    def reload(self) -> None:
        pass

    def emit(self, event: str, **payload) -> None:
        for callback in list(self.listeners):
            callback(event, **payload)


@unittest.skipUnless(HAS_QT, "PySide6 no instalado")
class UiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir_patch = mock.patch(
            "app.ui.profiles_dir", lambda: pathlib.Path(self.tmp.name)
        )
        self.dir_patch.start()
        self.addCleanup(self.dir_patch.stop)

    def _window(self, daemon=None):
        daemon = daemon or FakeDaemon()
        window = ui.MainWindow(daemon)
        self.addCleanup(window.daemon.unsubscribe, window._forward_event)
        return window, daemon

    def test_carga_el_perfil_activo(self):
        window, _daemon = self._window()
        self.assertEqual(window._current_name, "FPS")
        self.assertEqual(window.stage_list.count(), 6)
        self.assertIn("forward", window._button_edits)
        self.assertEqual(window.button_table.rowCount(), 6)

    def test_cambiar_de_perfil_no_aplica(self):
        from x6core.profiles import Profile

        daemon = FakeDaemon({"FPS": Profile(name="FPS"),
                             "Game": Profile(name="Game")}, active="FPS")
        window, _daemon = self._window(daemon)
        index = sorted(daemon.profiles).index("Game")
        window.profile_list.setCurrentRow(index)
        self.assertEqual(window._current_name, "Game")
        self.assertEqual(daemon.switched, [])

    def test_guardar_escribe_json(self):
        window, daemon = self._window()
        window.stage_dpi.setValue(1600)
        window.stage_active_btn.click()
        saved = window._commit()

        path = pathlib.Path(self.tmp.name) / "FPS.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["dpi_stages"][0], 1600)
        self.assertEqual(saved.active_stage, 0)
        self.assertEqual(daemon.profiles["FPS"].dpi_stages[0], 1600)
        self.assertFalse(window._is_dirty())

    def test_accion_invalida_no_guarda(self):
        from x6core.profiles import ProfileError

        window, _daemon = self._window()
        window._button_edits["left"].setText("inventada")
        with self.assertRaises(ProfileError):
            window._commit()
        self.assertFalse((pathlib.Path(self.tmp.name) / "FPS.json").exists())

    def test_commit_de_capa_gshift(self):
        window, daemon = self._window()
        index = window.shift_combo.findData("forward")
        window.shift_combo.setCurrentIndex(index)
        window.layer_table.cellWidget(0, 1).setText("media:mute")
        saved = window._commit()

        self.assertEqual(saved.shift_button, "forward")
        self.assertEqual(saved.shift_mode, "hold")
        self.assertEqual(saved.shift_layer, "shift")
        self.assertEqual(saved.layers["shift"]["left"].kind, "media")
        self.assertEqual(saved.layers["shift"]["left"].value, "mute")
        self.assertIn("FPS", daemon.profiles)

    def test_capa_vacia_con_disparador_es_valida(self):
        window, _daemon = self._window()
        index = window.shift_combo.findData("forward")
        window.shift_combo.setCurrentIndex(index)
        for edit in window._layer_edits.values():
            edit.setText("")
        saved = window._commit()
        self.assertEqual(saved.layers.get("shift"), {})

    def test_auto_global_actualiza_el_daemon(self):
        window, daemon = self._window()
        self.assertTrue(window.auto_global.isChecked())
        window.auto_global.setChecked(False)
        self.assertFalse(daemon.auto_apps_enabled)

    def test_nombre_visible_distinto_de_la_clave_no_ensucia(self):
        from x6core.profiles import Profile

        daemon = FakeDaemon({"gshift-side": Profile(name="G-Shift Lateral")})
        window, _daemon = self._window(daemon)
        self.assertFalse(window._is_dirty())
        saved = window._commit()
        self.assertEqual(saved.name, "G-Shift Lateral")
        data = json.loads((pathlib.Path(self.tmp.name) / "gshift-side.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "G-Shift Lateral")

    def test_disparador_de_tecla_se_conserva(self):
        from x6core.profiles import Profile

        profile = Profile(name="FPS", shift_button="q", layers={"shift": {}})
        window, _daemon = self._window(FakeDaemon({"FPS": profile}))
        self.assertEqual(window.shift_combo.currentData(), "q")
        saved = window._commit()
        self.assertEqual(saved.shift_button, "q")

    def test_auto_usa_la_ultima_ventana_vista(self):
        from x6core.appwatch import Window

        window, _daemon = self._window()
        window._last_window = Window("reaper.exe", "REAPER")
        window.auto_current_btn.click()
        self.assertEqual(window._auto_patterns(), ["reaper.exe"])

    def test_auto_ignora_su_propia_ventana(self):
        from x6core.appwatch import Window

        window, _daemon = self._window()
        propios = Window(window._own_process, "X6 Manager")
        with mock.patch("app.ui.foreground_window", lambda: propios):
            window._last_window = None
            window.auto_current_btn.click()
        self.assertEqual(window._auto_patterns(), [])
        self.assertIn("X6 Manager", window.status.text())

    def test_patrones_auto_se_guardan(self):
        window, _daemon = self._window()
        window.auto_edit.setText("cs2.exe")
        window.auto_add_btn.click()
        window.auto_edit.setText("title:*YouTube*")
        window.auto_add_btn.click()
        saved = window._commit()
        self.assertEqual(saved.auto_apps, ["cs2.exe", "title:*YouTube*"])

    def test_bateria_en_la_barra(self):
        window, daemon = self._window()
        daemon.emit("battery", percent=73, raw=73)
        self.app.processEvents()
        self.assertIn("73%", window.battery_label.text())
        self.assertIn("73", window.active_label.text())


if __name__ == "__main__":
    unittest.main()
