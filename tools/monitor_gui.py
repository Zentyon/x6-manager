"""Monitor visual de botones del mouse (solo observa, no bloquea nada).

Abre una ventana always-on-top: al pulsar un boton fisico se ilumina su recuadro y se
registra el evento. Sirve para saber si los botones laterales llegan como XBUTTON
(x1/x2), como clic normal, o como teclas (si el mouse los tiene remapeado en memoria).

    python tools/monitor_gui.py
    python tools/monitor_gui.py --log C:\\ruta\\eventos.log
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QGridLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from pynput import keyboard, mouse

BUTTONS = [
    ("left", "Izquierdo"),
    ("right", "Derecho"),
    ("middle", "Rueda (clic)"),
    ("x1", "Lateral x1"),
    ("x2", "Lateral x2"),
]

ON = "background:#1b5e20; color:white; border-radius:6px; padding:10px;"
OFF = "background:#2a2a30; color:#ddd; border-radius:6px; padding:10px;"
HINT = "Pulsa cada boton del mouse. Si un lateral no se marca, no llega como XBUTTON."


class Monitor(QWidget):
    event = Signal(str)

    def __init__(self, log_path: Path):
        super().__init__()
        self.log_path = log_path
        self.counts = {name: 0 for name, _ in BUTTONS}
        self.setWindowTitle("X6 - monitor de botones")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.resize(460, 520)

        layout = QVBoxLayout(self)
        title = QLabel("Monitor de botones (solo lectura)")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        layout.addWidget(title)
        hint = QLabel(HINT)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        grid = QGridLayout()
        self.labels: dict[str, QLabel] = {}
        for i, (name, text) in enumerate(BUTTONS):
            lbl = QLabel(f"{text}\n0")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(OFF)
            lbl.setFont(QFont("Segoe UI", 11, QFont.Bold))
            grid.addWidget(lbl, i // 2, i % 2)
            self.labels[name] = lbl
        layout.addLayout(grid)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log, 1)

        row = QPushButton("Reiniciar contadores")
        row.clicked.connect(self.reset)
        layout.addWidget(row)

        self.event.connect(self._on_event)

    def reset(self) -> None:
        for name, text in BUTTONS:
            self.counts[name] = 0
            self.labels[name].setText(f"{text}\n0")
        self.log.clear()

    def _on_event(self, line: str) -> None:
        ts = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log.append(f"{ts}  {line}")
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{ts} {line}\n")

    def flash(self, name: str, pressed: bool) -> None:
        if name not in self.labels:
            return
        text = dict(BUTTONS)[name]
        if pressed:
            self.counts[name] += 1
        self.labels[name].setText(f"{text}\n{self.counts[name]}")
        self.labels[name].setStyleSheet(ON if pressed else OFF)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=os.path.join(tempfile.gettempdir(), "x6-monitor.log"))
    args = ap.parse_args()
    log_path = Path(args.log)
    log_path.write_text("", encoding="utf-8")

    app = QApplication(sys.argv)
    win = Monitor(log_path)

    def on_click(_x, _y, button, pressed):
        name = str(button).split(".")[-1]
        if name not in win.counts:
            win.event.emit(f"??? {button} {'DOWN' if pressed else 'UP'}")
            return
        win.flash(name, pressed)
        win.event.emit(f"{name:7s} {'DOWN' if pressed else 'UP'}")

    def on_scroll(_x, _y, dx, dy):
        win.event.emit(f"scroll  dx={dx} dy={dy}")

    def on_press(key):
        win.event.emit(f"tecla   DOWN {key!r}")

    listener = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
    kl = keyboard.Listener(on_press=on_press)
    listener.start()
    kl.start()
    win.show()
    win.event.emit("listo: pulsa los botones del mouse")
    code = app.exec()
    listener.stop()
    kl.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
