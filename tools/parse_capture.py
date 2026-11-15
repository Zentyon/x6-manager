"""Decodifica una captura de hid_capture.log (bytes que escribe el software oficial).

    python tools/parse_capture.py C:\\Users\\Zent\\AppData\\Local\\Temp\\x6-capture\\hid_capture.log
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

LINE = re.compile(r"^(\d\d:\d\d:\d\d\.\d{3})\s+SET_FEATURE len=(\d+) \[[^\]]*\] ([0-9a-f ]+)")

DPI_STAGES = 8


def u16(lows, highs, i):
    return lows[i] | (highs[i] << 8)


def decode_04(data: bytes) -> str:
    lows = data[8:8 + DPI_STAGES]
    highs = data[16:16 + DPI_STAGES]
    stages = [((u16(lows, highs, i) + 1) * 50) for i in range(DPI_STAGES)]
    active = data[24]
    colors = []
    for i in range(8):
        b, g, r = data[25 + i * 3:28 + i * 3]
        colors.append(f"#{r:02x}{g:02x}{b:02x}")
    body = data[3:50]
    sum_body = sum(body) & 0xFFFF
    chk = (data[50] << 8) | data[51]
    ok = "OK" if sum_body == chk else f"NO (calc {sum_body:#06x})"
    return (f"0x04 DPI: stages={stages} activo={active} "
            f"colors={colors} chk={data[50]:02x}{data[51]:02x} [{ok}] "
            f"tail={data[52:].hex(' ')}")


def decode_05(data: bytes) -> str:
    return ("0x05 otros: " + " ".join(f"{b:02x}" for b in data[3:]) +
            f"  (len={len(data)})")


def decode_06(data: bytes) -> str:
    hz = {0x08: 125, 0x04: 250, 0x02: 500, 0x01: 1000}.get(data[3], "?")
    comp = "complemento OK" if data[4] == (0xFF - data[3]) else "complemento raro"
    return f"0x06 polling: {hz} Hz (val={data[3]:#04x}, {comp})"


def decode_08(data: bytes) -> str:
    body = data[3:]
    chk = body[-1]
    entries = body[:-1]
    triples = [entries[i:i + 3] for i in range(0, len(entries) - len(entries) % 3, 3)]
    s = sum(entries) & 0xFF
    ok = "OK" if s == chk else f"NO (sum={s:#04x})"
    rows = " ".join(
        f"{i + 1}:{t[0]:02x}" + ("" if t[1] == 0 and t[2] == 0 else f".{t[1]:02x}.{t[2]:02x}")
        for i, t in enumerate(triples)
    )
    return (f"0x08 botones ({len(triples)} entradas): {rows} "
            f"chk={chk:02x} [{ok}] rest={len(entries) % 3}")


def decode_0c(data: bytes) -> str:
    return "0x0c commit/modo: " + " ".join(f"{b:02x}" for b in data[1:])


DECODERS = {0x04: decode_04, 0x05: decode_05, 0x06: decode_06,
            0x08: decode_08, 0x0C: decode_0c}


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "hid_capture.log")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.match(line)
        if not m:
            continue
        ts, _length, hexbytes = m.groups()
        data = bytes.fromhex(hexbytes.replace(" ", ""))
        rid = data[0]
        fn = DECODERS.get(rid)
        text = fn(data) if fn else f"0x{rid:02x} sin decodificar: {data.hex(' ')}"
        print(f"{ts}  {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
