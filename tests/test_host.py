"""Tests del pegamento host <-> backend de teclado (sin hook ni hardware)."""
from __future__ import annotations

import unittest

from x6core.backends.pynput_backend import _VK_NAMES
from x6core.host import key_triggers_for
from x6core.profiles import Profile


class KeyTriggersTest(unittest.TestCase):
    def test_lateral_no_escucha_el_teclado(self):
        profile = Profile()
        profile.shift_button = "backward"
        self.assertEqual(key_triggers_for(profile), ())

    def test_tecla_del_rango_del_firmware(self):
        for name in ("9", "0", "a"):
            profile = Profile()
            profile.shift_button = name
            self.assertEqual(key_triggers_for(profile), (name,))

    def test_tecla_fuera_del_rango_no_se_escucha(self):
        # el descriptor del dongle acaba en 0x2F: F13/Scroll Lock nunca llegan
        for name in ("f13", "scroll_lock", "pause"):
            profile = Profile()
            profile.shift_button = name
            self.assertEqual(key_triggers_for(profile), ())

    def test_vk_solo_letras_y_digitos(self):
        self.assertEqual(_VK_NAMES[0x39], "9")
        self.assertEqual(_VK_NAMES[0x5A], "z")
        self.assertNotIn(0x70, _VK_NAMES)  # F1
        self.assertNotIn(0x2F, _VK_NAMES)  # no es un VK


if __name__ == "__main__":
    unittest.main()
