"""Prueba de escritura real y ACK, replicando al software oficial.

El software oficial usa `HidD_SetFeature` sobre la coleccion de configuracion
(interfaz 2) y espera un ACK en la coleccion de status. hidapi devuelve 62 al
enviar 59 bytes, asi que aqui se comparan las dos rutas.

Reenvia el ultimo 0x08 capturado: mismo estado, no cambia el mouse.

    python tools/probe_write.py
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hid

from x6core.protocol import PID, VID

PAYLOAD = bytes.fromhex(
    "083b010200000300000400000d00001000030800000600000500003c0000"
    "0100000100000100000100000100000100000100000a00000900000092"
)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.HANDLE]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

hid_dll = ctypes.WinDLL("hid", use_last_error=True)
hid_dll.HidD_SetFeature.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.ULONG]
hid_dll.HidD_SetFeature.restype = wintypes.BOOLEAN


def path_text(info: dict) -> str:
    p = info.get("path") or b""
    if isinstance(p, bytes):
        p = p.decode("utf-8", "replace")
    return str(p)


def main() -> int:
    devices = [d for d in hid.enumerate(VID, PID) if d.get("interface_number") == 2]
    read_info = next(d for d in devices if d.get("usage_page") == 0x0A)
    write_info = next(d for d in devices if d.get("usage_page") == 0x0B)

    reader = hid.device()
    reader.open_path(read_info["path"])
    reader.set_nonblocking(True)

    print("--- ruta ctypes HidD_SetFeature (la del software oficial) ---")
    handle = kernel32.CreateFileW(
        path_text(write_info),
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None, OPEN_EXISTING, 0, None,
    )
    if handle == wintypes.HANDLE(-1).value:
        print("CreateFile fallo:", ctypes.get_last_error())
        return 1
    try:
        buf = ctypes.create_string_buffer(PAYLOAD, len(PAYLOAD))
        ok = hid_dll.HidD_SetFeature(handle, buf, len(PAYLOAD))
        print(f"HidD_SetFeature({len(PAYLOAD)}B) -> {bool(ok)} err={ctypes.get_last_error()}")
    finally:
        kernel32.CloseHandle(handle)

    print("--- esperando ACK 3 s en la coleccion 0x0A ---")
    end = time.monotonic() + 3
    while time.monotonic() < end:
        data = reader.read(64)
        if data:
            print("  IN", bytes(data).hex(" "))
        else:
            time.sleep(0.01)
    reader.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
