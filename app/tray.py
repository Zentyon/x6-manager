"""Bandeja del sistema: cambio rapido de perfil, estado y bateria.

El menu es una funcion (pystray la re-evalua) y se refresca con `icon.update_menu()`
cuando el daemon emite eventos, asi el marcador del perfil activo no se queda pegado.
"""
from __future__ import annotations

import logging
import threading

import pystray
from PIL import Image, ImageDraw
from pystray import Menu, MenuItem

log = logging.getLogger(__name__)


def make_icon(color=(0, 200, 120)) -> Image.Image:
    img = Image.new("RGB", (64, 64), (20, 20, 25))
    dr = ImageDraw.Draw(img)
    dr.ellipse([12, 12, 52, 52], fill=color)
    return img


class TrayApp:
    def __init__(self, daemon, *, on_quit=None):
        self.daemon = daemon
        self.on_quit = on_quit
        self.battery: int | None = None
        self.dpi: int | None = None
        self._switch_lock = threading.Lock()
        self.icon = pystray.Icon(
            "x6", make_icon(), self._title(), menu=Menu(self._menu_items)
        )
        self.daemon.subscribe(self._on_daemon_event)

    # -- estado / titulo ---------------------------------------------------
    def _title(self) -> str:
        parts = [f"X6 [{self.daemon.active}]"]
        if self.dpi:
            parts.append(f"{self.dpi}dpi")
        if self.battery is not None:
            parts.append(f"{self.battery}%")
        return " ".join(parts)

    def _on_daemon_event(self, event: str, **payload) -> None:
        if event == "battery":
            self.battery = payload.get("percent", self.battery)
        elif event == "stage":
            self.dpi = payload.get("dpi", self.dpi)
        elif event in ("switch", "reload"):
            try:
                profile = self.daemon.profile
                self.dpi = profile.dpi_stages[profile.active_stage]
            except Exception:
                pass
        try:
            self.icon.title = self._title()
            self.icon.update_menu()
        except Exception:
            log.debug("no se pudo refrescar el tray", exc_info=True)

    # -- menu --------------------------------------------------------------
    def _menu_items(self):
        items = []
        for name in sorted(self.daemon.profiles):
            marker = "● " if name == self.daemon.active else "   "
            items.append(MenuItem(f"{marker}{name}", self._switcher(name)))
        items += [
            Menu.SEPARATOR,
            MenuItem("Siguiente perfil", lambda *_: self._run(self.daemon.cycle, +1)),
            MenuItem("Subir DPI", lambda *_: self._run(self.daemon.cycle_stage, +1)),
            MenuItem("Bajar DPI", lambda *_: self._run(self.daemon.cycle_stage, -1)),
            Menu.SEPARATOR,
            MenuItem("Recargar perfiles", lambda *_: self._run(self.daemon.reload)),
            MenuItem("Salir", self._quit),
        ]
        return items

    def _switcher(self, name: str):
        def _item(*_args):
            self._run(self.daemon.switch, name)
        return _item

    def _run(self, fn, *args) -> None:
        """Los SET_REPORT + ACK tardan hasta 3s: nunca en el hilo del tray."""
        if not self._switch_lock.acquire(blocking=False):
            return

        def _worker() -> None:
            try:
                fn(*args)
            except Exception:
                log.exception("accion del tray fallo (%s)", getattr(fn, "__name__", fn))
            finally:
                self._switch_lock.release()

        threading.Thread(target=_worker, name="x6-tray-action", daemon=True).start()

    def _quit(self, *_args) -> None:
        self.daemon.unsubscribe(self._on_daemon_event)
        if self.on_quit is not None:
            try:
                self.on_quit()
            except Exception:
                log.exception("on_quit fallo")
        self.icon.stop()

    # -- ciclo de vida -----------------------------------------------------
    def run(self) -> None:
        self.icon.run()


def run_tray(daemon, on_quit=None) -> None:
    TrayApp(daemon, on_quit=on_quit).run()
