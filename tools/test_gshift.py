"""Prueba en vivo del G-Shift con el perfil real, con estado en pantalla.

Abre una ventana flotante que muestra, en tiempo real:

  * si el hook esta recibiendo los botones laterales (y con que nombre),
  * si la capa se activa al mantener el boton de shift,
  * si el evento nativo se esta bloqueando (suppress=True),
  * cuantas veces se ejecuto la accion de la capa (p. ej. volume_up).

Asi no hace falta adivinar por el log: si el recuadro de la capa se pone verde y el
contador sube, el G-Shift funciona. Usa exactamente el mismo HostInput que `python -m app`.

    python tools/test_gshift.py
    python tools/test_gshift.py --profile gshift-test --seconds 600
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QGridLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from x6core.host import HostInput
from x6core.profiles import Profile, profiles_dir

GREEN = "background:#14532d; color:white; border-radius:8px; padding:14px;"
GRAY = "background:#27272a; color:#d4d4d8; border-radius:8px; padding:14px;"
BLUE = "background:#1e3a8a; color:white; border-radius:8px; padding:14px;"
RED = "background:#7f1d1d; color:white; border-radius:8px; padding:14px;"

SIDES = ("forward", "backward")
NAMES = {"forward": "ADELANTE (x2)", "backward": "ATRAS (x1)",
         "middle": "RUEDA", "left": "IZQUIERDO", "right": "DERECHO", "dpi": "DPI"}


class StubDaemon:
    """Lo minimo que HostInput espera de un daemon (no toca HID)."""

    def __init__(self, profile: Profile):
        self.profile = profile
        self._subs = []

    def subscribe(self, callback) -> None:
        self._subs.append(callback)

    def cycle_stage(self, _delta: int) -> None:
        pass


class Window(QWidget):
    event = Signal(object)

    def __init__(self, profile: Profile, log_path: Path, seconds: float):
        super().__init__()
        self.log_path = log_path
        self.actions = 0
        self.suppressed = 0
        self.seen: list[str] = []
        self.started = time.monotonic()

        self.setWindowTitle("X6 - prueba G-Shift en vivo")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.resize(620, 660)

        root = QVBoxLayout(self)

        title = QLabel("Prueba G-Shift (mismo codigo que la app)")
        title.setFont(QFont("Segoe UI", 15, QFont.Bold))
        root.addWidget(title)

        self.phase = QLabel("")
        self.phase.setWordWrap(True)
        self.phase.setAlignment(Qt.AlignCenter)
        self.phase.setFont(QFont("Segoe UI", 13, QFont.Bold))
        self.phase.setStyleSheet(BLUE)
        self.phase.setMinimumHeight(90)
        root.addWidget(self.phase)

        grid = QGridLayout()
        self.layer_box = QLabel("CAPA\nAPAGADA")
        self.layer_box.setAlignment(Qt.AlignCenter)
        self.layer_box.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.layer_box.setStyleSheet(GRAY)
        grid.addWidget(self.layer_box, 0, 0)

        self.action_box = QLabel("ACCIONES\n0")
        self.action_box.setAlignment(Qt.AlignCenter)
        self.action_box.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.action_box.setStyleSheet(GRAY)
        grid.addWidget(self.action_box, 0, 1)

        self.suppress_box = QLabel("BLOQUEOS\n0")
        self.suppress_box.setAlignment(Qt.AlignCenter)
        self.suppress_box.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.suppress_box.setStyleSheet(GRAY)
        grid.addWidget(self.suppress_box, 1, 0)

        self.name_box = QLabel("ULTIMO BOTON\n-")
        self.name_box.setAlignment(Qt.AlignCenter)
        self.name_box.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.name_box.setStyleSheet(GRAY)
        grid.addWidget(self.name_box, 1, 1)
        root.addLayout(grid)

        info = QLabel(
            "Shift configurado: "
            f"{NAMES.get(profile.shift_button or '', profile.shift_button or 'ninguno')} "
            f"(capa '{profile.shift_layer}', modo {profile.shift_mode})"
        )
        info.setWordWrap(True)
        root.addWidget(info)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 9))
        root.addWidget(self.log, 1)

        row = QGridLayout()
        self.reset = QPushButton("Reiniciar contadores")
        self.reset.clicked.connect(self.reset_counts)
        row.addWidget(self.reset, 0, 0)
        self.quit = QPushButton("SALIR")
        self.quit.setStyleSheet(RED)
        self.quit.clicked.connect(self.close)
        row.addWidget(self.quit, 0, 1)
        root.addLayout(row)

        self.event.connect(self.on_event)
        self.countdown = QTimer(self)
        self.countdown.timeout.connect(self.tick)
        self.countdown.start(1000)
        self.seconds = seconds
        self.phase.setText("Esperando botones...\nMANTEN el lateral de ADELANTE")

    # -- UI ----------------------------------------------------------------
    def tick(self) -> None:
        left = self.seconds - (time.monotonic() - self.started)
        if left <= 0:
            self.close()

    def reset_counts(self) -> None:
        self.actions = self.suppressed = 0
        self.seen.clear()
        self.log.clear()
        self.action_box.setText("ACCIONES\n0")
        self.suppress_box.setText("BLOQUEOS\n0")

    def write(self, line: str) -> None:
        ts = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        text = f"{ts}  {line}"
        self.log.append(text)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(text, flush=True)

    def on_event(self, payload: dict) -> None:
        button = payload["button"]
        pressed = payload["pressed"]
        suppress = payload["suppress"]
        layer = payload["layer"]
        steps = payload["steps"]

        self.name_box.setText(f"ULTIMO BOTON\n{NAMES.get(button, button)} "
                              f"{'DOWN' if pressed else 'UP'}")
        if suppress:
            self.suppressed += 1
            self.suppress_box.setText(f"BLOQUEOS\n{self.suppressed}")

        detail = ", ".join(f"{k}:{v}" for k, v in steps) or "-"
        self.write(f"{button:9s} {'DOWN' if pressed else 'UP  '} "
                   f"suppress={suppress!s:5s} capa={layer or '-':5s} pasos={detail}")

        if button in SIDES and button not in self.seen:
            self.seen.append(button)

        if steps:
            self.actions += 1
            self.action_box.setText(f"ACCIONES\n{self.actions}")
            self.action_box.setStyleSheet(GREEN)
            self.phase.setText(f"FUNCIONA\n{detail} (n.{self.actions})  ->  suelta todo")
            QTimer.singleShot(700, lambda: self.action_box.setStyleSheet(GRAY))
            try:
                import winsound

                winsound.Beep(1200, 120)
            except Exception:
                pass
            return

        if layer:
            self.layer_box.setText(f"CAPA\nACTIVA ({layer})")
            self.layer_box.setStyleSheet(GREEN)
            self.phase.setText("Capa ACTIVA.\nSIN SOLTAR, pulsa el OTRO lateral")
        else:
            self.layer_box.setText("CAPA\nAPAGADA")
            self.layer_box.setStyleSheet(GRAY)
            if button in SIDES:
                if button == payload["shift"]:
                    self.phase.setText("Capa ACTIVA.\nSIN SOLTAR, pulsa el OTRO lateral")
                elif not pressed:
                    self.phase.setText("MANTEN el lateral de ADELANTE\n"
                                       "(el que en el navegador va hacia delante)")
                else:
                    self.phase.setText(f"Ese es {NAMES.get(button, button)}.\n"
                                       "MANTEN el OTRO lateral y pulsa este")

    def stop_note(self) -> None:
        self.write("cerrando: soltando teclas y parando hook")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="gshift-test")
    ap.add_argument("--seconds", type=float, default=600,
                    help="cierre automatico de seguridad")
    ap.add_argument("--log", default=os.path.join(tempfile.gettempdir(), "x6-gshift-test.log"))
    args = ap.parse_args()

    path = profiles_dir() / f"{args.profile}.json"
    if not path.exists():
        print(f"no existe el perfil {path}", file=sys.stderr)
        return 1
    profile = Profile.load(path)
    log_path = Path(args.log)
    log_path.write_text("", encoding="utf-8")

    app = QApplication(sys.argv)
    win = Window(profile, log_path, args.seconds)

    def on_button(button: str, pressed: bool, result, layer: str | None) -> None:
        win.event.emit({
            "button": button,
            "pressed": pressed,
            "suppress": result.suppress,
            "steps": [(s.kind, s.value) for s in result.steps],
            "layer": layer,
            "shift": profile.shift_button,
        })

    host = HostInput(StubDaemon(profile), dry_run=False, hotkeys=False,
                     on_button=on_button)
    host.start()
    win.show()
    win.write(f"hook iniciado, perfil {args.profile}, supresion="
              f"{host.backend.supports_suppression}")

    code = app.exec()
    win.stop_note()
    host.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
