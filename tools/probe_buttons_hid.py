"""Lee en crudo lo que el dongle manda por las colecciones de teclado y raton.

Sirve para distinguir dos casos cuando `watch_keys.py` no ve nada:

  * el mouse no manda ningun report (config no aplicada / firmware),
  * el mouse manda un report que Windows/pynput no traduce a una tecla conocida.

    python tools/probe_buttons_hid.py --seconds 60

Imprime cada report con su coleccion de origen. No escribe nada en el mouse.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hid

from x6core.protocol import PID, VID

HIDP_ERROR_DEVICE_NOT_FOUND = "unable to open device"


def name_of(info: dict) -> str:
    return (f"if{info.get('interface_number')} "
            f"page={info.get('usage_page'):#04x} usage={info.get('usage'):#04x}")


def worker(info: dict, stop: threading.Event, start: float) -> None:
    dev = hid.device()
    try:
        dev.open_path(info["path"])
        dev.set_nonblocking(True)
    except Exception as exc:
        print(f"{name_of(info):38s} NO ABIERTO ({exc})", flush=True)
        return
    print(f"{name_of(info):38s} escuchando", flush=True)
    try:
        while not stop.is_set():
            data = dev.read(64)
            if data:
                elapsed = time.monotonic() - start
                print(f"{elapsed:7.2f}s {name_of(info):38s} "
                      f"{bytes(data).hex(' ')}", flush=True)
            else:
                time.sleep(0.005)
    finally:
        try:
            dev.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60.0)
    args = ap.parse_args()

    devices = list(hid.enumerate(VID, PID))
    # Solo las colecciones que pueden llevar pulsaciones: teclado (0x1) y
    # raton (0x2), en cualquier interfaz. Evitamos la de configuracion.
    targets = [d for d in devices
               if d.get("usage_page") in (0x01, 0x02)
               and d.get("usage") not in (0x80,)]
    if not targets:
        print("no hay colecciones de teclado/raton", flush=True)
        return 1

    stop = threading.Event()
    start = time.monotonic()
    threads = [threading.Thread(target=worker, args=(d, stop, start), daemon=True)
               for d in targets]
    for t in threads:
        t.start()
    print(f"escuchando {args.seconds:.0f}s; pulsa los botones...", flush=True)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    stop.set()
    for t in threads:
        t.join(timeout=1.0)
    print("fin", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
