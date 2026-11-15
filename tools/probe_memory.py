"""Sonda de SOLO LECTURA de la config del dongle (con tiempo limite por llamada).

Windows no da timeout a las GET_FEATURE_REPORT de hidapi: si el firmware no responde,
la llamada se queda colgada. Por eso cada coleccion se sondea en un hilo *daemon* con
presupuesto fijo; si se cuelga, se abandona y el proceso termina igual.

No envia nada al mouse.

    python tools/probe_memory.py
    python tools/probe_memory.py --budget 30
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


def say(*parts) -> None:
    with _lock:
        print(*parts, flush=True)


def label(d: dict) -> str:
    return (f"if{d.get('interface_number')} "
            f"usage={d.get('usage_page')}:{d.get('usage', 0):#x}")


def parse_descriptor(desc: bytes) -> list[str]:
    out: list[str] = []
    i = 0
    rid = None
    size = count = 0
    while i < len(desc):
        b = desc[i]
        if b == 0xFE:
            i += 3 + (desc[i + 1] if i + 1 < len(desc) else 0)
            continue
        n = b & 0x03
        if n == 3:
            n = 4
        tag = b & 0xFC
        data = desc[i + 1:i + 1 + n]
        value = int.from_bytes(data, "little") if data else 0
        if tag == 0x84:
            rid = value
        elif tag == 0x74:
            size = value
        elif tag == 0x94:
            count = value
        elif tag in (0x80, 0x90, 0xB0):
            kind = {0x80: "input", 0x90: "output", 0xB0: "feature"}[tag]
            out.append(f"{kind} rid=0x{(rid or 0):02x} bits={size} count={count} "
                       f"bytes={(size * count + 7) // 8}")
            size = count = 0
        i += 1 + n
    return out


def probe(info: dict, deadline: float) -> None:
    name = label(info)
    dev = hid.device()
    try:
        dev.open_path(info["path"])
    except Exception as exc:
        say(f"[{name}] no abre: {exc}")
        return
    try:
        say(f"[{name}] abierto")
        if time.monotonic() < deadline:
            try:
                desc = bytes(dev.get_report_descriptor(4096))
                lines = parse_descriptor(desc)
                say(f"[{name}] descriptor {len(desc)}B")
                for line in lines:
                    say(f"[{name}]   {line}")
            except Exception as exc:
                say(f"[{name}] sin descriptor: {type(exc).__name__} {exc}")

        for rid in list(range(0x01, 0x0C)):
            if time.monotonic() >= deadline:
                say(f"[{name}] presupuesto agotado (hasta rid 0x{rid - 1:02x})")
                return
            for length in (8, 16, 32, 64):
                try:
                    say(f"[{name}] GET_FEATURE 0x{rid:02x} len={length} ...")
                    data = bytes(dev.get_feature_report(rid, length))
                except Exception as exc:
                    if length == 64:
                        say(f"[{name}] GET_FEATURE 0x{rid:02x} -> "
                            f"{type(exc).__name__}: {exc}")
                    continue
                say(f"[{name}] GET_FEATURE 0x{rid:02x} OK {len(data)}B "
                    f"{data.hex(' ')}")
                break
    finally:
        try:
            dev.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=25.0)
    args = ap.parse_args()

    devices = hid.enumerate(VID, PID)
    if not devices:
        say(f"dongle {VID:04x}:{PID:04x} no encontrado")
        return 1

    # solo las colecciones de la interfaz 2 (interfaz de configuracion del dongle)
    targets = [d for d in devices if d.get("interface_number") == 2]
    say(f"colecciones a sondear: {len(targets)} (de {len(devices)} dispositivos)")

    deadline = time.monotonic() + args.budget
    threads = []
    for info in targets:
        t = threading.Thread(target=probe, args=(info, deadline), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join(max(0.0, deadline - time.monotonic() + 2))
    say("fin de la sonda")
    return 0


if __name__ == "__main__":
    sys.exit(main())
