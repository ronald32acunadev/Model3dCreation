"""The main window's state: library selection, diagnostics, and closing."""

import shutil
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from model3d_client.paths import AppPaths
from model3d_client.services.diagnostics import Check, engine_check, run_checks
from model3d_client.services.engine_api import EngineApi
from model3d_client.services.library import Entry, Library, Outcome
from model3d_client.services.settings import Settings
from model3d_client.services.supervisor import EngineState, EngineSupervisor
from model3d_client.viewmodels.generation import GenerationViewModel, Phase

DIAGNOSTIC_STATES = {EngineState.error, EngineState.crashed}


class CloseDecision(str, Enum):
    close = "close"
    confirm = "confirm"  # a generation runs: ask the user first


class _ChecksDone(QObject):
    done = Signal(object)


class _ChecksTask(QRunnable):
    """Runs the blocking checks on the thread pool; the result comes back as a queued signal."""

    def __init__(self, checks: Callable[[AppPaths], list[Check]], paths: AppPaths, signals: _ChecksDone):
        super().__init__()
        self._checks, self._paths, self._signals = checks, paths, signals

    def run(self) -> None:
        self._signals.done.emit(self._checks(self._paths))


class MainViewModel(QObject):
    selection_changed = Signal()
    entries_changed = Signal()
    diagnostics_changed = Signal()

    def __init__(
        self,
        paths: AppPaths,
        api: EngineApi,
        supervisor: EngineSupervisor,
        library: Library,
        generation: GenerationViewModel,
        settings: Settings,
        checks: Callable[[AppPaths], list[Check]] = run_checks,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.paths = paths
        self.api = api
        self.supervisor = supervisor
        self.library = library
        self.generation = generation
        self.settings = settings
        self.selected: Entry | None = None
        self.diagnostics: list[Check] = []
        self.show_diagnostics = False
        self._checks = checks
        self._checks_done = _ChecksDone(self)
        self._checks_done.done.connect(self._on_checks)
        supervisor.state_changed.connect(self._on_state)
        generation.library_changed.connect(self.entries_changed)
        generation.finished.connect(self._on_finished)

    def entries(self) -> list[Entry]:
        return self.library.entries()

    def select(self, entry_id: str | None) -> None:
        self.selected = next((e for e in self.entries() if e.entry_id == entry_id), None)
        self.selection_changed.emit()

    def selected_model(self) -> Path | None:
        """The selected entry's GLB, when it has one."""
        if self.selected is None or self.selected.outcome != Outcome.succeeded:
            return None
        path = self.library.model_path(self.selected.entry_id)
        return path if path.is_file() else None

    def rename(self, entry_id: str, name: str) -> None:
        entry = self.library.rename(entry_id, name)
        if self.selected is not None and self.selected.entry_id == entry_id:
            self.selected = entry
            self.selection_changed.emit()
        self.entries_changed.emit()

    def delete(self, entry_id: str) -> None:
        self.library.delete(entry_id)
        if self.selected is not None and self.selected.entry_id == entry_id:
            self.select(None)
        self.entries_changed.emit()

    def export(self, target: Path) -> None:
        model = self.selected_model()
        if model is None:
            raise ValueError("No model is selected")
        shutil.copyfile(model, target)

    def refresh_diagnostics(self) -> None:
        QThreadPool.globalInstance().start(_ChecksTask(self._checks, self.paths, self._checks_done))

    def restart_engine(self) -> None:
        self.supervisor.restart()

    def close_request(self) -> CloseDecision:
        return CloseDecision.confirm if self.generation.phase != Phase.idle else CloseDecision.close

    def shutdown(self, on_done: Callable[[], None]) -> None:
        """Record a running generation as interrupted, then stop the engine if the app owns it."""
        if self.generation.entry is not None:
            self.library.update(self.generation.entry.model_copy(update={"outcome": Outcome.interrupted}))
            self.generation.entry = None
        self.supervisor.stop(on_done)

    def _on_state(self, state: EngineState) -> None:
        show = state in DIAGNOSTIC_STATES
        if show:
            self.show_diagnostics = True
            self.refresh_diagnostics()
        elif self.show_diagnostics:
            self.show_diagnostics = False
            self.diagnostics_changed.emit()

    def _on_checks(self, checks: list[Check]) -> None:
        supervisor = self.supervisor
        self.diagnostics = [*checks, engine_check(supervisor.state, supervisor.health, supervisor.failure)]
        self.diagnostics_changed.emit()

    def _on_finished(self, entry: Entry) -> None:
        if entry.outcome == Outcome.succeeded:
            self.select(entry.entry_id)
