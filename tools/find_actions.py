"""Busca en los binarios del software oficial la tabla de acciones de botones.

La captura del report 0x08 da 18 entradas de 3 bytes. Los valores por defecto
(02 03 04 0d 1b 1c 06 05 3c ...) tienen que estar en algun sitio del .exe o de las
DLL: si aparecen juntos, alrededor estara la tabla que traduce "tecla elegida en la
UI" a codigo de accion.

    python tools/find_actions.py
"""
from __future__ import annotations

import os
import re
import sys

BASE = r"C:\Program Files (x86)\Attack SharkX6Mouse"
DEFAULTS = bytes([0x02, 0x00, 0x00, 0x03, 0x00, 0x00, 0x04, 0x00, 0x00,
                  0x0D, 0x00, 0x00, 0x1B, 0x00, 0x00, 0x1C, 0x00, 0x00])
SHORT = bytes([0x02, 0x03, 0x04, 0x0D, 0x1B, 0x1C, 0x06, 0x05, 0x3C])


def context(data: bytes, pos: int, before: int = 16, after: int = 32) -> str:
    a = max(0, pos - before)
    b = min(len(data), pos + after)
    return data[a:b].hex(" ")


def main() -> int:
    for name in sorted(os.listdir(BASE)):
        if not name.lower().endswith((".exe", ".dll", ".data")):
            continue
        path = os.path.join(BASE, name)
        if not os.path.isfile(path):
            continue
        data = open(path, "rb").read()
        hits = []
        for pat, label in ((DEFAULTS, "defaults 3B"), (SHORT, "defaults 1B")):
            start = 0
            while True:
                pos = data.find(pat, start)
                if pos < 0:
                    break
                hits.append((label, pos))
                start = pos + 1
        print(f"--- {name} ({len(data)} bytes): {len(hits)} coincidencias")
        for label, pos in hits[:12]:
            print(f"  {label} @ {pos:#x}: {context(data, pos)}")

        # strings UTF-16 chinas (para localizar la lista de teclas de la UI)
        wide = re.findall(rb"(?:[\x00-\xff][\x4e-\x9f]){2,}", data)
        if wide:
            print(f"  cadenas CJK anchas: {len(wide)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
