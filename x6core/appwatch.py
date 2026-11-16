"""Cambio automatico de perfil segun la aplicacion en primer plano.

Cada perfil puede declarar `auto_apps` con una lista de patrones:

    "auto_apps": ["cs2.exe", "valorant*", "title:*YouTube*"]

- Un patron sin prefijo se compara con el **nombre del ejecutable** (sin distinguir
  mayusculas) y tambien con su nombre sin extension: `notepad` vale para
  `notepad.exe`.
- `title:patron` compara con el **titulo de la ventana**.
- `*` y `?` funcionan como en `fnmatch`.

El cambio espera `settle` segundos con la misma app delante para no reescribir el
mouse al hacer alt-tab. Cuando ninguna regla coincide se vuelve al perfil base, el
que estaba activo al arrancar.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Callable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Window:
    """Ventana en primer plano. `process` va en minusculas y con extension."""

    process: str = ""
    title: str = ""

    def __bool__(self) -> bool:
        return bool(self.process or self.title)


# --- lectura de la ventana activa -----------------------------------------
def _win_foreground() -> Window | None:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    length = user32.GetWindowTextLengthW(hwnd)
    title = ""
    if length > 0:
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value

    name = ""
    # PROCESS_QUERY_LIMITED_INFORMATION: funciona sin permisos de administrador
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if handle:
        try:
            path = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(path))
            if kernel32.QueryFullProcessImageNameW(handle, 0, path,
                                                   ctypes.byref(size)):
                name = os.path.basename(path.value)
        finally:
            kernel32.CloseHandle(handle)

    return Window(name.lower(), title)


def _linux_foreground() -> Window | None:
    """X11 via xdotool; en Wayland o sin xdotool devuelve None (sin reglas)."""
    try:
        win = subprocess.run(["xdotool", "getactivewindow"], capture_output=True,
                             text=True, timeout=1.0, check=True).stdout.strip()
        if not win:
            return None
        title = subprocess.run(["xdotool", "getwindowname", win],
                               capture_output=True, text=True, timeout=1.0,
                               check=True).stdout.strip()
        pid = subprocess.run(["xdotool", "getwindowpid", win], capture_output=True,
                             text=True, timeout=1.0, check=True).stdout.strip()
        name = ""
        if pid:
            try:
                name = os.path.basename(
                    os.readlink(f"/proc/{pid}/exe")
                )
            except OSError:
                try:
                    with open(f"/proc/{pid}/comm", encoding="utf-8") as fh:
                        name = fh.read().strip()
                except OSError:
                    name = ""
        return Window(name.lower(), title)
    except (OSError, subprocess.SubprocessError):
        return None


def foreground_window() -> Window | None:
    """Ventana activa, o None si la plataforma no lo permite."""
    if sys.platform == "win32":
        try:
            return _win_foreground()
        except Exception:  # pragma: no cover - depende de la sesion de Windows
            log.debug("no se pudo leer la ventana activa", exc_info=True)
            return None
    if sys.platform.startswith("linux"):
        return _linux_foreground()
    return None


# --- reglas ---------------------------------------------------------------
def matches(pattern: str, window: Window | None) -> bool:
    """True si `pattern` describe la ventana dada."""
    if window is None:
        return False
    rule = pattern.strip().lower()
    if not rule:
        return False
    if rule.startswith("title:"):
        return fnmatchcase(window.title.lower(), rule[len("title:"):].strip())
    if not window.process:
        return False
    targets = {window.process, window.process.rsplit(".", 1)[0]}
    return any(fnmatchcase(target, rule) for target in targets)


class AutoSwitcher:
    """Vigila la ventana activa y cambia de perfil cuando toca."""

    def __init__(self, daemon, *, settle: float = 0.8, interval: float = 0.4,
                 clock: Callable[[], float] = time.monotonic,
                 reader: Callable[[], Window | None] = foreground_window):
        self._daemon = daemon
        self._settle = max(0.0, settle)
        self._interval = max(0.05, interval)
        self._clock = clock
        self._reader = reader
        self._base = daemon.active
        self._pending: str | None = None
        self._pending_at = 0.0
        self._lock = threading.Lock()
        subscribe = getattr(daemon, "subscribe", None)
        if subscribe is not None:
            subscribe(self._on_daemon_event)

    @property
    def base(self) -> str:
        with self._lock:
            return self._base

    def set_base(self, name: str) -> None:
        with self._lock:
            self._base = name

    def _on_daemon_event(self, event: str, **payload) -> None:
        """Un cambio manual pasa a ser el nuevo perfil base.

        Los cambios hechos por este mismo AutoSwitcher llegan con `source="auto"`
        y no tocan la base, para no perder el perfil del usuario.
        """
        if event != "switch" or payload.get("source") == "auto":
            return
        name = payload.get("name")
        if name and name in getattr(self._daemon, "profiles", {}):
            with self._lock:
                self._base = name

    def target_for(self, window: Window | None) -> str | None:
        """Perfil que corresponde a esa ventana (el primero por orden alfabetico)."""
        with self._lock:
            names = sorted(self._daemon.profiles)
        for name in names:
            profile = self._daemon.profiles.get(name)
            rules = getattr(profile, "auto_apps", None) or []
            if any(matches(rule, window) for rule in rules):
                return name
        return None

    def poll(self, window: Window | None = None) -> str | None:
        """Un ciclo. Devuelve el perfil al que cambio, o None si no hubo cambio."""
        if not getattr(self._daemon, "auto_apps_enabled", True):
            return None
        current = window if window is not None else self._reader()
        target = self.target_for(current)
        now = self._clock()

        if target != self._pending:
            self._pending = target
            self._pending_at = now
            return None
        if now - self._pending_at < self._settle:
            return None

        wanted = target if target is not None else self._base
        if not wanted or wanted == self._daemon.active:
            return None
        if wanted not in self._daemon.profiles:
            log.debug("perfil auto %r ya no existe", wanted)
            return None

        log.info("auto: %s -> perfil %s", _describe(current), wanted)
        self._daemon.switch(wanted, source="auto")
        return wanted

    def run(self, stop: threading.Event) -> None:
        log.info("cambio por app activo (base %s)", self._base)
        while not stop.wait(self._interval):
            try:
                self.poll()
            except Exception:
                log.exception("fallo el cambio automatico de perfil")
        self.close()

    def close(self) -> None:
        """Deja de escuchar los cambios manuales del daemon."""
        unsubscribe = getattr(self._daemon, "unsubscribe", None)
        if unsubscribe is not None:
            unsubscribe(self._on_daemon_event)


def _describe(window: Window | None) -> str:
    if not window:
        return "sin ventana"
    name = window.process or "?"
    return f"{name} ({window.title[:40]})" if window.title else name
