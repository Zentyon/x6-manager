"""Modelo de perfiles: onboard (memoria del mouse) vs host (daemon en 2do plano).

Onboard persiste sin software. Host permite macros/hotkeys/capas (G-Shift) ilimitadas
y auto-switch por app.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import asdict, dataclass, field, fields

from .protocol import DPI_MAX, DPI_MIN, DPI_STEP, MAX_STAGES, POLLING_MAP, build_dpi_report

PROFILE_VERSION = 1

ACTION_KINDS = {
    "click",       # value: left|right|middle|forward|backward
    "shortcut",    # value: "ctrl+alt+1" (tap al pulsar)
    "hold",        # value: "shift" (down al pulsar, up al soltar)
    "macro",       # macro_id o value
    "media",       # value: play_pause|next|previous|volume_up|volume_down|mute
    "dpi_cycle",
    "dpi_up",
    "dpi_down",
    "off",
}
MOUSE_BUTTONS = {"left", "right", "middle", "forward", "backward"}
MEDIA_KEYS = {"play_pause", "next", "previous", "volume_up", "volume_down", "mute"}
SHIFT_MODES = {"hold", "toggle"}
LODS = {"1mm", "2mm"}


class ProfileError(ValueError):
    pass


@dataclass
class ButtonAction:
    kind: str = "click"
    value: str = "left"
    macro_id: str = ""

    def validate(self, where: str = "accion") -> None:
        if self.kind not in ACTION_KINDS:
            raise ProfileError(f"{where}: kind desconocido {self.kind!r} (validos {sorted(ACTION_KINDS)})")
        if self.kind == "click" and self.value not in MOUSE_BUTTONS:
            raise ProfileError(f"{where}: click a {self.value!r} no es un boton ({sorted(MOUSE_BUTTONS)})")
        if self.kind == "media" and self.value not in MEDIA_KEYS:
            raise ProfileError(f"{where}: media {self.value!r} desconocida ({sorted(MEDIA_KEYS)})")
        if self.kind in ("shortcut", "hold") and not self.value:
            raise ProfileError(f"{where}: {self.kind} sin atajo")
        if self.kind == "macro" and not (self.macro_id or self.value):
            raise ProfileError(f"{where}: macro sin id")


@dataclass
class Profile:
    name: str = "FPS"
    version: int = PROFILE_VERSION
    # --- onboard (via report 0x04) ---
    dpi_stages: list[int] = field(default_factory=lambda: [800, 1200, 1600, 3200, 5600, 26000])
    dpi_colors: list[int] = field(
        default_factory=lambda: [0xFF0000, 0x00FF00, 0x0000FF, 0xFFFF00, 0xFF00FF, 0x00FFFF]
    )
    active_stage: int = 0
    polling: int = 1000
    lod: str = "1mm"  # 1mm|2mm
    debounce_ms: int = 4
    angle_snap: bool = False
    motion_sync: bool = True
    sleep_min: int = 10
    # --- host (daemon) ---
    buttons: dict[str, ButtonAction] = field(default_factory=lambda: {
        "left": ButtonAction("click", "left"),
        "right": ButtonAction("click", "right"),
        "middle": ButtonAction("click", "middle"),
        "forward": ButtonAction("shortcut", "alt+Right"),
        "backward": ButtonAction("shortcut", "alt+Left"),
        "dpi": ButtonAction("dpi_cycle"),
    })
    # capas tipo G-Shift: nombre -> {boton: accion}
    layers: dict[str, dict[str, ButtonAction]] = field(default_factory=dict)
    shift_button: str = ""          # "" = sin capa; p.ej. "forward" o "dpi"
    shift_layer: str = "shift"      # capa a activar
    shift_mode: str = "hold"        # hold | toggle
    macros: dict[str, list[dict]] = field(default_factory=dict)  # id -> [{key,down_ms,delay_ms}]
    auto_apps: list[str] = field(default_factory=list)  # ["cs2.exe", "firefox"]

    # -- validacion --------------------------------------------------------
    def validate(self) -> None:
        if not self.name.strip():
            raise ProfileError("el perfil necesita nombre")
        stages = self.dpi_stages
        if not 1 <= len(stages) <= MAX_STAGES:
            raise ProfileError(f"dpi_stages: se esperan 1..{MAX_STAGES}, hay {len(stages)}")
        for i, dpi in enumerate(stages):
            if not isinstance(dpi, int) or not DPI_MIN <= dpi <= DPI_MAX or dpi % DPI_STEP:
                raise ProfileError(
                    f"dpi_stages[{i}]={dpi!r}: debe ser entero {DPI_MIN}-{DPI_MAX} multiplo de {DPI_STEP}"
                )
        if not 0 <= self.active_stage < len(stages):
            raise ProfileError(f"active_stage={self.active_stage} fuera de 0..{len(stages) - 1} (1-indexado en el alambre)")
        if not 1 <= len(self.dpi_colors) <= MAX_STAGES:
            raise ProfileError(f"dpi_colors: se esperan 1..{MAX_STAGES}, hay {len(self.dpi_colors)}")
        for i, c in enumerate(self.dpi_colors):
            if not isinstance(c, int) or not 0 <= c <= 0xFFFFFF:
                raise ProfileError(f"dpi_colors[{i}]={c!r}: esperado 0xRRGGBB")
        if self.polling not in POLLING_MAP:
            raise ProfileError(f"polling={self.polling}: soportados {sorted(POLLING_MAP)}")
        if self.lod not in LODS:
            raise ProfileError(f"lod={self.lod!r}: soportados {sorted(LODS)}")
        if not 0 <= self.debounce_ms <= 255:
            raise ProfileError("debounce_ms fuera de 0..255")
        for name, action in self.buttons.items():
            if name not in MOUSE_BUTTONS and name != "dpi":
                raise ProfileError(f"boton desconocido {name!r}")
            action.validate(f"buttons[{name}]")
        if self.shift_button and self.shift_button not in MOUSE_BUTTONS | {"dpi"}:
            raise ProfileError(f"shift_button={self.shift_button!r} desconocido")
        if self.shift_mode not in SHIFT_MODES:
            raise ProfileError(f"shift_mode={self.shift_mode!r}: {sorted(SHIFT_MODES)}")
        if self.shift_button:
            if not self.shift_layer:
                raise ProfileError("shift_button sin shift_layer")
            if self.shift_layer in self.layers:
                for name, action in self.layers[self.shift_layer].items():
                    if name not in MOUSE_BUTTONS and name != "dpi":
                        raise ProfileError(f"layers[{self.shift_layer}][{name}]: boton desconocido")
                    action.validate(f"layers[{self.shift_layer}][{name}]")
            else:
                raise ProfileError(
                    f"shift_layer={self.shift_layer!r} no existe en layers ({sorted(self.layers)})"
                )
        for macro_id, steps in self.macros.items():
            if not isinstance(steps, list) or not steps:
                raise ProfileError(f"macro {macro_id!r} vacia")
            for i, step in enumerate(steps):
                if not isinstance(step, dict) or "key" not in step:
                    raise ProfileError(f"macro {macro_id}[{i}]: falta 'key'")

    # -- protocolo ---------------------------------------------------------
    def onboard_payload(self) -> bytes:
        self.validate()
        return build_dpi_report(self.dpi_stages, self.dpi_colors, self.active_stage)

    # -- capas -------------------------------------------------------------
    def layer(self, name: str | None = None) -> dict[str, ButtonAction]:
        return dict(self.layers.get(name or self.shift_layer, {}))

    def action_for(self, button: str, layer: str | None = None) -> ButtonAction | None:
        if layer:
            action = self.layers.get(layer, {}).get(button)
            if action is not None:
                return action
        return self.buttons.get(button)

    # -- persistencia ------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)  # asdict ya convierte los ButtonAction anidados

    def save(self, path: pathlib.Path) -> None:
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: pathlib.Path) -> "Profile":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProfileError(f"{path.name}: JSON invalido ({exc})") from exc
        if not isinstance(data, dict):
            raise ProfileError(f"{path.name}: se esperaba un objeto JSON")

        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ProfileError(
                f"{path.name}: claves desconocidas {sorted(unknown)} (validas {sorted(known)})"
            )
        data.pop("version", None)
        data["buttons"] = {
            k: _as_action(v, f"{path.name}: buttons[{k}]")
            for k, v in (data.get("buttons") or {}).items()
        }
        data["layers"] = {
            layer: {
                k: _as_action(v, f"{path.name}: layers[{layer}][{k}]")
                for k, v in (buttons or {}).items()
            }
            for layer, buttons in (data.get("layers") or {}).items()
        }
        try:
            profile = cls(**data)
        except TypeError as exc:
            raise ProfileError(f"{path.name}: {exc}") from exc
        profile.validate()
        return profile


def _as_action(raw: object, where: str) -> ButtonAction:
    if isinstance(raw, ButtonAction):
        action = raw
    elif isinstance(raw, str):
        # azucar: "ctrl+c" = shortcut, "left" = click
        action = ButtonAction("click", raw) if raw in MOUSE_BUTTONS else ButtonAction("shortcut", raw)
    elif isinstance(raw, dict):
        action = ButtonAction(**raw)
        # migracion: ButtonAction("click", "dpi_cycle") de la version inicial
        if action.kind == "click" and action.value not in MOUSE_BUTTONS:
            if action.value in ACTION_KINDS:
                action = ButtonAction(action.value, "", action.macro_id)
            else:
                raise ProfileError(f"{where}: click a {action.value!r} no es un boton")
    else:
        raise ProfileError(f"{where}: formato no soportado ({type(raw).__name__})")
    action.validate(where)
    return action


def _config_dir() -> pathlib.Path:
    import platformdirs

    # appauthor=False evita el tipico <autor>\<app> duplicado en Windows
    return pathlib.Path(platformdirs.user_config_dir("x6-manager", appauthor=False))


def profiles_dir() -> pathlib.Path:
    p = _config_dir() / "profiles"
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_path() -> pathlib.Path:
    return _config_dir() / "state.json"
