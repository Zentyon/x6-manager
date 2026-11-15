"""Sonda de botones: escucha (sin bloquear) raton+teclado con pynput y, en paralelo,
vuelca el canal vendor del dongle. Sirve para mapear que manda cada boton fisico.

    python tools/probe_buttons.py --seconds 45
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hid  # noqa: E402

from x6core.protocol import PID, VID  # noqa: E402

_lock = threading.Lock()
_t0 = time.monotonic()


def emit(kind: str, *parts) -> None:
    with _lock:
        print(f"{time.monotonic() - _t0:7.2f}s [{kind:5s}] " + " ".join(map(str, parts)),
              flush=True)


def vendor_targets() -> list[dict]:
    devices = hid.enumerate(VID, PID)
    # la coleccion que realmente empuja 0x03 en este firmware
    return [d for d in devices if d.get("interface_number") == 2
            and d.get("usage_page") == 0x0A]


def vendor_loop(seconds: float) -> None:
    targets = vendor_targets()
    if not targets:
        emit("vendor", "ninguna coleccion usage_page 0x0A")
        return
    dev = hid.device()
    dev.open_path(targets[0]["path"])
    dev.set_nonblocking(True)
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            try:
                raw = bytes(dev.read(64) or b"")
            except OSError as exc:
                emit("vendor", f"read error: {exc}")
                return
            if raw:
                emit("vendor", f"{len(raw)}B {raw.hex(' ')}")
            else:
                time.sleep(0.01)
    finally:
        dev.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=45)
    args = ap.parse_args()

    from pynput import keyboard, mouse  # import perezoso

    def on_click(x, y, button, pressed):
        emit("mouse", button, "DOWN" if pressed else "UP", f"({x},{y})")

    def on_press(key):
        emit("key", "DOWN", repr(key))

    def on_release(key):
        emit("key", "UP  ", repr(key))

    ml = mouse.Listener(on_click=on_click)
    kl = keyboard.Listener(on_press=on_press, on_release=on_release)
    ml.start()
    kl.start()
    vt = threading.Thread(target=vendor_loop, args=(args.seconds,), daemon=True)
    vt.start()
    emit("info", f"escuchando {args.seconds}s (sin bloquear eventos)")
    vt.join()
    ml.stop()
    kl.stop()
    emit("info", "fin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
