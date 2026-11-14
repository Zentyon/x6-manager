"""Tests de perfiles: validacion, persistencia y migracion del formato viejo."""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from x6core.profiles import ButtonAction, Profile, ProfileError


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_default_valido_y_payload(self):
        profile = Profile()
        profile.validate()
        payload = profile.onboard_payload()
        self.assertEqual(len(payload), 52)
        self.assertEqual(payload[0], 0x04)

    def test_roundtrip_con_capa(self):
        profile = Profile("G-Shift", shift_button="forward", shift_layer="shift")
        profile.layers = {"shift": {
            "backward": ButtonAction("shortcut", "ctrl+c"),
            "middle": ButtonAction("media", "mute"),
        }}
        path = self.dir / "gs.json"
        profile.save(path)
        loaded = Profile.load(path)
        self.assertEqual(loaded.name, "G-Shift")
        self.assertEqual(loaded.shift_button, "forward")
        self.assertEqual(loaded.layers["shift"]["backward"].value, "ctrl+c")
        self.assertEqual(loaded.action_for("backward", "shift").kind, "shortcut")
        self.assertEqual(loaded.action_for("left", "shift").kind, "click")

    def test_azucar_de_acciones(self):
        path = self.dir / "sugar.json"
        path.write_text(json.dumps({
            "name": "sugar",
            "buttons": {"forward": "ctrl+c", "middle": "middle"},
        }))
        loaded = Profile.load(path)
        self.assertEqual(loaded.buttons["forward"].kind, "shortcut")
        self.assertEqual(loaded.buttons["middle"].kind, "click")

    def test_migra_click_con_valor_de_accion(self):
        path = self.dir / "legacy.json"
        path.write_text(json.dumps({
            "name": "legacy",
            "buttons": {"dpi": {"kind": "click", "value": "dpi_cycle", "macro_id": ""}},
        }))
        loaded = Profile.load(path)
        self.assertEqual(loaded.buttons["dpi"].kind, "dpi_cycle")

    def test_clave_desconocida(self):
        path = self.dir / "bad.json"
        path.write_text(json.dumps({"name": "bad", "dpi_stage": [800]}))
        with self.assertRaises(ProfileError) as ctx:
            Profile.load(path)
        self.assertIn("desconocidas", str(ctx.exception))
        self.assertIn("bad.json", str(ctx.exception))

    def test_json_invalido(self):
        path = self.dir / "rot.json"
        path.write_text("{no es json")
        with self.assertRaises(ProfileError):
            Profile.load(path)

    def test_dpi_invalido(self):
        profile = Profile()
        profile.dpi_stages = [800, 801]
        with self.assertRaises(ProfileError):
            profile.validate()

    def test_demasiados_stages(self):
        profile = Profile()
        profile.dpi_stages = [800] * 9
        with self.assertRaises(ProfileError):
            profile.validate()

    def test_active_stage_fuera_de_rango(self):
        profile = Profile()
        profile.active_stage = len(profile.dpi_stages)
        with self.assertRaises(ProfileError):
            profile.validate()

    def test_shift_sin_capa(self):
        profile = Profile()
        profile.shift_button = "forward"
        with self.assertRaises(ProfileError):
            profile.validate()

    def test_shift_mode_invalido(self):
        profile = Profile()
        profile.shift_button = "forward"
        profile.layers = {"shift": {"backward": ButtonAction("shortcut", "ctrl+c")}}
        profile.shift_mode = "tap"
        with self.assertRaises(ProfileError):
            profile.validate()

    def test_accion_invalida(self):
        profile = Profile()
        profile.buttons["middle"] = ButtonAction("click", "dpi_cycle")
        with self.assertRaises(ProfileError):
            profile.validate()

    def test_ejemplos_del_repo_validan(self):
        examples = pathlib.Path(__file__).resolve().parent.parent / "examples"
        files = sorted(examples.glob("*.json"))
        self.assertTrue(files, "no hay ejemplos que validar")
        for path in files:
            with self.subTest(path=path.name):
                Profile.load(path)


if __name__ == "__main__":
    unittest.main()
