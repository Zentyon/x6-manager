"""Tests del cambio automatico de perfil (sin hardware ni ventanas reales)."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from x6core.appwatch import AutoSwitcher, Window, matches


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeDaemon:
    def __init__(self, rules: dict[str, list[str]], active: str = "base",
                 enabled: bool = True) -> None:
        self.profiles = {
            name: SimpleNamespace(auto_apps=patterns) for name, patterns in rules.items()
        }
        self.active = active
        self.auto_apps_enabled = enabled
        self.switches: list[str] = []
        self.listeners: list = []

    def subscribe(self, callback) -> None:
        self.listeners.append(callback)

    def unsubscribe(self, callback) -> None:
        if callback in self.listeners:
            self.listeners.remove(callback)

    def switch(self, name: str, *, source: str = "user") -> bool:
        self.switches.append(name)
        self.active = name
        for callback in list(self.listeners):
            callback("switch", name=name, source=source)
        return True


class MatchesTest(unittest.TestCase):
    def test_nombre_del_ejecutable(self):
        self.assertTrue(matches("notepad.exe", Window("notepad.exe")))
        self.assertFalse(matches("notepad.exe", Window("other.exe")))

    def test_sin_extension(self):
        self.assertTrue(matches("notepad", Window("notepad.exe")))
        self.assertTrue(matches("chrome", Window("chrome.exe")))

    def test_mayusculas(self):
        self.assertTrue(matches("NotePad.EXE", Window("notepad.exe")))

    def test_comodines(self):
        self.assertTrue(matches("valorant*", Window("valorant.exe")))
        self.assertTrue(matches("*team*", Window("steamwebhelper.exe")))

    def test_titulo(self):
        window = Window("chrome.exe", "YouTube - Google Chrome")
        self.assertTrue(matches("title:*youtube*", window))
        self.assertFalse(matches("title:*youtube*", Window("chrome.exe", "GitHub")))

    def test_vacio(self):
        self.assertFalse(matches("", Window("notepad.exe")))
        self.assertFalse(matches("notepad", None))
        self.assertFalse(matches("notepad", Window()))


class SwitcherTest(unittest.TestCase):
    def _switcher(self, rules, active="base", enabled=True, settle=1.0):
        daemon = FakeDaemon(rules, active=active, enabled=enabled)
        clock = Clock()
        return AutoSwitcher(daemon, settle=settle, clock=clock), daemon, clock

    def test_cambia_tras_el_settle(self):
        switcher, daemon, clock = self._switcher(
            {"base": [], "game": ["game.exe"]}
        )
        window = Window("game.exe")
        self.assertIsNone(switcher.poll(window))       # primer avistamiento
        clock.advance(0.5)
        self.assertIsNone(switcher.poll(window))       # aun no
        clock.advance(0.6)
        self.assertEqual(switcher.poll(window), "game")
        self.assertEqual(daemon.switches, ["game"])

    def test_no_repite_el_mismo_perfil(self):
        switcher, daemon, clock = self._switcher({"base": [], "game": ["game.exe"]})
        window = Window("game.exe")
        switcher.poll(window)
        clock.advance(2)
        switcher.poll(window)
        clock.advance(2)
        switcher.poll(window)
        self.assertEqual(daemon.switches, ["game"])

    def test_vuelve_al_perfil_base(self):
        switcher, daemon, clock = self._switcher({"base": [], "game": ["game.exe"]})
        game = Window("game.exe")
        switcher.poll(game)
        clock.advance(2)
        switcher.poll(game)

        desktop = Window("explorer.exe")
        switcher.poll(desktop)                        # cambia la ventana
        clock.advance(2)
        self.assertEqual(switcher.poll(desktop), "base")
        self.assertEqual(daemon.switches, ["game", "base"])

    def test_alt_tab_rapido_no_cambia(self):
        switcher, daemon, clock = self._switcher({"base": [], "game": ["game.exe"]})
        game = Window("game.exe")
        other = Window("explorer.exe")
        switcher.poll(game)
        clock.advance(0.2)
        switcher.poll(other)          # se va antes de asentarse
        clock.advance(0.2)
        switcher.poll(game)
        clock.advance(2)
        switcher.poll(game)
        self.assertEqual(daemon.switches, ["game"])

    def test_desactivado(self):
        switcher, daemon, clock = self._switcher(
            {"base": [], "game": ["game.exe"]}, enabled=False
        )
        window = Window("game.exe")
        switcher.poll(window)
        clock.advance(5)
        self.assertIsNone(switcher.poll(window))
        self.assertEqual(daemon.switches, [])

    def test_gano_la_primera_regla_por_orden(self):
        switcher, _daemon, _clock = self._switcher(
            {"a": ["game.exe"], "b": ["game*"]}
        )
        self.assertEqual(switcher.target_for(Window("game.exe")), "a")

    def test_perfil_base_desconocido(self):
        switcher, daemon, clock = self._switcher(
            {"game": ["game.exe"]}, active="borrado"
        )
        switcher.poll(Window("explorer.exe"))
        clock.advance(2)
        self.assertIsNone(switcher.poll(Window("explorer.exe")))
        self.assertEqual(daemon.switches, [])

    def test_cambio_manual_pasa_a_ser_la_base(self):
        switcher, daemon, clock = self._switcher(
            {"base": [], "game": ["game.exe"], "manual": []}
        )
        daemon.switch("manual")                 # eleccion del usuario
        self.assertEqual(switcher.base, "manual")

        game = Window("game.exe")
        switcher.poll(game)
        clock.advance(2)
        self.assertEqual(switcher.poll(game), "game")

        desktop = Window("explorer.exe")
        switcher.poll(desktop)
        clock.advance(2)
        self.assertEqual(switcher.poll(desktop), "manual")
        self.assertEqual(daemon.switches, ["manual", "game", "manual"])

    def test_cambio_auto_no_pisa_la_base(self):
        switcher, daemon, _clock = self._switcher(
            {"base": [], "game": ["game.exe"]}
        )
        daemon.switch("game", source="auto")
        self.assertEqual(switcher.base, "base")


if __name__ == "__main__":
    unittest.main()
