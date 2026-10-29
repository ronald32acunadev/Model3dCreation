"""Settings: language, generation defaults, export folder, and the engine's status and log."""

from pathlib import Path

from model3d_contracts.jobs import Quality
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from model3d_client.i18n import apply_language
from model3d_client.services.settings import Settings, save_settings
from model3d_client.setup.state import load_state
from model3d_client.viewmodels.main import MainViewModel

LOG_LINES = 200
ADDON_ZIP = Path("/opt/model3d/blender/model3d.zip")


class SettingsDialog(QDialog):
    def __init__(self, vm: MainViewModel, parent: QWidget | None = None):
        super().__init__(parent)
        self.vm = vm
        settings = vm.settings
        self.language_combo = QComboBox()
        self.language_combo.addItem("English", "en")  # language names stay in their own language
        self.language_combo.addItem("Español", "es")
        self.language_combo.setCurrentIndex(self.language_combo.findData(settings.language))
        self.texture_check = QCheckBox()
        self.texture_check.setChecked(settings.default_texture)
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("", Quality.draft)
        self.quality_combo.addItem("", Quality.final)
        self.quality_combo.addItem("", Quality.high)
        self.quality_combo.setCurrentIndex(self.quality_combo.findData(settings.default_quality))
        self.export_edit = QLineEdit(str(settings.export_dir))
        self.browse_button = QPushButton()
        self.browse_button.clicked.connect(self._browse)

        self.engine_group = QGroupBox()
        self.mode_label = QLabel()
        self.gpus_label = QLabel()
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.restart_button = QPushButton()
        self.restart_button.clicked.connect(vm.restart_engine)
        engine_layout = QVBoxLayout(self.engine_group)
        for widget in (self.mode_label, self.gpus_label, self.log_view, self.restart_button):
            engine_layout.addWidget(widget)

        self.install_group = QGroupBox()
        self.install_label = QLabel()
        self.install_label.setWordWrap(True)
        self.skeletons_button = QPushButton()
        self.skeletons_button.clicked.connect(self._add_skeletons)
        self.repair_button = QPushButton()
        self.repair_button.clicked.connect(self._repair)
        self.addon_label = QLabel()
        self.addon_label.setWordWrap(True)
        self.addon_edit = QLineEdit(str(ADDON_ZIP))
        self.addon_edit.setReadOnly(True)
        self.copy_button = QPushButton()
        self.copy_button.clicked.connect(self._copy_addon_path)
        install_layout = QVBoxLayout(self.install_group)
        install_layout.addWidget(self.install_label)
        actions_row = QHBoxLayout()
        actions_row.addWidget(self.skeletons_button)
        actions_row.addWidget(self.repair_button)
        actions_row.addStretch(1)
        install_layout.addLayout(actions_row)
        install_layout.addWidget(self.addon_label)
        addon_row = QHBoxLayout()
        addon_row.addWidget(self.addon_edit, 1)
        addon_row.addWidget(self.copy_button)
        install_layout.addLayout(addon_row)
        self.install_group.setVisible(vm.install_dir is not None)  # installed from the .deb only
        if vm.install_dir is not None:
            state = load_state(vm.install_dir)
            self.skeletons_button.setEnabled(state is None or "skeletons" not in state.components)

        export_row = QHBoxLayout()
        export_row.addWidget(self.export_edit, 1)
        export_row.addWidget(self.browse_button)
        self.form_labels = [QLabel() for _ in range(4)]
        self.form = QFormLayout()
        for label, field in zip(self.form_labels, (self.language_combo, self.texture_check, self.quality_combo)):
            self.form.addRow(label, field)
        self.form.addRow(self.form_labels[3], export_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(self.form)
        layout.addWidget(self.engine_group, 1)
        layout.addWidget(self.install_group)
        layout.addWidget(buttons)
        self.resize(640, 560)
        self.retranslate()

    def values(self) -> Settings:
        """The settings as edited in the dialog."""
        return self.vm.settings.model_copy(
            update={
                "language": self.language_combo.currentData(),
                "default_texture": self.texture_check.isChecked(),
                "default_quality": self.quality_combo.currentData(),
                "export_dir": Path(self.export_edit.text()).expanduser(),
            }
        )

    def accept(self) -> None:
        settings = self.values()
        save_settings(self.vm.paths.settings_file, settings)
        self.vm.settings = settings
        apply_language(QApplication.instance(), settings.language)
        super().accept()

    def retranslate(self) -> None:
        self.setWindowTitle(self.tr("Settings"))
        texts = (self.tr("Language"), self.tr("Texture by default"), self.tr("Default quality"), self.tr("Export folder"))
        for label, text in zip(self.form_labels, texts):
            label.setText(text)
        self.quality_combo.setItemText(0, self.tr("Draft"))
        self.quality_combo.setItemText(1, self.tr("Final"))
        self.quality_combo.setItemText(2, self.tr("High detail"))
        self.browse_button.setText(self.tr("Browse…"))
        self.engine_group.setTitle(self.tr("Engine"))
        self.install_group.setTitle(self.tr("Installation"))
        if self.vm.install_dir is not None:
            self.install_label.setText(self.tr("Installed in %1").replace("%1", str(self.vm.install_dir)))
        self.skeletons_button.setText(self.tr("Add skeletons"))
        self.repair_button.setText(self.tr("Repair installation"))
        self.addon_label.setText(self.tr("Blender add-on (install it from Blender's Preferences, Add-ons, Install from Disk):"))
        self.copy_button.setText(self.tr("Copy"))
        self.restart_button.setText(self.tr("Restart engine"))
        self._render_engine()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def _render_engine(self) -> None:
        health = self.vm.supervisor.health
        if health is None:
            self.mode_label.setText(self.tr("Engine not running"))
            self.gpus_label.setText("")
        else:
            self.mode_label.setText(self.tr("Mode: %1").replace("%1", health.mode.name))
            lines = []
            for index, gpu in enumerate(health.gpus):
                if gpu.stage is None:
                    continue
                line = self.tr("GPU %1: %2").replace("%1", str(index)).replace("%2", gpu.stage)
                if gpu.memory_free_mib is not None:
                    line += " · " + self.tr("%1 MiB free").replace("%1", str(gpu.memory_free_mib))
                lines.append(line)
            self.gpus_label.setText("\n".join(lines))
        try:
            lines = self.vm.paths.engine_log.read_text(errors="replace").splitlines()[-LOG_LINES:]
            self.log_view.setPlainText("\n".join(lines))
        except OSError:
            self.log_view.setPlainText(self.tr("No engine log yet."))

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self.tr("Export folder"), self.export_edit.text())
        if folder:
            self.export_edit.setText(folder)

    # Bound methods, not lambdas: Qt drops the connections when the dialog is destroyed.
    def _add_skeletons(self) -> None:
        self.vm.setup_requested.emit("add_skeletons")

    def _repair(self) -> None:
        self.vm.setup_requested.emit("repair")

    def _copy_addon_path(self) -> None:
        QApplication.clipboard().setText(self.addon_edit.text())
