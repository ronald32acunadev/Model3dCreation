"""The first-run setup wizard's state: requirements, components, folder, and the install run."""

import os
from collections.abc import Callable, Mapping
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from model3d_client.setup.checks import (
    FolderCheck,
    Requirement,
    Toolchain,
    check_folder,
    check_requirements,
    run_command,
)
from model3d_client.setup.recipe import COMPONENT_SIZES, Sources, build_recipe
from model3d_client.setup.runner import StepFailed, StepRunner
from model3d_client.setup.state import load_state, write_pointer

SPACE_MARGIN = 1.1


class SetupPage(str, Enum):
    welcome = "welcome"
    requirements = "requirements"
    components = "components"
    folder = "folder"
    install = "install"
    done = "done"
    missing_folder = "missing_folder"  # the pointer names a folder that is gone (an unplugged disk)


class _Work(QRunnable):
    def __init__(self, work: Callable[[], None]):
        super().__init__()
        self._work = work

    def run(self) -> None:
        self._work()


def _pool(work: Callable[[], None]) -> None:
    QThreadPool.globalInstance().start(_Work(work))


def _ram_gib() -> float:
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30


def default_check(env: Mapping[str, str]) -> Callable[[], tuple[list[Requirement], Toolchain | None]]:
    """The real requirement check for this machine."""
    return lambda: check_requirements(run_command, env, os.cpu_count() or 1, _ram_gib())


class SetupViewModel(QObject):
    changed = Signal()
    log = Signal(str)
    finished = Signal()
    # Results from worker threads arrive through these, queued onto the UI thread.
    _checked = Signal(object)
    _progressed = Signal(str, str, float)
    _logged = Signal(str)
    _ended = Signal(object)  # None on success, else the StepFailed

    def __init__(
        self,
        sources: Sources,
        env: Mapping[str, str],
        check: Callable[[], tuple[list[Requirement], Toolchain | None]],
        folder_check: Callable[[Path, int], FolderCheck] = check_folder,
        runner_factory: Callable[..., StepRunner] = StepRunner,
        recipe: Callable[..., list] = build_recipe,
        background: Callable[[Callable[[], None]], None] = _pool,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._sources, self._env, self._check = sources, env, check
        self._folder_check, self._runner_factory, self._recipe = folder_check, runner_factory, recipe
        self._background = background
        self.page = SetupPage.welcome
        self.requirements: list[Requirement] = []
        self.toolchain: Toolchain | None = None
        self.components = ["base", "skeletons"]
        self.folder: Path | None = None
        self.folder_check: FolderCheck | None = None
        self.error: StepFailed | None = None
        self.log_lines: list[str] = []
        self.current_step = ""
        self.progress = 0.0
        self.steps: list = []
        self._runner = None
        self._auto_install = False
        self._checked.connect(self._on_checked)
        self._progressed.connect(self._on_progressed)
        self._logged.connect(self._on_logged)
        self._ended.connect(self._on_ended)

    @property
    def needed_bytes(self) -> int:
        return int(sum(COMPONENT_SIZES[component] for component in self.components) * SPACE_MARGIN)

    @property
    def can_continue(self) -> bool:
        if self.page == SetupPage.requirements:
            return (bool(self.requirements) and self.toolchain is not None
                    and all(req.ok for req in self.requirements if req.required))
        if self.page == SetupPage.folder:
            return self.folder_check is not None and self.folder_check.ok
        return self.page in (SetupPage.welcome, SetupPage.components)

    def next(self) -> None:
        if self.page == SetupPage.welcome:
            self._go(SetupPage.requirements)
            self.recheck()
        elif self.page == SetupPage.requirements and self.can_continue:
            self._go(SetupPage.components)
        elif self.page == SetupPage.components:
            self._go(SetupPage.folder)
        elif self.page == SetupPage.folder and self.can_continue:
            self.install()

    def back(self) -> None:
        previous = {SetupPage.requirements: SetupPage.welcome, SetupPage.components: SetupPage.requirements,
                    SetupPage.folder: SetupPage.components}
        if self.page in previous:
            self._go(previous[self.page])

    def recheck(self) -> None:
        check, signal = self._check, self._checked
        self._background(lambda: signal.emit(check()))

    def set_skeletons(self, on: bool) -> None:
        self.components = ["base", "skeletons"] if on else ["base"]
        if self.folder is not None:
            self.choose_folder(self.folder)
        self.changed.emit()

    def choose_folder(self, path: Path) -> None:
        self.folder = path
        self.folder_check = self._folder_check(path, self.needed_bytes)
        self.changed.emit()

    def install(self) -> None:
        if self.folder is None or self.toolchain is None:
            return
        write_pointer(self._env, self.folder)  # first, so a crash resumes from the same folder
        self.error, self.log_lines = None, []
        self.page = SetupPage.install
        self.steps = self._recipe(self._sources, self.folder, self.toolchain, list(self.components))
        runner = self._runner_factory(self.folder, self.steps, self._sources.app_version, list(self.components),
                                      on_progress=self._progressed.emit, on_log=self._logged.emit)
        self._runner = runner
        self.changed.emit()
        ended = self._ended

        def work() -> None:
            try:
                runner.run()
            except StepFailed as error:
                ended.emit(error)
                return
            ended.emit(None)

        self._background(work)

    def cancel(self) -> None:
        if self._runner is not None:
            self._runner.cancel()

    def retry(self) -> None:
        self.install()

    def retry_folder(self) -> None:
        """After the user plugs the disk back in: resume there, or stay on the missing-folder page."""
        if self.folder is None or not self.folder.is_dir():
            self.changed.emit()
            return
        self.folder_check = self._folder_check(self.folder, 0)
        self._auto_install = True
        self._go(SetupPage.requirements)
        self.recheck()

    def start_over(self) -> None:
        self.folder, self.folder_check = None, None
        self._go(SetupPage.welcome)

    @classmethod
    def for_existing(cls, install_dir: Path, mode: str, **kwargs) -> "SetupViewModel":
        """A wizard for an existing install: resume, repair, add_skeletons, or missing_folder."""
        vm = cls(**kwargs)
        vm.folder = install_dir
        if mode == "missing_folder":
            vm.page = SetupPage.missing_folder
            return vm
        state = load_state(install_dir)
        vm.components = list(state.components) if state is not None else ["base"]
        if mode == "add_skeletons" and "skeletons" not in vm.components:
            vm.components.append("skeletons")
        vm.folder_check = vm._folder_check(install_dir, 0)
        vm.page = SetupPage.requirements
        vm._auto_install = True  # install as soon as the requirements pass
        vm.recheck()
        return vm

    def _go(self, page: SetupPage) -> None:
        self.page = page
        self.changed.emit()

    def _on_checked(self, result) -> None:
        self.requirements, self.toolchain = result
        if self._auto_install:
            self._auto_install = False
            if self.can_continue:
                self.install()
                return
        self.changed.emit()

    def _on_progressed(self, _step_id: str, title: str, value: float) -> None:
        self.current_step, self.progress = title, value
        self.changed.emit()

    def _on_logged(self, line: str) -> None:
        self.log_lines.append(line)
        self.log.emit(line)

    def _on_ended(self, error: StepFailed | None) -> None:
        if error is None:
            self._go(SetupPage.done)
            self.finished.emit()
        else:
            self.error = error
            self.changed.emit()
