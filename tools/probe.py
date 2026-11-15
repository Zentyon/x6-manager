"""Sonda de solo lectura del dongle X6.

Vuelca a pantalla todos los reports HID que llegan, con timestamp, para diagnosticar
si el mouse transmite y que manda en cada coleccion. No escribe nada en el hardware.

    python tools/probe.py --seconds 60
    python tools/probe.py --seconds 60 --interface 2 --usage-page 1 --usage 0x80
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hid  # noqa: E402

from x6core.protocol import PID, VID, parse_status  # noqa: E402

_lock = threading.Lock()


def dump(info: dict, seconds: float, label: str) -> None:
    path = info.get("path")
    if isinstance(path, bytes):
        path = path.decode("utf-8", "replace")
    dev = hid.device()
    try:
        dev.open_path(info["path"])
        dev.set_nonblocking(True)
    except Exception as exc:
        with _lock:
            print(f"[{label}] no se pudo abrir: {exc}", flush=True)
        return
    end = time.monotonic() + seconds
    count = 0
    with _lock:
        print(f"[{label}] abierto ({path.split('#')[-1][:30]})", flush=True)
    try:
        while time.monotonic() < end:
            try:
                raw = bytes(dev.read(64) or b"")
            except OSError as exc:
                with _lock:
                    print(f"[{label}] read error: {exc} (coleccion no legible)", flush=True)
                break
            if not raw:
                time.sleep(0.01)
                continue
            count += 1
            st = parse_status(raw)
            extra = ""
            if st.get("valid"):
                extra = f" type={st['type']}"
                if st["type"] == "heartbeat":
                    extra += f" battery_raw={st.get('battery_raw')} battery={st.get('battery')}"
                elif st["type"] == "dpi_button":
                    extra += f" stage={st.get('active_stage')} idx={st.get('stage_index')}"
            with _lock:
                print(f"{time.monotonic():8.3f} [{label}] {len(raw):2d}B "
                      f"{raw[:12].hex(' ')}{extra}", flush=True)
    finally:
        dev.close()
        with _lock:
            print(f"[{label}] {count} reports", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--all-interfaces", action="store_true",
                    help="ademas del canal vendor, escucha raton/teclado")
    ap.add_argument("--mouse-only", action="store_true",
                    help="solo la coleccion de raton (usage 1/0x2)")
    args = ap.parse_args()

    devices = hid.enumerate(VID, PID)
    if not devices:
        print(f"dongle {VID:04x}:{PID:04x} no encontrado", file=sys.stderr)
        return 1

    def name(d: dict) -> str:
        return f"if{d.get('interface_number')}/{d.get('usage_page')}:{d.get('usage', 0):#x}"

    targets = [d for d in devices if d.get("interface_number") == 2 and d.get("usage_page") == 1]
    if args.all_interfaces:
        targets = devices
    elif args.mouse_only:
        targets = [d for d in devices
                   if d.get("usage_page") == 1 and d.get("usage") == 2]
    if not targets:
        print("sin interfaces utiles", file=sys.stderr)
        return 1

    threads = [threading.Thread(target=dump, args=(d, args.seconds, name(d)), daemon=True)
               for d in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
