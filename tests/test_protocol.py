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
        payload = bytes(range(52))
        # suma de payload[3:50] = (3 + 49) * 47 / 2 = 1222 = 0x04C6
        self.assertEqual(p.checksum(payload), (0x04, 0xC6))


class BuildDpiReportTest(unittest.TestCase):
    def setUp(self):
        self.stages = [800, 1200, 1600, 3200, 5600, 26000]
        self.colors = [0xFF0000, 0x00FF00, 0x0000FF, 0xFFFF00, 0xFF00FF, 0x00FFFF]
        self.payload = p.build_dpi_report(self.stages, self.colors, active=0)

    def test_layout(self):
        b = self.payload
        self.assertEqual(len(b), 52)
        self.assertEqual(b[0], p.RID_DPI)
        self.assertEqual(b[1], 0x38)
        self.assertEqual(b[2], 0x01)
        self.assertEqual(b[5], 0b111111)
        self.assertEqual(b[24], 1)  # 1-indexado en el alambre
        self.assertEqual(b[49], 0x02)
        self.assertEqual(bytes(b[50:]), bytes(p.checksum(b)))

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
        self.assertEqual(bytes(b[28:31]), b"\xff\xff\xff")

    def test_active_se_respeta(self):
        b = p.build_dpi_report(self.stages, self.colors, active=3)
        self.assertEqual(b[24], 4)

    def test_enabled_mask(self):
        b = p.build_dpi_report(self.stages, self.colors, 0, enabled_mask=0b101)
        self.assertEqual(b[5], 0b101)

    def test_rechaza_configuraciones_invalidas(self):
        cases = [
            ([], self.colors, 0),
            ([800] * 9, self.colors, 0),
            ([800], self.colors, 1),
            ([800, 26050], self.colors, 0),
            ([800], [0x1000000], 0),
            ([800], [], 0),
        ]
        for stages, colors, active in cases:
            with self.assertRaises(ValueError, msg=f"{stages[:3]} {colors[:3]} {active}"):
                p.build_dpi_report(stages, colors, active)


class PollingTest(unittest.TestCase):
    def test_mapa(self):
        self.assertEqual(p.build_polling_short(1000)[3], 0x01)
        self.assertEqual(p.build_polling_short(125)[3], 0x08)

    def test_hz_no_soportado(self):
        with self.assertRaises(ValueError):
            p.build_polling_short(2000)


class ParseStatusTest(unittest.TestCase):
    def test_basura(self):
        self.assertFalse(p.parse_status(b"\x01\x02")["valid"])
        self.assertFalse(p.parse_status(b"\x04\x11\x00\x00")["valid"])

    def test_ack(self):
        self.assertEqual(p.parse_status(bytes([0x03, 0x12, 0x00, 0x00]))["type"], "ack")
        self.assertEqual(p.parse_status(bytes([0x03, 0x00, 0x50, 0x00]))["type"], "ack")

    def test_heartbeat(self):
        st = p.parse_status(bytes([0x03, 0x11, 0x00, 0x08]))
        self.assertEqual(st["type"], "heartbeat")
        self.assertEqual(st["battery"], 80)
        self.assertEqual(st["battery_raw"], 8)

    def test_heartbeat_fuera_de_rango_no_inventa_porcentaje(self):
        st = p.parse_status(bytes([0x03, 0x11, 0x00, 0xFF]))
        self.assertEqual(st["type"], "heartbeat")
        self.assertIsNone(st["battery"])
        self.assertEqual(st["battery_raw"], 0xFF)

    def test_boton_dpi_es_1_indexado(self):
        st = p.parse_status(bytes([0x03, 0x10, 0x03, 0x00]))
        self.assertEqual(st["type"], "dpi_button")
        self.assertEqual(st["active_stage"], 3)
        self.assertEqual(st["stage_index"], 2)

    def test_boton_dpi_fuera_de_rango(self):
        self.assertIsNone(p.stage_from_event(0))
        self.assertIsNone(p.stage_from_event(9))
        self.assertEqual(p.stage_from_event(1), 0)
        self.assertEqual(p.stage_from_event(8), 7)


if __name__ == "__main__":
    unittest.main()
