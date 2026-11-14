"""Marca de inyeccion: evita que nuestros propios eventos sinteticos reentren al hook.

La bandera es global (no thread-local) porque la inyeccion ocurre en el hilo trabajador
y el hook la consulta en su propio hilo.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

_lock = threading.Lock()
_depth = 0


@contextmanager
def injecting():
    global _depth
    with _lock:
        _depth += 1
    try:
        yield
    finally:
        with _lock:
            _depth -= 1


def is_injecting() -> bool:
    return _depth > 0
