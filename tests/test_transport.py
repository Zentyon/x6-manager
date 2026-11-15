"""Tests de transporte con un HID falso: seleccion de coleccion, lecturas y ACK."""
from __future__ import annotations

import unittest

from x6core import protocol as p
from x6core import transport
from x6core.protocol import DPI_PAYLOAD_LEN, RID_DPI

STATUS = [0x03, 0x10, 0x40, 0x01, 0x0A]
ACK = [0x03, 0x50, 0x00, 0x00]
ACK_04 = [0x03, 0x10, 0x50, 0x00, 0x04]
ACK_06 = [0x03, 0x10, 0x50, 0x00, 0x06]
ACK_08 = [0x03, 0x10, 0x50, 0x00, 0x08]


class FakeDevice:
    def __init__(self, reports=(), send_rc=None):
        self.reports = [list(r) for r in reports]
        self.sent: list[bytes] = []
        self.send_rc = send_rc
        self.closed = False

    def read(self, _size):
        return self.reports.pop(0) if self.reports else []

    def send_feature_report(self, data):
        self.sent.append(bytes(data))
        return len(data) if self.send_rc is None else self.send_rc

    def close(self):
        self.closed = True


def dev(interface=2, page=0x0A, usage=0x00, path="x"):
    return {"interface_number": interface, "usage_page": page, "usage": usage, "path": path}


class FindDongleTest(unittest.TestCase):
    def setUp(self):
        self._orig = transport.enumerate_devices

    def tearDown(self):
        transport.enumerate_devices = self._orig

    def test_prefiere_usage_page_0a(self):
        transport.enumerate_devices = lambda: [
            dev(page=0x01, usage=0x80, path="legacy"),
            dev(page=0x0A, usage=0x00, path="status"),
            dev(interface=1, page=0x01, usage=0x02, path="mouse"),
        ]
        self.assertEqual(transport.find_dongle()["path"], "status")

    def test_fallback_legacy_si_no_hay_0a(self):
        transport.enumerate_devices = lambda: [
            dev(interface=1, page=0x01, usage=0x02, path="mouse"),
            dev(page=0x01, usage=0x80, path="legacy"),
        ]
        self.assertEqual(transport.find_dongle()["path"], "legacy")

    def test_sin_interfaz_2(self):
        transport.enumerate_devices = lambda: [dev(interface=1, page=0x01, usage=0x02)]
        with self.assertRaises(transport.DongleNotFound):
            transport.find_dongle()

    def test_sin_dispositivos(self):
        transport.enumerate_devices = lambda: []
        with self.assertRaises(transport.DongleNotFound):
            transport.find_dongle()


class DongleIOTest(unittest.TestCase):
    def test_read_status(self):
        d = transport.Dongle(FakeDevice([STATUS]))
        st = d.read_status(timeout_ms=200)
        self.assertEqual(st["type"], "status")
        self.assertEqual(st["battery"], 100)

    def test_read_status_timeout(self):
        d = transport.Dongle(FakeDevice([]))
        with self.assertRaises(TimeoutError):
            d.read_status(timeout_ms=60)

    def test_apply_onboard_sin_ack_no_falla(self):
        fake = FakeDevice([])
        d = transport.Dongle(fake)
        payload = bytes([RID_DPI]) + bytes(DPI_PAYLOAD_LEN - 1)
        self.assertFalse(d.apply_onboard(payload, ack_timeout=0.05))
        self.assertEqual(len(fake.sent), 1)
        self.assertEqual(len(fake.sent[0]), DPI_PAYLOAD_LEN)

    def test_apply_onboard_con_ack(self):
        d = transport.Dongle(FakeDevice([STATUS, ACK]))
        payload = bytes([RID_DPI]) + bytes(DPI_PAYLOAD_LEN - 1)
        self.assertTrue(d.apply_onboard(payload, ack_timeout=0.5))

    def test_payload_invalido(self):
        d = transport.Dongle(FakeDevice([]))
        with self.assertRaises(ValueError):
            d.apply_onboard(bytes([RID_DPI]) + bytes(10))
        with self.assertRaises(ValueError):
            d.apply_onboard(bytes([0x06]) + bytes(DPI_PAYLOAD_LEN - 1))

    def test_send_parcial_es_error(self):
        d = transport.Dongle(FakeDevice([], send_rc=10))
        with self.assertRaises(OSError):
            d.send(bytes([RID_DPI]) + bytes(DPI_PAYLOAD_LEN - 1))

    def test_wait_ack_ignora_heartbeat(self):
        fake = FakeDevice([STATUS, ACK_08])
        d = transport.Dongle(fake)
        st = d.wait_ack(0x08, timeout_s=0.5)
        self.assertEqual(st["ack_report"], 0x08)
        self.assertEqual(st["profile"], 0)

    def test_wait_ack_no_acepta_otro_report(self):
        fake = FakeDevice([ACK_04])
        d = transport.Dongle(fake)
        with self.assertRaises(TimeoutError):
            d.wait_ack(0x08, timeout_s=0.1)

    def test_apply_config_ordena_y_espera_ack(self):
        fake = FakeDevice([ACK_04, ACK_06, ACK_08])
        d = transport.Dongle(fake)
        payloads = [
            p.build_buttons_report(p.DEFAULT_BUTTONS),
            p.build_polling_report(1000),
            p.build_dpi_report([800, 1200, 1600]),
        ]
        result = d.apply_config(payloads, ack_timeout=0.2, retries=1)
        self.assertEqual([rid for rid, _ in result], [0x04, 0x06, 0x08])
        self.assertTrue(all(acked for _, acked in result))
        self.assertEqual([sent[0] for sent in fake.sent], [0x04, 0x06, 0x08])

    def test_apply_config_sin_ack_no_aborta(self):
        """Sin ACK (config identica a la guardada) el resto se sigue enviando."""
        fake = FakeDevice([])
        d = transport.Dongle(fake)
        result = d.apply_config(
            [p.build_dpi_report([800, 1200, 1600]), p.build_polling_report(1000)],
            ack_timeout=0.05, retries=1, gap=0)
        self.assertEqual([rid for rid, _ in result], [0x04, 0x06])
        self.assertFalse(any(acked for _, acked in result))
        self.assertEqual(len(fake.sent), 2)

    def test_apply_config_con_ack_del_ultimo(self):
        fake = FakeDevice([ACK_06])
        d = transport.Dongle(fake)
        result = d.apply_config(
            [p.build_polling_report(1000)], ack_timeout=0.1, retries=1, gap=0)
        self.assertEqual(result, [(0x06, True)])

    def test_apply_config_rechaza_commit_y_duplicados(self):
        d = transport.Dongle(FakeDevice([]))
        with self.assertRaises(ValueError):
            d.apply_config([p.build_commit_report()])
        with self.assertRaises(ValueError):
            d.apply_config([p.build_polling_report(1000),
                            p.build_polling_report(500)])
        self.assertEqual(d._dev.sent, [])


if __name__ == "__main__":
    unittest.main()
