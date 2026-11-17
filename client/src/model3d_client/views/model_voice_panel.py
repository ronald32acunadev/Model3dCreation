"""The Voice area of the selected model: link a voice to its character and make it speak."""

from collections.abc import Callable

from model3d_contracts.voices import Voice
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

NEW_VOICE = "__new_voice__"  # item data of the last entry of the "Link voice" picker


class ModelVoicePanel(QWidget):
    def __init__(self, vm: CharacterViewModel,
                 new_voice_dialog_factory: Callable[[QWidget], QDialog] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.vm = vm
        self.new_voice_dialog_factory = new_voice_dialog_factory
        self._dialogs: list[QDialog] = []  # open "New voice" dialogs, kept alive until they finish
        self.title_label = QLabel()
        self.title_label.setObjectName("voice_title")
        self.empty_label = QLabel()
        self.empty_label.setObjectName("voice_empty")
        self.empty_label.setWordWrap(True)
        self.voice_label = QLabel()
        self.voice_label.setObjectName("linked_voice")
        self.link_label = QLabel()
        self.voice_combo = QComboBox()
        self.voice_combo.setObjectName("link_voice")
        self.voice_combo.activated.connect(self._on_voice_picked)
        self.unlink_button = QPushButton()
        self.unlink_button.setObjectName("unlink_voice")
        self.unlink_button.clicked.connect(self._unlink)
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
        self.say_hint = QLabel()
        self.say_hint.setObjectName("say_hint")
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

        link_row = QHBoxLayout()
        link_row.addWidget(self.link_label)
        link_row.addWidget(self.voice_combo, 1)
        link_row.addWidget(self.unlink_button)
        say_row = QHBoxLayout()
        say_row.addWidget(self.language_combo)
        say_row.addStretch(1)
        say_row.addWidget(self.say_button)
        phrase_buttons = QHBoxLayout()
        phrase_buttons.addStretch(1)
        phrase_buttons.addWidget(self.play_button)
        phrase_buttons.addWidget(self.delete_button)
        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(self.voice_label)
        content_layout.addLayout(link_row)
        content_layout.addWidget(self.text)
        content_layout.addLayout(say_row)
        content_layout.addWidget(self.say_hint)
        content_layout.addWidget(self.progress)
        content_layout.addWidget(self.progress_message)
        content_layout.addWidget(self.error_label)
        content_layout.addWidget(self.phrases_label)
        content_layout.addWidget(self.phrases, 1)
        content_layout.addLayout(phrase_buttons)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.content, 1)

        self.setFocusProxy(self.text)
        vm.changed.connect(self._update)
        vm.phrases_changed.connect(self._fill_phrases)
        self.retranslate()
        self._fill_voices()
        self._fill_phrases()
        self._update()

    def retranslate(self) -> None:
        self.empty_label.setText(self.tr("Select a model to give it a voice"))
        self.link_label.setText(self.tr("Link voice"))
        self.unlink_button.setText(self.tr("Unlink"))
        self.say_button.setText(self.tr("Say"))
        self.text.setPlaceholderText(self.tr("What should the character say?"))
        self.phrases_label.setText(self.tr("Phrases"))
        self.play_button.setText(self.tr("Play"))
        self.delete_button.setText(self.tr("Delete"))
        self._fill_voices()
        self._update()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def _voice_name(self) -> str | None:
        for voice in self.vm.voices:
            if voice.voice_id == self.vm.voice_id:
                return voice.name
        return self.vm.voice_id

    def _fill_voices(self) -> None:
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        self.voice_combo.addItem(self.tr("Choose a voice…"), None)
        for voice in self.vm.voices:
            self.voice_combo.addItem(voice.name, voice.voice_id)
        self.voice_combo.addItem(self.tr("New voice…"), NEW_VOICE)
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
        listed = [self.voice_combo.itemData(i) for i in range(1, self.voice_combo.count() - 1)]
        if listed != [voice.voice_id for voice in self.vm.voices] or (
                self.vm.voice_id is not None and self.voice_combo.currentData() != self.vm.voice_id):
            self._fill_voices()
        has_character = self.vm.character_id is not None
        self.empty_label.setVisible(not has_character)
        self.content.setVisible(has_character)
        self.title_label.setText(self.tr("Voice") + (f" · {self.vm.name}" if has_character else ""))
        name = self._voice_name()
        self.voice_label.setText(self.tr("Voice: %1").replace("%1", name) if name else self.tr("No voice"))
        linked = self.vm.voice_id is not None
        self.unlink_button.setEnabled(linked and not self.vm.busy)
        self.voice_combo.setEnabled(not self.vm.busy)
        can_say = bool(self.text.toPlainText().strip()) and not self.vm.busy and linked
        self.say_button.setEnabled(can_say)
        hint = "" if linked else self.tr("Link a voice first")
        self.say_hint.setText(hint)
        self.say_button.setToolTip(hint)
        self.progress.setVisible(self.vm.busy)
        self.progress.setValue(round(self.vm.progress * 100))
        self.progress_message.setText(self.vm.message if self.vm.busy else "")
        self.error_label.setText(self.vm.error.message if self.vm.error is not None else "")
        selected = self.phrases.currentRow() >= 0
        self.play_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)

    def _on_voice_picked(self, _index: int) -> None:
        data = self.voice_combo.currentData()
        if data == NEW_VOICE:
            self._open_new_voice()
        elif data is None:
            self._fill_voices()  # the placeholder is not a choice
        else:
            self.vm.set_voice(data)

    def _open_new_voice(self) -> None:
        self._fill_voices()  # leave the picker on the current voice while the dialog is open
        if self.new_voice_dialog_factory is None:
            return
        dialog = self.new_voice_dialog_factory(self)
        self._dialogs.append(dialog)
        dialog.voice_saved.connect(self._on_new_voice)
        dialog.finished.connect(self._drop_finished_dialogs)
        dialog.open()

    def _on_new_voice(self, voice: Voice) -> None:
        self.vm.link_new_voice(voice)

    def _drop_finished_dialogs(self, _result: int) -> None:
        self._dialogs = [dialog for dialog in self._dialogs if dialog.isVisible()]

    def _unlink(self) -> None:
        self.vm.set_voice(None)

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
