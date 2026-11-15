"""Comprueba si el dongle ACKea un SET_FEATURE y en que coleccion.

Reenvia el ultimo report 0x08 capturado (mismo estado, no cambia nada) y escucha
3 segundos en la coleccion de status. Sirve para validar el ACK antes de escribir.

    python tools/probe_ack.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hid

from x6core.protocol import PID, VID

LAST_0X08 = bytes.fromhex(
    "083b010200000300000400000d00001000030800000600000500003c0000"
    "0100000100000100000100000100000100000100000100000a00000900000092"
)


def main() -> int:
    devices = [d for d in hid.enumerate(VID, PID) if d.get("interface_number") == 2]
    for d in devices:
        print(f"if2 usage={d.get('usage_page'):#x}:{d.get('usage'):#x} "
              f"{str(d.get('path'))[-12:]}")

    target = next((d for d in devices if d.get("usage_page") == 0x0A), None)
    if target is None:
        print("no hay coleccion 0x0A")
        return 1
    dev = hid.device()
    dev.open_path(target["path"])
    dev.set_nonblocking(True)
    try:
        print("enviando 0x08 actual (sin cambiar nada)...")
        n = dev.send_feature_report(LAST_0X08)
        print("send_feature_report ->", n)
        print("escuchando 3 s...")
        end = time.monotonic() + 3
        while time.monotonic() < end:
            data = dev.read(64)
            if data:
                print("  IN", bytes(data).hex(" "))
            else:
                time.sleep(0.01)
    finally:
        dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
