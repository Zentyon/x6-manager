"""Entrypoint del X6 Manager.

    python -m app            # bandeja del sistema (por defecto)
    python -m app --ui       # ventana Qt
    python -m app --dry-run  # no escribe en el mouse, solo registra
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading

from x6core.host import HostInput

from .daemon import Daemon

log = logging.getLogger("x6")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="x6-manager", description="Manager para Attack Shark X6")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--tray", action="store_true", help="bandeja del sistema (defecto)")
    mode.add_argument("--ui", action="store_true", help="ventana Qt (PySide6)")
    p.add_argument("--no-host", action="store_true",
                   help="no capturar botones: solo aplicar perfiles onboard")
    p.add_argument("--no-hotkeys", action="store_true", help="sin Ctrl+Alt+1..9")
    p.add_argument("--swap-side-buttons", action="store_true",
                   help="intercambia atras/adelante si el mouse los manda al reves")
    p.add_argument("--profile", help="perfil a activar al arrancar")
    p.add_argument("--dry-run", action="store_true",
                   help="no toca hardware ni inyecta teclas; solo log")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    daemon = Daemon(dry_run=args.dry_run)
    if args.profile:
        if args.profile not in daemon.profiles:
            log.error("perfil %r no existe (%s)", args.profile, sorted(daemon.profiles))
            return 2
        daemon.active = args.profile

    stop = threading.Event()
    host: HostInput | None = None
    if not args.no_host:
        try:
            host = HostInput(
                daemon,
                dry_run=args.dry_run,
                swap_side_buttons=args.swap_side_buttons,
                hotkeys=not args.no_hotkeys,
            )
            host.start()
        except Exception as exc:
            log.warning("sin acciones host (%s). Usa --no-host para silenciar.", exc)
            host = None

    if host is not None:
        def _forward(event: str, **payload) -> None:
            if event == "dpi_button":
                host.notify_vendor_button("dpi")

        daemon.subscribe(_forward)

    # aplicar el perfil inicial y arrancar el monitor sin bloquear el arranque
    threading.Thread(target=daemon.start, name="x6-start", daemon=True).start()
    threading.Thread(target=daemon.monitor_loop, args=(stop,), name="x6-monitor",
                     daemon=True).start()

    def _shutdown() -> None:
        stop.set()
        if host is not None:
            host.stop()
        daemon.stop()

    try:
        if args.ui:
            from .ui import run_ui

            run_ui(daemon)
        else:
            from .tray import run_tray

            run_tray(daemon, on_quit=_shutdown)
    except ImportError as exc:
        log.error("faltan dependencias (%s). pip install -r requirements.txt", exc)
        return 2
    except KeyboardInterrupt:
        log.info("interrumpido por el usuario")
    finally:
        _shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
