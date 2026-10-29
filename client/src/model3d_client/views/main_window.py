"""The main window: library on the left, viewer in the center, generation panel on the right."""

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from model3d_client.services.converter import FORMATS
from model3d_client.viewmodels.main import CloseDecision, MainViewModel
from model3d_client.views.diagnostics_panel import DiagnosticsPanel
from model3d_client.views.generation_panel import GenerationPanel
from model3d_client.views.library_panel import LibraryPanel
from model3d_client.views.top_bar import TopBar


class MainWindow(QMainWindow):
    def __init__(self, vm: MainViewModel, viewer: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.vm = vm
        self.viewer = viewer
        self._closing = False  # shutdown finished: the next close is accepted
        self._shutting_down = False

        self.top_bar = TopBar()
        self.library_panel = LibraryPanel(vm.library)
        self.diagnostics_panel = DiagnosticsPanel()
        self.generation_panel = GenerationPanel(vm.generation)
        self.center = QStackedWidget()
        self.center.addWidget(viewer)
        self.center.addWidget(self.diagnostics_panel)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.library_panel)
        splitter.addWidget(self.center)
        splitter.addWidget(self.generation_panel)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 700, 300])
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self.top_bar)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(root)
        self.resize(1300, 800)

        vm.supervisor.state_changed.connect(self.top_bar.set_state)
        vm.supervisor.health_changed.connect(self.top_bar.set_health)
        self.top_bar.set_state(vm.supervisor.state)
        self.top_bar.set_health(vm.supervisor.health)
        self.top_bar.settings_requested.connect(self.open_settings)
        vm.entries_changed.connect(self._refresh_library)
        vm.selection_changed.connect(self._on_selection)
        vm.diagnostics_changed.connect(self._on_diagnostics)
        self.library_panel.entry_selected.connect(vm.select)
        self.library_panel.rename_requested.connect(vm.rename)
        self.library_panel.delete_requested.connect(vm.delete)
        self.library_panel.can_add_skeleton = vm.generation.can_add_skeleton
        self.library_panel.rig_reason = lambda: vm.generation.rig_reason
        self.library_panel.add_skeleton_requested.connect(vm.generation.add_skeleton)
        self.library_panel.can_add_walk = vm.can_add_walk
        self.library_panel.add_walk_requested.connect(vm.detect_body)
        vm.body_detected.connect(self._on_body_detected)
        vm.walk_added.connect(self._on_walk_added)
        vm.walk_failed.connect(self._on_walk_failed)
        vm.animation_state_changed.connect(self._on_animation_state)
        QApplication.instance().applicationStateChanged.connect(self._on_app_state)
        self.diagnostics_panel.restart_requested.connect(vm.restart_engine)
        self.generation_panel.save_requested.connect(self._save)
        self.generation_panel.textured_toggled.connect(viewer.set_textured)
        self.generation_panel.reset_camera_requested.connect(viewer.reset_camera)
        viewer.load_failed.connect(self._on_viewer_error)
        vm.export_state_changed.connect(self._on_export_state)
        vm.export_finished.connect(self._on_export_finished)
        vm.export_failed.connect(self._on_export_failed)
        self._refresh_library()
        self.retranslate()

    def open_settings(self) -> None:
        from model3d_client.views.settings_dialog import SettingsDialog

        SettingsDialog(self.vm, self).exec()

    def ask_close_anyway(self) -> bool:
        answer = QMessageBox.question(self, self.tr("Close Model3D"),
                                      self.tr("A model is being generated. Close anyway?"))
        return answer == QMessageBox.StandardButton.Yes

    def retranslate(self) -> None:
        self.setWindowTitle("Model3D")

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing:
            event.accept()
            return
        event.ignore()
        if self._shutting_down:
            return
        if self.vm.close_request() == CloseDecision.confirm and not self.ask_close_anyway():
            return
        self._shutting_down = True
        self.setEnabled(False)
        self.statusBar().showMessage(self.tr("Stopping the engine…"))
        self.vm.shutdown(self._finish_close)

    def _finish_close(self) -> None:
        self._closing = True
        # Deferred: a close() from inside closeEvent (an engine that stops at once) is ignored by Qt.
        QTimer.singleShot(0, self.close)

    def _on_app_state(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            self.vm.refresh_entries()

    def _refresh_library(self) -> None:
        selected = self.vm.selected.entry_id if self.vm.selected is not None else None
        self.library_panel.set_entries(self.vm.entries(), selected)

    def _on_selection(self) -> None:
        self._refresh_library()
        model = self.vm.selected_model()
        if model is None:
            self.viewer.clear()
        else:
            self.viewer.show_model(model)
        self._update_save_button()

    def _on_diagnostics(self) -> None:
        self.diagnostics_panel.set_checks(self.vm.diagnostics)
        self.center.setCurrentWidget(self.diagnostics_panel if self.vm.show_diagnostics else self.viewer)

    def _on_viewer_error(self, message: str) -> None:
        if message == "webgl_unavailable":
            message = self.tr("The 3D viewer could not start WebGL on this system.")
        self.statusBar().showMessage(message, 10000)

    def ask_export_path(self, start: str) -> tuple[str, str]:
        filters = ";;".join([
            self.tr("glTF binary (*.glb)"),
            self.tr("FBX (*.fbx)"),
            self.tr("Wavefront OBJ (*.obj)"),
            self.tr("STL (*.stl)"),
            self.tr("USDZ (*.usdz)"),
        ])
        return QFileDialog.getSaveFileName(self, self.tr("Export model"), start, filters)

    def warn_format_drops(self, drops: set[str]) -> None:
        lines = []
        if "skeleton" in drops:
            lines.append(self.tr("This format does not keep the skeleton."))
        if "texture" in drops:
            lines.append(self.tr("This format does not keep the texture."))
        QMessageBox.information(self, self.tr("Export model"), "\n".join(lines))

    def _save(self) -> None:
        selected = self.vm.selected
        if selected is None:
            return
        start = str(Path(self.vm.settings.export_dir) / f"{selected.name}.glb")
        path, selected_filter = self.ask_export_path(start)
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() not in FORMATS:
            target = Path(path + _filter_extension(selected_filter))
        drops = self.vm.export_drops(target.suffix)
        if drops:
            self.warn_format_drops(drops)
        try:
            self.vm.export(target)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, self.tr("Export model"), self.tr("Could not export the model: %1").replace("%1", str(error)))

    def _on_export_state(self) -> None:
        if self.vm.exporting:
            self.statusBar().showMessage(self.tr("Exporting the model…"))
        self._update_save_button()

    def _update_save_button(self) -> None:
        enabled = self.vm.selected_model() is not None and not self.vm.exporting and not self.vm.animating
        self.generation_panel.save_button.setEnabled(enabled)

    def ask_body_type(self, detected: str) -> str | None:
        """The body type to animate as, or None when the user cancels."""
        biped, quadruped = self.tr("Biped (two legs)"), self.tr("Quadruped (four legs)")
        items = [biped, quadruped]
        if detected == "biped":
            label = self.tr("Detected body type: %1. Animate it as:").replace("%1", self.tr("Biped"))
            current = 0
        elif detected == "quadruped":
            label = self.tr("Detected body type: %1. Animate it as:").replace("%1", self.tr("Quadruped"))
            current = 1
        else:
            label = self.tr("Body type not detected. Animate it as:")
            current = 0
        choice, ok = QInputDialog.getItem(self, self.tr("Add walk animation"), label, items, current, False)
        if not ok:
            return None
        return "biped" if items.index(choice) == 0 else "quadruped"

    def _on_body_detected(self, entry_id: str, result: dict) -> None:
        body = self.ask_body_type(result.get("body", "unknown"))
        if body is not None:
            self.vm.add_walk(entry_id, body)

    def _on_animation_state(self) -> None:
        if self.vm.animating:
            self.statusBar().showMessage(self.tr("Adding the walk animation…"))
        else:
            self.statusBar().clearMessage()
        self._update_save_button()

    def _on_walk_added(self, entry) -> None:
        self.statusBar().showMessage(self.tr("Added %1").replace("%1", entry.name), 10000)

    def _on_walk_failed(self, reason: str) -> None:
        self.statusBar().clearMessage()
        QMessageBox.warning(self, self.tr("Add walk animation"),
                            self.tr("Could not add the walk animation: %1").replace("%1", reason))

    def _on_export_finished(self, path: Path) -> None:
        self.statusBar().showMessage(self.tr("Exported %1").replace("%1", str(path)), 10000)

    def _on_export_failed(self, reason: str) -> None:
        self.statusBar().clearMessage()
        QMessageBox.warning(self, self.tr("Export model"), self.tr("Could not export the model: %1").replace("%1", reason))


def _filter_extension(selected_filter: str) -> str:
    """The extension named in a filter such as "FBX (*.fbx)"; empty when there is none."""
    if "(*" not in selected_filter:
        return ""
    return selected_filter.split("(*", 1)[1].split(")", 1)[0]
