"""The library list: one row per generation, newest first."""

from collections.abc import Callable

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QInputDialog, QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox, QVBoxLayout, QWidget

from model3d_client.services.library import Entry, Library, Outcome

THUMBNAIL = 64


def format_duration(seconds: float) -> str:
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes}:{rest:02d}"


class LibraryPanel(QWidget):
    entry_selected = Signal(object)  # entry id, or None
    rename_requested = Signal(str, str)  # entry id, new name
    delete_requested = Signal(str)
    add_skeleton_requested = Signal(str)  # entry id
    add_walk_requested = Signal(str)  # entry id

    def __init__(self, library: Library, parent: QWidget | None = None):
        super().__init__(parent)
        self._library = library
        self._entries: list[Entry] = []
        self._icons: dict[str, QIcon] = {}  # thumbnails, made once per entry
        self.can_add_skeleton: Callable[[Entry], bool] = lambda _entry: False  # set by the window
        self.can_add_walk: Callable[[Entry], bool] = lambda _entry: False  # set by the window
        self.rig_reason: Callable[[], str | None] = lambda: None  # why the engine cannot rig, if it cannot
        self.title = QLabel()
        self.list = QListWidget()
        self.list.setIconSize(QSize(THUMBNAIL, THUMBNAIL))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.currentRowChanged.connect(self._on_row)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.list)
        self.retranslate()

    def set_entries(self, entries: list[Entry], selected_id: str | None) -> None:
        self._entries = entries
        self.list.blockSignals(True)
        self.list.clear()
        for entry in entries:
            item = QListWidgetItem(self._icon(entry), self._text(entry))
            item.setData(Qt.ItemDataRole.UserRole, entry.entry_id)
            self.list.addItem(item)
        rows = [i for i, entry in enumerate(entries) if entry.entry_id == selected_id]
        self.list.setCurrentRow(rows[0] if rows else -1)
        self.list.blockSignals(False)

    def retranslate(self) -> None:
        self.title.setText(self.tr("Library"))
        for row in range(self.list.count()):
            self.list.item(row).setText(self._text(self._entries[row]))

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

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
        """The entry's context menu: rename, delete, add skeleton and add walk (when they apply)."""
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
        return menu

    def _icon(self, entry: Entry) -> QIcon:
        if entry.entry_id not in self._icons:
            pixmap = QPixmap(str(self._library.folder(entry.entry_id) / entry.input_file))
            if not pixmap.isNull():
                pixmap = pixmap.scaled(THUMBNAIL, THUMBNAIL, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
            self._icons[entry.entry_id] = QIcon(pixmap)
        return self._icons[entry.entry_id]

    def _on_row(self, row: int) -> None:
        self.entry_selected.emit(self._entries[row].entry_id if 0 <= row < len(self._entries) else None)

    def _menu(self, pos: QPoint) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        entry_id = item.data(Qt.ItemDataRole.UserRole)
        entry = next(e for e in self._entries if e.entry_id == entry_id)
        menu = self.build_menu(entry)
        rename, delete, add_skeleton, add_walk = menu.actions()
        chosen = menu.exec(self.list.mapToGlobal(pos))
        if chosen is add_skeleton:
            self.add_skeleton_requested.emit(entry_id)
        elif chosen is add_walk:
            self.add_walk_requested.emit(entry_id)
        elif chosen is rename:
            name, ok = QInputDialog.getText(self, self.tr("Rename"), self.tr("Name:"), text=entry.name)
            if ok and name.strip():
                self.rename_requested.emit(entry_id, name)
        elif chosen is delete:
            answer = QMessageBox.question(
                self, self.tr("Delete"), self.tr("Delete \"%1\" and its files?").replace("%1", entry.name)
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._icons.pop(entry_id, None)
                self.delete_requested.emit(entry_id)
