"""Comprueba que el dongle acepta el report 0x09 (macros onboard).

Escribe un macro de prueba en el slot 18 (sin boton asignado, asi que no cambia
el comportamiento del raton) y espera el ACK. Sirve para diagnosticar el enlace
cuando una escritura falla.

    py tools/probe_macro.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from x6core.protocol import build_macro_reports
from x6core.transport import Dongle

SLOT = 18
EVENTS = [("a", True, 0), ("a", False, 50)]


def main() -> int:
    reports = build_macro_reports(EVENTS, slot=SLOT)
    print("chunks:", [len(r) for r in reports],
          "longitudes utiles:", [r[1] for r in reports])
    try:
        with Dongle.open() as dongle:
            print("ACK:", "si" if dongle.apply_macro(reports) else "NO")
    except OSError as exc:
        print("error de comunicacion:", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
