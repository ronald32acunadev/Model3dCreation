"""The main window: library on the left, viewer in the center, generation panel on the right."""

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox, QSplitter, QStackedWidget, QVBoxLayout, QWidget

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
        QApplication.instance().applicationStateChanged.connect(self._on_app_state)
        self.diagnostics_panel.restart_requested.connect(vm.restart_engine)
        self.generation_panel.save_requested.connect(self._save)
        self.generation_panel.textured_toggled.connect(viewer.set_textured)
        self.generation_panel.reset_camera_requested.connect(viewer.reset_camera)
        viewer.load_failed.connect(self._on_viewer_error)
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
        self.generation_panel.save_button.setEnabled(model is not None)

    def _on_diagnostics(self) -> None:
        self.diagnostics_panel.set_checks(self.vm.diagnostics)
        self.center.setCurrentWidget(self.diagnostics_panel if self.vm.show_diagnostics else self.viewer)

    def _on_viewer_error(self, message: str) -> None:
        if message == "webgl_unavailable":
            message = self.tr("The 3D viewer could not start WebGL on this system.")
        self.statusBar().showMessage(message, 10000)

    def _save(self) -> None:
        selected = self.vm.selected
        if selected is None:
            return
        start = str(Path(self.vm.settings.export_dir) / f"{selected.name}.glb")
        path, _ = QFileDialog.getSaveFileName(self, self.tr("Save GLB"), start, self.tr("glTF binary (*.glb)"))
        if not path:
            return
        try:
            self.vm.export(Path(path))
        except OSError as error:
            QMessageBox.warning(self, self.tr("Save GLB"), self.tr("Could not save the file: %1").replace("%1", str(error)))
