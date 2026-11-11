"""The Voice section: saved voices on the left; Describe, Record and Generate on the right."""

from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from model3d_client.services.recorder import Recorder
from model3d_client.viewmodels.voice import VoiceViewModel

LANGUAGE_CODES = ("es", "en")


class VoicePanel(QWidget):
    def __init__(self, vm: VoiceViewModel, parent: QWidget | None = None):
        super().__init__(parent)
        self.vm = vm
        self._history_voice: str | None = None

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("color: #b9770e;")
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #c0392b;")

        self.voices_title = QLabel()
        self.voice_list = QListWidget()
        self.play_voice_button = QPushButton()
        self.rename_voice_button = QPushButton()
        self.delete_voice_button = QPushButton()
        self.tabs = QTabWidget()
        self.tabs.addTab(self._describe_tab(), "")
        self.tabs.addTab(self._record_tab(), "")
        self.tabs.addTab(self._generate_tab(), "")

        left = QVBoxLayout()
        left.addWidget(self.voices_title)
        left.addWidget(self.voice_list, 1)
        buttons = QHBoxLayout()
        for button in (self.play_voice_button, self.rename_voice_button, self.delete_voice_button):
            buttons.addWidget(button)
        left.addLayout(buttons)
        body = QHBoxLayout()
        body.addLayout(left, 1)
        body.addWidget(self.tabs, 2)
        layout = QVBoxLayout(self)
        layout.addWidget(self.banner)
        layout.addLayout(body, 1)
        layout.addWidget(self.error_label)

        self.voice_list.currentItemChanged.connect(self._render)
        self.play_voice_button.clicked.connect(self._on_play_voice)
        self.rename_voice_button.clicked.connect(self._on_rename_voice)
        self.delete_voice_button.clicked.connect(self._on_delete_voice)
        self.preview_button.clicked.connect(self._on_preview)
        self.play_preview_button.clicked.connect(vm.play_preview)
        self.save_preview_button.clicked.connect(self._on_save_preview)
        self.record_button.clicked.connect(self._on_record)
        self.play_take_button.clicked.connect(vm.play_take)
        self.rerecord_button.clicked.connect(self._on_rerecord)
        self.save_recording_button.clicked.connect(self._on_save_recording)
        self.generate_button.clicked.connect(self._on_generate)
        self.play_clip_button.clicked.connect(self._on_play_clip)
        self.save_clip_button.clicked.connect(self._on_save_clip)
        self.history_play_button.clicked.connect(self._on_play_history)
        self.history_delete_button.clicked.connect(self._on_delete_history)
        self.generate_voice_combo.currentIndexChanged.connect(self._render)
        for widget in (self.description_edit, self.sample_edit, self.generate_text_edit):
            widget.textChanged.connect(self._render)
        for widget in (self.preview_name_edit, self.record_name_edit):
            widget.textChanged.connect(self._render)
        vm.changed.connect(self._render)
        vm.voices_changed.connect(self._on_voices_changed)
        self.retranslate()
        self._on_voices_changed()

    def _language_combo(self) -> QComboBox:
        combo = QComboBox()
        for code in LANGUAGE_CODES:
            combo.addItem("", code)
        return combo

    def _describe_tab(self) -> QWidget:
        tab = QWidget()
        self.description_label = QLabel()
        self.description_edit = QPlainTextEdit()
        self.description_edit.setMaximumHeight(90)
        self.describe_language_label = QLabel()
        self.describe_language = self._language_combo()
        self.sample_label = QLabel()
        self.sample_edit = QPlainTextEdit()
        self.sample_edit.setMaximumHeight(70)
        self.preview_button = QPushButton()
        self.play_preview_button = QPushButton()
        self.preview_status = QLabel()
        self.preview_progress_bar = QProgressBar()
        self.preview_progress_bar.setRange(0, 100)
        self.preview_name_label = QLabel()
        self.preview_name_edit = QLineEdit()
        self.save_preview_button = QPushButton()
        layout = QVBoxLayout(tab)
        for widget in (self.description_label, self.description_edit, self.describe_language_label,
                       self.describe_language, self.sample_label, self.sample_edit):
            layout.addWidget(widget)
        row = QHBoxLayout()
        row.addWidget(self.preview_button)
        row.addWidget(self.play_preview_button)
        layout.addLayout(row)
        for widget in (self.preview_progress_bar, self.preview_status, self.preview_name_label, self.preview_name_edit,
                       self.save_preview_button):
            layout.addWidget(widget)
        layout.addStretch(1)
        return tab

    def _record_tab(self) -> QWidget:
        tab = QWidget()
        self.record_button = QPushButton()
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setTextVisible(False)
        self.elapsed_label = QLabel()
        self.play_take_button = QPushButton()
        self.rerecord_button = QPushButton()
        self.record_name_label = QLabel()
        self.record_name_edit = QLineEdit()
        self.record_language_label = QLabel()
        self.record_language = self._language_combo()
        self.save_recording_button = QPushButton()
        self.transcript_label = QLabel()
        self.transcript_label.setWordWrap(True)
        layout = QVBoxLayout(tab)
        layout.addWidget(self.record_button)
        layout.addWidget(self.level_bar)
        layout.addWidget(self.elapsed_label)
        row = QHBoxLayout()
        row.addWidget(self.play_take_button)
        row.addWidget(self.rerecord_button)
        layout.addLayout(row)
        for widget in (self.record_name_label, self.record_name_edit, self.record_language_label, self.record_language,
                       self.save_recording_button, self.transcript_label):
            layout.addWidget(widget)
        layout.addStretch(1)
        return tab

    def _generate_tab(self) -> QWidget:
        tab = QWidget()
        self.generate_voice_label = QLabel()
        self.generate_voice_combo = QComboBox()
        self.generate_language_label = QLabel()
        self.generate_language = self._language_combo()
        self.generate_text_label = QLabel()
        self.generate_text_edit = QPlainTextEdit()
        self.generate_button = QPushButton()
        self.generate_progress_bar = QProgressBar()
        self.generate_progress_bar.setRange(0, 100)
        self.generate_status = QLabel()
        self.play_clip_button = QPushButton()
        self.save_clip_button = QPushButton()
        self.history_label = QLabel()
        self.history_list = QListWidget()
        self.history_play_button = QPushButton()
        self.history_delete_button = QPushButton()
        layout = QVBoxLayout(tab)
        for widget in (self.generate_voice_label, self.generate_voice_combo, self.generate_language_label,
                       self.generate_language, self.generate_text_label, self.generate_text_edit,
                       self.generate_button, self.generate_progress_bar, self.generate_status):
            layout.addWidget(widget)
        row = QHBoxLayout()
        row.addWidget(self.play_clip_button)
        row.addWidget(self.save_clip_button)
        layout.addLayout(row)
        layout.addWidget(self.history_label)
        layout.addWidget(self.history_list, 1)
        history_row = QHBoxLayout()
        history_row.addWidget(self.history_play_button)
        history_row.addWidget(self.history_delete_button)
        layout.addLayout(history_row)
        return tab

    def retranslate(self) -> None:
        self.voices_title.setText(self.tr("My voices"))
        self.play_voice_button.setText(self.tr("Play"))
        self.rename_voice_button.setText(self.tr("Rename…"))
        self.delete_voice_button.setText(self.tr("Delete…"))
        self.tabs.setTabText(0, self.tr("Describe"))
        self.tabs.setTabText(1, self.tr("Record"))
        self.tabs.setTabText(2, self.tr("Generate"))
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
        self.generate_voice_label.setText(self.tr("Voice"))
        self.generate_text_label.setText(self.tr("Text to speak"))
        self.generate_button.setText(self.tr("Generate"))
        self.play_clip_button.setText(self.tr("Play"))
        self.save_clip_button.setText(self.tr("Save as WAV…"))
        self.history_label.setText(self.tr("History"))
        self.history_play_button.setText(self.tr("Play clip"))
        self.history_delete_button.setText(self.tr("Delete clip"))
        for label in (self.describe_language_label, self.record_language_label, self.generate_language_label):
            label.setText(self.tr("Language"))
        for combo in (self.describe_language, self.record_language, self.generate_language):
            combo.setItemText(0, self.tr("Spanish"))
            combo.setItemText(1, self.tr("English"))
        self._render()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def selected_voice_id(self) -> str | None:
        item = self.voice_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _on_voices_changed(self) -> None:
        selected = self.selected_voice_id()
        generate_selected = self.generate_voice_combo.currentData()
        self.voice_list.blockSignals(True)
        self.generate_voice_combo.blockSignals(True)
        self.voice_list.clear()
        self.generate_voice_combo.clear()
        for voice in self.vm.voices:
            item = QListWidgetItem(f"{voice.name} ({voice.kind.value}, {voice.language})")
            item.setData(Qt.ItemDataRole.UserRole, voice.voice_id)
            self.voice_list.addItem(item)
            if voice.voice_id == selected:
                self.voice_list.setCurrentItem(item)
            self.generate_voice_combo.addItem(voice.name, voice.voice_id)
            if voice.voice_id == generate_selected:
                self.generate_voice_combo.setCurrentIndex(self.generate_voice_combo.count() - 1)
        self.voice_list.blockSignals(False)
        self.generate_voice_combo.blockSignals(False)
        self._render()

    def _render(self, *_args) -> None:
        vm = self.vm
        on = vm.available
        idle = on and not vm.busy and not vm.recording
        self.banner.setText(self._unavailable_text())
        self.banner.setVisible(not on)
        self.tabs.setEnabled(on)
        self.voice_list.setEnabled(on)
        has_voice = self.selected_voice_id() is not None
        for button in (self.play_voice_button, self.rename_voice_button, self.delete_voice_button):
            button.setEnabled(on and has_voice)

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
        self.elapsed_label.setText(self.tr("%1 s of %2 s").replace("%1", f"{vm.elapsed:.1f}").replace("%2", str(Recorder.max_seconds)))
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

        voice_id = self.generate_voice_combo.currentData()
        self.generate_button.setEnabled(
            idle and voice_id is not None and bool(self.generate_text_edit.toPlainText().strip()))
        self.generate_progress_bar.setVisible(vm.generating)
        self.generate_progress_bar.setValue(round(vm.progress * 100))
        status = self.tr("Generating the speech…") if vm.generating else ""
        self.generate_status.setText(f"{status} — {vm.message}" if status and vm.message else status)
        self.play_clip_button.setEnabled(on and vm.last_clip is not None)
        self.save_clip_button.setEnabled(on and vm.last_clip is not None)
        self._render_history(voice_id)
        self.history_play_button.setEnabled(on and self.history_list.currentItem() is not None)
        self.history_delete_button.setEnabled(on and self.history_list.currentItem() is not None)
        self.error_label.setText(self._error_text())

    def _render_history(self, voice_id: str | None) -> None:
        clips = self.vm.history(voice_id) if voice_id is not None else []
        current = self.history_list.currentItem()
        selected = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        existing = [self.history_list.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.history_list.count())]
        if existing == clips:
            return
        self.history_list.clear()
        for clip in clips:
            item = QListWidgetItem(clip.name)
            item.setData(Qt.ItemDataRole.UserRole, clip)
            self.history_list.addItem(item)
            if clip == selected:
                self.history_list.setCurrentItem(item)

    def _unavailable_text(self) -> str:
        vm = self.vm
        if vm.available:
            return ""
        if not vm.engine_ready:
            return self.tr("The engine is not ready yet.")
        return vm.unavailable_reason or self.tr("Voice generation is not available.")

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
            "voice_not_found": self.tr("That voice no longer exists."),
            "engine_unreachable": self.tr("The engine does not answer."),
            "engine_not_ready": self.tr("The engine is not ready yet."),
            "engine_lost": self.tr("The engine stopped answering during the job."),
            "gpu_out_of_memory": self.tr("The engine ran out of GPU memory. Close other programs that use the GPU and try again."),
            "worker_crashed": self.tr("A GPU worker crashed; it restarts on its own. Try again."),
        }
        return texts.get(error.code, error.message)

    def _on_play_voice(self) -> None:
        voice_id = self.selected_voice_id()
        if voice_id is not None:
            self.vm.play_sample(voice_id)

    def ask_name(self, title: str, label: str, current: str) -> str | None:
        name, accepted = QInputDialog.getText(self, title, label, QLineEdit.EchoMode.Normal, current)
        return name.strip() if accepted and name.strip() else None

    def confirm_delete(self, name: str) -> bool:
        answer = QMessageBox.question(self, self.tr("Delete voice"),
                                      self.tr("Delete the voice %1? This cannot be undone.").replace("%1", name))
        return answer == QMessageBox.StandardButton.Yes

    def ask_save_path(self, start: str) -> str:
        path, _filter = QFileDialog.getSaveFileName(self, self.tr("Save as WAV"), start, self.tr("WAV audio (*.wav)"))
        return path

    def _on_rename_voice(self) -> None:
        voice_id = self.selected_voice_id()
        voice = self.vm.voice(voice_id) if voice_id is not None else None
        if voice is None:
            return
        name = self.ask_name(self.tr("Rename voice"), self.tr("New name:"), voice.name)
        if name is not None:
            self.vm.rename(voice.voice_id, name)

    def _on_delete_voice(self) -> None:
        voice_id = self.selected_voice_id()
        voice = self.vm.voice(voice_id) if voice_id is not None else None
        if voice is not None and self.confirm_delete(voice.name):
            self.vm.delete(voice.voice_id)

    def _on_preview(self) -> None:
        self.vm.preview(self.description_edit.toPlainText().strip(), self.describe_language.currentData(),
                        self.sample_edit.toPlainText().strip())

    def _on_save_preview(self) -> None:
        self.vm.save_preview(self.preview_name_edit.text().strip())

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

    def _on_generate(self) -> None:
        self.vm.generate(self.generate_voice_combo.currentData(), self.generate_language.currentData(),
                         self.generate_text_edit.toPlainText().strip())

    def _on_play_clip(self) -> None:
        if self.vm.last_clip is not None:
            self.vm.play_clip(self.vm.last_clip)

    def _on_save_clip(self) -> None:
        clip = self.vm.last_clip
        if clip is None:
            return
        path = self.ask_save_path(str(Path.home() / "speech.wav"))
        if path:
            self.vm.save_clip(Path(path if path.lower().endswith(".wav") else path + ".wav"))

    def _selected_clip(self) -> Path | None:
        item = self.history_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _on_play_history(self) -> None:
        clip = self._selected_clip()
        if clip is not None:
            self.vm.play_clip(clip)

    def _on_delete_history(self) -> None:
        clip = self._selected_clip()
        if clip is not None:
            self.vm.delete_clip(clip)
