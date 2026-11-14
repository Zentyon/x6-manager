"""UI minimalista estilo G Hub: sidebar de perfiles | DPI | capas (G-Shift).

Todo lo que habla con el mouse (SET_REPORT + ACK) se hace en un hilo aparte: la UI no
se congela y los errores se muestran en la barra de estado.
"""
from __future__ import annotations

import logging
import threading

try:  # pragma: no cover - depende de extras instalados
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import (
        QApplication, QComboBox, QHBoxLayout, QLabel, QListWidget, QMainWindow,
        QPushButton, QSpinBox, QVBoxLayout, QWidget,
    )

    HAS_QT = True
except ImportError:  # pragma: no cover
    HAS_QT = False

from x6core.layers import describe
from x6core.protocol import DPI_MAX, DPI_MIN, DPI_STEP, POLLING_MAP

log = logging.getLogger(__name__)


if HAS_QT:  # pragma: no cover

    class _Bridge(QObject):
        """Puente hilo-trabajador -> hilo GUI (emision cross-thread segura)."""
        applied = Signal(str)
        failed = Signal(str)
        event = Signal(str, dict)

    class MainWindow(QMainWindow):
        def __init__(self, daemon):
            super().__init__()
            self.daemon = daemon
            self.bridge = _Bridge()
            self.bridge.applied.connect(self._on_applied)
            self.bridge.failed.connect(self._on_failed)
            self.bridge.event.connect(self._on_daemon_event)

            self.setWindowTitle("X6 Manager")
            self.resize(960, 560)

            self.list = QListWidget()
            self.list.addItems(sorted(daemon.profiles))
            self.list.blockSignals(True)
            if daemon.active in daemon.profiles:
                self.list.setCurrentRow(sorted(daemon.profiles).index(daemon.active))
            self.list.blockSignals(False)
            self.list.currentTextChanged.connect(self._on_profile_selected)

            self.title = QLabel("Perfil")
            self.title.setStyleSheet("font-size: 18px; font-weight: 600;")

            self.stage_spin = QSpinBox()
            self.stage_spin.setPrefix("stage ")
            self.dpi_spin = QSpinBox()
            self.dpi_spin.setRange(DPI_MIN, DPI_MAX)
            self.dpi_spin.setSingleStep(DPI_STEP)
            self.apply_btn = QPushButton("Aplicar DPI al mouse")
            self.apply_btn.clicked.connect(self._on_apply)

            self.polling_combo = QComboBox()
            for hz in sorted(POLLING_MAP, reverse=True):
                self.polling_combo.addItem(f"{hz} Hz", hz)
            self.polling_combo.setEnabled(False)  # pendiente: falta capturar report 0x06
            self.polling_combo.setToolTip(
                "El report 0x06 no esta capturado/validado: no se envia al hardware"
            )

            self.status = QLabel("")
            self.status.setStyleSheet("color: #888;")
            self.battery = QLabel("Bateria: -")
            self.layers = QLabel("")
            self.layers.setWordWrap(True)

            right = QVBoxLayout()
            right.addWidget(self.title)
            right.addWidget(self.battery)
            row = QHBoxLayout()
            row.addWidget(self.stage_spin)
            row.addWidget(self.dpi_spin)
            row.addWidget(self.apply_btn)
            right.addLayout(row)
            row2 = QHBoxLayout()
            row2.addWidget(QLabel("Polling"))
            row2.addWidget(self.polling_combo)
            right.addLayout(row2)
            right.addWidget(QLabel("Capas (G-Shift)"))
            right.addWidget(self.layers)
            right.addStretch(1)
            right.addWidget(self.status)

            root = QHBoxLayout()
            root.addWidget(self.list, 1)
            right_host = QWidget()
            right_host.setLayout(right)
            root.addWidget(right_host, 3)
            central = QWidget()
            central.setLayout(root)
            self.setCentralWidget(central)

            self.daemon.subscribe(self._forward_event)
            self.refresh()

        # -- helpers -------------------------------------------------------
        def _forward_event(self, event: str, **payload) -> None:
            self.bridge.event.emit(event, payload)

        def _on_daemon_event(self, event: str, payload: dict) -> None:
            if event == "battery":
                percent = payload.get("percent")
                self.battery.setText(f"Bateria: {percent}%" if percent is not None
                                     else "Bateria: ?")
            elif event in ("switch", "reload", "stage"):
                self.refresh()

        def refresh(self) -> None:
            profile = self.daemon.profile
            self.title.setText(f"{profile.name} ({self.daemon.active})")
            stages = profile.dpi_stages
            self.stage_spin.setRange(1, len(stages))
            self.stage_spin.setValue(profile.active_stage + 1)
            self.dpi_spin.setValue(stages[profile.active_stage])
            if self.polling_combo.count():
                index = self.polling_combo.findData(profile.polling)
                if index >= 0:
                    self.polling_combo.setCurrentIndex(index)
            if profile.shift_button:
                layer = profile.layers.get(profile.shift_layer, {})
                detail = ", ".join(f"{b}->{describe(a)}" for b, a in sorted(layer.items())) or "vacia"
                self.layers.setText(
                    f"{profile.shift_button} ({profile.shift_mode}) activa "
                    f"'{profile.shift_layer}': {detail}"
                )
            else:
                self.layers.setText("sin capa configurada (shift_button vacio)")
            names = sorted(self.daemon.profiles)
            if self.list.count() != len(names):
                self.list.blockSignals(True)
                self.list.clear()
                self.list.addItems(names)
                self.list.blockSignals(False)
            if self.daemon.active in names:
                self.list.blockSignals(True)
                self.list.setCurrentRow(names.index(self.daemon.active))
                self.list.blockSignals(False)

        def _run(self, fn, *args) -> None:
            def _worker() -> None:
                try:
                    fn(*args)
                    self.bridge.applied.emit("ok")
                except Exception as exc:
                    log.exception("accion de UI fallo")
                    self.bridge.failed.emit(str(exc))

            self.apply_btn.setEnabled(False)
            threading.Thread(target=_worker, name="x6-ui-action", daemon=True).start()

        # -- slots ---------------------------------------------------------
        def _on_profile_selected(self, name: str) -> None:
            if not name or name == self.daemon.active:
                return
            self._run(self.daemon.switch, name)

        def _on_apply(self) -> None:
            profile = self.daemon.profile
            stage = self.stage_spin.value() - 1
            dpi = int(self.dpi_spin.value() // DPI_STEP * DPI_STEP)
            profile.dpi_stages[stage] = dpi
            profile.active_stage = stage
            try:
                self.daemon.save_active_profile()
            except Exception as exc:
                self._on_failed(f"perfil invalido: {exc}")
                return
            self._run(self.daemon.switch, profile.name)

        def _on_applied(self, _msg: str) -> None:
            self.apply_btn.setEnabled(True)
            self.status.setText("Aplicado.")
            self.refresh()

        def _on_failed(self, msg: str) -> None:
            self.apply_btn.setEnabled(True)
            self.status.setText(f"Error: {msg}")

        def closeEvent(self, event) -> None:  # noqa: N802 (API Qt)
            self.daemon.unsubscribe(self._forward_event)
            super().closeEvent(event)


else:  # pragma: no cover

    class MainWindow:  # type: ignore[no-redef]
        def __init__(self, daemon):
            raise RuntimeError("instala PySide6 para la UI: pip install PySide6")


def run_ui(daemon) -> None:
    import sys

    app = QApplication(sys.argv)
    window = MainWindow(daemon)
    window.show()
    app.exec()
