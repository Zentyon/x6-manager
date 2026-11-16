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
from x6core.protocol import build_polling_report

log = logging.getLogger(__name__)

DEFAULT_PROFILE_NAME = "FPS"


class Daemon:
    def __init__(self, *, dry_run: bool = False):
        self.profiles: dict[str, Profile] = {}
        self.active = ""
        self.dry_run = dry_run
        self.auto_apps_enabled = True
        self._dongle: transport.Dongle | None = None
        self._lock = threading.RLock()
        self._listeners: list = []
        state = self._load_state_data()
        self.auto_apps_enabled = bool(state.get("auto_apps", True))
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
                log.info("dongle abierto en %s", transport.display_path(self._dongle.info))
            return self._dongle

    def _drop_dongle(self) -> None:
        with self._lock:
            if self._dongle is not None:
                self._dongle.close()
            self._dongle = None

    def _apply(self, profile: Profile) -> bool:
        payload = profile.onboard_payload()
        if self.dry_run:
            log.info("[dry-run] aplicaria %d bytes a %s", len(payload), profile.name)
            return True
        dongle = self.ensure_open()
        acked = dongle.apply_onboard(payload)
        log.debug("payload 0x04 aplicado (%s)", "con ACK" if acked else "sin ACK")
        # El polling tambien es onboard: se manda aparte porque va en otro report.
        try:
            dongle.apply_config([build_polling_report(profile.polling)], wait_ack=False)
        except Exception:
            log.exception("no se pudo aplicar el polling (%s Hz)", profile.polling)
        return acked

    def switch(self, name: str, *, source: str = "user") -> bool:
        with self._lock:
            profile = self.profiles.get(name)
            if profile is None:
                raise KeyError(f"perfil {name!r} no existe ({sorted(self.profiles)})")
            self._apply(profile)
            self.active = name
            self._save_state()
        log.info("perfil activo: %s dpi=%s %dHz", name, profile.dpi_stages, profile.polling)
        self._emit("switch", name=name, source=source)
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
            except OSError as exc:
                # El raton inalambrico corta la lectura al dormirse: no es un fallo
                # del programa, solo hay que reabrir el handle y reintentar.
                log.warning("monitor: lectura interrumpida (%s), reintentando", exc)
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
        if kind not in ("status", "dpi"):
            return  # ack/unknown: nada que hacer
        raw = status.get("battery_raw")
        battery = status.get("battery")
        if battery is None:
            log.debug("bateria fuera de rango (raw=%s), ignorada", raw)
        else:
            self._emit("battery", percent=battery, raw=raw,
                       profile=status.get("profile"))
        if kind != "dpi":
            return
        # El boton DPI fisico no genera evento de raton: solo este aviso, sin
        # release. Puede ser el shift de una capa en modo toggle.
        stage = status.get("stage")
        self._emit("dpi_button", stage=stage, raw=status.get("raw"))
        index = stage - 1 if isinstance(stage, int) and 1 <= stage <= 8 else None
        if index is None:
            log.debug("evento DPI con stage desconocido: %s", status.get("raw"))
            return
        with self._lock:
            profile = self.profile
            if index >= len(profile.dpi_stages):
                log.debug("stage fisico %s fuera del perfil (%d stages)",
                          stage, len(profile.dpi_stages))
                return
            if profile.active_stage != index:
                profile.active_stage = index
                try:
                    profile.save(profiles_dir() / f"{self.active}.json")
                except Exception:
                    log.exception("no se pudo guardar el stage fisico")
        self._emit("stage", name=self.active, stage=index,
                   dpi=profile.dpi_stages[index], source="hardware")

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
    def _load_state_data(self) -> dict:
        try:
            data = json.loads(Path(state_path()).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception:
            log.warning("state.json ilegible", exc_info=True)
            return {}

    def _load_state(self) -> str:
        return str(self._load_state_data().get("active") or "")

    def set_auto_apps(self, enabled: bool) -> None:
        with self._lock:
            self.auto_apps_enabled = bool(enabled)
            self._save_state()
        log.info("cambio por app: %s", "activado" if enabled else "desactivado")
        self._emit("auto_apps", enabled=self.auto_apps_enabled)

    def _save_state(self) -> None:
        try:
            Path(state_path()).write_text(
                json.dumps({"active": self.active,
                            "auto_apps": self.auto_apps_enabled}, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception:
            log.warning("no se pudo guardar state.json", exc_info=True)

    def stop(self) -> None:
        self._drop_dongle()
