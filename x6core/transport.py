"""Transporte HID cross-platform. Linux: hidraw iface 2 (hidapi). Windows: mismo via hidapi/hid.dll.

El acceso se hace siempre a traves de `Dongle`, que serializa las operaciones con un
lock: un SET_FEATURE y la espera de ACK no pueden solaparse con un read de otro hilo.
"""
from __future__ import annotations

import logging
import threading
import time

from .protocol import (
    DPI_PAYLOAD_LEN,
    HID_INTERFACE,
    PID,
    RID_DPI,
    RID_STATUS,
    VID,
    parse_status,
)

log = logging.getLogger(__name__)

POLL_S = 0.02  # periodo de sondeo en modo no bloqueante (hidapi ignora timeout_ms ahi)


class DongleNotFound(RuntimeError):
    pass


def _hid():
    """Import perezoso: el paquete se puede importar/testear sin hidapi instalado."""
    try:
        import hid  # type: ignore
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise RuntimeError("falta hidapi: pip install hidapi") from exc
    return hid


def enumerate_devices() -> list[dict]:
    return list(_hid().enumerate(VID, PID))


def find_dongle() -> dict:
    """Devuelve el device de la interfaz 2 (el vendor collection del dongle).

    Preferimos interface_number == 2 + usage_page 1; si el backend no expone
    interface_number probamos por path/topologia y, si no, fallamos con detalle en
    vez de abrir a ciegas el primer HID del mouse (que seria teclado/consumer).
    """
    devices = enumerate_devices()
    if not devices:
        raise DongleNotFound(
            f"dongle {VID:04x}:{PID:04x} no encontrado. Conecta el receptor 2.4G."
        )

    def score(d: dict) -> int:
        s = 0
        if d.get("interface_number") == HID_INTERFACE:
            s += 4
        if d.get("usage_page") == 0x01:
            s += 2
        if d.get("usage") == 0x80:
            s += 1
        return s

    best = max(devices, key=score)
    if score(best) < 4:
        log.warning(
            "ninguna interfaz con interface_number=%s; usando %s (usage_page=%s usage=%s)",
            HID_INTERFACE, best.get("path"), best.get("usage_page"), best.get("usage"),
        )
    if best.get("interface_number") not in (None, -1, HID_INTERFACE):
        log.warning("interfaz inesperada %s, puede no ser el canal vendor",
                    best.get("interface_number"))
    return best


class Dongle:
    """Handle al canal vendor del dongle. Todas las operaciones van bajo lock."""

    def __init__(self, device, info: dict | None = None):
        self._dev = device
        self.info = info or {}
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def open(cls, device: dict | None = None) -> "Dongle":
        info = device or find_dongle()
        dev = _hid().device()
        try:
            dev.open_path(info["path"])
            dev.set_nonblocking(True)
        except Exception:
            try:
                dev.close()
            except Exception:
                pass
            raise
        log.debug("dongle abierto: %s", info.get("path"))
        return cls(dev, info)

    # -- lectura -----------------------------------------------------------
    def read_report(self, timeout_ms: int = 500) -> bytes:
        """Lee un report crudo. Devuelve b'' si no hay nada en `timeout_ms`."""
        end = time.monotonic() + timeout_ms / 1000
        with self._lock:
            while not self._closed:
                data = self._dev.read(64)
                if data:
                    return bytes(data)
                if time.monotonic() >= end:
                    return b""
                # modo no bloqueante: hay que ceder CPU si no hay datos
                time.sleep(POLL_S)
        return b""

    def read_status(self, timeout_ms: int = 3000) -> dict:
        """Espera el siguiente report 0x03 (bateria/DPI/ACK)."""
        end = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < end:
            data = self.read_report(timeout_ms=200)
            if data and data[0] == RID_STATUS:
                return parse_status(data)
        raise TimeoutError(
            "sin heartbeat 0x03 en %dms - revisa udev uaccess (Linux) o que el "
            "mouse este encendido" % timeout_ms
        )

    # -- escritura ---------------------------------------------------------
    def send(self, payload: bytes) -> int:
        if not payload:
            raise ValueError("payload vacio")
        with self._lock:
            if self._closed:
                raise RuntimeError("dongle cerrado")
            n = self._dev.send_feature_report(bytes(payload))
        if n != len(payload):
            raise IOError(f"send_feature_report escribio {n} de {len(payload)} bytes")
        return n

    def apply_onboard(self, payload: bytes, ack_timeout: float = 3.0) -> bool:
        """SET_FEATURE 0x04 + espera ACK 0x03/0x50. Solo bajo demanda."""
        if len(payload) != DPI_PAYLOAD_LEN or payload[0] != RID_DPI:
            raise ValueError(
                f"payload inesperado: {len(payload)}B rid=0x{payload[0]:02x}"
            )
        self.send(payload)
        self.wait_ack(ack_timeout)
        return True

    def wait_ack(self, timeout_s: float = 3.0) -> None:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            data = self.read_report(timeout_ms=250)
            if data and data[0] == RID_STATUS:
                if parse_status(data).get("type") == "ack":
                    return
        raise TimeoutError(
            "SET_REPORT aceptado pero sin ACK - suele aplicar igual. Revisa usbmon."
        )

    # -- ciclo de vida -----------------------------------------------------
    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._dev.close()
            except Exception:  # pragma: no cover
                log.debug("error cerrando dongle", exc_info=True)

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> "Dongle":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
