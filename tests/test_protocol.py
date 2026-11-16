"""Tests del protocolo HID (sin hardware)."""
from __future__ import annotations

import unittest

from x6core import protocol as p


class DpiWireTest(unittest.TestCase):
    def test_roundtrip(self):
        for dpi in (50, 400, 800, 1600, 26000):
            self.assertEqual(p.wire_to_dpi(p.dpi_to_wire(dpi)), dpi)

    def test_26000_es_el_maximo_del_alambre(self):
        self.assertEqual(p.dpi_to_wire(26000), 519)

    def test_rechaza_invalidos(self):
        for bad in (0, 49, 26050, 75, "800"):
            with self.assertRaises((TypeError, ValueError)):
                p.dpi_to_wire(bad)  # type: ignore[arg-type]


class ChecksumTest(unittest.TestCase):
    def test_suma_aditiva_be(self):
        payload = bytes(range(56))
        # suma de payload[3:50] = (3 + 49) * 47 / 2 = 1222 = 0x04C6
        self.assertEqual(p.dpi_checksum(payload), (0x04, 0xC6))


class BuildDpiReportTest(unittest.TestCase):
    def setUp(self):
        self.stages = [800, 1200, 1600, 3200, 5600, 26000]
        self.colors = [0xFF0000, 0x00FF00, 0x0000FF, 0xFFFF00, 0xFF00FF, 0x00FFFF]
        self.payload = p.build_dpi_report(self.stages, self.colors, active=0)

    def test_layout(self):
        b = self.payload
        self.assertEqual(len(b), 56)
        self.assertEqual(b[0], p.RID_DPI)
        self.assertEqual(b[1], 0x38)
        self.assertEqual(b[2], 0x01)
        self.assertEqual(b[5], 0b111111)
        self.assertEqual(b[24], 1)  # 1-indexado en el alambre
        self.assertEqual(b[49], 0x01)
        hi, lo = p.dpi_checksum(b)
        self.assertEqual(bytes(b[50:52]), bytes([hi, lo]))
        self.assertEqual(bytes(b[52:]), b"\x00\x00\x00\x00")

    def test_stages_le_16(self):
        for i, dpi in enumerate(self.stages):
            word = p.dpi_to_wire(dpi)
            self.assertEqual(self.payload[8 + i], word & 0xFF)
            self.assertEqual(self.payload[16 + i], (word >> 8) & 0xFF)

    def test_colores_bgr(self):
        self.assertEqual(bytes(self.payload[25:28]), bytes([0x00, 0x00, 0xFF]))  # rojo -> BGR
        self.assertEqual(bytes(self.payload[28:31]), bytes([0x00, 0xFF, 0x00]))  # verde

    def test_padding_de_colores(self):
        b = p.build_dpi_report([800], [0x123456], 0)
        # los huecos se rellenan con la paleta observada del software oficial
        self.assertEqual(bytes(b[28:31]), bytes([0x00, 0xFF, 0x00]))

    def test_active_se_respeta(self):
        b = p.build_dpi_report(self.stages, self.colors, active=3)
        self.assertEqual(b[24], 4)

    def test_enabled_mask(self):
        b = p.build_dpi_report(self.stages, self.colors, 0, enabled_mask=0b101)
        self.assertEqual(b[5], 0b101)

    def test_colores_vacios_usan_los_por_defecto(self):
        b = p.build_dpi_report([800], [], 0)
        self.assertEqual(bytes(b[25:28]), bytes([0xFF, 0x00, 0x00]))  # azul BGR

    def test_rechaza_configuraciones_invalidas(self):
        cases = [
            ([], self.colors, 0),
            ([800] * 9, self.colors, 0),
            ([800], self.colors, 1),
            ([800, 26050], self.colors, 0),
            ([800], [0x1000000], 0),
            ([800], [1] * 9, 0),
        ]
        for stages, colors, active in cases:
            with self.assertRaises(ValueError, msg=f"{stages[:3]} {colors[:3]} {active}"):
                p.build_dpi_report(stages, colors, active)


class PollingTest(unittest.TestCase):
    def test_mapa(self):
        self.assertEqual(p.build_polling_report(1000)[3], 0x01)
        self.assertEqual(p.build_polling_report(125)[3], 0x08)

    def test_hz_no_soportado(self):
        with self.assertRaises(ValueError):
            p.build_polling_report(2000)


class CapturedReportsTest(unittest.TestCase):
    """Reproduce byte a byte lo que escribio el software oficial (captura real)."""

    def test_commit(self):
        self.assertEqual(
            p.build_commit_report().hex(" "),
            "0c 0a 01 fe 01 fe 00 00 00 00",
        )

    def test_polling_1000(self):
        self.assertEqual(
            p.build_polling_report(1000).hex(" "),
            "06 09 01 01 fe 00 00 00 00",
        )

    def test_dpi_capturado_800_1200_1600_3200_5600_26000(self):
        payload = p.build_dpi_report([800, 1200, 1600, 3200, 5600, 26000])
        self.assertEqual(
            payload.hex(" "),
            "04 38 01 00 00 3f 00 01 0f 17 1f 3f 6f 07 00 00 00 00 00 00 00 "
            "02 00 00 01 ff 00 00 00 ff 00 00 00 ff ff ff 00 00 ff ff ff 00 "
            "ff ff 40 00 ff ff ff 01 0e 71 00 00 00 00",
        )

    def test_dpi_capturado_original_del_usuario(self):
        payload = p.build_dpi_report([800, 900, 1600, 3200, 2650, 2400])
        self.assertEqual(
            payload.hex(" "),
            "04 38 01 00 00 3f 00 01 0f 11 1f 3f 34 2f 00 00 00 00 00 00 00 "
            "00 00 00 01 ff 00 00 00 ff 00 00 00 ff ff ff 00 00 ff ff ff 00 "
            "ff ff 40 00 ff ff ff 01 0e 56 00 00 00 00",
        )

    def test_botones_capturado_original(self):
        slots = [0x02, 0x03, 0x04, 0x0D, 0x1B, 0x1C, 0x06, 0x05, 0x3C,
                 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x0A, 0x09]
        self.assertEqual(
            p.build_buttons_report(slots).hex(" "),
            "08 3b 01 02 00 00 03 00 00 04 00 00 0d 00 00 1b 00 00 1c 00 00 "
            "06 00 00 05 00 00 3c 00 00 01 00 00 01 00 00 01 00 00 01 00 00 "
            "01 00 00 01 00 00 01 00 00 0a 00 00 09 00 00 00 ae",
        )

    def test_botones_capturado_tras_asignar(self):
        slots = [0x02, 0x03, 0x04, 0x0D, (0x11, 0x00, 0x06), (0x11, 0x00, 0x21),
                 0x06, 0x05, 0x3C, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01,
                 0x0A, 0x09]
        self.assertEqual(
            p.build_buttons_report(slots).hex(" "),
            "08 3b 01 02 00 00 03 00 00 04 00 00 0d 00 00 11 00 06 11 00 21 "
            "06 00 00 05 00 00 3c 00 00 01 00 00 01 00 00 01 00 00 01 00 00 "
            "01 00 00 01 00 00 01 00 00 0a 00 00 09 00 00 00 c0",
        )


class ActionTableTest(unittest.TestCase):
    def test_acciones_conocidas(self):
        self.assertEqual(p.action_entry("copy"), (0x11, 0x01, 0x06))
        self.assertEqual(p.action_entry("volume_up"), (0x1B, 0x00, 0x00))
        self.assertEqual(p.action_entry("off"), (0x01, 0x00, 0x00))

    def test_atajo_de_teclado(self):
        self.assertEqual(p.keyboard_entry("ctrl+c"), (0x11, 0x01, 0x06))
        self.assertEqual(p.keyboard_entry("f13"), (0x11, 0x00, 0x68))
        self.assertEqual(p.keyboard_entry("f14"), (0x11, 0x00, 0x69))
        self.assertEqual(p.keyboard_entry("ctrl+shift+s"), (0x11, 0x03, 0x16))
        self.assertEqual(p.keyboard_entry("alt+tab"), (0x11, 0x04, 0x2B))

    def test_atajo_invalido(self):
        for bad in ("", "ctrl+", "nope", "ctrl+a+b"):
            with self.assertRaises(ValueError, msg=bad):
                p.keyboard_entry(bad)

    def test_indices_fisicos_del_x6(self):
        self.assertEqual(p.BUTTON_INDEX["dpi_up"], 4)
        self.assertEqual(p.BUTTON_INDEX["dpi_down"], 5)
        self.assertEqual(p.BUTTON_INDEX["forward"], 6)
        self.assertEqual(p.BUTTON_INDEX["backward"], 7)
        self.assertEqual(p.BUTTON_INDEX["wheel_up"], 17)

    def test_validate_report(self):
        p.validate_report(p.build_polling_report(1000))
        with self.assertRaises(ValueError):
            p.validate_report(b"")
        with self.assertRaises(ValueError):
            p.validate_report(bytes([0x7F, 9]) + bytes(7))
        with self.assertRaises(ValueError):
            p.validate_report(bytes([p.RID_BUTTONS, 59]) + bytes(10))


class ParseStatusTest(unittest.TestCase):
    def test_basura(self):
        self.assertFalse(p.parse_status(b"\x01\x02")["valid"])
        self.assertFalse(p.parse_status(b"\x04\x11\x00\x00")["valid"])
        self.assertFalse(p.parse_status(bytes([0x03, 0x10]))["valid"])

    def test_status_real_capturado(self):
        st = p.parse_status(bytes.fromhex("03 10 40 01 0a"))
        self.assertTrue(st["valid"])
        self.assertEqual(st["type"], "status")
        self.assertEqual(st["event"], 0x10)
        self.assertEqual(st["sub"], 0x40)
        self.assertEqual(st["stage"], 1)
        self.assertEqual(st["battery_raw"], 0x0A)
        self.assertEqual(st["battery"], 100)
        self.assertEqual(st["raw"], "03 10 40 01 0a")

    def test_ack_real_capturado(self):
        st = p.parse_status(bytes.fromhex("03 10 50 00 04"))
        self.assertEqual(st["type"], "ack")
        self.assertEqual(st["ack_report"], 0x04)
        self.assertEqual(st["profile"], 0)

    def test_ack_con_perfil(self):
        st = p.parse_status(bytes.fromhex("03 10 50 01 08"))
        self.assertEqual(st["type"], "ack")
        self.assertEqual(st["ack_report"], 0x08)
        self.assertEqual(st["profile"], 1)

    def test_boton_dpi_fisico(self):
        st = p.parse_status(bytes.fromhex("03 10 10 03 0a"))
        self.assertEqual(st["type"], "dpi")
        self.assertEqual(st["stage"], 3)
        self.assertEqual(st["battery"], 100)

    def test_bateria_90(self):
        st = p.parse_status(bytes.fromhex("03 10 40 01 09"))
        self.assertEqual(st["type"], "status")
        self.assertEqual(st["battery"], 90)

    def test_bateria_fuera_de_rango(self):
        st = p.parse_status(bytes.fromhex("03 10 40 01 ff"))
        self.assertEqual(st["type"], "status")
        self.assertEqual(st["battery_raw"], 0xFF)
        self.assertIsNone(st["battery"])

    def test_ack_legacy(self):
        for raw in ("03 50 00 00", "03 12 00 00", "03 21 00 00"):
            self.assertEqual(p.parse_status(bytes.fromhex(raw))["type"], "ack", raw)

    def test_evento_desconocido(self):
        st = p.parse_status(bytes.fromhex("03 11 00 08 00"))
        self.assertEqual(st["type"], "unknown")
        self.assertEqual(st["sub"], 0x00)


class MacroTest(unittest.TestCase):
    def test_evento_corto(self):
        self.assertEqual(p.encode_macro_event("a", True, 0), bytes([0x01, 0x04]))
        self.assertEqual(p.encode_macro_event("a", False, 100), bytes([0x8B, 0x04]))
        # 1270 ms es el maximo que cabe en 7 bits de decimas
        self.assertEqual(p.encode_macro_event("b", True, 1270), bytes([0x7F, 0x05]))

    def test_evento_largo_parte_el_retardo(self):
        # 2000 ms -> resto 0 ms en el evento + item extra (2000 // 200, 0x03)
        self.assertEqual(p.encode_macro_event("a", True, 2000),
                         bytes([0x01, 0x04, 0x0A, 0x03]))
        self.assertEqual(p.encode_macro_event("a", False, 2450),
                         bytes([0x85, 0x04, 0x0C, 0x03]))

    def test_buffer_logico(self):
        buf = p.build_macro_logical([("a", True, 0), ("a", False, 50)], slot=7)
        self.assertEqual(len(buf), 131)
        self.assertEqual(buf[0], 0x09)
        self.assertEqual(buf[1], 131)
        self.assertEqual(buf[2], 7)
        self.assertEqual(buf[7], 1)
        self.assertEqual(buf[28], 2)
        self.assertEqual(buf[29:33], bytes([0x01, 0x04, 0x85, 0x04]))
        self.assertEqual(buf[-2] << 8 | buf[-1], sum(buf[3:129]) & 0xFFFF)

    def test_chunks_64(self):
        reports = p.build_macro_reports([("a", True, 0)], slot=3, report_len=64)
        self.assertEqual([len(r) for r in reports], [64, 64, 64])
        self.assertEqual([r[1] for r in reports], [64, 64, 12])
        self.assertEqual([r[3] for r in reports], [0, 1, 2])
        self.assertTrue(all(r[0] == 0x09 and r[2] == 3 for r in reports))
        logical = p.build_macro_logical([("a", True, 0)], slot=3)
        joined = reports[0][4:] + reports[1][4:] + reports[2][4:12]
        self.assertEqual(joined, logical[3:])
        self.assertEqual(reports[2][12:], bytes(52))

    def test_chunks_63(self):
        reports = p.build_macro_reports([("a", True, 0)], slot=3, report_len=63)
        self.assertEqual([len(r) for r in reports], [63, 63, 63])
        self.assertEqual([r[1] for r in reports], [63, 63, 14])
        logical = p.build_macro_logical([("a", True, 0)], slot=3)
        joined = reports[0][4:] + reports[1][4:] + reports[2][4:14]
        self.assertEqual(joined, logical[3:])

    def test_chunks_pasan_la_validacion(self):
        for length in (63, 64):
            for report in p.build_macro_reports([("a", True, 0)], report_len=length):
                self.assertEqual(p.validate_report(report), report)

    def test_validacion_rechaza_longitud_mala(self):
        report = bytearray(p.build_macro_reports([("a", True, 0)], report_len=64)[0])
        report[1] = 63
        with self.assertRaises(ValueError):
            p.validate_report(bytes(report))
        with self.assertRaises(ValueError):
            p.validate_report(bytes([0x09, 20]) + bytes(18))

    def test_macro_demasiado_largo(self):
        events = [("a", True, 0), ("a", False, 0)] * 30
        with self.assertRaises(ValueError):
            p.build_macro_logical(events)

    def test_entrada_de_boton_macro(self):
        self.assertEqual(p.macro_entry(6), (0x12, 0x00, 6))
        self.assertEqual(p.action_entry((0x12, 0, 6)), (0x12, 0, 6))
        for bad in (0, 19, "6"):
            with self.assertRaises(ValueError):
                p.macro_entry(bad)

    def test_tecla_desconocida(self):
        with self.assertRaises(ValueError):
            p.encode_macro_event("nope", True, 0)


if __name__ == "__main__":
    unittest.main()
