"""Tests del motor de capas (G-Shift) sin hook ni hardware."""
from __future__ import annotations

import unittest

from x6core.layers import LayerEngine
from x6core.profiles import ButtonAction, Profile


def make_profile(**kwargs) -> Profile:
    profile = Profile("test", **kwargs)
    profile.buttons = {
        "left": ButtonAction("click", "left"),
        "right": ButtonAction("click", "right"),
        "middle": ButtonAction("click", "middle"),
        "forward": ButtonAction("shortcut", "alt+Right"),
        "backward": ButtonAction("shortcut", "alt+Left"),
    }
    return profile


class BaseTest(unittest.TestCase):
    def test_boton_a_si_mismo_es_passthrough(self):
        engine = LayerEngine(make_profile())
        result = engine.handle("left", True)
        self.assertFalse(result.suppress)
        self.assertEqual(result.steps, ())

    def test_boton_sin_mapear_no_se_toca(self):
        profile = make_profile()
        profile.buttons.pop("middle")
        engine = LayerEngine(profile)
        self.assertFalse(engine.handle("middle", True).suppress)
        self.assertFalse(engine.handle("middle", False).suppress)

    def test_remap_inyecta_y_bloquea(self):
        engine = LayerEngine(make_profile())
        down = engine.handle("forward", True)
        self.assertTrue(down.suppress)
        self.assertEqual(down.steps[0].kind, "keys_tap")
        self.assertEqual(down.steps[0].value, "alt+Right")
        # un tap no deja nada pulsado al soltar
        self.assertEqual(engine.handle("forward", False).steps, ())

    def test_off_bloquea_sin_accion(self):
        profile = make_profile()
        profile.buttons["middle"] = ButtonAction("off")
        engine = LayerEngine(profile)
        self.assertTrue(engine.handle("middle", True).suppress)
        self.assertEqual(engine.handle("middle", True).steps, ())


class ShiftLayerTest(unittest.TestCase):
    def setUp(self):
        self.profile = make_profile(shift_button="forward", shift_layer="shift", shift_mode="hold")
        self.profile.layers = {"shift": {
            "backward": ButtonAction("shortcut", "ctrl+c"),
            "left": ButtonAction("click", "right"),
            "middle": ButtonAction("hold", "shift"),
        }}
        self.engine = LayerEngine(self.profile)

    def test_hold_activa_y_desactiva(self):
        self.assertIsNone(self.engine.layer)
        self.assertTrue(self.engine.handle("forward", True).suppress)
        self.assertEqual(self.engine.layer, "shift")
        self.assertTrue(self.engine.handle("forward", False).suppress)
        self.assertIsNone(self.engine.layer)

    def test_capa_remplaza_accion(self):
        self.engine.handle("forward", True)
        result = self.engine.handle("backward", True)
        self.assertTrue(result.suppress)
        self.assertEqual(result.steps[0].value, "ctrl+c")
        self.engine.handle("backward", False)

    def test_capa_cae_a_base_si_no_esta_definido(self):
        self.engine.handle("forward", True)
        result = self.engine.handle("right", True)
        self.assertFalse(result.suppress)  # right -> click right: passthrough

    def test_click_remap_emite_down_y_up(self):
        self.engine.handle("forward", True)
        down = self.engine.handle("left", True)
        self.assertEqual(down.steps[0].kind, "click_down")
        self.assertEqual(down.steps[0].value, "right")
        up = self.engine.handle("left", False)
        self.assertEqual(up.steps[0].kind, "click_up")

    def test_hold_suelta_aunque_la_capa_se_apague_antes(self):
        self.engine.handle("forward", True)   # activa capa
        down = self.engine.handle("middle", True)  # hold shift
        self.assertEqual(down.steps[0].kind, "keys_down")
        self.engine.handle("forward", False)  # capa off con la tecla aun pulsada
        up = self.engine.handle("middle", False)
        self.assertEqual(up.steps[0].kind, "keys_up")
        self.assertEqual(up.steps[0].value, "shift")

    def test_toggle(self):
        self.profile.shift_mode = "toggle"
        self.engine.handle("forward", True)
        self.assertEqual(self.engine.layer, "shift")
        self.engine.handle("forward", False)   # el release no cambia nada
        self.assertEqual(self.engine.layer, "shift")
        self.engine.handle("forward", True)
        self.assertIsNone(self.engine.layer)

    def test_reset_suelta_teclas(self):
        self.engine.handle("forward", True)
        self.engine.handle("middle", True)
        steps = self.engine.reset()
        self.assertEqual([s.kind for s in steps], ["keys_up"])
        self.assertEqual(self.engine.handle("middle", False).steps, ())
        self.assertIsNone(self.engine.layer)

    def test_shift_no_mapeado_en_la_capa(self):
        result = self.engine.handle("forward", True)
        self.assertEqual(result.steps, ())


class KeyboardShiftTest(unittest.TestCase):
    """Un boton de arriba remapeado a una tecla dispara la capa desde el hook.

    El firmware del X6 solo emite usages 0x00-0x2F (letras/digitos), asi que la
    tecla disparadora tiene que estar en ese rango.
    """

    def setUp(self):
        self.profile = make_profile(shift_button="9", shift_layer="shift",
                                    shift_mode="hold")
        self.profile.layers = {"shift": {
            "backward": ButtonAction("shortcut", "ctrl+c"),
            "middle": ButtonAction("media", "mute"),
            "0": ButtonAction("shortcut", "ctrl+v"),
        }}
        self.engine = LayerEngine(self.profile)

    def test_tecla_activa_la_capa_y_se_bloquea(self):
        down = self.engine.handle("9", True)
        self.assertTrue(down.suppress)
        self.assertEqual(self.engine.layer, "shift")
        up = self.engine.handle("9", False)
        self.assertTrue(up.suppress)
        self.assertIsNone(self.engine.layer)

    def test_tecla_que_no_es_el_shift(self):
        self.assertFalse(self.engine.handle("0", True).suppress)
        self.assertIsNone(self.engine.layer)

    def test_accion_de_capa_con_el_shift_pulsado(self):
        self.engine.handle("9", True)
        down = self.engine.handle("backward", True)
        self.assertTrue(down.suppress)
        self.assertEqual(down.steps[0].value, "ctrl+c")
        cero = self.engine.handle("0", True)
        self.assertEqual(cero.steps[0].value, "ctrl+v")


class SideMapTest(unittest.TestCase):
    def test_mapea_x1_x2(self):
        engine = LayerEngine(make_profile(), side_button_map={"x1": "backward", "x2": "forward"})
        self.assertTrue(engine.handle("x2", True).suppress)
        self.assertEqual(engine.handle("x2", True).steps[0].value, "alt+Right")


if __name__ == "__main__":
    unittest.main()
