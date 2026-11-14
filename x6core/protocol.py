"""Protocolo Attack Shark X6 - decodificado de X6.exe + validado en dongle real.

Fuente: blak0p/attack-shark-linux docs/protocol-x6.md + descriptor local hidraw5.
Todo lo que escribe va por SET_REPORT feature 0x0304 en interfaz 2.
La memoria NO es legible (GET_REPORT = STALL): hay que guardar estado local.
"""
from __future__ import annotations

from typing import Sequence

VID = 0x1D57
PID = 0xFA60
HID_INTERFACE = 2
STATUS_ENDPOINT = 0x83  # interrupt IN
SET_REPORT_VALUE = 0x0304

# Report IDs vistos en /sys/class/hidraw/hidraw5/device/report_descriptor (236 bytes)
RID_STATUS = 0x03      # input 4B: bateria + eventos
RID_DPI = 0x04         # feature 51B (+ID = 52B): DPI/RGB/flags
RID_SLEEP = 0x05       # feature 12B
RID_POLLING = 0x06     # feature 8B
RID_BUTTONS = 0x07     # feature 7B: remap hotkeys (pendiente captura)
RID_MACRO = 0x09       # feature 63B: macros (pendiente captura)

DPI_MIN, DPI_MAX, DPI_STEP = 50, 26000, 50
DPI_PAYLOAD_LEN = 52
MAX_STAGES = 8
POLLING_MAP = {125: 0x08, 250: 0x04, 500: 0x02, 1000: 0x01}


def validate_dpi(dpi: int) -> int:
    """Valida un DPI antes de enviarlo al mouse (nunca usar assert: -O lo elimina)."""
    if not isinstance(dpi, int):
        raise TypeError(f"DPI debe ser int, no {type(dpi).__name__}")
    if not DPI_MIN <= dpi <= DPI_MAX:
        raise ValueError(f"DPI {dpi} fuera de rango [{DPI_MIN}, {DPI_MAX}]")
    if dpi % DPI_STEP:
        raise ValueError(f"DPI {dpi} no es múltiplo de {DPI_STEP}")
    return dpi


def dpi_to_wire(dpi: int) -> int:
    """X6 guarda DPI/50-1 en LE de 16 bits (distinto del X3 que usa indice)."""
    return validate_dpi(dpi) // DPI_STEP - 1


def wire_to_dpi(w: int) -> int:
    if not 0 <= w <= 0xFFFF:
        raise ValueError(f"valor de alambre invalido: {w}")
    return (w + 1) * DPI_STEP


def checksum(payload: bytes) -> tuple[int, int]:
    """Suma aditiva de payload[3:50], BE en [-2:]. Validado: factory payload aceptado rc=54 + ACK."""
    s = sum(payload[3:50]) & 0xFFFF
    return (s >> 8) & 0xFF, s & 0xFF


def build_dpi_report(stages: Sequence[int], colors: Sequence[int], active: int,
                     enabled_mask: int | None = None) -> bytes:
    """stages: hasta 8 DPIs. colors: 8 ints 0xRRGGBB. active: 0-7. Retorna 52B con ID 0x04."""
    stages = list(stages)
    colors = list(colors)
    if not 1 <= len(stages) <= MAX_STAGES:
        raise ValueError(f"se esperan 1..{MAX_STAGES} stages, hay {len(stages)}")
    if not 0 <= active < len(stages):
        raise ValueError(f"stage activo {active} fuera de 0..{len(stages) - 1}")
    if not 1 <= len(colors) <= MAX_STAGES:
        raise ValueError(f"se esperan 1..{MAX_STAGES} colores, hay {len(colors)}")
    for i, dpi in enumerate(stages):
        try:
            validate_dpi(dpi)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"stage {i}: {exc}") from exc
    for i, c in enumerate(colors):
        if not isinstance(c, int) or not 0 <= c <= 0xFFFFFF:
            raise ValueError(f"color {i} invalido: {c!r} (esperado 0xRRGGBB)")

    n = len(stages)
    if enabled_mask is None:
        enabled_mask = (1 << n) - 1
    if not 0 <= enabled_mask <= 0xFF:
        raise ValueError(f"enabled_mask invalido: {enabled_mask}")

    buf = bytearray(DPI_PAYLOAD_LEN)
    buf[0] = RID_DPI
    buf[1] = 0x38  # 56? largo fijo que espera el dongle (builder FUN_004143a0)
    buf[2] = 0x01  # profile index
    buf[5] = enabled_mask & 0xFF
    for i, dpi in enumerate(stages):
        w = dpi_to_wire(dpi)
        buf[8 + i] = w & 0xFF            # lows
        buf[16 + i] = (w >> 8) & 0xFF    # highs
    buf[24] = active + 1  # 1-indexed en el alambre
    padded = colors + [0xFFFFFF] * (MAX_STAGES - len(colors))
    for i, c in enumerate(padded):
        # 3B por color en orden BGR en [25..48]
        buf[25 + i * 3] = c & 0xFF
        buf[26 + i * 3] = (c >> 8) & 0xFF
        buf[27 + i * 3] = (c >> 16) & 0xFF
    buf[49] = 0x02  # fijo
    hi, lo = checksum(bytes(buf))
    buf[50], buf[51] = hi, lo
    return bytes(buf)


def build_polling_short(hz: int) -> bytes:
    """Reporte corto 9B para cambio realtime: 06 09 01 VAL CHK 00..

    OJO: usa complemento a uno como checksum, mientras que build_dpi_report usa suma
    aditiva. Solo uno de los dos puede ser correcto: falta captura usbmon de un cambio
    de polling real antes de enviar esto al hardware.
    """
    try:
        v = POLLING_MAP[hz]
    except KeyError:
        raise ValueError(f"polling {hz}Hz no soportado, usa {sorted(POLLING_MAP)}") from None
    return bytes([RID_POLLING, 0x09, 0x01, v, 0xFF - v, 0, 0, 0, 0])


def stage_from_event(sub: int) -> int | None:
    """El firmware reporta el stage 1-indexado (igual que buf[24] = active + 1).

    Devuelve el indice 0-based o None si el valor no es un stage valido.
    """
    if 1 <= sub <= MAX_STAGES:
        return sub - 1
    return None


def parse_status(data: bytes) -> dict:
    """Input 0x03 4B: [03 EV SUB BAT]. EV 0x11 heartbeat, 0x10 boton DPI, 0x50 ACK."""
    if len(data) < 4 or data[0] != RID_STATUS:
        return {"valid": False}
    ev, sub, raw_bat = data[1], data[2], data[3]
    out: dict = {"valid": True, "raw": bytes(data).hex(" "), "event": ev, "sub": sub}
    if sub == 0x50 or ev in (0x12, 0x21, 0x50):
        out["type"] = "ack"
    elif ev == 0x11:
        out["type"] = "heartbeat"
        out["battery_raw"] = raw_bat
        pct = raw_bat * 10
        # Sin documentar: 0xFF y similares no son porcentajes. No inventamos semantica.
        out["battery"] = pct if pct <= 100 else None
    elif ev == 0x10:
        out["type"] = "dpi_button"
        out["active_stage"] = sub
        out["stage_index"] = stage_from_event(sub)
    else:
        out["type"] = "unknown"
    return out
