"""Transporte HID cross-platform. Linux: hidraw iface 2 (hidapi). Windows: mismo via hidapi/hid.dll."""
from __future__ import annotations
import hid  # pip: hidapi

from .protocol import VID, PID

def find_dongle():
    for d in hid.enumerate(VID, PID):
        # En Linux buscar interface_number==2, usage_page 1 usage 0x80 (hidraw5)
        if d.get("interface_number", 2) == 2:
            return d
    # fallback: primer match (dongle sin serial)
    devs = hid.enumerate(VID, PID)
    return devs[0] if devs else None

def open_dongle():
    info = find_dongle()
    if not info:
        raise RuntimeError("Dongle 1d57:fa60 no encontrado. Conecta el dongle 2.4G.")
    h = hid.device()
    h.open_path(info["path"])
    h.set_nonblocking(True)
    return h

def read_status(h, timeout_ms=3000):
    """Lectura pasiva: el dongle empuja report 0x03 cada ~1.5s (bateria/DPI/ACK)."""
    import time
    from .protocol import parse_status
    end = time.time() + timeout_ms / 1000
    while time.time() < end:
        data = h.read(64, timeout_ms=500)
        if data and data[0] == 0x03:
            return parse_status(bytes(data))
    raise TimeoutError("Sin heartbeat 0x03 - revisa udev/TAG uaccess en Linux")

def apply_onboard(h, payload: bytes):
    """SET_FEATURE 0x04 + espera ACK 0x03/0x50. Solo bajo demanda (click Aplicar / cambio perfil)."""
    assert payload[0] == 0x04 and len(payload) == 52, (len(payload), payload[:4])
    n = h.send_feature_report(list(payload))
    assert n == len(payload), n
    # ACK: leer hasta 3s buscando type ack
    import time
    from .protocol import parse_status
    end = time.time() + 3
    while time.time() < end:
        data = h.read(64, timeout_ms=500)
        if data and data[0] == 0x03 and parse_status(bytes(data)).get("type") == "ack":
            return True
    raise TimeoutError("SET_REPORT aceptado pero sin ACK - igual suele aplicar. Revisa usbmon.")
