"""Vigila el estado de silencio del audio del sistema.

Sirve para verificar de forma objetiva que una accion `media: mute` del daemon
llega al sistema operativo (sin depender de la ventana que tenga el foco).

    python tools/mute_state.py --seconds 60
"""
from __future__ import annotations

import argparse
import sys
import time


def muted() -> bool | None:
    try:
        from pycaw.pycaw import AudioUtilities
    except ImportError:
        return None
    devices = AudioUtilities.GetSpeakers()
    volume = getattr(devices, "EndpointVolume", None)
    if volume is None:
        from comtypes import CLSCTX_ALL
        from ctypes import POINTER, cast
        from pycaw.pycaw import IAudioEndpointVolume

        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
    return bool(volume.GetMute())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60.0)
    args = ap.parse_args()

    last = muted()
    if last is None:
        print("falta pycaw/comtypes", flush=True)
        return 1
    print(f"estado inicial: {'MUTE' if last else 'con sonido'}", flush=True)
    start = time.monotonic()
    while time.monotonic() - start < args.seconds:
        current = muted()
        if current != last:
            print(f"{time.monotonic() - start:6.2f}s -> "
                  f"{'MUTE' if current else 'con sonido'}", flush=True)
            last = current
        time.sleep(0.2)
    print("fin", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
