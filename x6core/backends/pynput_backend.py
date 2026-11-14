"""Captura global de botones con pynput.

Windows: usa `win32_event_filter`, que corre dentro del hook WH_MOUSE_LL y permite
*bloquear* el evento nativo (imprescindible para el G-Shift: si no bloqueas el boton
de shift, el juego tambien recibe "atras"). Los eventos inyectados por nosotros llevan
LLMHF_INJECTED y se ignoran.

Linux/X11: pynput solo observa (XRecord no puede suprimir). Para G-Shift con supresion
haria falta `evdev` con grab + uinput; queda como backend pendiente.
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

# --- constantes Win32 (evitamos ctypes: pynput ya nos pasa el msg) ----------
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C
LLMHF_INJECTED = 0x00000001

_BUTTONS = {
    WM_LBUTTONDOWN: ("left", True),
    WM_LBUTTONUP: ("left", False),
    WM_RBUTTONDOWN: ("right", True),
    WM_RBUTTONUP: ("right", False),
    WM_MBUTTONDOWN: ("middle", True),
    WM_MBUTTONUP: ("middle", False),
    WM_XBUTTONDOWN: (None, True),
    WM_XBUTTONUP: (None, False),
}


class PynputBackend:
    def __init__(self, on_event, *, swap_side_buttons: bool = False):
        self._on_event = on_event
        self._swap = swap_side_buttons
        self._listener = None
        self._running = False
        self._last_error = None

    @property
    def supports_suppression(self) -> bool:
        return sys.platform == "win32"

    # -- ciclo de vida -----------------------------------------------------
    def start(self) -> None:
        try:
            from pynput import mouse  # type: ignore
        except ImportError as exc:
            raise RuntimeError("falta pynput: pip install pynput") from exc
        except Exception as exc:  # X11 sin DISPLAY, Wayland, etc.
            raise RuntimeError(f"pynput no pudo inicializarse: {exc}") from exc

        if self.supports_suppression:
            self._listener = mouse.Listener(win32_event_filter=self._win32_filter)
        else:
            self._listener = mouse.Listener(on_click=self._on_click)
        self._listener.start()
        self._running = True
        if not self.supports_suppression:
            log.warning(
                "este backend solo observa: los botones reasignados seguiran "
                "haciendo su accion nativa ademas de la nueva"
            )

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                log.debug("error parando listener", exc_info=True)
            self._listener = None
        self._running = False

    # -- Windows -----------------------------------------------------------
    def _win32_filter(self, msg, data) -> bool | None:
        """Se ejecuta en el hilo del hook (bloquea el evento si hace falta)."""
        if getattr(data, "flags", 0) & LLMHF_INJECTED:
            return None  # evento sintetico nuestro
        info = _BUTTONS.get(msg)
        if info is None:
            return None
        button, pressed = info
        if button is None:  # XBUTTON: high word de mouseData
            xbutton = (data.mouseData >> 16) & 0xFFFF
            button = self._side_button(xbutton)
            if button is None:
                return None
        try:
            suppress = bool(self._on_event(button, pressed))
        except Exception:
            log.exception("on_event fallo (%s %s)", button, pressed)
            return None
        if suppress:
            self._listener.suppress_event()
        return None

    def _side_button(self, xbutton: int) -> str | None:
        # Windows: XBUTTON1 = atras, XBUTTON2 = adelante
        if xbutton == 1:
            return "forward" if self._swap else "backward"
        if xbutton == 2:
            return "backward" if self._swap else "forward"
        return None

    # -- otros SO (solo observacion) ---------------------------------------
    def _on_click(self, _x, _y, button, pressed) -> None:
        name = self._button_name(button)
        if name is None:
            return
        try:
            self._on_event(name, pressed)
        except Exception:
            log.exception("on_event fallo (%s %s)", name, pressed)

    def _button_name(self, button) -> str | None:
        from pynput.mouse import Button  # type: ignore

        if button == Button.left:
            return "left"
        if button == Button.right:
            return "right"
        if button == Button.middle:
            return "middle"
        if button == getattr(Button, "x1", None):
            return "forward" if self._swap else "backward"
        if button == getattr(Button, "x2", None):
            return "backward" if self._swap else "forward"
        return None
