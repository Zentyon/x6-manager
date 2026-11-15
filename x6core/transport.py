"""Transporte HID cross-platform.

En el X6 real hay dos colecciones distintas en la interfaz 2:

  * `usage_page 0x0A` (Col03): solo entrada; entrega el heartbeat y los ACK.
  * `usage_page 0x0B` (Col04): solo configuracion; acepta los SET_FEATURE.

Escribir en 0x0A devuelve -1 en Windows. Por eso `Dongle` mantiene dos handles:
lectura por hidapi en 0x0A y escritura por `HidD_SetFeature` en 0x0B (igual que el
software oficial). En Linux se usan hidraw/hidapi para ambos.

Todas las operaciones van bajo un lock: un SET_FEATURE y la espera del ACK no
pueden solaparse con un read de otro hilo.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time

from .protocol import (
    CONFIG_USAGE,
    CONFIG_USAGE_PAGE,
    DPI_PAYLOAD_LEN,
    HID_INTERFACE,
    LEGACY_USAGE,
    LEGACY_USAGE_PAGE,
    PID,
    REPORT_LENGTHS,
    RID_COMMIT,
    RID_DPI,
    RID_STATUS,
    STATUS_USAGE,
    STATUS_USAGE_PAGE,
    VID,
    WRITE_ORDER,
    parse_status,
    validate_report,
)

log = logging.getLogger(__name__)

POLL_S = 0.02  # periodo de sondeo en modo no bloqueante (hidapi ignora timeout_ms ahi)


class DongleNotFound(RuntimeError):
    pass


class WriteNotAcknowledged(RuntimeError):
    """El SET_FEATURE se envio pero el dongle no lo confirmo con el ACK 0x03."""


def _hid():
    """Import perezoso: el paquete se puede importar/testear sin hidapi instalado."""
    try:
        import hid  # type: ignore
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise RuntimeError("falta hidapi: pip install hidapi") from exc
    return hid


def enumerate_devices() -> list[dict]:
    return list(_hid().enumerate(VID, PID))


def _pick(devices: list[dict], page: int, usage: int) -> dict | None:
    for d in devices:
        if (d.get("interface_number") == HID_INTERFACE
                and d.get("usage_page") == page
                and d.get("usage") == usage):
            return d
    return None


def find_dongle() -> dict:
    """Coleccion de la interfaz 2 que entrega el status/ACK 0x03 (usage_page 0x0A)."""
    devices = enumerate_devices()
    if not devices:
        raise DongleNotFound(
            f"dongle {VID:04x}:{PID:04x} no encontrado. Conecta el receptor 2.4G."
        )

    def score(d: dict) -> int:
        if d.get("interface_number") != HID_INTERFACE:
            return 0
        page, usage = d.get("usage_page"), d.get("usage")
        if page == STATUS_USAGE_PAGE and usage == STATUS_USAGE:
            return 10
        if page == LEGACY_USAGE_PAGE and usage == LEGACY_USAGE:
            return 5
        return 1  # otra coleccion de la interfaz 2: ultimo recurso

    best = max(devices, key=score)
    if score(best) == 0:
        raise DongleNotFound(
            f"el dongle {VID:04x}:{PID:04x} esta presente pero sin interfaz {HID_INTERFACE}"
        )
    if score(best) == 1:
        log.warning(
            "no se encontro la coleccion usage_page=%#x; usando if=%s usage=%s/%s",
            STATUS_USAGE_PAGE, best.get("interface_number"),
            best.get("usage_page"), best.get("usage"),
        )
    log.debug("coleccion de lectura: interface=%s usage=%s/%s",
              best.get("interface_number"), best.get("usage_page"), best.get("usage"))
    return best


def find_write_collection() -> dict:
    """Coleccion que acepta SET_FEATURE (usage_page 0x0B en el X6)."""
    devices = enumerate_devices()
    if not devices:
        raise DongleNotFound(f"dongle {VID:04x}:{PID:04x} no encontrado")
    found = _pick(devices, CONFIG_USAGE_PAGE, CONFIG_USAGE)
    if found is None:
        # Firmwares antiguos aceptan escritura en la misma coleccion de status.
        log.warning("sin coleccion de configuracion %#x; se usara la de lectura",
                    CONFIG_USAGE_PAGE)
        return find_dongle()
    log.debug("coleccion de escritura: interface=%s usage=%s/%s",
              found.get("interface_number"), found.get("usage_page"), found.get("usage"))
    return found


def display_path(info: dict) -> str:
    """hidapi devuelve `path` como bytes en Windows: legible para logs."""
    path = info.get("path") or ""
    if isinstance(path, bytes):
        path = path.decode("utf-8", "replace")
    return str(path)


# --- escritura ------------------------------------------------------------
class _HidFeatureWriter:
    """Escritura por hidapi (Linux/macOS) sobre la coleccion de configuracion."""

    def __init__(self, info: dict):
        self._dev = _hid().device()
        self._dev.open_path(info["path"])
        self._dev.set_nonblocking(True)

    def send(self, payload: bytes) -> None:
        n = self._dev.send_feature_report(bytes(payload))
        if n != len(payload):
            raise IOError(
                f"send_feature_report escribio {n} de {len(payload)} bytes"
            )

    def close(self) -> None:
        try:
            self._dev.close()
        except Exception:  # pragma: no cover
            log.debug("error cerrando escritor", exc_info=True)


class _Win32FeatureWriter:
    """Escritura con `HidD_SetFeature`, exactamente como el software oficial."""

    def __init__(self, path: str):
        from ctypes import wintypes

        self._wintypes = wintypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        self._kernel32.CreateFileW.restype = wintypes.HANDLE
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._hid = ctypes.WinDLL("hid", use_last_error=True)
        self._hid.HidD_SetFeature.argtypes = [
            wintypes.HANDLE, wintypes.LPVOID, wintypes.ULONG,
        ]
        self._hid.HidD_SetFeature.restype = wintypes.BOOLEAN

        self._handle = self._kernel32.CreateFileW(
            path,
            0x80000000 | 0x40000000,          # GENERIC_READ | GENERIC_WRITE
            0x00000001 | 0x00000002,          # FILE_SHARE_READ | FILE_SHARE_WRITE
            None,
            3,                                # OPEN_EXISTING
            0,
            None,
        )
        if self._handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())

    def send(self, payload: bytes) -> None:
        buf = ctypes.create_string_buffer(bytes(payload), len(payload))
        if not self._hid.HidD_SetFeature(self._handle, buf, len(payload)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        try:
            self._kernel32.CloseHandle(self._handle)
        except Exception:  # pragma: no cover
            log.debug("error cerrando handle", exc_info=True)


# --- handle del dongle ----------------------------------------------------
class Dongle:
    """Lectura por la coleccion de status y escritura por la de configuracion."""

    def __init__(self, device, info: dict | None = None, writer=None):
        self._dev = device
        self._writer = writer
        self.info = info or {}
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def open(cls, device: dict | None = None, write_device: dict | None = None) -> "Dongle":
        info = device or find_dongle()
        write_info = write_device or find_write_collection()

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

        try:
            if sys.platform == "win32":
                writer = _Win32FeatureWriter(display_path(write_info))
            else:
                writer = _HidFeatureWriter(write_info)
        except Exception:
            try:
                dev.close()
            except Exception:
                pass
            raise

        log.debug("dongle abierto: lectura=%s escritura=%s",
                  display_path(info), display_path(write_info))
        return cls(dev, info, writer)

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
        """Espera el siguiente report 0x03 (heartbeat, boton DPI o ACK)."""
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
        """Envia un feature report validando ID y longitud exacta."""
        payload = bytes(validate_report(bytes(payload)))
        with self._lock:
            if self._closed:
                raise RuntimeError("dongle cerrado")
            if self._writer is not None:
                self._writer.send(payload)
            else:
                n = self._dev.send_feature_report(payload)
                if n != len(payload):
                    raise IOError(
                        f"send_feature_report escribio {n} de {len(payload)} bytes"
                    )
        return len(payload)

    def wait_ack(self, report_id: int | None = None, timeout_s: float = 2.0) -> dict:
        """Espera el ACK `03 10 50 <perfil> <report_id>` del dongle."""
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            data = self.read_report(timeout_ms=250)
            if not data or data[0] != RID_STATUS:
                continue
            st = parse_status(data)
            if st.get("type") != "ack":
                continue
            acked = st.get("ack_report")
            if report_id is None or acked is None or acked == report_id:
                return st
        raise TimeoutError(
            f"sin ACK del report {report_id:#04x} en {timeout_s:.1f}s"
            if report_id is not None else f"sin ACK en {timeout_s:.1f}s"
        )

    def apply_config(self, payloads, wait_ack: bool = True,
                     ack_timeout: float = 2.5, retries: int = 2,
                     gap: float = 0.1) -> list[tuple[int, bool]]:
        """Envia varios reports validados, en el orden del software oficial.

        No envia 0x0C (commit): su significado es desconocido y las fuentes del
        protocolo recomiendan no usarlo. Devuelve [(report_id, acked), ...].

        El dongle solo ACKea los reports que cambian algo: si la config ya estaba
        aplicada, el report se acepta pero no hay ACK. Por eso la falta de ACK se
        registra y no interrumpe el envio del resto.
        """
        reports = [bytes(validate_report(bytes(p))) for p in payloads]
        seen = set()
        for report in reports:
            rid = report[0]
            if rid == RID_COMMIT:
                raise ValueError("0x0C no se envia: comando desconocido")
            if rid in seen:
                raise ValueError(f"report {rid:#04x} duplicado")
            seen.add(rid)
        reports.sort(key=lambda r: WRITE_ORDER.index(r[0]))

        results: list[tuple[int, bool]] = []
        with self._lock:
            for report in reports:
                rid = report[0]
                acked = False
                for attempt in range(1, retries + 1):
                    self.send(report)
                    if not wait_ack:
                        acked = True
                        break
                    try:
                        self.wait_ack(rid, ack_timeout)
                        acked = True
                        break
                    except TimeoutError:
                        log.warning("report %#04x sin ACK (intento %d/%d)",
                                    rid, attempt, retries)
                results.append((rid, acked))
                if wait_ack and not acked:
                    # Verificado en hardware: el dongle solo ACKea cuando el report
                    # cambia algo. Reenviar la misma config no produce ACK, asi que
                    # la ausencia de ACK no es un fallo y no debe abortar el resto.
                    log.info("report %#04x sin ACK (igual que lo ya guardado)", rid)
                if gap:
                    time.sleep(gap)
        return results

    def apply_onboard(self, payload: bytes, ack_timeout: float = 1.0) -> bool:
        """SET_FEATURE 0x04 (DPI). Devuelve True si llego ACK, False si solo se envio."""
        if len(payload) != DPI_PAYLOAD_LEN or payload[0] != RID_DPI:
            raise ValueError(
                f"payload inesperado: {len(payload)}B rid=0x{payload[0]:02x}"
            )
        with self._lock:
            self.send(payload)
            try:
                self.wait_ack(RID_DPI, ack_timeout)
                return True
            except TimeoutError:
                log.info("SET_FEATURE enviado sin ACK (revisa el cable/receptor)")
                return False

    # -- ciclo de vida -----------------------------------------------------
    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._writer is not None:
                try:
                    self._writer.close()
                except Exception:  # pragma: no cover
                    log.debug("error cerrando escritor", exc_info=True)
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
