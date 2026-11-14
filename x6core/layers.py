"""Motor de capas tipo G-Shift (Logitech).

Es logica pura: recibe eventos de boton fisico y devuelve (a) si hay que bloquear el
evento nativo del SO y (b) que acciones ejecutar. No importa pynput ni toca hardware,
por eso se puede testear sin mouse conectado.

Semantica:
  - `shift_button` es el boton "G-Shift". Mientras este pulsado (modo hold) las demas
    pulsaciones se resuelven contra `layers[shift_layer]` y caen a `buttons` si el boton
    no esta redefinido en la capa.
  - modo `toggle`: cada pulsacion del shift alterna la capa (util para el boton DPI,
    que por el canal vendor solo entrega evento de press, sin release).
  - Un boton redefinido se bloquea (suppress) y se inyecta la accion nueva. Un boton
    mapeado a si mismo pasa sin tocar (sin latencia extra).
"""
from __future__ import annotations

from dataclasses import dataclass

from .profiles import ButtonAction, Profile


@dataclass(frozen=True)
class Step:
    """Accion a ejecutar por el inyector."""
    kind: str
    value: str = ""


@dataclass(frozen=True)
class Result:
    suppress: bool = False
    steps: tuple[Step, ...] = ()


NONE = Result()


class LayerEngine:
    def __init__(self, profile: Profile, side_button_map: dict[str, str] | None = None):
        self._side_map = dict(side_button_map or {})
        self._profile: Profile
        self._layer_on = False
        self._held: dict[str, ButtonAction] = {}
        self.set_profile(profile)

    # -- configuracion -----------------------------------------------------
    def set_profile(self, profile: Profile) -> None:
        self._profile = profile
        self._layer_on = False
        self._held.clear()

    def reset(self) -> tuple[Step, ...]:
        """Suelta todo lo que quedara pulsado (cambio de perfil, stop)."""
        steps: list[Step] = []
        for action in self._held.values():
            steps.extend(self._release_steps(action))
        self._held.clear()
        self._layer_on = False
        return tuple(steps)

    @property
    def layer(self) -> str | None:
        return self._profile.shift_layer if self._layer_on else None

    # -- eventos -----------------------------------------------------------
    def handle(self, button: str, pressed: bool) -> Result:
        button = self._side_map.get(button, button)
        shift = self._profile.shift_button
        if shift and button == shift:
            return self._handle_shift(pressed)
        if pressed:
            return self._handle_press(button)
        return self._handle_release(button)

    def _handle_shift(self, pressed: bool) -> Result:
        if self._profile.shift_mode == "toggle":
            if pressed:
                self._layer_on = not self._layer_on
            return Result(suppress=True)
        self._layer_on = pressed
        return Result(suppress=True)

    def resolve(self, button: str) -> ButtonAction | None:
        if self._layer_on:
            layer = self._profile.layers.get(self._profile.shift_layer) or {}
            if button in layer:
                return layer[button]
        return self._profile.buttons.get(button)

    def _handle_press(self, button: str) -> Result:
        action = self.resolve(button)
        if action is None:
            return NONE
        if action.kind == "off":
            return Result(suppress=True)
        if action.kind == "click" and action.value == button:
            return NONE  # passthrough: cero latencia
        self._held[button] = action
        return Result(suppress=True, steps=(self._press_step(action),))

    def _handle_release(self, button: str) -> Result:
        action = self._held.pop(button, None)
        if action is None:
            return NONE
        return Result(suppress=True, steps=self._release_steps(action))

    # -- traduccion accion -> pasos ---------------------------------------
    @staticmethod
    def _press_step(action: ButtonAction) -> Step:
        kind = action.kind
        if kind == "click":
            return Step("click_down", action.value)
        if kind == "hold":
            return Step("keys_down", action.value)
        if kind == "shortcut":
            return Step("keys_tap", action.value)
        if kind == "media":
            return Step("media", action.value)
        if kind == "macro":
            return Step("macro", action.macro_id or action.value)
        if kind in ("dpi_cycle", "dpi_up", "dpi_down"):
            return Step(kind)
        raise ValueError(f"accion no ejecutable: {kind!r}")

    @staticmethod
    def _release_steps(action: ButtonAction) -> tuple[Step, ...]:
        if action.kind == "click":
            return (Step("click_up", action.value),)
        if action.kind == "hold":
            return (Step("keys_up", action.value),)
        # shortcut/macro/media/dpi ya se ejecutaron al pulsar
        return ()


def describe(action: ButtonAction | None) -> str:
    """Texto corto para UI/tray."""
    if action is None:
        return "-"
    if action.kind in ("click", "media", "dpi_cycle", "dpi_up", "dpi_down", "off"):
        return action.kind
    return f"{action.kind}:{action.macro_id or action.value}"
