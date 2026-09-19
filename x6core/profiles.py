"""Modelo de perfiles: onboard (memoria del mouse) vs host (daemon en 2do plano).
Onboard persiste sin software. Host permite macros/hotkeys ilimitados + auto-switch por app.
"""
from __future__ import annotations
import json, pathlib
from dataclasses import dataclass, field, asdict

@dataclass
class ButtonAction:
    # kind: click | shortcut | macro | dpi_cycle | dpi_up | dpi_down | media | off
    kind: str = "click"
    value: str = "left"  # ej: "left", "ctrl+c", "macro:fire", "play_pause"
    macro_id: str = ""

@dataclass
class Profile:
    name: str = "FPS"
    # --- onboard (via report 0x04/0x06) ---
    dpi_stages: list[int] = field(default_factory=lambda: [800, 1200, 1600, 3200, 5600, 26000][:6])
    dpi_colors: list[int] = field(default_factory=lambda: [0xFF0000, 0x00FF00, 0x0000FF, 0xFFFF00, 0xFF00FF, 0x00FFFF, 0xFFFFFF, 0xFFFFFF][:8])
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
        "dpi": ButtonAction("click", "dpi_cycle"),
    })
    macros: dict[str, list[dict]] = field(default_factory=dict)  # id -> [{key,down_ms,delay_ms}]
    auto_apps: list[str] = field(default_factory=list)  # ej: ["cs2.exe","firefox"] -> auto-switch

    def onboard_payload(self) -> bytes:
        from .protocol import build_dpi_report
        colors = (self.dpi_colors + [0xFFFFFF] * 8)[:8]
        return build_dpi_report(self.dpi_stages, colors, self.active_stage)

    def save(self, path: pathlib.Path):
        d = asdict(self)
        # serializar ButtonAction
        d["buttons"] = {k: asdict(v) for k, v in self.buttons.items()}
        path.write_text(json.dumps(d, indent=2))

    @staticmethod
    def load(path: pathlib.Path) -> "Profile":
        d = json.loads(path.read_text())
        btns = {k: ButtonAction(**v) for k, v in d.get("buttons", {}).items()}
        d["buttons"] = btns
        return Profile(**d)


def profiles_dir() -> pathlib.Path:
    import platformdirs
    p = pathlib.Path(platformdirs.user_config_dir("x6-manager")) / "profiles"
    p.mkdir(parents=True, exist_ok=True)
    return p
