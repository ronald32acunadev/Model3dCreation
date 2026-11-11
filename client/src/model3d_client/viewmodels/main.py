"""The main window's state: library selection, diagnostics, and closing."""

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from model3d_client.paths import AppPaths
from model3d_client.services.diagnostics import Check, engine_check, run_checks
from model3d_client.services.engine_api import ApiError, EngineApi
from model3d_client.services.formats import CONVERTED, KEEPS_SKELETON, KEEPS_TEXTURE
from model3d_client.services.library import Entry, Library, Outcome
from model3d_client.services.settings import Settings
from model3d_client.services.supervisor import EngineState, EngineSupervisor
from model3d_client.viewmodels.generation import GenerationViewModel, Phase

BODIES = ("biped", "quadruped")
POLL_MS = 1000

DIAGNOSTIC_STATES = {EngineState.error, EngineState.crashed}


class CloseDecision(str, Enum):
    close = "close"
    confirm = "confirm"  # a generation runs: ask the user first


@dataclass
class _Run:
    kind: str  # "detect" or "walk"
    entry_id: str  # the rigged source entry
    derived_id: str | None = None  # the new entry a walk writes into


@dataclass
class _Export:
    entry_id: str
    fmt: str
    target: Path


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
    setup_requested = Signal(str)  # a setup wizard mode: add_skeletons or repair
    export_finished = Signal(object)  # the written Path
    export_failed = Signal(str)  # the reason
    export_state_changed = Signal()
    body_detected = Signal(str, dict)  # entry id, detection result
    walk_added = Signal(object)  # the new Entry
    walk_failed = Signal(str)  # the reason
    animation_state_changed = Signal()

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
        self.install_dir: Path | None = None  # set when installed from the .deb
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
        self._run: _Run | None = None
        self._export: _Export | None = None
        self.poll_ms = POLL_MS
        supervisor.state_changed.connect(self._on_state)
        generation.library_changed.connect(self.entries_changed)
        generation.finished.connect(self._on_finished)

    def entries(self) -> list[Entry]:
        return self.library.entries()

    def refresh_entries(self) -> None:
        """Read the library again: another program (the Blender add-on) may have added entries."""
        self.entries_changed.emit()

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

    @property
    def exporting(self) -> bool:
        return self._export is not None

    def export(self, target: Path) -> None:
        """Copy the GLB, or start a conversion; the outcome comes back as export_finished or export_failed."""
        model = self.selected_model()
        if model is None:
            raise ValueError("No model is selected")
        suffix = target.suffix.lower()
        if suffix == ".glb":
            shutil.copyfile(model, target)
            self.export_finished.emit(target)
        elif suffix in CONVERTED:
            if self._export is not None:
                raise RuntimeError("An export is already running")
            fmt = suffix.lstrip(".")
            self._export = _Export(self.selected.entry_id, fmt, target)
            self.export_state_changed.emit()
            self.api.start_export(self.selected.entry_id, fmt, self._on_export_started)
        else:
            raise ValueError(f"Unsupported format: {suffix}")

    def export_drops(self, suffix: str) -> set[str]:
        """What the selected model loses in the format: "skeleton" and/or "texture"."""
        entry = self.selected
        if entry is None:
            return set()
        suffix = suffix.lower()
        drops = set()
        if Library.has_skeleton(entry) and suffix not in KEEPS_SKELETON:
            drops.add("skeleton")
        if entry.stats is not None and entry.stats.textured and suffix not in KEEPS_TEXTURE:
            drops.add("texture")
        return drops

    @property
    def animating(self) -> bool:
        return self._run is not None

    def can_add_walk(self, entry: Entry) -> bool:
        return (
            entry.outcome == Outcome.succeeded
            and Library.has_skeleton(entry)
            and self.library.model_path(entry.entry_id).is_file()
            and not self.exporting
            and not self.animating
        )

    def detect_body(self, entry_id: str) -> None:
        """Detect the body type; the result comes back as body_detected, or walk_failed."""
        entry = self._entry(entry_id)
        if not self.can_add_walk(entry):
            raise ValueError("The walk animation needs a rigged model")
        self._start(_Run("detect", entry_id))
        self.api.body(entry_id, self._on_body)

    def add_walk(self, entry_id: str, body: str) -> None:
        """Write the walk into a new "<name> (walk)" entry; the source stays unchanged."""
        source = self._entry(entry_id)
        if body not in BODIES:
            raise ValueError(f"Unsupported body type: {body}")
        if not self.can_add_walk(source):
            raise ValueError("The walk animation needs a rigged model")
        self._start(_Run("walk", entry_id))
        self.api.start_walk(entry_id, body, self._on_walk_started)

    def _start(self, run: _Run) -> None:
        self._run = run
        self.animation_state_changed.emit()

    def _finish(self) -> None:
        self._run = None
        self.animation_state_changed.emit()

    def _entry(self, entry_id: str) -> Entry:
        entry = next((e for e in self.entries() if e.entry_id == entry_id), None)
        if entry is None:
            raise ValueError(f"No entry {entry_id}")
        return entry

    def _on_export_started(self, result: ApiError | None) -> None:
        if isinstance(result, ApiError):
            self._fail_export(result.message)
        else:
            self._poll_export()

    def _poll_export(self) -> None:
        export = self._export
        if export is None:
            return
        self.api.download_export(export.entry_id, export.fmt, export.target, self._on_export_polled)

    def _on_export_polled(self, result: Path | ApiError) -> None:
        export = self._export
        if export is None:
            return
        if isinstance(result, Path):
            self._export = None
            self.export_state_changed.emit()
            self.export_finished.emit(result)
        elif isinstance(result, ApiError):
            if result.code == "export_not_ready" and result.message.endswith("is running"):
                QTimer.singleShot(self.poll_ms, self._poll_export)
            else:
                prefix = f"Export {export.fmt} failed: "
                reason = result.message[len(prefix):] if result.message.startswith(prefix) else result.message
                self._fail_export(reason)

    def _fail_export(self, reason: str) -> None:
        self._export = None
        self.export_state_changed.emit()
        self.export_failed.emit(reason)

    def _on_body(self, result: dict | ApiError) -> None:
        run = self._run
        if run is None or run.kind != "detect":
            return
        self._finish()
        if isinstance(result, ApiError):
            self.walk_failed.emit(result.message)
        else:
            self.body_detected.emit(run.entry_id, result)

    def _on_walk_started(self, result: Entry | ApiError) -> None:
        run = self._run
        if run is None or run.kind != "walk":
            return
        if isinstance(result, ApiError):
            self._finish()
            self.walk_failed.emit(result.message)
        else:
            run.derived_id = result.entry_id
            self._poll_walk()

    def _poll_walk(self) -> None:
        run = self._run
        if run is None or run.kind != "walk" or run.derived_id is None:
            return
        self.api.entry(run.derived_id, self._on_walk_polled)

    def _on_walk_polled(self, result: Entry | ApiError) -> None:
        run = self._run
        if run is None or run.kind != "walk":
            return
        if isinstance(result, ApiError):
            self._finish()
            self.walk_failed.emit(result.message)
        elif result.outcome == Outcome.running:
            QTimer.singleShot(self.poll_ms, self._poll_walk)
        elif result.outcome == Outcome.succeeded:
            self._finish()
            derived = self._entry(run.derived_id)
            self.entries_changed.emit()
            self.select(derived.entry_id)
            self.walk_added.emit(derived)
        else:
            self._finish()
            try:
                self.library.delete(run.derived_id)
            except OSError:
                pass
            self.entries_changed.emit()
            self.walk_failed.emit(result.error.message if result.error is not None else "The walk animation failed")

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
