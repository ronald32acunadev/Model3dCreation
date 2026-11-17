"""The library tree: one item per character, newest first, with its versions as children."""

from collections.abc import Callable

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from model3d_contracts.characters import Character, Characters
from PySide6.QtWidgets import (
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from model3d_client.services.library import Entry, Library, Outcome

THUMBNAIL = 64
KIND_ROLE = Qt.ItemDataRole.UserRole + 1
CHARACTER = "character"
VERSION = "version"


def format_duration(seconds: float) -> str:
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes}:{rest:02d}"


class LibraryPanel(QWidget):
    entry_selected = Signal(object)  # entry id, or None
    rename_requested = Signal(str, str)  # entry id, new name
    delete_requested = Signal(str)
    add_skeleton_requested = Signal(str)  # entry id
    add_walk_requested = Signal(str)  # entry id
    regenerate_requested = Signal(str)  # entry id
    character_requested = Signal(str)  # character id: open its voice and phrases dialog
    export_character_requested = Signal(str)  # character id
    import_character_requested = Signal()

    def __init__(self, library: Library, parent: QWidget | None = None):
        super().__init__(parent)
        self._library = library
        self._entries: dict[str, Entry] = {}
        self._characters: dict[str, Character] = {}
        self._icons: dict[str, QIcon] = {}  # thumbnails, made once per entry
        self.can_add_skeleton: Callable[[Entry], bool] = lambda _entry: False  # set by the window
        self.can_add_walk: Callable[[Entry], bool] = lambda _entry: False  # set by the window
        self.can_regenerate: Callable[[Entry], bool] = lambda _entry: False  # set by the window
        self.rig_reason: Callable[[], str | None] = lambda: None  # why the engine cannot rig, if it cannot
        self.title = QLabel()
        self.import_button = QPushButton()
        self.import_button.setObjectName("import_character")
        self.import_button.clicked.connect(self.import_character_requested)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QSize(THUMBNAIL, THUMBNAIL))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.currentItemChanged.connect(self._on_current)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.import_button)
        layout.addWidget(self.tree)
        self.retranslate()

    def set_entries(self, entries: list[Entry], selected_id: str | None) -> None:
        self._entries = {entry.entry_id: entry for entry in entries}
        grouped = Characters(self._library).characters()
        self._characters = {
            character.character_id: character.model_copy(
                update={"versions": [v for v in character.versions if v in self._entries]})
            for character in grouped
            if character.character_id in self._entries
        }
        self.tree.blockSignals(True)
        self.tree.clear()
        current = None
        for character in self._characters.values():
            top = QTreeWidgetItem([self._character_text(character)])
            top.setIcon(0, self._icon(self._entries[character.character_id]))
            top.setData(0, Qt.ItemDataRole.UserRole, character.character_id)
            top.setData(0, KIND_ROLE, CHARACTER)
            self.tree.addTopLevelItem(top)
            for version_id in character.versions:
                entry = self._entries[version_id]
                child = QTreeWidgetItem([self._text(entry)])
                child.setIcon(0, self._icon(entry))
                child.setData(0, Qt.ItemDataRole.UserRole, version_id)
                child.setData(0, KIND_ROLE, VERSION)
                top.addChild(child)
                if version_id == selected_id:
                    current = child
            top.setExpanded(True)
        self.tree.setCurrentItem(current)
        self.tree.blockSignals(False)

    def retranslate(self) -> None:
        self.title.setText(self.tr("Library"))
        self.import_button.setText(self.tr("Import character…"))
        for row in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(row)
            top.setText(0, self._character_text(self._characters[top.data(0, Qt.ItemDataRole.UserRole)]))
            for index in range(top.childCount()):
                child = top.child(index)
                child.setText(0, self._text(self._entries[child.data(0, Qt.ItemDataRole.UserRole)]))

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def _character_text(self, character: Character) -> str:
        parts = [
            character.created_at.astimezone().strftime("%Y-%m-%d %H:%M"),
            self.tr("versions: %1").replace("%1", str(len(character.versions))),
        ]
        if character.voice_id is not None:
            parts.append(self.tr("voice"))
        return f"{character.name}\n{' · '.join(parts)}"

    def _text(self, entry: Entry) -> str:
        outcomes = {
            Outcome.running: self.tr("generating"),
            Outcome.succeeded: self.tr("ready"),
            Outcome.failed: self.tr("failed"),
            Outcome.cancelled: self.tr("cancelled"),
            Outcome.interrupted: self.tr("interrupted"),
        }
        parts = [entry.created_at.astimezone().strftime("%Y-%m-%d %H:%M")]
        if entry.stats is not None:
            parts.append(format_duration(entry.stats.duration_s))
        parts.append(outcomes[entry.outcome])
        if entry.options.rig:
            parts.append(self.tr("skeleton"))
        return f"{entry.name}\n{' · '.join(parts)}"

    def build_menu(self, entry: Entry) -> QMenu:
        """The entry's context menu: rename, delete, add skeleton, add walk and regenerate (when they apply)."""
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        menu.addAction(self.tr("Rename…")).setObjectName("rename")
        menu.addAction(self.tr("Delete…")).setObjectName("delete")
        add_skeleton = menu.addAction(self.tr("Add skeleton"))
        add_skeleton.setObjectName("add_skeleton")
        add_skeleton.setEnabled(self.can_add_skeleton(entry))
        if not add_skeleton.isEnabled() and self.rig_reason():
            add_skeleton.setToolTip(self.rig_reason())
        add_walk = menu.addAction(self.tr("Add walk animation"))
        add_walk.setObjectName("add_walk")
        add_walk.setEnabled(self.can_add_walk(entry))
        regenerate = menu.addAction(self.tr("Regenerate with new seed"))
        regenerate.setObjectName("regenerate")
        regenerate.setEnabled(self.can_regenerate(entry))
        return menu

    def build_character_menu(self) -> QMenu:
        """A character's context menu: voice and phrases, export, rename and delete."""
        menu = QMenu(self)
        menu.addAction(self.tr("Voice and phrases…")).setObjectName("voice_phrases")
        menu.addAction(self.tr("Export character…")).setObjectName("export_character")
        menu.addAction(self.tr("Rename…")).setObjectName("rename")
        menu.addAction(self.tr("Delete…")).setObjectName("delete")
        return menu

    def _icon(self, entry: Entry) -> QIcon:
        if entry.entry_id not in self._icons:
            pixmap = QPixmap(str(self._library.folder(entry.entry_id) / entry.input_file))
            if not pixmap.isNull():
                pixmap = pixmap.scaled(THUMBNAIL, THUMBNAIL, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
            self._icons[entry.entry_id] = QIcon(pixmap)
        return self._icons[entry.entry_id]

    def _on_current(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None) -> None:
        if current is None:
            self.entry_selected.emit(None)
        elif current.data(0, KIND_ROLE) == VERSION:
            self.entry_selected.emit(current.data(0, Qt.ItemDataRole.UserRole))
        else:
            self.entry_selected.emit(self._newest_version(current.data(0, Qt.ItemDataRole.UserRole)))

    def _newest_version(self, character_id: str) -> str:
        """The newest finished version of a character; the newest of any outcome when none finished."""
        versions = self._characters[character_id].versions
        finished = [v for v in versions if self._entries[v].outcome == Outcome.succeeded]
        return (finished or versions)[-1]

    def _menu(self, pos: QPoint) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        item_id = item.data(0, Qt.ItemDataRole.UserRole)
        if item.data(0, KIND_ROLE) == CHARACTER:
            self._character_menu(item_id, self.tree.mapToGlobal(pos))
        else:
            self._version_menu(self._entries[item_id], self.tree.mapToGlobal(pos))

    def _character_menu(self, character_id: str, global_pos: QPoint) -> None:
        character = self._characters[character_id]
        menu = self.build_character_menu()
        voice, export, rename, delete = menu.actions()
        chosen = menu.exec(global_pos)
        if chosen is voice:
            self.character_requested.emit(character_id)
        elif chosen is export:
            self.export_character_requested.emit(character_id)
        elif chosen is rename:
            self._ask_rename(character_id, character.name)
        elif chosen is delete:
            self._ask_delete_character(character)

    def _ask_delete_character(self, character: Character) -> None:
        answer = QMessageBox.question(
            self, self.tr("Delete"),
            self.tr("Delete \"%1\" with all its versions and files?").replace("%1", character.name),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for version_id in reversed(character.versions):
            self._icons.pop(version_id, None)
            self.delete_requested.emit(version_id)

    def _ask_rename(self, entry_id: str, current: str) -> None:
        name, ok = QInputDialog.getText(self, self.tr("Rename"), self.tr("Name:"), text=current)
        if ok and name.strip():
            self.rename_requested.emit(entry_id, name)

    def _version_menu(self, entry: Entry, global_pos: QPoint) -> None:
        entry_id = entry.entry_id
        menu = self.build_menu(entry)
        rename, delete, add_skeleton, add_walk, regenerate = menu.actions()
        chosen = menu.exec(global_pos)
        if chosen is add_skeleton:
            self.add_skeleton_requested.emit(entry_id)
        elif chosen is add_walk:
            self.add_walk_requested.emit(entry_id)
        elif chosen is regenerate:
            self.regenerate_requested.emit(entry_id)
        elif chosen is rename:
            self._ask_rename(entry_id, entry.name)
        elif chosen is delete:
            answer = QMessageBox.question(
                self, self.tr("Delete"), self.tr("Delete \"%1\" and its files?").replace("%1", entry.name)
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._icons.pop(entry_id, None)
                self.delete_requested.emit(entry_id)
