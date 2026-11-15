"""Busca la tabla de acciones en TODOS los archivos del software oficial (recursivo)."""
from __future__ import annotations

import os
import sys

BASE = r"C:\Program Files (x86)\Attack SharkX6Mouse"
PATS = {
    "defaults 3B": bytes([0x02, 0, 0, 0x03, 0, 0, 0x04, 0, 0, 0x0D, 0, 0,
                          0x1B, 0, 0, 0x1C, 0, 0, 0x06, 0, 0, 0x05, 0, 0, 0x3C, 0, 0]),
    "defaults 1B": bytes([0x02, 0x03, 0x04, 0x0D, 0x1B, 0x1C, 0x06, 0x05, 0x3C]),
    "payload 0x08": bytes.fromhex(
        "083b010200000300000400000d00001b00001c00000600000500003c0000"
        "0100000100000100000100000100000100000100000100000a000009000000ae"),
}


def main() -> int:
    for root, _dirs, files in os.walk(BASE):
        for name in files:
            path = os.path.join(root, name)
            try:
                data = open(path, "rb").read()
            except OSError:
                continue
            found = []
            for label, pat in PATS.items():
                pos = data.find(pat)
                if pos >= 0:
                    found.append((label, pos))
            if found:
                rel = os.path.relpath(path, BASE)
                print(f"{rel} ({len(data)} bytes): {found}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
