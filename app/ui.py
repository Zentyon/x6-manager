"""UI estilo Logitech G Hub (MVP PySide6): sidebar perfiles | DPI sliders+color | Botones | Macros.
Final recomendado: Tauri+React para polish total, reutilizando x6core via Rust hidapi.
"""
try:
    from PySide6.QtWidgets import (QApplication, QMainWindow, QListWidget, QSlider,
        QVBoxLayout, QWidget, QHBoxLayout, QLabel, QPushButton, QSpinBox)
    from PySide6.QtCore import Qt
    HAS_QT = True
except ImportError:
    HAS_QT = False

class MainWindow(QMainWindow if HAS_QT else object):
    def __init__(self, daemon):
        if not HAS_QT:
            raise RuntimeError("pip install PySide6 para la UI")
        super().__init__()
        self.daemon = daemon
        self.setWindowTitle("X6 Manager - estilo G Hub")
        self.resize(1100, 650)
        root = QHBoxLayout()
        self.list = QListWidget()
        self.list.addItems(sorted(daemon.profiles))
        self.list.currentTextChanged.connect(self.on_profile)
        self.list.setCurrentRow(0)
        root.addWidget(self.list, 1)
        mid = QVBoxLayout()
        mid.addWidget(QLabel("DPI stages (50-26000, paso 50) - color por stage - polling - LOD"))
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(50, 26000); self.dpi_spin.setSingleStep(50)
        self.apply_btn = QPushButton("Aplicar al mouse (SET_REPORT 0x04)")
        self.apply_btn.clicked.connect(self.on_apply)
        mid.addWidget(self.dpi_spin); mid.addWidget(self.apply_btn)
        mid.addWidget(QLabel("Botones: click en imagen del mouse para asignar shortcut/macro/DPI cycle (report 0x07 pendiente captura usbmon)"))
        mid.addWidget(QLabel("Macros: editor key/down/delay/repeat (report 0x09 pendiente captura usbmon)"))
        w = QWidget(); w.setLayout(root)
        # layout derecha
        rw = QWidget(); rw.setLayout(mid)
        root.addWidget(rw, 3)
        c = QWidget(); c.setLayout(root); self.setCentralWidget(c)

    def on_profile(self, name):
        if name: self.daemon.switch(name)

    def on_apply(self):
        # MVP: cambia primer stage al valor del spin y aplica
        p = self.daemon.profiles[self.daemon.active]
        p.dpi_stages[0] = int(self.dpi_spin.value() // 50 * 50)
        from pathlib import Path
        from .daemon import profiles_dir
        p.save(profiles_dir() / f"{p.name}.json")
        self.daemon.switch(p.name)

def run_ui(daemon):
    import sys
    app = QApplication(sys.argv)
    w = MainWindow(daemon)
    w.show()
    app.exec()
