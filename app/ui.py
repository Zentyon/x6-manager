"""UI Qt del X6 Manager: lista de perfiles a la izquierda y editor por pestanas.

Reglas de seguridad:

- Seleccionar un perfil **solo carga el editor** (una copia en memoria). No escribe
  nada en el mouse ni cambia el perfil activo.
- `Guardar` escribe el JSON del perfil.
- `Aplicar al mouse` guarda y manda DPI + polling al dongle; ademas deja el perfil
  como activo del daemon.

Solo DPI y polling viajan al raton (report `0x04` y `0x06`). Los botones, capas,
macros y reglas de auto-cambio los ejecuta el daemon en el PC.

Todo lo que habla con el HID va en un hilo aparte para no congelar la ventana.
"""
from __future__ import annotations

import copy
import logging
import os
import sys
import threading

try:  # pragma: no cover - depende de extras instalados
    from PySide6.QtCore import QObject, Qt, QTimer, Signal
    from PySide6.QtGui import QColor, QFont
    from PySide6.QtWidgets import (
        QAbstractItemView, QApplication, QCheckBox, QColorDialog, QComboBox,
        QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
        QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
        QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
        QVBoxLayout, QWidget,
    )

    HAS_QT = True
except ImportError:  # pragma: no cover
    HAS_QT = False

from x6core.profiles import (
    MAX_STAGES, Profile, ProfileError, action_from_text, action_to_text,
    profiles_dir,
)
from x6core.protocol import DPI_MAX, DPI_MIN, DPI_STEP, POLLING_MAP
from x6core.appwatch import foreground_window

log = logging.getLogger(__name__)

BUTTON_LABELS = {
    "left": "Izquierdo",
    "right": "Derecho",
    "middle": "Rueda",
    "forward": "Lateral delantero",
    "backward": "Lateral trasero",
    "dpi": "Boton DPI (detras de la rueda)",
}
BUTTON_ORDER = ["left", "right", "middle", "forward", "backward", "dpi"]

ACTION_HELP = (
    "Formatos: <code>click:left</code>, <code>shortcut:ctrl+c</code>, "
    "<code>hold:shift</code>, <code>media:mute</code>, <code>macro:nombre</code>, "
    "<code>dpi_cycle</code>, <code>dpi_up</code>, <code>dpi_down</code>, "
    "<code>off</code>. Vacio = sin tocar (pasa el boton original)."
)


def _button_label(button: str) -> str:
    return BUTTON_LABELS.get(button, f"Tecla {button.upper()}")


if HAS_QT:  # pragma: no cover - requiere Qt y una sesion grafica

    class _Bridge(QObject):
        """Puente hilo-trabajador -> hilo GUI (emision cross-thread segura)."""

        applied = Signal(str)
        failed = Signal(str)
        event = Signal(str, dict)

    class MainWindow(QMainWindow):
        def __init__(self, daemon):
            super().__init__()
            self.daemon = daemon
            self.working: Profile | None = None
            self._original: Profile | None = None
            self._current_name = ""
            self._layer_origin = ""
            self._loading = False
            self._active_color = QColor("#2e9e6b")
            self._own_process = os.path.basename(sys.executable).lower()
            self._last_window = None
            # Antes de que la ventana se muestre, el primer plano es la app que el
            # usuario estaba usando: es la que interesa recordar.
            initial = foreground_window()
            if initial and initial.process and initial.process.lower() != self._own_process:
                self._last_window = initial
            self._stage_color = 0xFFFFFF

            self.bridge = _Bridge()
            self.bridge.applied.connect(self._on_applied)
            self.bridge.failed.connect(self._on_failed)
            self.bridge.event.connect(self._on_daemon_event)

            self.setWindowTitle("X6 Manager")
            self.resize(1040, 660)

            self._build_ui()
            self._wire()
            self._fg_timer = QTimer(self)
            self._fg_timer.setInterval(800)
            self._fg_timer.timeout.connect(self._remember_foreground)
            self._fg_timer.start()
            self.daemon.subscribe(self._forward_event)
            self._reload_list(self.daemon.active)
            if self._current_name == "":
                names = sorted(self.daemon.profiles)
                if names:
                    self._load_profile(names[0])
            self._update_badge()

        # -- construccion --------------------------------------------------
        def _build_ui(self) -> None:
            self.profile_list = QListWidget()
            self.profile_list.setMinimumWidth(190)
            self.profile_list.setToolTip("Seleccionar un perfil solo abre su editor")

            side = QVBoxLayout()
            side.addWidget(self._section("Perfiles"))
            side.addWidget(self.profile_list, 1)
            row = QHBoxLayout()
            self.new_btn = QPushButton("Nuevo")
            self.dup_btn = QPushButton("Duplicar")
            self.del_btn = QPushButton("Borrar")
            row.addWidget(self.new_btn)
            row.addWidget(self.dup_btn)
            row.addWidget(self.del_btn)
            side.addLayout(row)
            self.reload_btn = QPushButton("Recargar desde disco")
            side.addWidget(self.reload_btn)
            side.addSpacing(8)
            self.active_label = QLabel("")
            self.active_label.setWordWrap(True)
            side.addWidget(self.active_label)
            self.battery_label = QLabel("Bateria: -")
            side.addWidget(self.battery_label)
            self.mode_label = QLabel("")
            self.mode_label.setWordWrap(True)
            self.mode_label.setStyleSheet("color: #888;")
            side.addWidget(self.mode_label)
            side.addStretch(1)
            side_host = QWidget()
            side_host.setLayout(side)

            self.title = QLabel("")
            self.title.setStyleSheet("font-size: 20px; font-weight: 600;")
            self.subtitle = QLabel("")
            self.subtitle.setStyleSheet("color: #888;")
            self.subtitle.setWordWrap(True)

            self.tabs = QTabWidget()
            self.tabs.addTab(self._tab_dpi(), "DPI y sensor")
            self.tabs.addTab(self._tab_buttons(), "Botones")
            self.tabs.addTab(self._tab_layers(), "G-Shift")
            self.tabs.addTab(self._tab_auto(), "Auto por app")

            self.status = QLabel("")
            self.status.setWordWrap(True)
            self.status.setStyleSheet("color: #888;")
            self.save_btn = QPushButton("Guardar")
            self.revert_btn = QPushButton("Descartar")
            self.apply_btn = QPushButton("Aplicar al mouse")
            self.apply_btn.setDefault(True)
            self.apply_btn.setStyleSheet("font-weight: 600;")

            footer = QHBoxLayout()
            footer.addWidget(self.status, 1)
            footer.addWidget(self.revert_btn)
            footer.addWidget(self.save_btn)
            footer.addWidget(self.apply_btn)

            right = QVBoxLayout()
            right.addWidget(self.title)
            right.addWidget(self.subtitle)
            right.addWidget(self.tabs, 1)
            right.addLayout(footer)
            right_host = QWidget()
            right_host.setLayout(right)

            root = QHBoxLayout()
            root.addWidget(side_host)
            root.addWidget(right_host, 1)
            central = QWidget()
            central.setLayout(root)
            self.setCentralWidget(central)

        def _section(self, text: str) -> QLabel:
            label = QLabel(text)
            label.setStyleSheet("font-weight: 600; margin-top: 4px;")
            return label

        def _tab_dpi(self) -> QWidget:
            tab = QWidget()
            layout = QVBoxLayout(tab)

            stages_box = QGroupBox("Stages de DPI (hasta 8, se guardan en el mouse)")
            stages_layout = QVBoxLayout(stages_box)
            self.stage_list = QListWidget()
            self.stage_list.setMaximumHeight(170)
            stages_layout.addWidget(self.stage_list)

            edit_row = QHBoxLayout()
            self.stage_dpi = QSpinBox()
            self.stage_dpi.setRange(DPI_MIN, DPI_MAX)
            self.stage_dpi.setSingleStep(DPI_STEP)
            self.stage_dpi.setSuffix(" DPI")
            self.stage_dpi.setMinimumWidth(140)
            self.color_btn = QPushButton("Color")
            self.stage_active_btn = QPushButton("Marcar activo")
            self.stage_add_btn = QPushButton("Anadir")
            self.stage_remove_btn = QPushButton("Quitar")
            for widget in (self.stage_dpi, self.color_btn, self.stage_active_btn,
                           self.stage_add_btn, self.stage_remove_btn):
                edit_row.addWidget(widget)
            edit_row.addStretch(1)
            stages_layout.addLayout(edit_row)
            layout.addWidget(stages_box)

            sensor_box = QGroupBox("Sensor")
            form = QFormLayout(sensor_box)
            self.polling_combo = QComboBox()
            for hz in sorted(POLLING_MAP, reverse=True):
                self.polling_combo.addItem(f"{hz} Hz", hz)
            self.lod_combo = QComboBox()
            self.lod_combo.addItems(["1mm", "2mm"])
            self.debounce_spin = QSpinBox()
            self.debounce_spin.setRange(0, 255)
            self.debounce_spin.setSuffix(" ms")
            self.sleep_spin = QSpinBox()
            self.sleep_spin.setRange(1, 60)
            self.sleep_spin.setSuffix(" min")
            self.snap_check = QCheckBox("Angle snap")
            self.sync_check = QCheckBox("Motion sync")
            form.addRow("Polling rate", self.polling_combo)
            form.addRow("LOD", self.lod_combo)
            form.addRow("Debounce", self.debounce_spin)
            form.addRow("Sleep", self.sleep_spin)
            form.addRow(self.snap_check)
            form.addRow(self.sync_check)
            sensor_note = QLabel(
                "LOD, debounce, angle snap, motion sync y sleep se guardan en el "
                "perfil, pero el firmware aun no los recibe: solo se envian DPI y "
                "polling."
            )
            sensor_note.setWordWrap(True)
            sensor_note.setStyleSheet("color: #888;")
            form.addRow(sensor_note)
            layout.addWidget(sensor_box)
            layout.addStretch(1)
            return tab

        def _tab_buttons(self) -> QWidget:
            tab = QWidget()
            layout = QVBoxLayout(tab)
            layout.addWidget(QLabel(
                "Acciones que ejecuta el daemon en el PC (no van al firmware)."
            ))
            self.button_table = QTableWidget(0, 2)
            self.button_table.setHorizontalHeaderLabels(["Boton", "Accion"])
            self.button_table.verticalHeader().setVisible(False)
            self.button_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            self.button_table.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeMode.Stretch
            )
            layout.addWidget(self.button_table, 1)
            help_label = QLabel(ACTION_HELP)
            help_label.setWordWrap(True)
            help_label.setStyleSheet("color: #888;")
            layout.addWidget(help_label)
            self.macro_hint = QLabel("")
            self.macro_hint.setWordWrap(True)
            layout.addWidget(self.macro_hint)
            return tab

        def _tab_layers(self) -> QWidget:
            tab = QWidget()
            layout = QVBoxLayout(tab)
            layout.addWidget(QLabel(
                "Mientras el disparador esta pulsado, los botones usan esta capa. "
                "Un boton sin accion propia hereda la de la pestana Botones."
            ))
            row = QHBoxLayout()
            row.addWidget(QLabel("Disparador"))
            self.shift_combo = QComboBox()
            self.shift_combo.addItem("(sin capa)", "")
            for button in BUTTON_ORDER:
                self.shift_combo.addItem(BUTTON_LABELS[button], button)
            row.addWidget(self.shift_combo)
            row.addWidget(QLabel("Modo"))
            self.shift_mode_combo = QComboBox()
            self.shift_mode_combo.addItem("Mantener pulsado (hold)", "hold")
            self.shift_mode_combo.addItem("Alternar (toggle)", "toggle")
            row.addWidget(self.shift_mode_combo)
            row.addStretch(1)
            layout.addLayout(row)

            row2 = QHBoxLayout()
            row2.addWidget(QLabel("Capa"))
            self.layer_combo = QComboBox()
            row2.addWidget(self.layer_combo)
            self.new_layer_btn = QPushButton("Nueva capa")
            row2.addWidget(self.new_layer_btn)
            row2.addStretch(1)
            layout.addLayout(row2)
            self.shift_note = QLabel("")
            self.shift_note.setWordWrap(True)
            self.shift_note.setStyleSheet("color: #888;")
            layout.addWidget(self.shift_note)

            self.layer_table = QTableWidget(0, 2)
            self.layer_table.setHorizontalHeaderLabels(["Boton", "Accion en la capa"])
            self.layer_table.verticalHeader().setVisible(False)
            self.layer_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            self.layer_table.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeMode.Stretch
            )
            layout.addWidget(self.layer_table, 1)
            layer_help = QLabel(ACTION_HELP)
            layer_help.setWordWrap(True)
            layer_help.setStyleSheet("color: #888;")
            layout.addWidget(layer_help)
            return tab

        def _tab_auto(self) -> QWidget:
            tab = QWidget()
            layout = QVBoxLayout(tab)
            self.auto_global = QCheckBox(
                "Cambiar de perfil automaticamente segun la aplicacion en primer "
                "plano (global)"
            )
            layout.addWidget(self.auto_global)
            layout.addWidget(QLabel(
                "Patrones de este perfil. Sin prefijo se comparan con el ejecutable "
                "(notepad vale para notepad.exe); title:... con el titulo de la ventana. "
                "Admite * y ?. Gana el primer perfil por orden alfabetico."
            ))
            self.auto_list = QListWidget()
            self.auto_list.setMaximumHeight(180)
            layout.addWidget(self.auto_list)

            row = QHBoxLayout()
            self.auto_edit = QLineEdit()
            self.auto_edit.setPlaceholderText("cs2.exe, valorant*, title:*YouTube*")
            self.auto_add_btn = QPushButton("Anadir")
            self.auto_remove_btn = QPushButton("Quitar")
            row.addWidget(self.auto_edit, 1)
            row.addWidget(self.auto_add_btn)
            row.addWidget(self.auto_remove_btn)
            layout.addLayout(row)

            self.auto_current_btn = QPushButton("Usar la ultima app en primer plano")
            layout.addWidget(self.auto_current_btn)
            auto_note = QLabel(
                "El cambio espera ~0.8 s con la misma app delante (para no reescribir "
                "el mouse al hacer alt-tab) y vuelve al perfil base al cerrar la app. "
                "Guardar ya pone las reglas a disposición del daemon: no hace falta "
                "que este sea el perfil activo."
            )
            auto_note.setWordWrap(True)
            auto_note.setStyleSheet("color: #888;")
            layout.addWidget(auto_note)
            layout.addStretch(1)
            return tab

        # -- conexiones ----------------------------------------------------
        def _wire(self) -> None:
            self.profile_list.currentTextChanged.connect(self._on_profile_selected)
            self.new_btn.clicked.connect(self._on_new)
            self.dup_btn.clicked.connect(self._on_duplicate)
            self.del_btn.clicked.connect(self._on_delete)
            self.reload_btn.clicked.connect(self._on_reload)
            self.save_btn.clicked.connect(self._on_save)
            self.revert_btn.clicked.connect(self._on_discard)
            self.apply_btn.clicked.connect(self._on_apply)

            self.stage_list.currentRowChanged.connect(self._on_stage_selected)
            self.stage_dpi.valueChanged.connect(self._on_stage_dpi)
            self.color_btn.clicked.connect(self._on_stage_color)
            self.stage_active_btn.clicked.connect(self._on_stage_active)
            self.stage_add_btn.clicked.connect(self._on_stage_add)
            self.stage_remove_btn.clicked.connect(self._on_stage_remove)

            self.shift_combo.currentIndexChanged.connect(self._on_shift_changed)
            self.shift_mode_combo.currentIndexChanged.connect(self._update_shift_note)
            self.layer_combo.currentIndexChanged.connect(self._on_layer_changed)
            self.new_layer_btn.clicked.connect(self._on_new_layer)

            self.auto_add_btn.clicked.connect(self._on_auto_add)
            self.auto_edit.returnPressed.connect(self._on_auto_add)
            self.auto_remove_btn.clicked.connect(self._on_auto_remove)
            self.auto_current_btn.clicked.connect(self._on_auto_current)
            self.auto_global.toggled.connect(self._on_auto_global)

        # -- lista de perfiles --------------------------------------------
        def _reload_list(self, select: str | None = None) -> None:
            names = sorted(self.daemon.profiles)
            self.profile_list.blockSignals(True)
            self.profile_list.clear()
            active_font = QFont()
            active_font.setBold(True)
            for name in names:
                item = QListWidgetItem(name)
                if name == self.daemon.active:
                    item.setFont(active_font)
                    item.setForeground(self._active_color)
                self.profile_list.addItem(item)
            target = select if select in names else self.daemon.active
            if target in names:
                self.profile_list.setCurrentRow(names.index(target))
            self.profile_list.blockSignals(False)
            self._update_badge()

        def _select_in_list(self, name: str) -> None:
            names = sorted(self.daemon.profiles)
            if name not in names:
                return
            self.profile_list.blockSignals(True)
            self.profile_list.setCurrentRow(names.index(name))
            self.profile_list.blockSignals(False)

        def _on_profile_selected(self, name: str) -> None:
            if self._loading or not name or name == self._current_name:
                return
            if name not in self.daemon.profiles:
                return
            if self._is_dirty():
                answer = QMessageBox.question(
                    self, "Cambios sin guardar",
                    f"'{self._current_name}' tiene cambios sin guardar.",
                    QMessageBox.StandardButton.Save
                    | QMessageBox.StandardButton.Discard
                    | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Save,
                )
                if answer == QMessageBox.StandardButton.Cancel:
                    self._select_in_list(self._current_name)
                    return
                if answer == QMessageBox.StandardButton.Save and not self._save_silent():
                    self._select_in_list(self._current_name)
                    return
            self._load_profile(name)

        def _is_dirty(self) -> bool:
            if self.working is None or self._original is None:
                return False
            try:
                snapshot = self._working_snapshot()
            except ProfileError:
                log.info("cambios sin guardar en %s: accion no valida", self._current_name)
                return True
            original = self._original.to_dict()
            if snapshot == original:
                return False
            fields = sorted(k for k in set(snapshot) | set(original)
                            if snapshot.get(k) != original.get(k))
            log.info("cambios sin guardar en %s: %s", self._current_name, fields)
            return True

        def _working_snapshot(self) -> dict:
            """Vista actual del editor, sin escribir nada."""
            self._read_buttons_into(self.working)
            self._read_sensor_into(self.working)
            self._read_shift_into(self.working)
            self._stash_layer()
            self.working.auto_apps = self._auto_patterns()
            return self.working.to_dict()

        # -- carga del editor ---------------------------------------------
        def _load_profile(self, name: str) -> None:
            profile = self.daemon.profiles.get(name)
            if profile is None:
                return
            self._loading = True
            try:
                self.working = copy.deepcopy(profile)
                self._original = copy.deepcopy(profile)
                self._current_name = name
                self._fill_widgets()
            finally:
                self._loading = False
            self.status.setText(f"Editando '{name}' (sin guardar no se manda nada).")
            self._update_badge()

        def _fill_widgets(self) -> None:
            profile = self.working
            assert profile is not None
            self.title.setText(profile.name)
            self.subtitle.setText(
                f"{len(profile.dpi_stages)} stages · {profile.polling} Hz · "
                f"{len(profile.buttons)} acciones de boton · "
                f"{len(profile.layers)} capa(s)"
            )

            self.stage_list.blockSignals(True)
            self.stage_list.clear()
            colors = self._stage_colors()
            for i, dpi in enumerate(profile.dpi_stages):
                self.stage_list.addItem(self._stage_text(i, dpi, colors[i]))
            active = min(profile.active_stage, len(profile.dpi_stages) - 1)
            self.stage_list.setCurrentRow(active)
            self.stage_list.blockSignals(False)
            self._on_stage_selected(active)

            index = self.polling_combo.findData(profile.polling)
            self.polling_combo.setCurrentIndex(index if index >= 0 else 0)
            self.lod_combo.setCurrentText(profile.lod)
            self.debounce_spin.setValue(profile.debounce_ms)
            self.sleep_spin.setValue(profile.sleep_min)
            self.snap_check.setChecked(profile.angle_snap)
            self.sync_check.setChecked(profile.motion_sync)

            self._fill_buttons()
            self.macro_hint.setText(
                "Macros definidos: " + ", ".join(sorted(profile.macros))
                if profile.macros else
                "Sin macros en este perfil (se definen en el JSON o con tools/write_config.py)."
            )

            self.shift_combo.blockSignals(True)
            self.shift_combo.clear()
            self.shift_combo.addItem("(sin capa)", "")
            for button in BUTTON_ORDER:
                self.shift_combo.addItem(BUTTON_LABELS[button], button)
            if profile.shift_button and self.shift_combo.findData(profile.shift_button) < 0:
                # disparadores de teclado (perfiles antiguos o editados a mano)
                self.shift_combo.addItem(
                    f"Tecla {profile.shift_button.upper()}", profile.shift_button
                )
            self.shift_combo.setCurrentIndex(
                max(0, self.shift_combo.findData(profile.shift_button))
            )
            self.shift_combo.blockSignals(False)
            mode = self.shift_mode_combo.findData(profile.shift_mode)
            self.shift_mode_combo.setCurrentIndex(mode if mode >= 0 else 0)
            self._reload_layer_combo(profile.shift_layer or "shift")
            self._load_layer_table(profile.shift_layer or "shift")
            self._update_shift_note()

            self._reload_auto()

        def _fill_buttons(self) -> None:
            profile = self.working
            assert profile is not None
            extras = [b for b in profile.buttons if b not in BUTTON_ORDER]
            rows = BUTTON_ORDER + sorted(extras)
            self.button_table.setRowCount(len(rows))
            self._button_edits: dict[str, QLineEdit] = {}
            for row, button in enumerate(rows):
                label = QTableWidgetItem(_button_label(button))
                label.setFlags(label.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.button_table.setItem(row, 0, label)
                edit = QLineEdit()
                action = profile.buttons.get(button)
                edit.setText(action_to_text(action) if action else "")
                edit.setPlaceholderText("(hereda el boton original)")
                self.button_table.setCellWidget(row, 1, edit)
                self._button_edits[button] = edit

        def _reload_layer_combo(self, select: str) -> None:
            profile = self.working
            assert profile is not None
            names = sorted(set(profile.layers) | ({select} if select else set()))
            if not names:
                names = ["shift"]
            current = select or names[0]
            self.layer_combo.blockSignals(True)
            self.layer_combo.clear()
            self.layer_combo.addItems(names)
            self.layer_combo.setCurrentText(current)
            self.layer_combo.blockSignals(False)
            self._layer_origin = current

        def _load_layer_table(self, layer: str) -> None:
            profile = self.working
            assert profile is not None
            mapping = profile.layers.get(layer, {})
            self.layer_table.setRowCount(len(BUTTON_ORDER))
            self._layer_edits: dict[str, QLineEdit] = {}
            for row, button in enumerate(BUTTON_ORDER):
                label = QTableWidgetItem(_button_label(button))
                label.setFlags(label.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.layer_table.setItem(row, 0, label)
                edit = QLineEdit()
                action = mapping.get(button)
                edit.setText(action_to_text(action) if action else "")
                edit.setPlaceholderText("(hereda de Botones)")
                self.layer_table.setCellWidget(row, 1, edit)
                self._layer_edits[button] = edit

        def _reload_auto(self) -> None:
            profile = self.working
            assert profile is not None
            self.auto_list.clear()
            self.auto_list.addItems(profile.auto_apps)
            self.auto_global.blockSignals(True)
            self.auto_global.setChecked(bool(getattr(self.daemon, "auto_apps_enabled", True)))
            self.auto_global.blockSignals(False)

        # -- lectura del editor -------------------------------------------
        def _stage_colors(self) -> list[int]:
            profile = self.working
            assert profile is not None
            colors = list(profile.dpi_colors)
            while len(colors) < len(profile.dpi_stages):
                colors.append(0xFFFFFF)
            return colors[:len(profile.dpi_stages)]

        def _stage_text(self, index: int, dpi: int, color: int) -> str:
            mark = " *" if index == self.working.active_stage else ""
            return f"{index + 1}.  {dpi:>5} DPI    #{color:06X}{mark}"

        def _read_buttons_into(self, profile: Profile) -> None:
            mapping = {}
            for button, edit in getattr(self, "_button_edits", {}).items():
                text = edit.text().strip()
                if not text:
                    continue
                try:
                    mapping[button] = action_from_text(text)
                except ProfileError as exc:
                    raise ProfileError(f"{_button_label(button)}: {exc}") from exc
            profile.buttons = mapping

        def _read_sensor_into(self, profile: Profile) -> None:
            profile.polling = int(self.polling_combo.currentData() or profile.polling)
            profile.lod = self.lod_combo.currentText() or profile.lod
            profile.debounce_ms = self.debounce_spin.value()
            profile.sleep_min = self.sleep_spin.value()
            profile.angle_snap = self.snap_check.isChecked()
            profile.motion_sync = self.sync_check.isChecked()

        def _read_shift_into(self, profile: Profile) -> None:
            profile.shift_button = self.shift_combo.currentData() or ""
            profile.shift_mode = self.shift_mode_combo.currentData() or "hold"
            layer = self.layer_combo.currentText().strip()
            if layer:
                profile.shift_layer = layer

        def _read_layer(self) -> dict:
            mapping = {}
            for button, edit in getattr(self, "_layer_edits", {}).items():
                text = edit.text().strip()
                if not text:
                    continue
                try:
                    mapping[button] = action_from_text(text)
                except ProfileError as exc:
                    raise ProfileError(f"capa: {_button_label(button)}: {exc}") from exc
            return mapping

        def _stash_layer(self) -> None:
            if self.working is None or not self._layer_origin:
                return
            mapping = self._read_layer()
            # Una capa vacia sigue siendo valida si es la del G-Shift: el perfil
            # puede tener el disparador configurado y heredar todo de `buttons`.
            keep = mapping or (self.working.shift_button
                               and self._layer_origin == self.working.shift_layer)
            if keep:
                self.working.layers[self._layer_origin] = mapping
            else:
                self.working.layers.pop(self._layer_origin, None)

        def _auto_patterns(self) -> list[str]:
            return [self.auto_list.item(i).text()
                    for i in range(self.auto_list.count())]

        def _commit(self) -> Profile:
            """Valida y guarda el perfil en disco; devuelve la copia guardada."""
            if self.working is None:
                raise ProfileError("no hay perfil cargado")
            self._read_buttons_into(self.working)
            self._read_sensor_into(self.working)
            self._read_shift_into(self.working)
            self._stash_layer()
            self.working.auto_apps = self._auto_patterns()
            self.working.validate()
            saved = copy.deepcopy(self.working)
            saved.save(profiles_dir() / f"{self._current_name}.json")
            self.daemon.profiles[self._current_name] = saved
            self._original = copy.deepcopy(saved)
            self.subtitle.setText(
                f"{len(saved.dpi_stages)} stages · {saved.polling} Hz · "
                f"{len(saved.buttons)} acciones de boton · "
                f"{len(saved.layers)} capa(s)"
            )
            self._update_badge()
            return saved

        # -- acciones de perfil -------------------------------------------
        def _on_new(self) -> None:
            name, ok = QInputDialog.getText(self, "Nuevo perfil", "Nombre:")
            name = (name or "").strip()
            if not ok or not name:
                return
            if name in self.daemon.profiles:
                QMessageBox.warning(self, "Nuevo perfil", f"'{name}' ya existe.")
                return
            try:
                profile = Profile(name=name)
                profile.validate()
                profile.save(profiles_dir() / f"{name}.json")
            except Exception as exc:
                QMessageBox.warning(self, "Nuevo perfil", str(exc))
                return
            self.daemon.profiles[name] = profile
            self._reload_list(name)
            self._load_profile(name)
            self.status.setText(f"Perfil '{name}' creado (sin aplicar).")

        def _on_duplicate(self) -> None:
            if self.working is None:
                return
            origin = self._current_name
            name, ok = QInputDialog.getText(
                self, "Duplicar perfil", "Nombre del nuevo perfil:",
                text=f"{origin} copia",
            )
            name = (name or "").strip()
            if not ok or not name:
                return
            if name in self.daemon.profiles:
                QMessageBox.warning(self, "Duplicar perfil", f"'{name}' ya existe.")
                return
            try:
                self._read_buttons_into(self.working)
                self._read_sensor_into(self.working)
                self._read_shift_into(self.working)
                self._stash_layer()
                self.working.auto_apps = self._auto_patterns()
                profile = copy.deepcopy(self.working)
                profile.name = name
                profile.validate()
                profile.save(profiles_dir() / f"{name}.json")
            except Exception as exc:
                QMessageBox.warning(self, "Duplicar perfil", str(exc))
                return
            self.daemon.profiles[name] = profile
            self._reload_list(name)
            self._load_profile(name)
            self.status.setText(f"Perfil '{name}' creado a partir de '{origin}'.")

        def _on_delete(self) -> None:
            if self.working is None:
                return
            name = self._current_name
            if name == self.daemon.active:
                QMessageBox.information(
                    self, "Borrar perfil",
                    "No puedes borrar el perfil activo: aplica otro primero."
                )
                return
            answer = QMessageBox.question(
                self, "Borrar perfil", f"Borrar '{name}' del disco?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                (profiles_dir() / f"{name}.json").unlink(missing_ok=True)
            except OSError as exc:
                QMessageBox.warning(self, "Borrar perfil", str(exc))
                return
            self.daemon.profiles.pop(name, None)
            self._current_name = ""
            self.working = None
            self._original = None
            remaining = sorted(self.daemon.profiles)
            self._reload_list(remaining[0] if remaining else "")
            if remaining:
                self._load_profile(remaining[0])
            self.status.setText(f"Perfil '{name}' borrado.")

        def _on_reload(self) -> None:
            if self._is_dirty():
                answer = QMessageBox.question(
                    self, "Recargar", "Se perderan los cambios sin guardar. Continuar?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            self.daemon.reload()
            self._reload_list(self._current_name or self.daemon.active)
            if self._current_name in self.daemon.profiles:
                self._load_profile(self._current_name)
            else:
                names = sorted(self.daemon.profiles)
                if names:
                    self._load_profile(names[0])
            self.status.setText("Perfiles recargados desde disco.")

        def _on_save(self) -> None:
            if self._save_silent():
                self.status.setText("Guardado. Pulsa 'Aplicar al mouse' para activarlo.")

        def _save_silent(self) -> bool:
            try:
                self._commit()
            except ProfileError as exc:
                QMessageBox.warning(self, "Perfil invalido", str(exc))
                return False
            except Exception as exc:
                log.exception("no se pudo guardar el perfil")
                QMessageBox.warning(self, "Error al guardar", str(exc))
                return False
            return True

        def _on_discard(self) -> None:
            if self.working is None:
                return
            answer = QMessageBox.question(
                self, "Descartar", "Descartar los cambios sin guardar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._load_profile(self._current_name)
            self.status.setText("Cambios descartados.")

        def _on_apply(self) -> None:
            if self.working is None:
                return
            if not self._save_silent():
                return
            name = self._current_name
            self._set_busy(True)
            self.status.setText(f"Aplicando '{name}' al mouse...")

            def _worker() -> None:
                try:
                    self.daemon.switch(name)
                    self.bridge.applied.emit(name)
                except Exception as exc:
                    log.exception("fallo al aplicar el perfil")
                    self.bridge.failed.emit(str(exc))

            threading.Thread(target=_worker, name="x6-ui-apply", daemon=True).start()

        def _set_busy(self, busy: bool) -> None:
            self.apply_btn.setEnabled(not busy)
            self.save_btn.setEnabled(not busy)
            self.revert_btn.setEnabled(not busy)

        # -- DPI -----------------------------------------------------------
        def _on_stage_selected(self, row: int) -> None:
            if self._loading or self.working is None or row < 0:
                return
            if row >= len(self.working.dpi_stages):
                return
            colors = self._stage_colors()
            self.stage_dpi.blockSignals(True)
            self.stage_dpi.setValue(self.working.dpi_stages[row])
            self.stage_dpi.blockSignals(False)
            self._stage_color = colors[row]
            self._paint_color_button()

        def _paint_color_button(self) -> None:
            color = QColor(self._stage_color)
            text_color = "#000" if color.lightness() > 140 else "#fff"
            self.color_btn.setText(f"#{self._stage_color:06X}")
            self.color_btn.setStyleSheet(
                f"background: {color.name()}; color: {text_color};"
            )

        def _on_stage_dpi(self, value: int) -> None:
            if self._loading or self.working is None:
                return
            row = self.stage_list.currentRow()
            if not 0 <= row < len(self.working.dpi_stages):
                return
            dpi = int(value // DPI_STEP * DPI_STEP)
            self.working.dpi_stages[row] = dpi
            colors = self._stage_colors()
            self.stage_list.item(row).setText(self._stage_text(row, dpi, colors[row]))

        def _on_stage_color(self) -> None:
            if self.working is None:
                return
            color = QColorDialog.getColor(QColor(self._stage_color), self,
                                          "Color del stage")
            if not color.isValid():
                return
            self._stage_color = color.rgb() & 0xFFFFFF
            self._paint_color_button()
            row = self.stage_list.currentRow()
            if 0 <= row < len(self.working.dpi_stages):
                colors = self._stage_colors()
                colors[row] = self._stage_color
                self.working.dpi_colors = colors
                self.stage_list.item(row).setText(
                    self._stage_text(row, self.working.dpi_stages[row],
                                     self._stage_color)
                )

        def _on_stage_active(self) -> None:
            if self.working is None:
                return
            row = self.stage_list.currentRow()
            if not 0 <= row < len(self.working.dpi_stages):
                return
            self.working.active_stage = row
            colors = self._stage_colors()
            for i, dpi in enumerate(self.working.dpi_stages):
                self.stage_list.item(i).setText(self._stage_text(i, dpi, colors[i]))

        def _on_stage_add(self) -> None:
            if self.working is None or len(self.working.dpi_stages) >= MAX_STAGES:
                return
            colors = self._stage_colors()
            last = self.working.dpi_stages[-1] if self.working.dpi_stages else 800
            self.working.dpi_stages.append(last)
            self.working.dpi_colors = colors + [0xFFFFFF]
            self.stage_list.addItem(self._stage_text(
                len(self.working.dpi_stages) - 1, last, 0xFFFFFF))
            self.stage_list.setCurrentRow(len(self.working.dpi_stages) - 1)
            self._on_stage_selected(self.stage_list.currentRow())

        def _on_stage_remove(self) -> None:
            if self.working is None or len(self.working.dpi_stages) <= 1:
                return
            row = self.stage_list.currentRow()
            if not 0 <= row < len(self.working.dpi_stages):
                return
            colors = self._stage_colors()
            del self.working.dpi_stages[row]
            del colors[row]
            self.working.dpi_colors = colors
            if self.working.active_stage >= len(self.working.dpi_stages):
                self.working.active_stage = len(self.working.dpi_stages) - 1
            elif self.working.active_stage > row:
                self.working.active_stage -= 1
            self.stage_list.blockSignals(True)
            self.stage_list.clear()
            for i, dpi in enumerate(self.working.dpi_stages):
                self.stage_list.addItem(self._stage_text(i, dpi, colors[i]))
            self.stage_list.setCurrentRow(min(row, len(colors) - 1))
            self.stage_list.blockSignals(False)
            self._on_stage_selected(self.stage_list.currentRow())
            self._on_stage_active()

        # -- capas ---------------------------------------------------------
        def _on_shift_changed(self) -> None:
            if self._loading or self.working is None:
                return
            self.working.shift_button = self.shift_combo.currentData() or ""
            self._update_shift_note()

        def _update_shift_note(self) -> None:
            if self.working is None:
                self.shift_note.setText("")
                return
            button = self.shift_combo.currentData() or ""
            if not button:
                self.shift_note.setText(
                    "Sin disparador: la capa no se usa. Elige un boton lateral "
                    "(el DPI solo funciona en modo toggle)."
                )
                return
            mode = self.shift_mode_combo.currentData()
            extra = ""
            if button == "dpi" and mode != "toggle":
                extra = " El boton DPI no manda release: usa modo toggle."
            self.shift_note.setText(
                f"G-Shift en '{_button_label(button)}' ({mode})." + extra
            )

        def _on_layer_changed(self) -> None:
            if self._loading or self.working is None:
                return
            new = self.layer_combo.currentText()
            if not new or new == self._layer_origin:
                return
            try:
                self._stash_layer()
            except ProfileError as exc:
                QMessageBox.warning(self, "Capa invalida", str(exc))
                self.layer_combo.blockSignals(True)
                self.layer_combo.setCurrentText(self._layer_origin)
                self.layer_combo.blockSignals(False)
                return
            self._layer_origin = new
            self.working.shift_layer = new
            self._load_layer_table(new)
            self._update_shift_note()

        def _on_new_layer(self) -> None:
            if self.working is None:
                return
            name, ok = QInputDialog.getText(self, "Nueva capa",
                                            "Nombre de la capa:", text="shift2")
            name = (name or "").strip()
            if not ok or not name:
                return
            try:
                self._stash_layer()
            except ProfileError as exc:
                QMessageBox.warning(self, "Capa invalida", str(exc))
                return
            self.working.layers.setdefault(name, {})
            self._layer_origin = name
            self._reload_layer_combo(name)
            self.working.shift_layer = name
            self._load_layer_table(name)
            self._update_shift_note()

        # -- auto ----------------------------------------------------------
        def _on_auto_add(self) -> None:
            pattern = self.auto_edit.text().strip()
            if not pattern or pattern in self._auto_patterns():
                self.auto_edit.clear()
                return
            self.auto_list.addItem(pattern)
            self.auto_edit.clear()

        def _on_auto_remove(self) -> None:
            row = self.auto_list.currentRow()
            if row >= 0:
                self.auto_list.takeItem(row)

        def _remember_foreground(self) -> None:
            window = foreground_window()
            if (window and window.process
                    and window.process.lower() != self._own_process):
                self._last_window = window

        def _on_auto_current(self) -> None:
            window = self._last_window or foreground_window()
            if not window or not window.process:
                self.status.setText(
                    "No se pudo leer la ultima ventana activa (o no tiene proceso)."
                )
                return
            if window.process.lower() == self._own_process:
                self.status.setText(
                    "La ventana activa es el propio X6 Manager: enfoca la app y "
                    "vuelve a intentarlo."
                )
                return
            pattern = window.process
            if pattern not in self._auto_patterns():
                self.auto_list.addItem(pattern)
            self.auto_list.setCurrentRow(self.auto_list.count() - 1)
            self.status.setText(f"Anadido '{pattern}' a este perfil.")

        def _on_auto_global(self, checked: bool) -> None:
            try:
                self.daemon.set_auto_apps(checked)
            except Exception as exc:
                log.exception("no se pudo cambiar el auto-cambio")
                self.status.setText(f"Error: {exc}")
                return
            self.status.setText(
                "Cambio automatico por app activado."
                if checked else "Cambio automatico por app desactivado."
            )

        # -- eventos y estado ---------------------------------------------
        def _forward_event(self, event: str, **payload) -> None:
            self.bridge.event.emit(event, payload)

        def _on_daemon_event(self, event: str, payload: dict) -> None:
            if event == "battery":
                percent = payload.get("percent")
                self.battery_label.setText(
                    f"Bateria: {percent}%" if percent is not None else "Bateria: ?"
                )
                self._update_badge()
            elif event == "auto_apps":
                self.auto_global.blockSignals(True)
                self.auto_global.setChecked(bool(payload.get("enabled")))
                self.auto_global.blockSignals(False)
            elif event in ("switch", "reload"):
                self._reload_list(self._current_name or payload.get("name"))
                self._update_badge()
            elif event == "stage":
                self._update_badge()

        def _update_badge(self) -> None:
            active = self.daemon.active
            battery = self.battery_label.text().replace("Bateria: ", "")
            if active:
                self.active_label.setText(
                    f"<b>Activo:</b> {active}<br><span style='color:#888'>"
                    f"Bateria {battery}</span>"
                )
            else:
                self.active_label.setText("Sin perfil activo")
            auto_on = bool(getattr(self.daemon, "auto_apps_enabled", True))
            self.mode_label.setText(
                f"Auto por app: {'on' if auto_on else 'off'}"
            )

        def _on_applied(self, name: str) -> None:
            self._set_busy(False)
            self.status.setText(f"'{name}' aplicado al mouse y activado.")
            self._update_badge()
            self._reload_list(name)

        def _on_failed(self, message: str) -> None:
            self._set_busy(False)
            self.status.setText(f"Error: {message}")
            QMessageBox.warning(self, "Error", message)

        def closeEvent(self, event) -> None:  # noqa: N802 (API Qt)
            if self._is_dirty():
                answer = QMessageBox.question(
                    self, "Salir", "Hay cambios sin guardar. Guardarlos?",
                    QMessageBox.StandardButton.Save
                    | QMessageBox.StandardButton.Discard
                    | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Save,
                )
                if answer == QMessageBox.StandardButton.Cancel:
                    event.ignore()
                    return
                if answer == QMessageBox.StandardButton.Save and not self._save_silent():
                    event.ignore()
                    return
            self._fg_timer.stop()
            self.daemon.unsubscribe(self._forward_event)
            super().closeEvent(event)

else:  # pragma: no cover

    class MainWindow:  # type: ignore[no-redef]
        def __init__(self, daemon):
            raise RuntimeError("instala PySide6 para la UI: pip install PySide6")


def run_ui(daemon) -> None:
    import sys

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(daemon)
    window.show()
    app.exec()
