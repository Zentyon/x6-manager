"""Daemon en 2do plano: aplica perfil onboard + ejecuta hotkeys host + auto-switch por app.
Tray hace cambio rapido: click perfil o hotkey global Ctrl+Alt+1..9.
"""
from __future__ import annotations
import time, pathlib
from .profiles import Profile, profiles_dir
from . import transport

class Daemon:
    def __init__(self):
        self.profiles: dict[str, Profile] = {}
        self.active = ""
        self.h = None
        self.reload()

    def reload(self):
        d = profiles_dir()
        for p in d.glob("*.json"):
            try:
                self.profiles[p.stem] = Profile.load(p)
            except Exception as e:
                print("perfil roto", p, e)
        if not self.profiles:
            fp = Profile("FPS")
            fp.save(d / "FPS.json")
            self.profiles["FPS"] = fp
        if self.active not in self.profiles:
            self.active = sorted(self.profiles)[0]

    def ensure_open(self):
        if self.h is None:
            self.h = transport.open_dongle()

    def switch(self, name: str):
        assert name in self.profiles, name
        self.ensure_open()
        prof = self.profiles[name]
        transport.apply_onboard(self.h, prof.onboard_payload())
        # TODO host: reprogramar pynput hotkeys + macros de prof.buttons/macros
        self.active = name
        print(f"[daemon] perfil activo: {name} {prof.dpi_stages} {prof.polling}Hz")
        return True

    def cycle(self, delta=+1):
        names = sorted(self.profiles)
        i = (names.index(self.active) + delta) % len(names)
        return self.switch(names[i])

    def monitor_loop(self):
        """Escucha heartbeats/boton DPI fisico y re-resuelve DPI desde mapping local."""
        self.ensure_open()
        while True:
            try:
                st = transport.read_status(self.h)
                if st.get("type") == "dpi_button":
                    prof = self.profiles[self.active]
                    idx = st["active_stage"]
                    if 0 <= idx < len(prof.dpi_stages):
                        print(f"[daemon] DPI fisico -> stage {idx} = {prof.dpi_stages[idx]}")
                    else:
                        print(f"[daemon] DPI fisico stage {idx} desconocido")
                elif st.get("type") == "heartbeat":
                    pass  # TODO actualizar bateria en UI/tray
            except Exception as e:
                print("[daemon]", e)
                time.sleep(2)
                try:
                    self.h.close()
                except Exception:
                    pass
                self.h = None
                time.sleep(2)
                self.ensure_open()
