"""HostInput: une backend de captura + motor de capas + inyector + daemon.

Sin esto, el mouse solo tiene lo que el firmware tiene en memoria. Con esto se pueden
hacer cosas que el onboard no puede: capas tipo G-Shift, macros, atajos y cambio de
DPI por software.

IMPORTANTE: el callback del backend corre dentro del hook de bajo nivel del SO. Ahi
solo se decide (rapido) si el evento se bloquea; la ejecucion de acciones se encola en
un hilo aparte. Si bloquearamos el hook con un SET_REPORT + ACK de 3s, Windows
descartaria el hook (LowLevelHooksTimeout) y el mouse se quedaria atascado.
"""
from __future__ import annotations

import logging
import queue
import threading

from .actions import ActionExecutor
from .backends import make_backend
from .inject import is_injecting
from .layers import LayerEngine, Step

log = logging.getLogger(__name__)


class _StepWorker(threading.Thread):
    def __init__(self, executor: ActionExecutor):
        super().__init__(name="x6-steps", daemon=True)
        self._executor = executor
        self._queue: queue.Queue[Step | None] = queue.Queue()

    def submit(self, steps: tuple[Step, ...]) -> None:
        for step in steps:
            self._queue.put(step)

    def run(self) -> None:
        while True:
            step = self._queue.get()
            if step is None:
                return
            try:
                self._executor.execute(step)
            except Exception:
                log.exception("paso %s fallo", step)

    def stop(self) -> None:
        self._queue.put(None)


class HostInput:
    def __init__(self, daemon, *, dry_run: bool = False,
                 side_button_map: dict[str, str] | None = None,
                 swap_side_buttons: bool = False,
                 hotkeys: bool = True):
        self._daemon = daemon
        self._hotkeys_enabled = hotkeys
        self.engine = LayerEngine(daemon.profile, side_button_map=side_button_map)
        self.executor = ActionExecutor(daemon, dry_run=dry_run)
        self.backend = make_backend(self._handle, swap_side_buttons=swap_side_buttons)
        self._worker = _StepWorker(self.executor)
        self._hotkeys = None
        self._switch_lock = threading.Lock()
        daemon.subscribe(self._on_daemon_event)

    # -- eventos -----------------------------------------------------------
    def _handle(self, button: str, pressed: bool) -> bool:
        """Se ejecuta en el hilo del hook: solo decide y encola."""
        if is_injecting():
            return False
        result = self.engine.handle(button, pressed)
        if result.steps:
            self._worker.submit(result.steps)
        return result.suppress

    def _on_daemon_event(self, event: str, **payload) -> None:
        if event not in ("switch", "reload"):
            return
        self.executor.release_all()
        self.engine.set_profile(self._daemon.profile)
        if event == "switch":
            self._warn_if_not_suppressible()

    def notify_vendor_button(self, button: str = "dpi") -> None:
        """Evento de boton que llega por el canal vendor (sin release)."""
        profile = self._daemon.profile
        if profile.shift_button == button and profile.shift_mode != "toggle":
            log.debug("boton vendor %r ignorado: el firmware no manda release, "
                      "usa shift_mode=toggle", button)
            return
        self._handle(button, True)

    def _warn_if_not_suppressible(self) -> None:
        profile = self._daemon.profile
        if profile.shift_button and not self.backend.supports_suppression:
            log.warning(
                "shift_button=%r configurado pero este backend no puede bloquear el "
                "evento nativo: el boton hara su accion original Y la capa",
                profile.shift_button,
            )

    # -- ciclo de vida -----------------------------------------------------
    def start(self) -> None:
        self._worker.start()
        self.backend.start()
        self._warn_if_not_suppressible()
        if self._hotkeys_enabled:
            self._start_hotkeys()

    def stop(self) -> None:
        self._stop_hotkeys()
        self.backend.stop()
        self.executor.release_all()
        self._worker.stop()

    # -- hotkeys globales --------------------------------------------------
    def _start_hotkeys(self) -> None:
        try:
            from pynput import keyboard  # type: ignore
        except Exception:
            log.warning("pynput no disponible: sin hotkeys globales")
            return
        names = sorted(self._daemon.profiles)[:9]
        mapping = {f"<ctrl>+<alt>+{i}": self._switcher(name)
                   for i, name in enumerate(names, start=1)}
        if not mapping:
            return
        try:
            self._hotkeys = keyboard.GlobalHotKeys(mapping)
            self._hotkeys.start()
            log.info("hotkeys Ctrl+Alt+1..%d -> %s", len(mapping), names)
        except Exception:
            log.exception("no se pudieron registrar los hotkeys")
            self._hotkeys = None

    def _switcher(self, name: str):
        def _cb() -> None:
            if not self._switch_lock.acquire(blocking=False):
                return  # ya hay un cambio en curso

            def _run() -> None:
                try:
                    self._daemon.switch(name)
                except Exception:
                    log.exception("no se pudo cambiar a %r", name)
                finally:
                    self._switch_lock.release()

            threading.Thread(target=_run, name=f"x6-switch-{name}", daemon=True).start()

        return _cb

    def _stop_hotkeys(self) -> None:
        if self._hotkeys is not None:
            try:
                self._hotkeys.stop()
            except Exception:
                log.debug("error parando hotkeys", exc_info=True)
            self._hotkeys = None
