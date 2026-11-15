"""Demo de capas G-Shift sin bandeja ni UI: captura real + motor de capas.

Carga un perfil, arranca el hook de pynput y muestra por consola cada boton con
la decision (suprimir o no) y los pasos que ejecutaria el daemon. No inyecta
nada: sirve para verificar en hardware que el shift y la capa funcionan.

    python tools/gshift_demo.py --profile gshift-side --seconds 120
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from x6core.backends import make_backend
from x6core.host import key_triggers_for
from x6core.layers import LayerEngine
from x6core.profiles import Profile, profiles_dir


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="gshift-side")
    ap.add_argument("--seconds", type=float, default=120.0)
    args = ap.parse_args()

    path = profiles_dir() / f"{args.profile}.json"
    profile = Profile.load(path)
    engine = LayerEngine(profile)
    start = time.monotonic()
    print(f"perfil: {profile.name}  shift={profile.shift_button!r} "
          f"capa={profile.shift_layer!r}", flush=True)
    print("capa:", {k: f"{v.kind}:{v.value}" for k, v in
                    profile.layers.get(profile.shift_layer, {}).items()}, flush=True)

    def handle(button: str, pressed: bool) -> bool:
        result = engine.handle(button, pressed)
        steps = [f"{s.kind}:{s.value}" for s in result.steps] or ["-"]
        print(f"{time.monotonic() - start:7.2f}s {button:9s} "
              f"{'DOWN' if pressed else 'UP  '} "
              f"suppress={result.suppress!s:5s} capa={engine.layer!s:5s} "
              f"pasos={steps}", flush=True)
        return result.suppress

    backend = make_backend(handle, key_triggers=key_triggers_for(profile))
    backend.start()
    print(f"escuchando {args.seconds:.0f}s...", flush=True)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    backend.stop()
    print("fin", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
