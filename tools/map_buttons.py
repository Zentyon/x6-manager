"""Asistente para mapear los botones fisicos del X6, uno por uno.

Muestra el nombre del boton que toca, captura el siguiente evento (raton o tecla) y
guarda la tabla en JSON. Si un boton no manda nada al SO, se marca como "sin evento"
tras unos segundos.

    python tools/map_buttons.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from pynput import keyboard, mouse

STEPS = [
    ("top1", "BOTON EXTRA de ARRIBA, el mas a la IZQUIERDA"),
    ("top2", "BOTON EXTRA de ARRIBA, el otro"),
    ("side_far", "LATERAL de la palma, el mas ALEJADO"),
    ("side_near", "LATERAL de la palma, el mas CERCANO"),
    ("wheel", "CLIC de la RUEDA (boton central)"),
    ("dpi", "BOTON DPI (detras de la rueda)"),
    ("profile", "BOTON de PERFIL (debajo, si existe)"),
]


class Wizard(QWidget):
    captured = Signal(str)

    def __init__(self, timeout: float, out: Path):
        super().__init__()
        self.timeout = timeout
        self.out = out
        self.index = 0
        self.results: dict[str, str] = {}
        self.waiting = False

        self.setWindowTitle("X6 - mapeo de botones")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.resize(560, 480)

        layout = QVBoxLayout(self)
        self.progress = QLabel("")
        self.progress.setFont(QFont("Segoe UI", 9))
        layout.addWidget(self.progress)

        self.prompt = QLabel("")
        self.prompt.setWordWrap(True)
        self.prompt.setAlignment(Qt.AlignCenter)
        self.prompt.setFont(QFont("Segoe UI", 16, QFont.Bold))
        self.prompt.setStyleSheet("background:#1f2937; color:white; border-radius:8px; padding:24px;")
        layout.addWidget(self.prompt)

        self.status = QLabel("")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setFont(QFont("Segoe UI", 11))
        layout.addWidget(self.status)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log, 1)

        row = QHBoxLayout()
        self.skip = QPushButton("Saltar este boton")
        self.skip.clicked.connect(self.skip_step)
        self.cancel = QPushButton("Terminar")
        self.cancel.clicked.connect(self.finish)
        row.addWidget(self.skip)
        row.addWidget(self.cancel)
        layout.addLayout(row)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.on_timeout)
        self.captured.connect(self.on_captured)

    # -- flujo -------------------------------------------------------------
    def start(self) -> None:
        self.ask()
        self.show()

    def ask(self) -> None:
        if self.index >= len(STEPS):
            self.finish()
            return
        key, text = STEPS[self.index]
        self.progress.setText(f"Paso {self.index + 1} de {len(STEPS)}")
        self.prompt.setText(text)
        self.status.setText(f"Pulsa ese boton... (si no manda nada en {self.timeout:.0f}s se marca solo)")
        self.waiting = True
        self.timer.start(int(self.timeout * 1000))

    def record(self, value: str) -> None:
        if not self.waiting:
            return
        self.waiting = False
        self.timer.stop()
        key, text = STEPS[self.index]
        self.results[key] = value
        self.log.append(f"{text}\n    -> {value}")
        self.index += 1
        QTimer.singleShot(400, self.ask)

    def skip_step(self) -> None:
        if not self.waiting:
            return
        self.waiting = False
        self.timer.stop()
        key, text = STEPS[self.index]
        self.results[key] = "sin evento"
        self.log.append(f"{text}\n    -> sin evento (saltado)")
        self.index += 1
        QTimer.singleShot(150, self.ask)

    def on_timeout(self) -> None:
        if not self.waiting:
            return
        key, text = STEPS[self.index]
        self.results[key] = "sin evento"
        self.log.append(f"{text}\n    -> sin evento (timeout)")
        self.waiting = False
        self.index += 1
        QTimer.singleShot(400, self.ask)

    def on_captured(self, value: str) -> None:
        self.record(value)

    def finish(self) -> None:
        self.timer.stop()
        self.waiting = False
        data = {"steps": [s[0] for s in STEPS], "map": self.results}
        self.out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        self.prompt.setText("Listo")
        self.status.setText(f"Guardado en {self.out}. Cierra la ventana.")
        self.skip.setEnabled(False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=6.0)
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "x6-mapping.json"))
    args = ap.parse_args()
    out = Path(args.out)
    out.write_text("{}\n", encoding="utf-8")

    app = QApplication(sys.argv)
    win = Wizard(args.timeout, out)

    def on_click(_x, _y, button, pressed):
        if pressed:
            win.captured.emit(f"mouse: {button}")

    def on_scroll(_x, _y, dx, dy):
        win.captured.emit(f"scroll: dx={dx} dy={dy}")

    def on_press(key):
        name = f"tecla: {key}"
        win.captured.emit(name)

    ml = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
    kl = keyboard.Listener(on_press=on_press)
    ml.start()
    kl.start()
    win.start()
    code = app.exec()
    ml.stop()
    kl.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
