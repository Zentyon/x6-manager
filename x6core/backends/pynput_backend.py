"""Captura global de botones (y de las teclas del disparador) con pynput.

Windows: usa `win32_event_filter`, que corre dentro de los hooks WH_MOUSE_LL y
WH_KEYBOARD_LL y permite *bloquear* el evento nativo (imprescindible para el G-Shift:
si no bloqueas el boton de shift, el juego tambien recibe "atras"). Los eventos
inyectados por nosotros llevan LLMHF_INJECTED / LLKHF_INJECTED y se ignoran.

Linux/X11: pynput solo observa (XRecord no puede suprimir). Para G-Shift con supresion
haria falta `evdev` con grab + uinput; queda como backend pendiente.
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

# --- constantes Win32 (evitamos ctypes: pynput ya nos pasa el msg) ----------
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C
LLMHF_INJECTED = 0x00000001
LLKHF_INJECTED = 0x00000010

# VK de letras y digitos: el unico rango que el teclado del dongle puede emitir
# y que por tanto tiene sentido como disparador desde un boton del mouse.
_VK_NAMES = {vk: chr(vk).lower() for vk in range(0x30, 0x3A)}
_VK_NAMES.update({vk: chr(vk).lower() for vk in range(0x41, 0x5B)})

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
    def __init__(self, on_event, *, swap_side_buttons: bool = False,
                 key_triggers=()):
        self._on_event = on_event
        self._swap = swap_side_buttons
        self._key_triggers = {str(k).lower() for k in key_triggers}
        self._listener = None
        self._keyboard_listener = None
        self._running = False
        self._last_error = None

    def set_key_triggers(self, names) -> None:
        """Teclas del teclado que actuan como disparador (shift_button)."""
        self._key_triggers = {str(n).lower() for n in names}

    @property
    def supports_suppression(self) -> bool:
        return sys.platform == "win32"

    # -- ciclo de vida -----------------------------------------------------
    def start(self) -> None:
        try:
            from pynput import keyboard, mouse  # type: ignore
        except ImportError as exc:
            raise RuntimeError("falta pynput: pip install pynput") from exc
        except Exception as exc:  # X11 sin DISPLAY, Wayland, etc.
            raise RuntimeError(f"pynput no pudo inicializarse: {exc}") from exc

        if self.supports_suppression:
            self._listener = mouse.Listener(win32_event_filter=self._win32_filter)
        else:
            self._listener = mouse.Listener(on_click=self._on_click)
        self._listener.start()

        # El firmware del X6 puede remapear los dos botones de arriba a una tecla
        # del rango 0x00-0x2F (letras/digitos). Solo se escuchan las que el perfil
        # use como disparador, para no procesar cada pulsacion del teclado.
        if self.supports_suppression:
            # En Windows hay que decidir en el propio hook para poder bloquear la
            # tecla (si no, el caracter llegaria igualmente a la ventana activa).
            self._keyboard_listener = keyboard.Listener(
                win32_event_filter=self._keyboard_filter
            )
        else:
            self._keyboard_listener = keyboard.Listener(
                on_press=self._on_key_press, on_release=self._on_key_release
            )
        self._keyboard_listener.start()
        self._running = True
        if not self.supports_suppression:
            log.warning(
                "este backend solo observa: los botones reasignados seguiran "
                "haciendo su accion nativa ademas de la nueva"
            )

    def stop(self) -> None:
        for listener in (self._listener, self._keyboard_listener):
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    log.debug("error parando listener", exc_info=True)
        self._listener = None
        self._keyboard_listener = None
        self._running = False

    # -- teclado (una tecla del rango 0x00-0x2F como disparador) -----------
    @staticmethod
    def _key_name(key) -> str | None:
        name = getattr(key, "name", None)
        if name is None:
            char = getattr(key, "char", None)
            name = char
        return str(name).lower() if name else None

    def _on_key_press(self, key) -> None:
        name = self._key_name(key)
        if name not in self._key_triggers:
            return
        try:
            self._on_event(name, True)
        except Exception:
            log.exception("on_event fallo (%s DOWN)", name)

    def _on_key_release(self, key) -> None:
        name = self._key_name(key)
        if name not in self._key_triggers:
            return
        try:
            self._on_event(name, False)
        except Exception:
            log.exception("on_event fallo (%s UP)", name)

    def _keyboard_filter(self, msg, data) -> bool:
        """Hook de teclado de Windows: decide y bloquea en el mismo sitio.

        Devuelve False para que pynput no vuelva a emitir el evento (ya lo hemos
        despachado aqui) y lanza `suppress_event` cuando la accion lo pide.
        """
        if not self._key_triggers:
            return True
        if getattr(data, "flags", 0) & LLKHF_INJECTED:
            return True  # evento sintetico nuestro
        name = _VK_NAMES.get(getattr(data, "vkCode", None))
        if name is None or name not in self._key_triggers:
            return True
        if msg not in (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP):
            return True
        pressed = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
        try:
            suppress = bool(self._on_event(name, pressed))
        except Exception:
            log.exception("on_event fallo (%s %s)", name, pressed)
            return True
        if suppress:
            self._keyboard_listener.suppress_event()
        return False

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
