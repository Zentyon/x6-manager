"""Protocolo Attack Shark X6 - decodificado de X6.exe + validado en dongle real.
Fuente: blak0p/attack-shark-linux docs/protocol-x6.md + descriptor local hidraw5.
Todo lo que escribe va por SET_REPORT feature 0x0304 en interfaz 2.
La memoria NO es legible (GET_REPORT = STALL): hay que guardar estado local.
"""
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
POLLING_MAP = {125: 0x08, 250: 0x04, 500: 0x02, 1000: 0x01}


def dpi_to_wire(dpi: int) -> int:
    """X6 guarda DPI/50-1 en LE de 16 bits (distinto del X3 que usa indice)."""
    assert DPI_MIN <= dpi <= DPI_MAX and dpi % DPI_STEP == 0, dpi
    return dpi // 50 - 1


def wire_to_dpi(w: int) -> int:
    return (w + 1) * 50


def checksum(payload: bytes) -> tuple[int, int]:
    """Suma aditiva de payload[3:50], BE en [-2:]. Validado: factory payload aceptado rc=54 + ACK."""
    s = sum(payload[3:50]) & 0xFFFF
    return (s >> 8) & 0xFF, s & 0xFF


def build_dpi_report(stages: list[int], colors: list[int], active: int,
                     enabled_mask: int | None = None) -> bytes:
    """stages: hasta 8 DPIs. colors: 8 ints 0xRRGGBB. active: 0-7. Retorna 52B con ID 0x04."""
    assert 1 <= len(stages) <= 8
    assert len(colors) == 8 and 0 <= active < len(stages)
    n = len(stages)
    if enabled_mask is None:
        enabled_mask = (1 << n) - 1
    buf = bytearray(52)
    buf[0] = RID_DPI
    buf[1] = 0x38  # 56? largo fijo que espera el dongle (builder FUN_004143a0)
    buf[2] = 0x01  # profile index
    buf[5] = enabled_mask & 0xFF
    for i, dpi in enumerate(stages):
        w = dpi_to_wire(dpi)
        buf[8 + i] = w & 0xFF            # lows
        buf[16 + i] = (w >> 8) & 0xFF    # highs
    buf[24] = active + 1  # 1-indexed en el alambre
    for i, c in enumerate(colors):
        # 3B por color en orden BGR en [25..48]
        buf[25 + i * 3] = c & 0xFF
        buf[26 + i * 3] = (c >> 8) & 0xFF
        buf[27 + i * 3] = (c >> 16) & 0xFF
    buf[49] = 0x02  # fijo
    hi, lo = checksum(bytes(buf))
    buf[50], buf[51] = hi, lo
    return bytes(buf)


def build_polling_short(hz: int) -> bytes:
    """Reporte corto 9B para cambio realtime: 06 09 01 VAL CHK 00.."""
    v = POLLING_MAP[hz]
    return bytes([RID_POLLING, 0x09, 0x01, v, 0xFF - v, 0, 0, 0, 0])


def parse_status(data: bytes) -> dict:
    """Input 0x03 4B: [03 EV SUB BAT]. EV 0x11 heartbeat, 0x10 boton DPI, 0x50 ACK."""
    if len(data) < 4 or data[0] != RID_STATUS:
        return {"valid": False}
    ev, sub, bat = data[1], data[2], data[3]
    out = {"valid": True, "raw": data.hex(" "), "event": ev, "sub": sub}
    if sub == 0x50 or ev in (0x12, 0x21, 0x50):
        out.update({"type": "ack"})
    elif ev == 0x11:
        out.update({"type": "heartbeat", "battery": bat * 10})
    elif ev == 0x10:
        out.update({"type": "dpi_button", "active_stage": sub})
    elif ev == 0x50 or sub == 0x50 or ev & 0x50:
        out.update({"type": "ack"})
    else:
        out.update({"type": "unknown"})
    return out
