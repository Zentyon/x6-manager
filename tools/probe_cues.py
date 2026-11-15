"""Prueba guiada por pitidos: cada pitido indica "pulsa el boton N de la lista".

Sirve para mapear botones fisicos sin depender de que el usuario y el log coincidan en
tiempo. El pitido y la marca `CUE n` se emiten en el mismo instante.

    python tools/probe_cues.py --interval 4
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

CUES = ["left", "right", "middle", "forward", "backward", "dpi", "dpi_hold"]


def emit(kind: str, *parts) -> None:
    with _lock:
        print(f"{time.monotonic() - _t0:7.2f}s [{kind:8s}] " + " ".join(map(str, parts)),
              flush=True)


def beep(times: int = 1) -> None:
    try:
        import winsound

        for _ in range(times):
            winsound.Beep(880, 120)
            time.sleep(0.18)
    except Exception:
        print("\a", end="", flush=True)


def cue_loop(interval: float, cues: list[str], delay: float = 0.0) -> None:
    if delay:
        emit("info", f"empiezo en {delay:.0f}s...")
        time.sleep(delay)
    time.sleep(0.5)
    for i, label in enumerate(cues, start=1):
        emit("CUE", i, label)
        beep(2 if label.startswith("dpi") else 1)
        time.sleep(interval)
    emit("CUE", "fin")


def vendor_loop(seconds: float) -> None:
    targets = [d for d in hid.enumerate(VID, PID)
               if d.get("interface_number") == 2 and d.get("usage_page") == 0x0A]
    if not targets:
        emit("vendor", "sin coleccion 0x0A")
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
    ap.add_argument("--interval", type=float, default=4.0)
    ap.add_argument("--delay", type=float, default=0.0,
                    help="espera inicial antes del primer pitido")
    ap.add_argument("--cues", default=",".join(CUES))
    args = ap.parse_args()
    cues = [c for c in args.cues.split(",") if c]
    total = args.delay + 1.0 + len(cues) * args.interval + 2

    from pynput import keyboard, mouse

    def on_click(x, y, button, pressed):
        emit("mouse", button, "DOWN" if pressed else "UP")

    def on_scroll(x, y, dx, dy):
        emit("scroll", dx, dy)

    def on_press(key):
        emit("key", "DOWN", repr(key))

    def on_release(key):
        emit("key", "UP  ", repr(key))

    ml = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
    kl = keyboard.Listener(on_press=on_press, on_release=on_release)
    ml.start()
    kl.start()
    vt = threading.Thread(target=vendor_loop, args=(total,), daemon=True)
    vt.start()
    emit("info", f"{total:.0f}s, {len(cues)} pitidos cada {args.interval}s")
    cue_loop(args.interval, cues, delay=args.delay)
    vt.join()
    ml.stop()
    kl.stop()
    emit("info", "fin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
