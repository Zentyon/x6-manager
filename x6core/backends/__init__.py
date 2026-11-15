"""Backends de captura de botones del mouse."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def make_backend(on_event, *, swap_side_buttons: bool = False,
                 key_triggers=()):
    """Devuelve el backend adecuado para la plataforma.

    `on_event(button, pressed) -> bool` debe devolver True si hay que bloquear el
    evento nativo (solo posible donde el backend lo soporte). `key_triggers` son
    las teclas que el perfil usa como disparador de capa (shift_button).
    """
    from .pynput_backend import PynputBackend

    return PynputBackend(on_event, swap_side_buttons=swap_side_buttons,
                         key_triggers=key_triggers)
