"""The "New voice" dialog: pick a method (describe or record), then only that method's fields."""

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from model3d_client.services.recorder import Recorder
from model3d_client.viewmodels.voice import VoiceViewModel

LANGUAGE_CODES = ("es", "en")

PAGE_CHOICE, PAGE_DESCRIBE, PAGE_RECORD = 0, 1, 2


class NewVoiceDialog(QDialog):
    voice_saved = Signal(object)

    def __init__(self, vm: VoiceViewModel, parent: QWidget | None = None):
        super().__init__(parent)
        self.vm = vm
        self._awaiting_save = False
        self._detached = False
        self.setObjectName("newVoiceDialog")
        self.setMinimumWidth(420)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._choice_page())
        self.pages.addWidget(self._describe_page())
        self.pages.addWidget(self._record_page())
        self.back_button = QPushButton()
        self.back_button.setObjectName("newVoiceBackButton")
        self.cancel_button = QPushButton()
        self.cancel_button.setObjectName("newVoiceCancelButton")
        self.error_label = QLabel()
        self.error_label.setObjectName("newVoiceError")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #c0392b;")

        footer = QHBoxLayout()
        footer.addWidget(self.back_button)
        footer.addStretch(1)
        footer.addWidget(self.cancel_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.pages, 1)
        layout.addWidget(self.error_label)
        layout.addLayout(footer)

        self.describe_button.clicked.connect(self._show_describe)
        self.record_choice_button.clicked.connect(self._show_record)
        self.back_button.clicked.connect(self._on_back)
        self.cancel_button.clicked.connect(self.reject)
        self.preview_button.clicked.connect(self._on_preview)
        self.play_preview_button.clicked.connect(vm.play_preview)
        self.save_preview_button.clicked.connect(self._on_save_preview)
        self.record_button.clicked.connect(self._on_record)
        self.play_take_button.clicked.connect(vm.play_take)
        self.rerecord_button.clicked.connect(self._on_rerecord)
        self.save_recording_button.clicked.connect(self._on_save_recording)
        for widget in (self.description_edit, self.sample_edit):
            widget.textChanged.connect(self._render)
        for widget in (self.preview_name_edit, self.record_name_edit):
            widget.textChanged.connect(self._render)
        vm.changed.connect(self._render)
        vm.voice_saved.connect(self._on_voice_saved)
        self.retranslate()

    def _choice_page(self) -> QWidget:
        page = QWidget()
        self.choice_label = QLabel()
        self.choice_label.setWordWrap(True)
        self.describe_button = QPushButton()
        self.describe_button.setObjectName("describeItButton")
        self.record_choice_button = QPushButton()
        self.record_choice_button.setObjectName("recordItButton")
        for button in (self.describe_button, self.record_choice_button):
            button.setMinimumHeight(64)
        layout = QVBoxLayout(page)
        layout.addWidget(self.choice_label)
        layout.addWidget(self.describe_button)
        layout.addWidget(self.record_choice_button)
        layout.addStretch(1)
        return page

    def _language_combo(self, name: str) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName(name)
        for code in LANGUAGE_CODES:
            combo.addItem("", code)
        return combo

    def _describe_page(self) -> QWidget:
        page = QWidget()
        self.description_label = QLabel()
        self.description_edit = QPlainTextEdit()
        self.description_edit.setObjectName("descriptionEdit")
        self.description_edit.setMaximumHeight(90)
        self.describe_language_label = QLabel()
        self.describe_language = self._language_combo("describeLanguage")
        self.sample_label = QLabel()
        self.sample_edit = QPlainTextEdit()
        self.sample_edit.setObjectName("sampleEdit")
        self.sample_edit.setMaximumHeight(70)
        self.preview_button = QPushButton()
        self.preview_button.setObjectName("previewButton")
        self.play_preview_button = QPushButton()
        self.play_preview_button.setObjectName("playPreviewButton")
        self.preview_status = QLabel()
        self.preview_status.setObjectName("previewStatus")
        self.preview_progress_bar = QProgressBar()
        self.preview_progress_bar.setObjectName("previewProgress")
        self.preview_progress_bar.setRange(0, 100)
        self.preview_name_label = QLabel()
        self.preview_name_edit = QLineEdit()
        self.preview_name_edit.setObjectName("previewNameEdit")
        self.save_preview_button = QPushButton()
        self.save_preview_button.setObjectName("savePreviewButton")
        layout = QVBoxLayout(page)
        for widget in (self.description_label, self.description_edit, self.describe_language_label,
                       self.describe_language, self.sample_label, self.sample_edit):
            layout.addWidget(widget)
        row = QHBoxLayout()
        row.addWidget(self.preview_button)
        row.addWidget(self.play_preview_button)
        layout.addLayout(row)
        for widget in (self.preview_progress_bar, self.preview_status, self.preview_name_label,
                       self.preview_name_edit, self.save_preview_button):
            layout.addWidget(widget)
        layout.addStretch(1)
        return page

    def _record_page(self) -> QWidget:
        page = QWidget()
        self.record_button = QPushButton()
        self.record_button.setObjectName("recordButton")
        self.level_bar = QProgressBar()
        self.level_bar.setObjectName("levelBar")
        self.level_bar.setRange(0, 100)
        self.level_bar.setTextVisible(False)
        self.elapsed_label = QLabel()
        self.elapsed_label.setObjectName("elapsedLabel")
        self.play_take_button = QPushButton()
        self.play_take_button.setObjectName("playTakeButton")
        self.rerecord_button = QPushButton()
        self.rerecord_button.setObjectName("rerecordButton")
        self.record_name_label = QLabel()
        self.record_name_edit = QLineEdit()
        self.record_name_edit.setObjectName("recordNameEdit")
        self.record_language_label = QLabel()
        self.record_language = self._language_combo("recordLanguage")
        self.save_recording_button = QPushButton()
        self.save_recording_button.setObjectName("saveRecordingButton")
        self.transcript_label = QLabel()
        self.transcript_label.setObjectName("transcriptLabel")
        self.transcript_label.setWordWrap(True)
        layout = QVBoxLayout(page)
        layout.addWidget(self.record_button)
        layout.addWidget(self.level_bar)
        layout.addWidget(self.elapsed_label)
        row = QHBoxLayout()
        row.addWidget(self.play_take_button)
        row.addWidget(self.rerecord_button)
        layout.addLayout(row)
        for widget in (self.record_name_label, self.record_name_edit, self.record_language_label,
                       self.record_language, self.save_recording_button, self.transcript_label):
            layout.addWidget(widget)
        layout.addStretch(1)
        return page

    def retranslate(self) -> None:
        self.setWindowTitle(self.tr("New voice"))
        self.choice_label.setText(self.tr("How do you want to create the voice?"))
        self.describe_button.setText(self.tr("Describe it"))
        self.record_choice_button.setText(self.tr("Record it"))
        self.back_button.setText(self.tr("Back"))
        self.cancel_button.setText(self.tr("Cancel"))
        self.description_label.setText(self.tr("Describe the voice"))
        self.description_edit.setPlaceholderText(self.tr("A deep, calm voice of an old dwarf"))
        self.sample_label.setText(self.tr("Sample text"))
        self.sample_edit.setPlaceholderText(self.tr("What the preview says"))
        self.preview_button.setText(self.tr("Preview"))
        self.play_preview_button.setText(self.tr("Play preview"))
        self.preview_name_label.setText(self.tr("Voice name"))
        self.save_preview_button.setText(self.tr("Save voice"))
        self.record_name_label.setText(self.tr("Voice name"))
        self.save_recording_button.setText(self.tr("Save voice"))
        self.play_take_button.setText(self.tr("Play take"))
        self.rerecord_button.setText(self.tr("Re-record"))
        for label in (self.describe_language_label, self.record_language_label):
            label.setText(self.tr("Language"))
        for combo in (self.describe_language, self.record_language):
            combo.setItemText(0, self.tr("Spanish"))
            combo.setItemText(1, self.tr("English"))
        self._render()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def _show_describe(self) -> None:
        self.pages.setCurrentIndex(PAGE_DESCRIBE)
        self._render()

    def _show_record(self) -> None:
        self.pages.setCurrentIndex(PAGE_RECORD)
        self._render()

    def _on_back(self) -> None:
        self.vm.reset_draft()
        self.pages.setCurrentIndex(PAGE_CHOICE)
        self._render()

    def reject(self) -> None:
        self.vm.reset_draft()
        self._detach()
        super().reject()

    def done(self, result: int) -> None:
        self._detach()
        super().done(result)

    def _detach(self) -> None:
        if self._detached:
            return
        self._detached = True
        self.vm.voice_saved.disconnect(self._on_voice_saved)
        self.vm.changed.disconnect(self._render)

    def _render(self, *_args) -> None:
        vm = self.vm
        on = vm.available
        idle = on and not vm.busy and not vm.recording
        page = self.pages.currentIndex()
        self.back_button.setVisible(page != PAGE_CHOICE)
        self.back_button.setEnabled(not vm.busy)
        self.describe_button.setEnabled(on)
        self.record_choice_button.setEnabled(on)

        self.preview_button.setEnabled(
            idle and bool(self.description_edit.toPlainText().strip()) and bool(self.sample_edit.toPlainText().strip()))
        self.play_preview_button.setEnabled(on and vm.preview_ready)
        self.save_preview_button.setEnabled(idle and vm.preview_ready and bool(self.preview_name_edit.text().strip()))
        self.preview_progress_bar.setVisible(vm.previewing)
        self.preview_progress_bar.setValue(round(vm.preview_progress * 100))
        self.preview_status.setText(self._preview_text())

        self.record_button.setText(self.tr("Stop") if vm.recording else self.tr("Record"))
        self.record_button.setEnabled(on and not vm.busy)
        self.level_bar.setValue(round(vm.level * 100))
        self.elapsed_label.setText(
            self.tr("%1 s of %2 s").replace("%1", f"{vm.elapsed:.1f}").replace("%2", str(Recorder.max_seconds)))
        has_take = vm.take is not None
        self.play_take_button.setEnabled(on and has_take and not vm.recording)
        self.rerecord_button.setEnabled(idle and has_take)
        self.save_recording_button.setEnabled(idle and has_take and bool(self.record_name_edit.text().strip()))
        if vm.transcribing:
            self.transcript_label.setText(self.tr("Transcribing…"))
        elif vm.transcript:
            self.transcript_label.setText(self.tr("Transcript: %1").replace("%1", vm.transcript))
        else:
            self.transcript_label.setText("")
        self.error_label.setText(self._error_text())

    def _preview_text(self) -> str:
        vm = self.vm
        if vm.previewing:
            text = self.tr("Designing the voice…")
            return f"{text} — {vm.preview_message}" if vm.preview_message else text
        return self.tr("Preview ready. Name it and save the voice.") if vm.preview_ready else ""

    def _error_text(self) -> str:
        error = self.vm.error
        if error is None:
            return ""
        texts = {
            "invalid_description": self.tr("Describe the voice first."),
            "invalid_text": self.tr("The text is empty or too long."),
            "invalid_name": self.tr("Give the voice a name."),
            "invalid_audio": self.tr("The recording is too short, too long or unreadable (3 to 60 seconds)."),
            "engine_unreachable": self.tr("The engine does not answer."),
            "engine_not_ready": self.tr("The engine is not ready yet."),
            "engine_lost": self.tr("The engine stopped answering during the job."),
            "gpu_out_of_memory": self.tr("The engine ran out of GPU memory. Close other programs that use the GPU and try again."),
            "worker_crashed": self.tr("A GPU worker crashed; it restarts on its own. Try again."),
        }
        return texts.get(error.code, error.message)

    def _on_preview(self) -> None:
        self.vm.preview(self.description_edit.toPlainText().strip(), self.describe_language.currentData(),
                        self.sample_edit.toPlainText().strip())

    def _on_save_preview(self) -> None:
        self.vm.save_preview(self.preview_name_edit.text().strip())
        self._awaiting_save = self.vm.saving

    def _on_record(self) -> None:
        if self.vm.recording:
            self.vm.stop_recording()
        else:
            self.vm.start_recording()

    def _on_rerecord(self) -> None:
        self.vm.discard_take()
        self.vm.start_recording()

    def _on_save_recording(self) -> None:
        self.vm.save_recording(self.record_name_edit.text().strip(), self.record_language.currentData())
        self._awaiting_save = self.vm.transcribing

    def _on_voice_saved(self, voice) -> None:
        if not self._awaiting_save:
            return
        self._awaiting_save = False
        self.voice_saved.emit(voice)
        self.accept()
