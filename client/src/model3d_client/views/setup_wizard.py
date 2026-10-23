"""The first-run setup wizard: checks the machine, then installs the engine into the folder the user picks."""

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
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

from model3d_client.setup.checks import Requirement, RequirementId
from model3d_client.viewmodels.setup import SetupPage, SetupViewModel

CUDA_URL = "https://developer.nvidia.com/cuda-downloads"
NAV_PAGES = (SetupPage.welcome, SetupPage.requirements, SetupPage.components, SetupPage.folder)


def _gb(size: int) -> str:
    return str(round(size / 10**9))


def _wrapped(selectable: bool = False) -> QLabel:
    label = QLabel()
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    if selectable:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _row(*widgets: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    for widget in widgets:
        row.addWidget(widget)
    row.addStretch(1)
    return row


class SetupWizard(QWidget):
    completed = Signal()

    def __init__(self, vm: SetupViewModel, parent: QWidget | None = None):
        super().__init__(parent)
        self.vm = vm
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 16pt; font-weight: bold;")

        self.welcome_label = _wrapped()
        self.requirements_label = _wrapped(selectable=True)
        self.recheck_button = QPushButton()
        self.recheck_button.clicked.connect(vm.recheck)
        self.skeletons_check = QCheckBox()
        self.skeletons_check.toggled.connect(vm.set_skeletons)
        self.space_label = QLabel()
        self.folder_edit = QLineEdit()
        self.folder_edit.setReadOnly(True)
        self.browse_button = QPushButton()
        self.browse_button.clicked.connect(self._browse)
        self.folder_status = _wrapped()
        self.step_label = QLabel()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.error_label = _wrapped(selectable=True)
        self.cancel_button = QPushButton()
        self.cancel_button.clicked.connect(vm.cancel)
        self.retry_button = QPushButton()
        self.retry_button.clicked.connect(vm.retry)
        self.done_label = QLabel()
        self.open_button = QPushButton()
        self.open_button.clicked.connect(self.completed)
        self.missing_label = _wrapped(selectable=True)
        self.choose_button = QPushButton()
        self.choose_button.clicked.connect(vm.start_over)
        self.retry_folder_button = QPushButton()
        self.retry_folder_button.clicked.connect(vm.retry_folder)

        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.browse_button)
        contents = {
            SetupPage.welcome: [self.welcome_label],
            SetupPage.requirements: [self.requirements_label, _row(self.recheck_button)],
            SetupPage.components: [self.skeletons_check, self.space_label],
            SetupPage.folder: [folder_row, self.folder_status],
            SetupPage.install: [self.step_label, self.progress_bar, self.log_view, self.error_label,
                                _row(self.cancel_button, self.retry_button)],
            SetupPage.done: [self.done_label, _row(self.open_button)],
            SetupPage.missing_folder: [self.missing_label, _row(self.choose_button, self.retry_folder_button)],
        }
        self.stack = QStackedWidget()
        self.pages: dict[SetupPage, QWidget] = {}
        for page in SetupPage:
            widget = QWidget()
            layout = QVBoxLayout(widget)
            for item in contents[page]:
                if isinstance(item, QHBoxLayout):
                    layout.addLayout(item)
                else:
                    layout.addWidget(item)
            if page != SetupPage.install:  # the log takes the free space there
                layout.addStretch(1)
            self.pages[page] = widget
            self.stack.addWidget(widget)

        self.back_button = QPushButton()
        self.back_button.clicked.connect(vm.back)
        self.next_button = QPushButton()
        self.next_button.clicked.connect(vm.next)
        nav = QHBoxLayout()
        nav.addWidget(self.back_button)
        nav.addStretch(1)
        nav.addWidget(self.next_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.stack, 1)
        layout.addLayout(nav)

        vm.changed.connect(self._render)
        vm.log.connect(self.log_view.appendPlainText)
        self.setWindowTitle("Model3D")
        self.resize(720, 560)
        self.retranslate()

    def retranslate(self) -> None:
        self.back_button.setText(self.tr("Back"))
        self.recheck_button.setText(self.tr("Check again"))
        self.skeletons_check.setText(self.tr("Skeletons for animation (UniRig)"))
        self.browse_button.setText(self.tr("Browse…"))
        self.cancel_button.setText(self.tr("Cancel"))
        self.retry_button.setText(self.tr("Retry"))
        self.open_button.setText(self.tr("Open Model3D"))
        self.choose_button.setText(self.tr("Choose another folder"))
        self.retry_folder_button.setText(self.tr("Try again"))
        self.welcome_label.setText(self.tr(
            "Model3D turns an image into a 3D model on your own NVIDIA GPU. Before the first use it installs its "
            "engine and models, about 40 GB, into a folder you choose. This takes a while; you can stop and "
            "continue later."))
        self.done_label.setText(self.tr("Model3D is ready."))
        self._render()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def requirement_text(self, req: Requirement) -> str:
        """One requirement as shown: mark, name, detail, and what to do when it fails."""
        names = {
            RequirementId.driver: self.tr("NVIDIA driver 580 or later"),
            RequirementId.gpu: self.tr("Supported NVIDIA GPU"),
            RequirementId.vram: self.tr("Video memory"),
            RequirementId.cuda_toolkit: self.tr("CUDA toolkit 13"),
            RequirementId.compiler: self.tr("C and C++ compilers"),
        }
        first = f"{'✓' if req.ok else '✗'} {names[req.id]}"
        if req.detail and req.id != RequirementId.vram:
            first += f" ({req.detail})"
        lines = [first]
        if req.ok and req.id == RequirementId.vram and req.detail == "shape_only":
            lines.append(self.tr("Enough for shapes; textures need a card with about 10.5 GB."))
        if not req.ok:
            help_lines = {
                RequirementId.driver: [self.tr("Install or update the NVIDIA driver, then restart the computer:"),
                                       "    sudo ubuntu-drivers install"],
                RequirementId.gpu: [self.tr("This GPU is too old for the engine; it needs compute capability 7.5 or newer.")],
                RequirementId.vram: [self.tr("The engine needs a GPU with at least 9.5 GB of memory.")],
                RequirementId.cuda_toolkit: [self.tr("Install the CUDA toolkit 13 from NVIDIA's repository:"),
                                             "    sudo apt install cuda-toolkit-13-1",
                                             self.tr("Instructions: %1").replace("%1", CUDA_URL)],
                RequirementId.compiler: [self.tr("Install the compilers:"), "    sudo apt install build-essential"],
            }
            lines += help_lines[req.id]
        return "\n".join(lines)

    def folder_text(self) -> str:
        check = self.vm.folder_check
        if check is None:
            return ""
        sizes = (_gb(check.free_bytes), _gb(check.needed_bytes))
        if check.ok:
            return self.tr("%1 GB free, %2 GB needed.").replace("%1", sizes[0]).replace("%2", sizes[1])
        problems = {
            "no_space": self.tr("Not enough free space: %1 GB free, %2 GB needed.")
            .replace("%1", sizes[0]).replace("%2", sizes[1]),
            "not_writable": self.tr("Model3D cannot write to this folder. Choose a folder you own."),
            "no_symlinks": self.tr("This drive's file system (for example exFAT or FAT32) cannot hold the install. "
                                   "Choose a drive formatted as ext4 or Btrfs."),
            "no_exec": self.tr("This drive is mounted without permission to run programs (noexec). Choose another drive."),
        }
        return problems.get(check.problem, "")

    def error_text(self) -> str:
        error = self.vm.error
        if error is None:
            return ""
        messages = {
            "no_space": self.tr("The disk is full. Free some space and press Retry."),
            "network": self.tr("A download failed. Check the internet connection and press Retry."),
            "cancelled": self.tr("Installation stopped. Press Retry to continue where it stopped."),
        }
        return messages.get(error.code) or self.tr("A step failed: %1. The log above has the details.").replace(
            "%1", error.message)

    def _render(self) -> None:
        vm = self.vm
        page = vm.page
        titles = {
            SetupPage.welcome: self.tr("Welcome to Model3D"),
            SetupPage.requirements: self.tr("Requirements"),
            SetupPage.components: self.tr("Components"),
            SetupPage.folder: self.tr("Install folder"),
            SetupPage.install: self.tr("Installing"),
            SetupPage.done: self.tr("Done"),
            SetupPage.missing_folder: self.tr("Install folder not found"),
        }
        self.stack.setCurrentWidget(self.pages[page])
        self.title.setText(titles[page])
        self.back_button.setVisible(page in NAV_PAGES[1:])
        self.next_button.setVisible(page in NAV_PAGES)
        self.next_button.setText(self.tr("Install") if page == SetupPage.folder else self.tr("Next"))
        self.next_button.setEnabled(vm.can_continue)

        self.requirements_label.setText(
            "\n\n".join(self.requirement_text(req) for req in vm.requirements) or self.tr("Checking…"))
        self.skeletons_check.blockSignals(True)
        self.skeletons_check.setChecked("skeletons" in vm.components)
        self.skeletons_check.blockSignals(False)
        self.space_label.setText(self.tr("Space needed: %1 GB").replace("%1", _gb(vm.needed_bytes)))
        self.folder_edit.setText(str(vm.folder) if vm.folder is not None else "")
        self.folder_status.setText(self.folder_text())
        self.step_label.setText(vm.current_step)
        self.progress_bar.setValue(int(vm.progress * 100))
        self.error_label.setText(self.error_text())
        self.retry_button.setVisible(vm.error is not None)
        self.cancel_button.setVisible(vm.error is None)
        self.missing_label.setText(self.tr(
            "The install folder %1 cannot be found. If it is on a disk that is not connected, connect it and "
            "try again.").replace("%1", str(vm.folder)))

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.tr("Install folder"))
        if path:
            self.vm.choose_folder(Path(path))
