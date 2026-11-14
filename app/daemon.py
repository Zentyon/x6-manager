"""Daemon en 2do plano: aplica perfil onboard + escucha eventos del mouse.

El hilo de monitoreo es el unico que lee el HID de forma continua; los cambios de perfil
se pueden pedir desde tray/UI/hotkeys y se serializan con un lock (mas el lock interno
de `Dongle`), asi el ACK de un SET_FEATURE no se lo come el monitor.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from x6core import transport
from x6core.profiles import Profile, ProfileError, profiles_dir, state_path

log = logging.getLogger(__name__)

DEFAULT_PROFILE_NAME = "FPS"


class Daemon:
    def __init__(self, *, dry_run: bool = False):
        self.profiles: dict[str, Profile] = {}
        self.active = ""
        self.dry_run = dry_run
        self._dongle: transport.Dongle | None = None
        self._lock = threading.RLock()
        self._listeners: list = []
        self.reload()

    # -- perfiles ----------------------------------------------------------
    @property
    def profile(self) -> Profile:
        with self._lock:
            try:
                return self.profiles[self.active]
            except KeyError:
                raise RuntimeError(f"perfil activo {self.active!r} no existe") from None

    def reload(self) -> None:
        directory = profiles_dir()
        loaded: dict[str, Profile] = {}
        for path in sorted(directory.glob("*.json")):
            try:
                loaded[path.stem] = Profile.load(path)
            except ProfileError as exc:
                log.error("perfil ignorado: %s", exc)
            except Exception:
                log.exception("perfil ilegible: %s", path.name)

        if not loaded:
            profile = Profile(DEFAULT_PROFILE_NAME)
            profile.save(directory / f"{DEFAULT_PROFILE_NAME}.json")
            loaded[DEFAULT_PROFILE_NAME] = profile
            log.info("no habia perfiles: creado %s", DEFAULT_PROFILE_NAME)

        with self._lock:
            self.profiles = loaded
            active = self.active if self.active in loaded else self._load_state()
            if active not in loaded:
                active = sorted(loaded)[0]
            self.active = active
        log.info("perfiles: %s (activo %s)", sorted(loaded), active)
        self._emit("reload", active=active)

    def save_active_profile(self) -> None:
        with self._lock:
            self.profile.save(profiles_dir() / f"{self.active}.json")

    # -- aplicacion --------------------------------------------------------
    def ensure_open(self) -> transport.Dongle:
        with self._lock:
            if self._dongle is None or self._dongle.closed:
                self._dongle = transport.Dongle.open()
                log.info("dongle abierto en %s", self._dongle.info.get("path"))
            return self._dongle

    def _drop_dongle(self) -> None:
        with self._lock:
            if self._dongle is not None:
                self._dongle.close()
            self._dongle = None

    def _apply(self, profile: Profile) -> None:
        payload = profile.onboard_payload()
        if self.dry_run:
            log.info("[dry-run] aplicaria %d bytes a %s", len(payload), profile.name)
            return
        self.ensure_open().apply_onboard(payload)

    def switch(self, name: str) -> bool:
        with self._lock:
            profile = self.profiles.get(name)
            if profile is None:
                raise KeyError(f"perfil {name!r} no existe ({sorted(self.profiles)})")
            self._apply(profile)
            self.active = name
            self._save_state()
        log.info("perfil activo: %s dpi=%s %dHz", name, profile.dpi_stages, profile.polling)
        self._emit("switch", name=name)
        return True

    def cycle(self, delta: int = +1) -> bool:
        names = sorted(self.profiles)
        if not names:
            return False
        index = (names.index(self.active) + delta) % len(names) if self.active in names else 0
        return self.switch(names[index])

    def cycle_stage(self, delta: int = +1) -> int:
        """Cambia el stage de DPI por software (lo que haria el boton fisico)."""
        with self._lock:
            profile = self.profile
            profile.active_stage = (profile.active_stage + delta) % len(profile.dpi_stages)
            self._apply(profile)
            profile.save(profiles_dir() / f"{self.active}.json")
            stage, dpi = profile.active_stage, profile.dpi_stages[profile.active_stage]
        log.info("stage %d -> %d DPI", stage + 1, dpi)
        self._emit("stage", name=self.active, stage=stage, dpi=dpi)
        return dpi

    def start(self) -> bool:
        """Aplica el perfil activo al arrancar (tolerante a que no haya dongle)."""
        try:
            return self.switch(self.active)
        except Exception as exc:
            log.warning("no se pudo aplicar el perfil inicial: %s", exc)
            return False

    # -- monitor -----------------------------------------------------------
    def monitor_loop(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        backoff = 2.0
        while not stop.is_set():
            try:
                dongle = self.ensure_open()
                status = dongle.read_status(timeout_ms=3000)
                backoff = 2.0
                self._on_status(status)
            except TimeoutError as exc:
                log.warning("monitor: %s", exc)
                self._drop_dongle()
                stop.wait(backoff)
                backoff = min(backoff * 2, 30)
            except Exception:
                log.exception("monitor: fallo de E/S")
                self._drop_dongle()
                stop.wait(backoff)
                backoff = min(backoff * 2, 30)

    def _on_status(self, status: dict) -> None:
        kind = status.get("type")
        if kind == "heartbeat":
            battery = status.get("battery")
            if battery is not None:
                self._emit("battery", percent=battery, raw=status.get("battery_raw"))
        elif kind == "dpi_button":
            index = status.get("stage_index")
            # el boton DPI tambien puede ser el trigger de una capa (shift_mode=toggle)
            self._emit("dpi_button", stage=index, raw=status.get("raw"))
            if index is None:
                log.debug("evento DPI con stage desconocido: %s", status.get("raw"))
                return
            with self._lock:
                profile = self.profile
                if index >= len(profile.dpi_stages):
                    log.debug("stage fisico %s fuera del perfil (%d stages)",
                              index + 1, len(profile.dpi_stages))
                    return
                if profile.active_stage != index:
                    profile.active_stage = index
                    try:
                        profile.save(profiles_dir() / f"{self.active}.json")
                    except Exception:
                        log.exception("no se pudo guardar el stage fisico")
            self._emit("stage", name=self.active, stage=index,
                       dpi=profile.dpi_stages[index], source="hardware")
        # ack / unknown: no hay nada que hacer

    # -- eventos -----------------------------------------------------------
    def subscribe(self, callback) -> None:
        self._listeners.append(callback)

    def unsubscribe(self, callback) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def _emit(self, event: str, **payload) -> None:
        for callback in list(self._listeners):
            try:
                callback(event, **payload)
            except Exception:
                log.exception("listener %r fallo en %s", callback, event)

    # -- estado ------------------------------------------------------------
    def _load_state(self) -> str:
        try:
            data = json.loads(Path(state_path()).read_text(encoding="utf-8"))
            return str(data.get("active") or "")
        except FileNotFoundError:
            return ""
        except Exception:
            log.warning("state.json ilegible", exc_info=True)
            return ""

    def _save_state(self) -> None:
        try:
            Path(state_path()).write_text(json.dumps({"active": self.active}, indent=2) + "\n",
                                          encoding="utf-8")
        except Exception:
            log.warning("no se pudo guardar state.json", exc_info=True)

    def stop(self) -> None:
        self._drop_dongle()
