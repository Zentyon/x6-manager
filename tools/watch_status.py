"""Monitor del canal vendor: imprime y decodifica cada report 0x03 del dongle.

Sirve para ver que hace el mouse cuando pulsas un boton que no manda teclas
(p.ej. el DPI fisico o "easy aim"): el firmware avisa por aqui.

    python tools/watch_status.py --seconds 60
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from x6core.protocol import parse_status
from x6core.transport import Dongle


def describe(st: dict) -> str:
    kind = st.get("type")
    if kind == "ack":
        return f"ACK report=0x{st['ack_report']:02x} byte3={st.get('profile')}"
    if kind == "dpi":
        return f"BOTON DPI -> stage {st.get('stage')} (bateria {st.get('battery')}%)"
    if kind == "status":
        return (f"heartbeat stage={st.get('stage')} battery={st.get('battery')}% "
                f"(raw {st.get('battery_raw')})")
    return f"desconocido {st.get('raw')}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60.0)
    args = ap.parse_args()

    start = time.monotonic()
    with Dongle.open() as dongle:
        print(f"escuchando {args.seconds:.0f}s; pulsa los botones...", flush=True)
        while time.monotonic() - start < args.seconds:
            data = dongle.read_report(200)
            if not data:
                continue
            stamp = f"{time.monotonic() - start:7.2f}s"
            if data[0] != 0x03:
                print(f"{stamp} OTRO {data.hex(' ')}", flush=True)
                continue
            print(f"{stamp} {data.hex(' '):20s} {describe(parse_status(data))}",
                  flush=True)
    print("fin", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
