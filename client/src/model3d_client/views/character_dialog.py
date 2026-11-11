"""The character dialog: pick its voice, make it say something, and keep its phrases."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from model3d_client.viewmodels.characters import LANGUAGES, CharacterViewModel


class CharacterDialog(QDialog):
    def __init__(self, vm: CharacterViewModel, parent: QWidget | None = None):
        super().__init__(parent)
        self.vm = vm
        self.setMinimumWidth(480)
        self.heading = QLabel()
        self.voice_label = QLabel()
        self.voice_combo = QComboBox()
        self.voice_combo.setObjectName("voice_combo")
        self.voice_combo.activated.connect(self._on_voice_picked)
        self.language_combo = QComboBox()
        self.language_combo.setObjectName("language_combo")
        for language in LANGUAGES:
            self.language_combo.addItem(language, language)
        self.text = QPlainTextEdit()
        self.text.setObjectName("say_text")
        self.text.setMaximumHeight(80)
        self.text.textChanged.connect(self._update)
        self.say_button = QPushButton()
        self.say_button.setObjectName("say")
        self.say_button.clicked.connect(self._say)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress_message = QLabel()
        self.error_label = QLabel()
        self.error_label.setObjectName("error")
        self.error_label.setWordWrap(True)
        self.phrases_label = QLabel()
        self.phrases = QListWidget()
        self.phrases.setObjectName("phrases")
        self.phrases.currentRowChanged.connect(self._update)
        self.play_button = QPushButton()
        self.play_button.setObjectName("play_phrase")
        self.play_button.clicked.connect(self._play)
        self.delete_button = QPushButton()
        self.delete_button.setObjectName("delete_phrase")
        self.delete_button.clicked.connect(self._delete)

        voice_row = QHBoxLayout()
        voice_row.addWidget(self.voice_label)
        voice_row.addWidget(self.voice_combo, 1)
        say_row = QHBoxLayout()
        say_row.addWidget(self.language_combo)
        say_row.addStretch(1)
        say_row.addWidget(self.say_button)
        phrase_buttons = QHBoxLayout()
        phrase_buttons.addStretch(1)
        phrase_buttons.addWidget(self.play_button)
        phrase_buttons.addWidget(self.delete_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addLayout(voice_row)
        layout.addWidget(self.text)
        layout.addLayout(say_row)
        layout.addWidget(self.progress)
        layout.addWidget(self.progress_message)
        layout.addWidget(self.error_label)
        layout.addWidget(self.phrases_label)
        layout.addWidget(self.phrases)
        layout.addLayout(phrase_buttons)

        vm.changed.connect(self._update)
        vm.phrases_changed.connect(self._fill_phrases)
        self.retranslate()
        self._fill_voices()
        self._fill_phrases()
        self._update()

    def retranslate(self) -> None:
        self.setWindowTitle(self.tr("Voice and phrases"))
        self.voice_label.setText(self.tr("Voice:"))
        self.say_button.setText(self.tr("Say"))
        self.text.setPlaceholderText(self.tr("What should the character say?"))
        self.phrases_label.setText(self.tr("Phrases"))
        self.play_button.setText(self.tr("Play"))
        self.delete_button.setText(self.tr("Delete"))
        self._update()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def _fill_voices(self) -> None:
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        self.voice_combo.addItem(self.tr("No voice"), None)
        for voice in self.vm.voices:
            self.voice_combo.addItem(voice.name, voice.voice_id)
        self.voice_combo.setCurrentIndex(max(0, self.voice_combo.findData(self.vm.voice_id)))
        self.voice_combo.blockSignals(False)

    def _fill_phrases(self) -> None:
        self.phrases.blockSignals(True)
        self.phrases.clear()
        for phrase in self.vm.phrases:
            when = phrase.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
            item = QListWidgetItem(f"{phrase.text}\n{phrase.language} · {when}")
            item.setData(Qt.ItemDataRole.UserRole, phrase.phrase_id)
            self.phrases.addItem(item)
        self.phrases.blockSignals(False)
        self._update()

    def _update(self, *_args) -> None:
        voices = [self.voice_combo.itemData(i) for i in range(1, self.voice_combo.count())]
        if voices != [voice.voice_id for voice in self.vm.voices] or self.voice_combo.currentData() != self.vm.voice_id:
            self._fill_voices()
        self.heading.setText(self.vm.name)
        can_say = bool(self.text.toPlainText().strip()) and not self.vm.busy and self.vm.voice_id is not None
        self.say_button.setEnabled(can_say)
        self.voice_combo.setEnabled(not self.vm.busy)
        self.progress.setVisible(self.vm.busy)
        self.progress.setValue(round(self.vm.progress * 100))
        self.progress_message.setText(self.vm.message if self.vm.busy else "")
        self.error_label.setText(self.vm.error.message if self.vm.error is not None else "")
        selected = self.phrases.currentRow() >= 0
        self.play_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)

    def _on_voice_picked(self, _index: int) -> None:
        self.vm.set_voice(self.voice_combo.currentData())

    def _say(self) -> None:
        self.vm.say(self.text.toPlainText().strip(), self.language_combo.currentData())

    def _current_phrase_id(self) -> str | None:
        item = self.phrases.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _play(self) -> None:
        phrase_id = self._current_phrase_id()
        if phrase_id is not None:
            self.vm.play_phrase(phrase_id)

    def _delete(self) -> None:
        phrase_id = self._current_phrase_id()
        if phrase_id is None:
            return
        answer = QMessageBox.question(self, self.tr("Delete"), self.tr("Delete this phrase?"))
        if answer == QMessageBox.StandardButton.Yes:
            self.vm.delete_phrase(phrase_id)
