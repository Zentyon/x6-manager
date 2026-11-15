"""Escribe la configuracion onboard del Attack Shark X6.

La memoria del mouse **no se puede leer** (GET_FEATURE devuelve STALL), asi que este
programa mantiene una copia local (`onboard.json`) y siempre guarda un backup antes
de escribir. Los reports se envian a la coleccion 0x0B con `HidD_SetFeature` y se
espera el ACK real del dongle.

Ejemplos:

    # ver lo que se enviaria, sin tocar el mouse
    python tools/write_config.py --dry-run --set dpi_up=f13 --set dpi_down=f14

    # restaurar el DPI original capturado del software oficial
    python tools/write_config.py --dpi 800,900,1600,3200,2650,2400 --active 0

    # mapear los dos botones de arriba a F13/F14 y escribir de verdad
    python tools/write_config.py --set dpi_up=f13 --set dpi_down=f14 --yes

    # deshacer: restaurar un backup
    python tools/write_config.py --restore "%LOCALAPPDATA%\\x6-manager\\backups\\onboard-....json" --yes

    # leer el ultimo estado local (no del mouse)
    python tools/write_config.py --show
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from x6core import protocol as p  # noqa: E402
from x6core.profiles import _config_dir  # noqa: E402
from x6core.transport import Dongle  # noqa: E402

# DPI que tenia el usuario en la primera captura (antes de la sesion de captura).
CAPTURED_ORIGINAL_DPI = [800, 900, 1600, 3200, 2650, 2400]
CAPTURED_POLLING = 1000
CAPTURED_MISC = bytes.fromhex("050f015003a800ff00010401ff0000")

REPORT_NAMES = {
    0x04: "DPI/colores",
    0x05: "luces/sleep/debounce",
    0x06: "polling",
    0x08: "botones",
    0x09: "macros",
}


# --- estado local ---------------------------------------------------------
def state_file() -> Path:
    return _config_dir() / "onboard.json"


def backups_dir() -> Path:
    d = _config_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_state() -> dict | None:
    path = state_file()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_state(reports: dict[str, str]) -> None:
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "updated": dt.datetime.now().isoformat(timespec="seconds"),
        "reports": reports,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def save_backup(reports: dict[str, str], reason: str) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = backups_dir() / f"onboard-{stamp}.json"
    data = {"created": stamp, "reason": reason, "reports": reports}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def reports_from_state(state: dict | None) -> dict[int, bytes]:
    """Devuelve los reports guardados; si no hay estado, un baseline conservador."""
    out: dict[int, bytes] = {}
    if state:
        for key, value in (state.get("reports") or {}).items():
            rid = int(key, 16) if isinstance(key, str) else int(key)
            out[rid] = bytes.fromhex(value)
    if p.RID_DPI not in out:
        out[p.RID_DPI] = p.build_dpi_report(CAPTURED_ORIGINAL_DPI, active=0)
    if p.RID_BUTTONS not in out:
        out[p.RID_BUTTONS] = p.build_buttons_report(p.DEFAULT_BUTTONS)
    if p.RID_POLLING not in out:
        out[p.RID_POLLING] = p.build_polling_report(CAPTURED_POLLING)
    return out


# --- descripcion legible --------------------------------------------------
def _reverse_key(usage: int) -> str:
    for name, value in p.KEY_USAGE.items():
        if value == usage and len(name) > 1:
            return name
    for name, value in p.KEY_USAGE.items():
        if value == usage:
            return name
    return f"0x{usage:02x}"


def describe_entry(entry) -> str:
    code, arg1, arg2 = p.normalize_slot(entry)
    if code == 0x11:
        mods = [name for name, bit in (("ctrl", 1), ("shift", 2), ("alt", 4), ("win", 8))
                if arg1 & bit]
        return "+".join(mods + [_reverse_key(arg2)])
    for name, triple in p.BUTTON_ENTRIES.items():
        if triple == (code, arg1, arg2):
            return name
    return f"{code:#04x} {arg1:#04x} {arg2:#04x}"


def decode_dpi(report: bytes) -> str:
    lows, highs = report[8:16], report[16:24]
    stages = [((lows[i] | (highs[i] << 8)) + 1) * p.DPI_STEP for i in range(8)]
    colors = []
    for i in range(8):
        b, g, r = report[25 + i * 3:28 + i * 3]
        colors.append(f"#{r:02x}{g:02x}{b:02x}")
    return (f"DPI {stages} activo={report[24]} mascara={report[5]:#04x}\n"
            f"  colores {colors}")


def describe_report(report: bytes) -> str:
    rid = report[0]
    if rid == p.RID_DPI:
        return decode_dpi(report)
    if rid == p.RID_POLLING:
        hz = {v: k for k, v in p.POLLING_MAP.items()}.get(report[3], "?")
        return f"polling {hz} Hz (valor={report[3]:#04x})"
    if rid == p.RID_BUTTONS:
        rows = []
        for i in range(p.BUTTON_SLOTS):
            entry = tuple(report[3 + i * 3:6 + i * 3])
            rows.append(f"  [{i:02d}] {entry[0]:#04x} {entry[1]:#04x} {entry[2]:#04x}  "
                        f"{describe_entry(entry)}")
        return "botones:\n" + "\n".join(rows)
    return report.hex(" ")


# --- construccion ---------------------------------------------------------
def parse_set(values: list[str], slots: list) -> None:
    for item in values:
        if "=" not in item:
            raise SystemExit(f"--set espera BOTON=ACCION, no {item!r}")
        name, action = (part.strip() for part in item.split("=", 1))
        key = p.BUTTON_INDEX_ALIASES.get(name, name)
        if key not in p.BUTTON_INDEX:
            raise SystemExit(
                f"boton desconocido {name!r}; validos: {sorted(p.BUTTON_INDEX)}"
            )
        index = p.BUTTON_INDEX[key]
        slots[index] = p.action_entry(action)


def parse_list(text: str, cast=int) -> list:
    return [cast(part.strip()) for part in text.split(",") if part.strip()]


def parse_colors(text: str) -> list[int]:
    colors = []
    for part in text.split(","):
        part = part.strip().lstrip("#")
        if part:
            colors.append(int(part, 16))
    return colors


def build(args, base: dict[int, bytes]) -> dict[int, bytes]:
    reports = dict(base)

    slots = _slots_from(reports[p.RID_BUTTONS])
    parse_set(args.set, slots)
    reports[p.RID_BUTTONS] = p.build_buttons_report(slots)

    if args.dpi:
        stages = parse_list(args.dpi)
        colors = parse_colors(args.colors) if args.colors else None
        reports[p.RID_DPI] = p.build_dpi_report(
            stages, colors, active=args.active if args.active is not None else 0
        )
    elif args.active is not None:
        # solo cambia el stage activo, conservando los DPI actuales
        old = reports[p.RID_DPI]
        lows, highs = old[8:16], old[16:24]
        stages = [((lows[i] | (highs[i] << 8)) + 1) * p.DPI_STEP for i in range(8)]
        # recorta los stages deshabilitados (mascara) conservando al menos 1
        enabled = [s for i, s in enumerate(stages) if old[5] & (1 << i)] or stages[:1]
        reports[p.RID_DPI] = p.build_dpi_report(
            enabled, active=args.active, enabled_mask=old[5]
        )

    if args.polling:
        reports[p.RID_POLLING] = p.build_polling_report(args.polling)

    if args.misc_hex:
        reports[p.RID_MISC] = bytes.fromhex(args.misc_hex.replace(" ", ""))
    if args.with_misc and p.RID_MISC not in reports:
        reports[p.RID_MISC] = CAPTURED_MISC

    for rid, report in reports.items():
        p.validate_report(report)
    return reports


def _slots_from(report: bytes) -> list:
    return [tuple(report[3 + i * 3:6 + i * 3]) for i in range(p.BUTTON_SLOTS)]


# --- main -----------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", action="append", default=[], metavar="BOTON=ACCION",
                    help="p.ej. dpi_up=f13, dpi_down=f14, backward=volume_up")
    ap.add_argument("--dpi", help="lista separada por comas: 800,1200,1600,3200")
    ap.add_argument("--active", type=int, help="stage activo (0-based)")
    ap.add_argument("--colors", help="colores 0xRRGGBB separados por comas")
    ap.add_argument("--polling", type=int, choices=sorted(p.POLLING_MAP))
    ap.add_argument("--misc-hex", help="report 0x05 completo en hex (avanzado)")
    ap.add_argument("--with-misc", action="store_true",
                    help="incluye el 0x05 capturado (luces/sleep) sin cambios")
    ap.add_argument("--dry-run", action="store_true", help="solo muestra y valida")
    ap.add_argument("--yes", action="store_true", help="escribir de verdad en el mouse")
    ap.add_argument("--show", action="store_true", help="muestra el estado local")
    ap.add_argument("--restore", metavar="JSON", help="restaura un backup")
    args = ap.parse_args()

    if args.restore:
        data = json.loads(Path(args.restore).read_text(encoding="utf-8"))
        reports = {int(k, 16): bytes.fromhex(v) for k, v in data["reports"].items()}
        print(f"backup: {args.restore}")
    else:
        state = load_state()
        if state is None and not args.dry_run and not args.show:
            print("aviso: no hay estado local; se parte del baseline capturado.")
        reports = build(args, reports_from_state(state))

    if args.show:
        print(f"estado local: {state_file()}")
        for rid in sorted(reports):
            print(f"\n=== 0x{rid:02x} ({REPORT_NAMES.get(rid, '?')}) ===")
            print(describe_report(reports[rid]))
        return 0

    print("reports a enviar (en este orden):")
    for rid in sorted(reports, key=lambda r: p.WRITE_ORDER.index(r)):
        print(f"\n=== 0x{rid:02x} ({REPORT_NAMES.get(rid, '?')}) "
              f"{len(reports[rid])} bytes ===")
        print(describe_report(reports[rid]))
        print("hex:", reports[rid].hex(" "))

    if args.dry_run or not args.yes:
        if not args.dry_run:
            print("\nDRY-RUN: agrega --yes para escribir en el mouse.")
        return 0

    previous = reports_from_state(load_state())
    backup = save_backup({f"0x{rid:02x}": rep.hex() for rid, rep in previous.items()},
                         "antes de escribir")
    print(f"\nbackup guardado en: {backup}")

    try:
        with Dongle.open() as dongle:
            results = dongle.apply_config(reports.values())
    except OSError as exc:
        print(f"\nERROR de comunicacion: {exc}")
        print("No se guardo el estado local; usa --restore con el backup si hace falta.")
        return 2

    for rid, acked in results:
        note = "ACK" if acked else "sin ACK (ya estaba igual)"
        print(f"  0x{rid:02x}: {note}")
    save_state({f"0x{rid:02x}": rep.hex() for rid, rep in reports.items()})
    print(f"estado guardado en: {state_file()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
