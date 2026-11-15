"""Monitor de teclas/raton para comprobar que emiten los botones del X6.

Uso:
    python tools/watch_keys.py --seconds 120

Imprime cada evento con timestamp. Sirve para verificar que los dos botones de
arriba mandan F13/F14 despues de escribir el report 0x08.
"""
from __future__ import annotations

import argparse
import time

from pynput import keyboard, mouse


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=120.0)
    args = ap.parse_args()

    start = time.monotonic()

    def stamp() -> str:
        return f"{time.monotonic() - start:7.2f}s"

    def on_press(key):
        print(f"{stamp()} KEY DOWN {key!r}", flush=True)

    def on_release(key):
        print(f"{stamp()} KEY UP   {key!r}", flush=True)

    def on_click(x, y, button, pressed):
        print(f"{stamp()} MOUSE {'DOWN' if pressed else 'UP  '} {button!r}",
              flush=True)

    def on_scroll(x, y, dx, dy):
        print(f"{stamp()} WHEEL dx={dx} dy={dy}", flush=True)

    kl = keyboard.Listener(on_press=on_press, on_release=on_release)
    ml = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
    kl.start()
    ml.start()
    print(f"escuchando {args.seconds:.0f}s...", flush=True)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    kl.stop()
    ml.stop()
    print("fin", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
