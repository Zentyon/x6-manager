"""Protocolo Attack Shark X6, medido en el hardware y capturado del software oficial.

Todo lo que hay aqui esta verificado con dos fuentes independientes (2026-11-14,
dongle `1d57:fa60`, firmware 0x0113):

  1. Lectura pasiva del dongle.
  2. Captura de los `HidD_SetFeature` del software oficial `Attack SharkX6Mouse`
     (ver `tools/hidshim/`), con checksums comprobados byte a byte.

Canal de entrada (unico report que empuja el dongle)
----------------------------------------------------
Status de 5 bytes cada ~2.0 s en la coleccion **usage_page 0x0A** de la interfaz 2:

    03 10 40 01 0a      # rid=03 evento=10 bateria?=0x40 perfil?=01 trailer 0a

La coleccion `1/0x80` abre pero nunca entrega datos. `0x11` no existe en este firmware.

Memoria: SOLO ESCRITURA
-----------------------
`GET_FEATURE_REPORT` de los IDs 0x01..0x0B devuelve STALL en las cuatro colecciones de
la interfaz 2. No se puede leer la config del mouse: hay que guardar copia local.

Reports de escritura (todos por feature report, primer byte = report ID)
-----------------------------------------------------------------------
    0x0C  10 B   "commit"/modo, se envia al empezar una sincronizacion completa
    0x04  56 B   DPI: 8 stages, colores, stage activo, mascara
    0x05  15 B   varios (sleep/LED/bateria); sin decodificar, se reenvia tal cual
    0x06   9 B   polling rate (valor + complemento a uno)
    0x08  59 B   remapeo de botones: 18 entradas de 3 bytes
    0x09  63 B   macros (no capturado)

Formato 0x04 (56 bytes, verificado contra captura):
    [0]=0x04 [1]=0x38 [2]=perfil(1) [5]=mascara de stages activos
    [8..15]=DPI bajo (8 bits) [16..23]=DPI alto  -> valor = dpi/50 - 1
    [24]=stage activo + 1
    [25..48]=8 colores en BGR
    [49]=1 (constante observada)
    [50..51]=suma de [3:50] en big-endian
    [52..55]=0

Formato 0x08 (59 bytes, verificado contra captura):
    [0]=0x08 [1]=0x3b [2]=perfil(1)
    [3..56]=18 entradas de 3 bytes [codigo, arg1, arg2]
    [57]=0
    [58]=suma de [3:57] & 0xFF

Formato 0x06 (9 bytes, verificado):
    [0]=0x06 [1]=0x09 [2]=0x01 [3]=valor [4]=~valor [5..8]=0

Formato 0x0C (10 bytes, verificado):
    0c 0a 01 fe 01 fe 00 00 00 00
"""
from __future__ import annotations

from typing import Sequence

VID = 0x1D57
PID = 0xFA60
HID_INTERFACE = 2
STATUS_USAGE_PAGE = 0x0A   # coleccion que entrega el heartbeat/ACK 0x03
STATUS_USAGE = 0x00
CONFIG_USAGE_PAGE = 0x0B   # coleccion que acepta los SET_FEATURE de configuracion
CONFIG_USAGE = 0x00
LEGACY_USAGE_PAGE = 0x01   # 1/0x80: abre pero no entrega reports en este firmware
LEGACY_USAGE = 0x80
SET_REPORT_VALUE = 0x0304

RID_STATUS = 0x03      # input 5B: status/heartbeat
RID_DPI = 0x04         # feature 56B: DPI, colores, stage activo
RID_MISC = 0x05        # feature 15B: sleep/LED (sin decodificar)
RID_POLLING = 0x06     # feature 9B: polling rate
RID_BUTTONS = 0x08     # feature 59B: remapeo de botones (18 entradas)
RID_MACRO = 0x09       # feature 63B: macros (sin capturar)
RID_COMMIT = 0x0C      # feature 10B: commit / modo escritura

DPI_MIN, DPI_MAX, DPI_STEP = 50, 26000, 50
MAX_STAGES = 8
DPI_PAYLOAD_LEN = 56
BUTTON_PAYLOAD_LEN = 59
BUTTON_SLOTS = 18
MISC_PAYLOAD_LEN = 15
POLLING_PAYLOAD_LEN = 9
MACRO_PAYLOAD_LEN = 63
COMMIT_PAYLOAD_LEN = 10

# Longitud exacta de cada feature report (el firmware valida el byte 1).
REPORT_LENGTHS = {
    RID_DPI: DPI_PAYLOAD_LEN,
    RID_MISC: MISC_PAYLOAD_LEN,
    RID_POLLING: POLLING_PAYLOAD_LEN,
    RID_BUTTONS: BUTTON_PAYLOAD_LEN,
    RID_MACRO: MACRO_PAYLOAD_LEN,
}
# Orden que usa el software oficial al sincronizar (sin 0x0C: es peligroso/desconocido).
WRITE_ORDER = (RID_DPI, RID_MISC, RID_POLLING, RID_BUTTONS, RID_MACRO)

POLLING_MAP = {125: 0x08, 250: 0x04, 500: 0x02, 1000: 0x01}
POLLING_DEFAULT = 0x01          # 1000 Hz

DEFAULT_COLORS = (0x0000FF, 0x00FF00, 0xFF0000, 0x00FFFF,
                  0xFFFF00, 0xFF00FF, 0x0040FF, 0xFFFFFF)
"""Colores por defecto observados (en el alambre van en BGR; aqui 0xRRGGBB)."""

COMMIT_TEMPLATE = bytes([RID_COMMIT, 0x0A, 0x01, 0xFE, 0x01, 0xFE, 0, 0, 0, 0])

STATUS_LEN = 5
STATUS_EVENT = 0x10        # primer byte de datos del heartbeat observado
STATUS_TRAILER = 0x0A      # ultimo byte observado, constante


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
    """X6 guarda DPI/50 - 1 en 16 bits."""
    return validate_dpi(dpi) // DPI_STEP - 1


def wire_to_dpi(w: int) -> int:
    if not 0 <= w <= 0xFFFF:
        raise ValueError(f"valor de alambre invalido: {w}")
    return (w + 1) * DPI_STEP


def dpi_checksum(payload: bytes) -> tuple[int, int]:
    """Suma de payload[3:50] en big-endian en [50:52]. Verificado contra captura."""
    s = sum(payload[3:50]) & 0xFFFF
    return (s >> 8) & 0xFF, s & 0xFF


def build_dpi_report(stages: Sequence[int], colors: Sequence[int] | None = None,
                     active: int = 0, enabled_mask: int | None = None,
                     profile: int = 1) -> bytes:
    """Report 0x04 (56B). `stages` hasta 8 DPIs, `active` 0-based."""
    stages = list(stages)
    # el software oficial rellena los 8 colores aunque haya menos stages
    colors = list(DEFAULT_COLORS) if not colors else list(colors)
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
        if not isinstance(c, int) or not 1 <= c <= 0xFFFFFF:
            raise ValueError(f"color {i} invalido: {c!r} (esperado 0xRRGGBB)")
    if not 1 <= profile <= 5:
        raise ValueError(f"perfil fuera de rango: {profile}")

    n = len(stages)
    if enabled_mask is None:
        enabled_mask = (1 << n) - 1
    if not 0 <= enabled_mask <= 0xFF:
        raise ValueError(f"enabled_mask invalido: {enabled_mask}")

    buf = bytearray(DPI_PAYLOAD_LEN)
    buf[0] = RID_DPI
    buf[1] = DPI_PAYLOAD_LEN
    buf[2] = profile
    buf[5] = enabled_mask
    buf[7] = 0x01
    for i, dpi in enumerate(stages):
        w = dpi_to_wire(dpi)
        buf[8 + i] = w & 0xFF
        buf[16 + i] = (w >> 8) & 0xFF
    buf[24] = active + 1
    for i in range(MAX_STAGES):
        if i < len(colors):
            c = colors[i]
        else:
            c = DEFAULT_COLORS[i] if i < len(DEFAULT_COLORS) else 0xFFFFFF
        buf[25 + i * 3] = c & 0xFF          # B
        buf[26 + i * 3] = (c >> 8) & 0xFF   # G
        buf[27 + i * 3] = (c >> 16) & 0xFF  # R
    buf[49] = 0x01
    hi, lo = dpi_checksum(bytes(buf))
    buf[50], buf[51] = hi, lo
    return bytes(buf)


def buttons_checksum(buf: bytes) -> int:
    return sum(buf[3:57]) & 0xFF


def normalize_slot(slot) -> tuple[int, int, int]:
    """Acepta int (codigo) o (codigo, arg1, arg2) y devuelve la terna."""
    if isinstance(slot, int):
        return slot, 0, 0
    seq = tuple(slot)
    if len(seq) == 1:
        return seq[0], 0, 0
    if len(seq) == 2:
        return seq[0], seq[1], 0
    if len(seq) == 3:
        return seq[0], seq[1], seq[2]
    raise ValueError(f"entrada de boton invalida: {slot!r}")


def build_buttons_report(slots: Sequence, profile: int = 1) -> bytes:
    """Report 0x08 (59B): 18 entradas de 3 bytes. `slots` admite int o terna."""
    if len(slots) != BUTTON_SLOTS:
        raise ValueError(f"se esperan {BUTTON_SLOTS} entradas, hay {len(slots)}")
    buf = bytearray(BUTTON_PAYLOAD_LEN)
    buf[0] = RID_BUTTONS
    buf[1] = BUTTON_PAYLOAD_LEN
    buf[2] = profile
    for i, slot in enumerate(slots):
        try:
            code, arg1, arg2 = normalize_slot(slot)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"entrada {i}: {exc}") from exc
        for value in (code, arg1, arg2):
            if not isinstance(value, int) or not 0 <= value <= 0xFF:
                raise ValueError(f"entrada {i}: byte invalido {value!r}")
        buf[3 + i * 3] = code
        buf[4 + i * 3] = arg1
        buf[5 + i * 3] = arg2
    buf[57] = 0
    buf[58] = buttons_checksum(bytes(buf))
    return bytes(buf)


def build_polling_report(hz: int, profile: int = 1) -> bytes:
    """Report 0x06 (9B): valor + complemento a uno. Verificado con la captura."""
    try:
        value = POLLING_MAP[hz]
    except KeyError:
        raise ValueError(f"polling {hz}Hz no soportado, usa {sorted(POLLING_MAP)}") from None
    return bytes([RID_POLLING, POLLING_PAYLOAD_LEN, profile, value, 0xFF - value, 0, 0, 0, 0])


def build_commit_report(profile: int = 1) -> bytes:
    """Report 0x0C (10B) del software oficial.

    Solo se envia en una resincronizacion completa y su significado es desconocido;
    la documentacion del protocolo recomienda **no enviarlo**. Se conserva el builder
    para poder reproducir capturas, no para usarlo en `apply_config`.
    """
    if not 1 <= profile <= 5:
        raise ValueError(f"perfil fuera de rango: {profile}")
    buf = bytearray(COMMIT_TEMPLATE)
    buf[2] = profile
    return bytes(buf)


# --- report 0x08: mapa de botones -----------------------------------------
#
# Codigos verificados con la tabla del software oficial (misma familia X3/R1/X11/X6).
# Una entrada es (codigo, arg1, arg2); las teclas usan (0x11, modificadores, HID usage).
BUTTON_ENTRIES = {
    "off": (0x01, 0x00, 0x00),
    "left": (0x02, 0x00, 0x00),
    "right": (0x03, 0x00, 0x00),
    "middle": (0x04, 0x00, 0x00),
    "forward": (0x06, 0x00, 0x00),
    "backward": (0x05, 0x00, 0x00),
    "dpi_cycle": (0x0D, 0x00, 0x00),
    "dpi_up": (0x0E, 0x00, 0x00),
    "dpi_down": (0x0F, 0x00, 0x00),
    "double_click": (0x07, 0x00, 0x00),
    "fire": (0x08, 0x00, 0x00),
    "scroll_up": (0x09, 0x00, 0x00),
    "scroll_down": (0x0A, 0x00, 0x00),
    "tilt_left": (0x0B, 0x00, 0x00),
    "tilt_right": (0x0C, 0x00, 0x00),
    "easy_aim": (0x10, 0x00, 0x03),
    "led_loop": (0x29, 0x00, 0x03),
    "media_player": (0x15, 0x00, 0x00),
    "play_pause": (0x18, 0x00, 0x00),
    "stop": (0x19, 0x00, 0x00),
    "previous": (0x16, 0x00, 0x00),
    "next": (0x17, 0x00, 0x00),
    "volume_up": (0x1B, 0x00, 0x00),
    "volume_down": (0x1C, 0x00, 0x00),
    "mute": (0x1A, 0x00, 0x00),
    "browser_home": (0x25, 0x00, 0x00),
    "browser_favorites": (0x11, 0x03, 0x12),
    "browser_forward": (0x20, 0x00, 0x00),
    "browser_back": (0x21, 0x00, 0x00),
    "browser_stop": (0x22, 0x00, 0x00),
    "browser_refresh": (0x24, 0x00, 0x00),
    "browser_search": (0x26, 0x00, 0x00),
    "email": (0x1E, 0x00, 0x00),
    "calculator": (0x1D, 0x00, 0x00),
    "my_computer": (0x23, 0x00, 0x00),
    "cut": (0x11, 0x01, 0x1B),
    "copy": (0x11, 0x01, 0x06),
    "paste": (0x11, 0x01, 0x19),
    "open": (0x11, 0x01, 0x12),
    "save": (0x11, 0x01, 0x16),
    "find": (0x11, 0x01, 0x09),
    "redo": (0x11, 0x01, 0x1C),
    "select_all": (0x11, 0x01, 0x04),
    "print": (0x11, 0x01, 0x13),
    "close_window": (0x11, 0x04, 0x3D),
    "swap_windows": (0x11, 0x04, 0x2B),
    "show_desktop": (0x11, 0x08, 0x07),
    "run": (0x11, 0x08, 0x15),
    "lock": (0x11, 0x08, 0x0F),
    "screen_capture": (0x11, 0x0A, 0x16),
}
"""Acciones asignables a un boton (tabla del software oficial)."""

# Indice de cada entrada en el report 0x08 (0-based).
BUTTON_INDEX = {
    "left": 0,
    "right": 1,
    "middle": 2,
    "dpi_cycle": 3,     # celda de configuracion, sin boton fisico en el X6
    "dpi_up": 4,        # boton superior junto a la rueda (DPI+)
    "dpi_down": 5,      # boton superior inferior (DPI-)
    "forward": 6,       # lateral delantero
    "backward": 7,      # lateral trasero
    "profile": 8,       # 0x3c por defecto; sin documentar
    "wheel_down": 16,
    "wheel_up": 17,
}
BUTTON_INDEX_ALIASES = {
    "scroll_down": "wheel_down",
    "scroll_up": "wheel_up",
    "side1": "forward",
    "side2": "backward",
}
"""Nombres fisicos del X6 y su posicion en el report 0x08."""

# Reparto de fabrica observado en la captura (perfil ordinario de la app).
DEFAULT_BUTTONS = (
    0x02, 0x03, 0x04, 0x0D, 0x1B, 0x1C, 0x06, 0x05, 0x3C,
    0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x0A, 0x09,
)

MODIFIERS = {
    "ctrl": 0x01, "control": 0x01, "ctl": 0x01,
    "shift": 0x02,
    "alt": 0x04, "option": 0x04,
    "win": 0x08, "super": 0x08, "meta": 0x08, "cmd": 0x08, "gui": 0x08,
}

KEY_USAGE = {chr(c): 0x04 + (c - ord("a")) for c in range(ord("a"), ord("z") + 1)}
KEY_USAGE.update({str(d): 0x1E + (d - 1) for d in range(1, 10)})
KEY_USAGE["0"] = 0x27
KEY_USAGE.update({
    "enter": 0x28, "return": 0x28, "esc": 0x29, "escape": 0x29,
    "backspace": 0x2A, "tab": 0x2B, "space": 0x2C,
    "minus": 0x2D, "-": 0x2D,
    "equal": 0x2E, "equals": 0x2E, "=": 0x2E,
    "leftbracket": 0x2F, "[": 0x2F,
    "rightbracket": 0x30, "]": 0x30,
    "backslash": 0x31, "\\": 0x31,
    "semicolon": 0x33, ";": 0x33,
    "quote": 0x34, "'": 0x34,
    "grave": 0x35, "`": 0x35,
    "comma": 0x36, ",": 0x36,
    "period": 0x37, ".": 0x37,
    "slash": 0x38, "/": 0x38,
    "capslock": 0x39,
    "printscreen": 0x46, "scrolllock": 0x47, "pause": 0x48,
    "insert": 0x49, "home": 0x4A, "pageup": 0x4B,
    "delete": 0x4C, "end": 0x4D, "pagedown": 0x4E,
    "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52,
    "numlock": 0x53,
})
KEY_USAGE.update({f"f{i}": 0x3A + (i - 1) for i in range(1, 13)})
KEY_USAGE.update({f"f{i}": 0x68 + (i - 13) for i in range(13, 25)})
"""HID keyboard usage IDs usados por el report 0x08."""


def keyboard_entry(combo: str) -> tuple[int, int, int]:
    """Convierte `"ctrl+shift+s"` o `"f13"` en la terna `(0x11, mods, usage)`."""
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("atajo vacio")
    mods = 0
    key = None
    for part in parts:
        if part in MODIFIERS:
            mods |= MODIFIERS[part]
        elif key is not None:
            raise ValueError(f"atajo con mas de una tecla: {combo!r}")
        else:
            key = part
    if key is None:
        raise ValueError(f"atajo sin tecla final: {combo!r}")
    if key not in KEY_USAGE:
        raise ValueError(f"tecla desconocida {key!r} en {combo!r}")
    if mods > 0x0F:
        raise ValueError(f"demasiados modificadores: {combo!r}")
    return 0x11, mods, KEY_USAGE[key]


def action_entry(spec) -> tuple[int, int, int]:
    """Acepta una accion con nombre (`"copy"`), un atajo (`"ctrl+c"`) o una terna."""
    if isinstance(spec, int):
        return normalize_slot(spec)
    if isinstance(spec, (tuple, list)):
        return normalize_slot(spec)
    name = str(spec).strip()
    low = name.lower()
    if low in BUTTON_ENTRIES:
        return BUTTON_ENTRIES[low]
    if "+" in low or low in KEY_USAGE:
        return keyboard_entry(low)
    raise ValueError(
        f"accion desconocida {name!r}; validas: {sorted(BUTTON_ENTRIES)} "
        "o un atajo tipo ctrl+c / f13"
    )


def macro_entry(slot: int) -> tuple[int, int, int]:
    """Accion "macro" para el report 0x08: `(0x12, 0x00, slot)`.

    `slot` es el numero que se usa en el report 0x09 (indice de boton + 1).
    """
    if not isinstance(slot, int) or not 1 <= slot <= BUTTON_SLOTS:
        raise ValueError(f"slot de macro fuera de rango: {slot!r}")
    return 0x12, 0x00, slot


# --- report 0x09: macros onboard -----------------------------------------
#
# Un macro es una secuencia de eventos de teclado con su retardo, guardada en
# el raton. Se envia en 3 feature reports que juntos forman un buffer logico:
#
#   L[0]=0x09  L[1]=0x83 (longitud logica)  L[2]=slot (indice de boton + 1)
#   L[3..6]   cabecera tal cual la manda la app oficial
#   L[7]      modo de reproduccion (minimo 1)
#   L[8..27]  0
#   L[28]     numero de items de 2 bytes
#   L[29..128] items
#   L[129..130] suma de L[3..128] en big-endian
#
# En el cable cada chunk es `09 <len> <slot> <chunk#> + datos`, donde `len` es
# la longitud total del report (los 4 bytes de cabecera incluidos).
#
# Las cabeceras (semantica de L[3..7]) no estan confirmadas: la app las rellena
# con campos de su estructura de macro. Con 0 / 1 el firmware acepta el macro
# igual, segun la captura de la familia (CMS_4).

MACRO_LOGICAL_LEN = 131
MACRO_CONTENT_LEN = 126          # L[3..128]
MACRO_ITEM_OFFSET = 29
MACRO_ITEM_BYTES = 100           # L[29..128]
MACRO_ITEMS_MAX = MACRO_ITEM_BYTES // 2
MACRO_DELAY_SPLIT = 1270         # ms; por encima se parte en un item extra
MACRO_PRESS = 0x01
MACRO_RELEASE = 0x81
MACRO_LONG_MARK = 0x03
MACRO_REPORT_LEN = 64            # tamano del feature report en el X6
MACRO_REPORT_LENS = frozenset({12, 14, 63, 64})
"""Longitudes validas de un chunk de macro (12/14 = ultimo chunk sin relleno)."""


def macro_key(key) -> int:
    """Acepta un nombre de `KEY_USAGE` ("a", "enter") o un usage 0..255."""
    if isinstance(key, int) and not isinstance(key, bool):
        usage = key
    else:
        name = str(key).strip().lower()
        if name not in KEY_USAGE:
            raise ValueError(f"tecla desconocida para macro: {key!r}")
        usage = KEY_USAGE[name]
    if not 0 <= usage <= 0xFF:
        raise ValueError(f"usage fuera de rango: {usage!r}")
    return usage


def encode_macro_event(key, pressed: bool, delay_ms: int = 0) -> bytes:
    """Codifica un evento (tecla, pulsacion, retardo) en 2 o 4 bytes.

    Retardo <= 1270 ms: `[round(delay/10) | flag, usage]` (flag 0x01 pulsa,
    0x81 suelta; el bit 7 es el de "soltar").
    Retardo > 1270 ms: el evento con el resto y un item extra
    `[delay/200, 0x03]`, porque el primer byte solo tiene 7 bits utiles.
    """
    usage = macro_key(key)
    delay = int(delay_ms)
    if delay < 0:
        raise ValueError(f"retardo negativo: {delay_ms!r}")
    flag = MACRO_PRESS if pressed else MACRO_RELEASE
    if delay <= MACRO_DELAY_SPLIT:
        return bytes([(round(delay / 10) & 0x7F) | flag, usage])
    head = bytes([(round((delay % 200) / 10) & 0x7F) | flag, usage])
    return head + bytes([delay // 200, MACRO_LONG_MARK])


def build_macro_logical(events, slot: int = 1, header: bytes = b"\x00\x00\x00\x00",
                        play: int = 1) -> bytes:
    """Buffer logico de 131 bytes del report 0x09.

    `events` es una secuencia de `(tecla, pulsada, retardo_ms)`.
    """
    if not isinstance(slot, int) or not 1 <= slot <= BUTTON_SLOTS:
        raise ValueError(f"slot de macro fuera de rango: {slot!r}")
    header = bytes(header)
    if len(header) != 4:
        raise ValueError(f"la cabecera son 4 bytes, hay {len(header)}")
    if not 0 <= int(play) <= 0xFF:
        raise ValueError(f"modo de reproduccion invalido: {play!r}")

    items = b"".join(encode_macro_event(*event) for event in events)
    if len(items) > MACRO_ITEM_BYTES:
        raise ValueError(
            f"macro demasiado larga: {len(items)} B de {MACRO_ITEM_BYTES} "
            f"(maximo {MACRO_ITEMS_MAX} items de 2 B)"
        )

    buf = bytearray(MACRO_LOGICAL_LEN)
    buf[0] = RID_MACRO
    buf[1] = MACRO_LOGICAL_LEN
    buf[2] = slot
    buf[3:7] = header
    buf[7] = int(play)
    buf[28] = len(items) // 2
    buf[MACRO_ITEM_OFFSET:MACRO_ITEM_OFFSET + len(items)] = items
    checksum = sum(buf[3:MACRO_LOGICAL_LEN - 2]) & 0xFFFF
    buf[-2] = checksum >> 8
    buf[-1] = checksum & 0xFF
    return bytes(buf)


def build_macro_reports(events, slot: int = 1, report_len: int = MACRO_REPORT_LEN,
                        header: bytes = b"\x00\x00\x00\x00",
                        play: int = 1) -> list[bytes]:
    """Divide el macro en los 3 feature reports que espera el dongle.

    Todos los reports salen del tamano del HID (`report_len`, 64 en el X6,
    verificado en hardware: Windows rechaza los cortos). El ultimo lleva solo
    los 8 bytes utiles (6 de datos + checksum) y el resto a cero, con el byte
    de longitud a 12; el firmware lo acepta asi y devuelve ACK.

    Con `report_len=63` el ultimo chunk lleva 10 bytes y el byte de longitud
    vale 14 (variante de la familia, sin verificar en este dongle).
    """
    chunk = int(report_len) - 4
    if not 8 <= chunk <= MACRO_CONTENT_LEN:
        raise ValueError(f"longitud de report invalida: {report_len!r}")
    logical = build_macro_logical(events, slot=slot, header=header, play=play)

    reports: list[bytes] = []
    for index in range(3):
        start = 3 + index * chunk
        if index == 2:
            data = logical[start:]
            length = 4 + len(data)
        else:
            data = logical[start:start + chunk]
            length = report_len
        report = bytes([RID_MACRO, length, slot, index]) + bytes(data)
        if len(report) < report_len:
            report += bytes(report_len - len(report))
        reports.append(report)
    return reports


def validate_report(payload: bytes) -> bytes:
    """Comprueba report ID y longitud exacta antes de enviar nada al mouse."""
    if not payload:
        raise ValueError("report vacio")
    rid = payload[0]
    if rid == RID_MACRO:
        # Los macros van en 3 chunks: dos llenos y el ultimo con el checksum.
        if len(payload) not in MACRO_REPORT_LENS:
            raise ValueError(
                f"report {rid:#04x}: longitud {len(payload)} no valida "
                f"(esperadas {sorted(MACRO_REPORT_LENS)})"
            )
        chunk = payload[3]
        if chunk > 2:
            raise ValueError(f"report {rid:#04x}: chunk {chunk} inexistente")
        if chunk < 2:
            if payload[1] != len(payload):
                raise ValueError(
                    f"report {rid:#04x}: byte de longitud {payload[1]} != "
                    f"{len(payload)}"
                )
        else:
            if payload[1] not in (12, 14):
                raise ValueError(
                    f"report {rid:#04x}: longitud util {payload[1]} no valida"
                )
            if any(payload[payload[1]:]):
                raise ValueError("ultimo chunk del macro con relleno no nulo")
        return payload
    expected = REPORT_LENGTHS.get(rid)
    if expected is None:
        raise ValueError(f"report ID {rid:#04x} no es de escritura")
    if len(payload) != expected:
        raise ValueError(
            f"report {rid:#04x}: se esperan {expected} bytes, hay {len(payload)}"
        )
    return payload


def parse_status(data: bytes) -> dict:
    """Decodifica el report input 0x03 (5 B) del dongle.

    Formatos verificados en el X6:

        03 10 40 01 0a     heartbeat: byte2=0x40, byte3=stage activo (1-8),
                           byte4*10 = bateria % (0x0a = 100 %)
        03 10 10 03 0a     boton DPI fisico: byte3 = stage nuevo
        03 10 50 00 04     ACK: byte3 = perfil, byte4 = report ID aceptado
    """
    if len(data) < 3 or data[0] != RID_STATUS:
        return {"valid": False}
    ev = data[1]
    out: dict = {"valid": True, "raw": bytes(data).hex(" "), "event": ev}

    if ev == STATUS_EVENT and len(data) >= STATUS_LEN:
        sub = data[2]
        if sub == 0x50 and data[4] in REPORT_LENGTHS:
            out.update({
                "type": "ack",
                "ack_report": data[4],
                "profile": data[3],
            })
            return out
        battery = data[4] * 10 if data[4] <= 10 else None
        out.update({
            "type": "status",
            "sub": sub,
            "stage": data[3],
            "battery_raw": data[4],
            "battery": battery,
        })
        if sub == 0x10:
            out["type"] = "dpi"
        return out
    if ev in (0x12, 0x21, 0x50):
        out["type"] = "ack"
        return out
    out["type"] = "unknown"
    out["sub"] = data[2]
    return out
