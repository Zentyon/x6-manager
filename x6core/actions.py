"""Ejecucion de los pasos que produce LayerEngine (inyeccion de teclas/clicks).

pynput se importa de forma perezosa: sin el instalado, el daemon sigue funcionando y
solo se pierden las acciones host (el modo `dry_run` sirve para probar sin inyectar).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from . import macros
from .inject import injecting

log = logging.getLogger(__name__)

MODIFIERS = {
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "option": "alt",
    "shift": "shift",
    "win": "cmd", "super": "cmd", "cmd": "cmd", "meta": "cmd",
}

KEY_ALIASES = {
    "esc": "esc", "escape": "esc", "enter": "enter", "return": "enter",
    "space": "space", "tab": "tab", "backspace": "backspace", "delete": "delete",
    "del": "delete", "insert": "insert", "ins": "insert", "home": "home", "end": "end",
    "pageup": "page_up", "pgup": "page_up", "pagedown": "page_down", "pgdn": "page_down",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "capslock": "caps_lock", "printscreen": "print_screen", "pause": "pause",
}

MEDIA_ALIASES = {
    "play_pause": "media_play_pause",
    "next": "media_next",
    "previous": "media_previous",
    "volume_up": "media_volume_up",
    "volume_down": "media_volume_down",
    "mute": "media_volume_mute",
}

# Botones que pynput llama x1/x2. Convencion Windows: XBUTTON1 = atras, XBUTTON2 = adelante.
MOUSE_BUTTON_NAMES = {"left": "left", "right": "right", "middle": "middle",
                      "backward": "x1", "forward": "x2"}


def _keyboard():
    from pynput import keyboard  # type: ignore

    return keyboard


def _mouse():
    from pynput import mouse  # type: ignore

    return mouse


def parse_combo(combo: str) -> list[Any]:
    """'ctrl+alt+1' -> [Key.ctrl, Key.alt, '1'] (el ultimo token es la tecla)."""
    if not combo or not combo.strip():
        raise ValueError("atajo vacio")
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"atajo invalido: {combo!r}")
    keyboard = _keyboard()
    out: list[Any] = []
    for token in parts[:-1]:
        name = MODIFIERS.get(token.lower())
        if name is None:
            raise ValueError(f"modificador desconocido {token!r} en {combo!r}")
        out.append(getattr(keyboard.Key, name))
    out.append(_key(parts[-1], keyboard))
    return out


def _key(token: str, keyboard) -> Any:
    low = token.lower()
    name = KEY_ALIASES.get(low, low)
    if len(token) == 1:
        return keyboard.KeyCode.from_char(token)
    if low.startswith("f") and low[1:].isdigit():
        key = getattr(keyboard.Key, low, None)
        if key is not None:
            return key
    if name in MEDIA_ALIASES:
        return getattr(keyboard.Key, MEDIA_ALIASES[name])
    key = getattr(keyboard.Key, name, None)
    if key is None:
        raise ValueError(f"tecla desconocida {token!r}")
    return key


def press_keys(keys: list[Any], keyboard) -> None:
    for key in keys:
        keyboard.press(key)


def release_keys(keys: list[Any], keyboard) -> None:
    for key in reversed(keys):
        keyboard.release(key)


class ActionExecutor:
    """Traduce Step -> efecto real. Mantiene el estado de teclas en hold."""

    def __init__(self, daemon=None, dry_run: bool = False):
        self._daemon = daemon
        self.dry_run = dry_run
        self._held: dict[str, list[Any]] = {}
        self._keyboard = None

    def _kb(self):
        if self._keyboard is None:
            self._keyboard = _keyboard().Controller()
        return self._keyboard

    def execute(self, step) -> None:
        kind, value = step.kind, step.value
        if self.dry_run or kind in ("dpi_cycle", "dpi_up", "dpi_down"):
            handler = getattr(self, f"_do_{kind}", None)
            if handler is not None:
                try:
                    handler(value)
                except Exception:
                    log.exception("accion %s fallo", kind)
                return
            log.info("[dry-run] %s %s", kind, value)
            return
        try:
            if kind == "click_down":
                with injecting():
                    _mouse().Controller().press(self._button(value))
            elif kind == "click_up":
                with injecting():
                    _mouse().Controller().release(self._button(value))
            elif kind == "keys_tap":
                self._tap(value)
            elif kind == "keys_down":
                self._down(value)
            elif kind == "keys_up":
                self._up(value)
            elif kind == "media":
                self._media(value)
            elif kind == "macro":
                self._macro(value)
            else:
                log.warning("paso desconocido: %s", step)
        except Exception:
            log.exception("no se pudo ejecutar %s %s", kind, value)

    # -- helpers -----------------------------------------------------------
    def _button(self, name: str):
        mouse = _mouse()
        return getattr(mouse.Button, MOUSE_BUTTON_NAMES.get(name, name))

    def _tap(self, combo: str) -> None:
        keys = parse_combo(combo)
        kb = self._kb()
        with injecting():
            press_keys(keys, kb)
            time.sleep(0.01)
            release_keys(keys, kb)

    def _down(self, combo: str) -> None:
        if combo in self._held:
            return
        keys = parse_combo(combo)
        with injecting():
            press_keys(keys, self._kb())
        self._held[combo] = keys

    def _up(self, combo: str) -> None:
        keys = self._held.pop(combo, None)
        if keys is None:
            return
        with injecting():
            release_keys(keys, self._kb())

    def _media(self, name: str) -> None:
        keyboard = _keyboard()
        key = getattr(keyboard.Key, MEDIA_ALIASES[name])
        kb = self._kb()
        with injecting():
            kb.press(key)
            kb.release(key)

    def _macro(self, macro_id: str) -> None:
        profile = getattr(self._daemon, "profile", None)
        steps = (profile.macros.get(macro_id) if profile else None) or []
        if not steps:
            log.warning("macro %r no existe en el perfil activo", macro_id)
            return
        macros.play_async(steps)

    # -- acciones onboard (no necesitan pynput) ----------------------------
    def _do_dpi_cycle(self, _value: str = "") -> None:
        if self._daemon is not None:
            self._daemon.cycle_stage(+1)

    def _do_dpi_up(self, _value: str = "") -> None:
        if self._daemon is not None:
            self._daemon.cycle_stage(+1)

    def _do_dpi_down(self, _value: str = "") -> None:
        if self._daemon is not None:
            self._daemon.cycle_stage(-1)

    def release_all(self) -> None:
        for combo in list(self._held):
            self._up(combo)
