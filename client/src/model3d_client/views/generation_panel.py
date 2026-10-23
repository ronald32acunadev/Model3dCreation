"""The generation panel: input image, options, Generate/Cancel, progress, Save GLB, viewer controls."""

from pathlib import Path

from model3d_contracts.jobs import JobError, Quality
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from model3d_client.viewmodels.generation import GenerationViewModel, Phase

PREVIEW = 220


class DropArea(QLabel):
    """Shows the chosen image and accepts an image file dropped on it."""

    dropped = Signal(object)  # Path

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(PREVIEW, PREVIEW * 3 // 4)
        self.setStyleSheet("border: 2px dashed #888; border-radius: 6px;")
        self.setWordWrap(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = [url for url in event.mimeData().urls() if url.isLocalFile()]
        if urls:
            self.dropped.emit(Path(urls[0].toLocalFile()))


class GenerationPanel(QWidget):
    save_requested = Signal()
    textured_toggled = Signal(bool)
    reset_camera_requested = Signal()

    def __init__(self, vm: GenerationViewModel, parent: QWidget | None = None):
        super().__init__(parent)
        self.vm = vm
        self.source_tabs = QTabWidget()
        image_page = QWidget()
        self.image_preview = DropArea()
        self.image_preview.dropped.connect(self.image_dropped)
        self.choose_button = QPushButton()
        self.choose_button.clicked.connect(self._choose)
        image_layout = QVBoxLayout(image_page)
        image_layout.addWidget(self.image_preview)
        image_layout.addWidget(self.choose_button)
        self.source_tabs.addTab(image_page, "")
        self.source_tabs.addTab(QWidget(), "")  # text input: a later phase
        self.source_tabs.setTabEnabled(1, False)

        self.texture_check = QCheckBox()
        self.texture_check.setChecked(vm.texture)
        self.texture_check.toggled.connect(vm.set_texture)
        self.rig_check = QCheckBox()
        self.rig_check.setChecked(vm.rig)
        self.rig_check.toggled.connect(vm.set_rig)
        self.rig_hint = QLabel()  # why skeletons are unavailable, shown under the option
        self.rig_hint.setWordWrap(True)
        self.rig_hint.setStyleSheet("color: palette(placeholder-text);")
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("", Quality.draft)
        self.quality_combo.addItem("", Quality.final)
        self.quality_combo.addItem("", Quality.high)
        self.quality_combo.setCurrentIndex(self.quality_combo.findData(vm.quality))
        self.quality_combo.currentIndexChanged.connect(lambda _i: vm.set_quality(self.quality_combo.currentData()))
        self.generate_button = QPushButton()
        self.generate_button.clicked.connect(self._on_generate)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.stage_label = QLabel()
        self.stage_label.setWordWrap(True)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #c0392b;")
        self.save_button = QPushButton()
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_requested)
        self.show_texture_check = QCheckBox()
        self.show_texture_check.setChecked(True)
        self.show_texture_check.toggled.connect(self.textured_toggled)
        self.reset_camera_button = QPushButton()
        self.reset_camera_button.clicked.connect(self.reset_camera_requested)

        layout = QVBoxLayout(self)
        for widget in (self.source_tabs, self.texture_check, self.rig_check, self.rig_hint, self.quality_combo, self.generate_button,
                       self.progress_bar, self.stage_label, self.error_label, self.save_button,
                       self.show_texture_check, self.reset_camera_button):
            layout.addWidget(widget)
        layout.addStretch(1)
        vm.changed.connect(self.render)
        self.retranslate()

    def image_dropped(self, path: Path) -> None:
        self.vm.set_image(path)
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.image_preview.setPixmap(QPixmap())
            self.image_preview.setText(path.name)
        else:
            self.image_preview.setPixmap(pixmap.scaled(PREVIEW, PREVIEW, Qt.AspectRatioMode.KeepAspectRatio,
                                                       Qt.TransformationMode.SmoothTransformation))

    def retranslate(self) -> None:
        self.source_tabs.setTabText(0, self.tr("Image"))
        self.source_tabs.setTabText(1, self.tr("Text (coming later)"))
        if self.vm.image is None:
            self.image_preview.setText(self.tr("Drop an image here"))
        self.choose_button.setText(self.tr("Choose image…"))
        self.texture_check.setText(self.tr("Texture"))
        self.rig_check.setText(self.tr("With skeleton"))
        self.quality_combo.setItemText(0, self.tr("Quality: draft"))
        self.quality_combo.setItemText(1, self.tr("Quality: final"))
        self.quality_combo.setItemText(2, self.tr("Quality: high detail"))
        self.quality_combo.setItemData(
            2, self.tr("Sharper texture (768 px views: logos, eyes, small details) and more geometry. About 1.5 minutes more per model."),
            Qt.ItemDataRole.ToolTipRole,
        )
        self.save_button.setText(self.tr("Save GLB…"))
        self.show_texture_check.setText(self.tr("Show texture"))
        self.reset_camera_button.setText(self.tr("Reset camera"))
        self.render()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def render(self) -> None:
        vm = self.vm
        available = vm.texture_available
        self.texture_check.setEnabled(available and vm.phase == Phase.idle)
        self.texture_check.setToolTip("" if available else self.tr("This engine runs without the paint stage"))
        rig_available = vm.rig_available
        self.rig_check.setEnabled(rig_available and vm.phase == Phase.idle)
        self.rig_check.setToolTip(
            self.tr("Adds a skeleton (UniRig) so the model is ready to animate. About 2 minutes more per model.")
            if rig_available
            else vm.rig_reason or self.tr("This engine cannot add skeletons")
        )
        # Only when the engine said why; before its first health answer there is nothing to explain.
        self.rig_hint.setText(vm.rig_reason or "")
        self.rig_hint.setVisible(not rig_available and bool(vm.rig_reason))
        self.quality_combo.setEnabled(vm.phase == Phase.idle)
        if vm.phase == Phase.idle:
            self.generate_button.setText(self.tr("Generate"))
            self.generate_button.setEnabled(vm.can_generate)
        elif vm.phase == Phase.cancelling:
            self.generate_button.setText(self.tr("Cancelling…"))
            self.generate_button.setEnabled(False)
        else:
            self.generate_button.setText(self.tr("Cancel"))
            self.generate_button.setEnabled(vm.can_cancel)
        self.progress_bar.setValue(int(vm.progress * 100))
        self.progress_bar.setVisible(vm.phase != Phase.idle)
        phases = {
            Phase.idle: "",
            Phase.submitting: self.tr("Sending the image…"),
            Phase.queued: self.tr("Waiting in the queue…"),
            Phase.shaping: self.tr("Building the shape"),
            Phase.painting: self.tr("Painting the texture"),
            Phase.rigging: self.tr("Adding skeleton…"),
            Phase.cancelling: self.tr("Cancelling: the engine finishes the current stage first…"),
        }
        stage = phases[vm.phase]
        self.stage_label.setText(f"{stage} — {vm.message}" if stage and vm.message else stage)
        self.error_label.setText(self._problem_text())

    def _problem_text(self) -> str:
        problems = {
            "unsupported_format": self.tr("Use a PNG or JPEG image."),
            "too_large": self.tr("The image is larger than 20 MB."),
            "missing": self.tr("The image file no longer exists."),
        }
        if self.vm.image_problem is not None:
            return problems[self.vm.image_problem]
        return self.error_text(self.vm.error) if self.vm.error is not None else ""

    def error_text(self, error: JobError) -> str:
        """A sentence for each engine and client error code; unknown codes show the engine's message."""
        texts = {
            "invalid_image": self.tr("The engine could not read this image."),
            "image_too_large": self.tr("The image is larger than 20 MB."),
            "engine_not_ready": self.tr("The engine is not ready yet."),
            "texture_unavailable": self.tr("This engine runs without the paint stage; turn Texture off."),
            "rig_unavailable": self.tr("This engine cannot add skeletons; see the setup guide."),
            "rig_failed": self.tr("Adding the skeleton failed. The job folder has rig.log with the details."),
            "invalid_model": self.tr("The engine could not read this model."),
            "model_too_large": self.tr("The model is too large to add a skeleton to."),
            "gpu_out_of_memory": self.tr("The engine ran out of GPU memory. Close other programs that use the GPU and try again."),
            "worker_crashed": self.tr("A GPU worker crashed; it restarts on its own. Try again."),
            "worker_unavailable": self.tr("A GPU worker could not be started. Restart the engine."),
            "stage_timeout": self.tr("A stage took too long and was stopped. Try again."),
            "engine_lost": self.tr("The engine stopped answering during the generation."),
            "engine_unreachable": self.tr("The engine does not answer."),
            "internal_error": self.tr("The engine hit an internal error. See the engine log."),
        }
        return texts.get(error.code, error.message)

    def _on_generate(self) -> None:
        if self.vm.phase == Phase.idle:
            self.vm.generate()
        else:
            self.vm.cancel()

    def _choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Choose image"), "", self.tr("Images (*.png *.jpg *.jpeg)"))
        if path:
            self.image_dropped(Path(path))
