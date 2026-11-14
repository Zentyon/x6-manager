"""Reproduccion de macros: [{key, down_ms, delay_ms}, ...] en un hilo aparte."""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)


def play(steps: list[dict], *, on_done=None) -> None:
    """Reproduce la macro de forma bloqueante (llamar desde un hilo)."""
    # import local: actions importa macros, evitar ciclo a nivel de modulo
    from .actions import parse_combo, press_keys, release_keys

    keyboard = None
    try:
        from pynput import keyboard as kb  # type: ignore

        keyboard = kb.Controller()
    except Exception:
        log.exception("pynput no disponible: no se puede reproducir la macro")

    for i, step in enumerate(steps):
        combo = str(step.get("key", ""))
        down_ms = int(step.get("down_ms", 30) or 0)
        delay_ms = int(step.get("delay_ms", 20) or 0)
        if not combo:
            continue
        if keyboard is None:
            log.info("[sin pynput] macro paso %d: %s (%dms)", i, combo, down_ms)
        else:
            try:
                keys = parse_combo(combo)
                press_keys(keys, keyboard)
                if down_ms:
                    time.sleep(down_ms / 1000)
                release_keys(keys, keyboard)
            except Exception:
                log.exception("paso %d (%s) de la macro fallo", i, combo)
        if delay_ms:
            time.sleep(delay_ms / 1000)
    if on_done is not None:
        on_done()


def play_async(steps: list[dict]) -> threading.Thread:
    t = threading.Thread(target=play, args=(steps,), name="x6-macro", daemon=True)
    t.start()
    return t
