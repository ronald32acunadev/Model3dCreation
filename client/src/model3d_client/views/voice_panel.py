"""The Voice section: my voices and "New voice" on the left, "Try a voice" on the right."""

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from model3d_client.viewmodels.voice import VoiceViewModel
from model3d_client.views.new_voice_dialog import NewVoiceDialog

LANGUAGE_CODES = ("es", "en")


class VoicePanel(QWidget):
    def __init__(self, vm: VoiceViewModel, parent: QWidget | None = None):
        super().__init__(parent)
        self.vm = vm
        self.new_voice_dialog: NewVoiceDialog | None = None
        self.row_play_buttons: dict[str, QToolButton] = {}

        self.banner = QLabel()
        self.banner.setObjectName("voiceBanner")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("color: #b9770e;")
        self.error_label = QLabel()
        self.error_label.setObjectName("voiceError")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #c0392b;")

        self.voices_title = QLabel()
        self.voice_list = QListWidget()
        self.voice_list.setObjectName("voiceList")
        self.voice_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.empty_hint = QLabel()
        self.empty_hint.setObjectName("emptyHint")
        self.empty_hint.setWordWrap(True)
        self.new_voice_button = QPushButton()
        self.new_voice_button.setObjectName("newVoiceButton")

        left = QVBoxLayout()
        left.addWidget(self.voices_title)
        left.addWidget(self.empty_hint)
        left.addWidget(self.voice_list, 1)
        left.addWidget(self.new_voice_button)

        self.try_title = QLabel()
        self.generate_voice_label = QLabel()
        self.generate_voice_combo = QComboBox()
        self.generate_voice_combo.setObjectName("generateVoiceCombo")
        self.generate_language_label = QLabel()
        self.generate_language = QComboBox()
        self.generate_language.setObjectName("generateLanguage")
        for code in LANGUAGE_CODES:
            self.generate_language.addItem("", code)
        self.generate_text_label = QLabel()
        self.generate_text_edit = QPlainTextEdit()
        self.generate_text_edit.setObjectName("generateTextEdit")
        self.generate_button = QPushButton()
        self.generate_button.setObjectName("speakButton")
        self.generate_progress_bar = QProgressBar()
        self.generate_progress_bar.setObjectName("speakProgress")
        self.generate_progress_bar.setRange(0, 100)
        self.generate_status = QLabel()
        self.generate_status.setObjectName("speakStatus")
        self.play_clip_button = QPushButton()
        self.play_clip_button.setObjectName("playClipButton")
        self.save_clip_button = QPushButton()
        self.save_clip_button.setObjectName("saveClipButton")
        self.history_label = QLabel()
        self.history_list = QListWidget()
        self.history_list.setObjectName("historyList")
        self.history_play_button = QPushButton()
        self.history_play_button.setObjectName("historyPlayButton")
        self.history_delete_button = QPushButton()
        self.history_delete_button.setObjectName("historyDeleteButton")

        right = QVBoxLayout()
        for widget in (self.try_title, self.generate_voice_label, self.generate_voice_combo,
                       self.generate_language_label, self.generate_language, self.generate_text_label,
                       self.generate_text_edit, self.generate_button, self.generate_progress_bar,
                       self.generate_status):
            right.addWidget(widget)
        row = QHBoxLayout()
        row.addWidget(self.play_clip_button)
        row.addWidget(self.save_clip_button)
        right.addLayout(row)
        right.addWidget(self.history_label)
        right.addWidget(self.history_list, 1)
        history_row = QHBoxLayout()
        history_row.addWidget(self.history_play_button)
        history_row.addWidget(self.history_delete_button)
        right.addLayout(history_row)

        body = QHBoxLayout()
        body.addLayout(left, 1)
        body.addLayout(right, 2)
        layout = QVBoxLayout(self)
        layout.addWidget(self.banner)
        layout.addLayout(body, 1)
        layout.addWidget(self.error_label)

        self.voice_list.customContextMenuRequested.connect(self._on_context_menu)
        self.new_voice_button.clicked.connect(self._on_new_voice)
        self.generate_button.clicked.connect(self._on_generate)
        self.play_clip_button.clicked.connect(self._on_play_clip)
        self.save_clip_button.clicked.connect(self._on_save_clip)
        self.history_play_button.clicked.connect(self._on_play_history)
        self.history_delete_button.clicked.connect(self._on_delete_history)
        self.generate_voice_combo.currentIndexChanged.connect(self._render)
        self.generate_text_edit.textChanged.connect(self._render)
        vm.changed.connect(self._render)
        vm.voices_changed.connect(self._on_voices_changed)
        self.retranslate()
        self._on_voices_changed()

    def retranslate(self) -> None:
        self.voices_title.setText(self.tr("My voices"))
        self.empty_hint.setText(self.tr('No voices yet. Click "New voice" to create one.'))
        self.new_voice_button.setText(self.tr("New voice"))
        self.try_title.setText(self.tr("Try a voice"))
        self.generate_voice_label.setText(self.tr("Voice"))
        self.generate_language_label.setText(self.tr("Language"))
        self.generate_language.setItemText(0, self.tr("Spanish"))
        self.generate_language.setItemText(1, self.tr("English"))
        self.generate_text_label.setText(self.tr("Text to speak"))
        self.generate_button.setText(self.tr("Speak"))
        self.play_clip_button.setText(self.tr("Play"))
        self.save_clip_button.setText(self.tr("Save as WAV…"))
        self.history_label.setText(self.tr("History"))
        self.history_play_button.setText(self.tr("Play clip"))
        self.history_delete_button.setText(self.tr("Delete clip"))
        for button in self.row_play_buttons.values():
            button.setText(self.tr("Play"))
        self._render()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def _on_voices_changed(self) -> None:
        generate_selected = self.generate_voice_combo.currentData()
        self.generate_voice_combo.blockSignals(True)
        self.voice_list.clear()
        self.generate_voice_combo.clear()
        self.row_play_buttons = {}
        for voice in self.vm.voices:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, voice.voice_id)
            self.voice_list.addItem(item)
            self.voice_list.setItemWidget(item, self._voice_row(voice.voice_id, voice.name, f"{voice.kind.value}, {voice.language}"))
            item.setSizeHint(self.voice_list.itemWidget(item).sizeHint())
            self.generate_voice_combo.addItem(voice.name, voice.voice_id)
            if voice.voice_id == generate_selected:
                self.generate_voice_combo.setCurrentIndex(self.generate_voice_combo.count() - 1)
        self.generate_voice_combo.blockSignals(False)
        self._render()

    def _voice_row(self, voice_id: str, name: str, detail: str) -> QWidget:
        row = QWidget()
        text = QLabel(f"{name}\n{detail}")
        text.setObjectName("voiceRowText")
        play = QToolButton()
        play.setObjectName("playVoiceButton")
        play.setText(self.tr("Play"))
        play.clicked.connect(lambda _checked=False, vid=voice_id: self.vm.play_sample(vid))
        self.row_play_buttons[voice_id] = play
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(text, 1)
        layout.addWidget(play)
        return row

    def _render(self, *_args) -> None:
        vm = self.vm
        on = vm.available
        idle = on and not vm.busy and not vm.recording
        self.banner.setText(self._unavailable_text())
        self.banner.setVisible(not on)
        self.voice_list.setEnabled(on)
        self.empty_hint.setVisible(not self.vm.voices)
        self.new_voice_button.setEnabled(idle)
        for button in self.row_play_buttons.values():
            button.setEnabled(on)
        self.generate_voice_combo.setEnabled(on)
        self.generate_language.setEnabled(on)
        self.generate_text_edit.setEnabled(on)

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

    def _on_context_menu(self, position: QPoint) -> None:
        item = self.voice_list.itemAt(position)
        if item is None or not self.vm.available:
            return
        voice_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction(self.tr("Rename…"), lambda: self.rename_voice(voice_id))
        menu.addAction(self.tr("Delete…"), lambda: self.delete_voice(voice_id))
        menu.exec(self.voice_list.viewport().mapToGlobal(position))

    def rename_voice(self, voice_id: str) -> None:
        voice = self.vm.voice(voice_id)
        if voice is None:
            return
        name = self.ask_name(self.tr("Rename voice"), self.tr("New name:"), voice.name)
        if name is not None:
            self.vm.rename(voice.voice_id, name)

    def delete_voice(self, voice_id: str) -> None:
        voice = self.vm.voice(voice_id)
        if voice is not None and self.confirm_delete(voice.name):
            self.vm.delete(voice.voice_id)

    def _on_new_voice(self) -> None:
        dialog = NewVoiceDialog(self.vm, self)
        self.new_voice_dialog = dialog
        dialog.finished.connect(self._on_new_voice_closed)
        dialog.open()

    def _on_new_voice_closed(self, _result: int) -> None:
        if self.new_voice_dialog is not None:
            self.new_voice_dialog.deleteLater()
            self.new_voice_dialog = None

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
