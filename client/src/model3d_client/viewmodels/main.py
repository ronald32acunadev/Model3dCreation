"""The main window's state: library selection, diagnostics, and closing."""

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from model3d_client.paths import AppPaths
from model3d_client.services.animator import BODIES, RigAnimator
from model3d_client.services.diagnostics import Check, engine_check, run_checks
from model3d_client.services.engine_api import EngineApi
from model3d_client.services.converter import CONVERTED, KEEPS_SKELETON, KEEPS_TEXTURE, ModelConverter
from model3d_client.services.library import Entry, Library, Outcome
from model3d_client.services.settings import Settings
from model3d_client.services.supervisor import EngineState, EngineSupervisor
from model3d_client.viewmodels.generation import GenerationViewModel, Phase

DIAGNOSTIC_STATES = {EngineState.error, EngineState.crashed}


class CloseDecision(str, Enum):
    close = "close"
    confirm = "confirm"  # a generation runs: ask the user first


@dataclass
class _Run:
    kind: str  # "detect" or "walk"
    entry_id: str  # the rigged source entry
    derived_id: str | None = None  # the new entry a walk writes into


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
        converter: ModelConverter | None = None,
        animator: RigAnimator | None = None,
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
        self.converter = converter if converter is not None else ModelConverter(paths.engine_python, parent=self)
        self.converter.finished.connect(self._on_export_finished)
        self.converter.failed.connect(self._on_export_failed)
        self._run: _Run | None = None
        self.animator = animator if animator is not None else RigAnimator(paths.engine_python, parent=self)
        self.animator.detected.connect(self._on_detected)
        self.animator.walk_finished.connect(self._on_walk_finished)
        self.animator.failed.connect(self._on_animation_failed)
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
        return self.converter.busy

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
            self.converter.start(model, target)
            self.export_state_changed.emit()
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
        model = self.library.model_path(entry_id)
        self._start(_Run("detect", entry_id), lambda: self.animator.detect(model))

    def add_walk(self, entry_id: str, body: str) -> None:
        """Write the walk into a new "<name> (walk)" entry; the source stays unchanged."""
        source = self._entry(entry_id)
        if body not in BODIES:
            raise ValueError(f"Unsupported body type: {body}")
        if not self.can_add_walk(source):
            raise ValueError("The walk animation needs a rigged model")
        derived = self.library.create_derived(entry_id, suffix=" (walk)")
        model = self.library.model_path(entry_id)
        target = self.library.model_path(derived.entry_id)
        try:
            self._start(_Run("walk", entry_id, derived.entry_id),
                        lambda: self.animator.walk(model, target, body))
        except Exception:
            self.library.delete(derived.entry_id)
            raise

    def _start(self, run: _Run, start: Callable[[], None]) -> None:
        self._run = run
        self.animation_state_changed.emit()
        try:
            start()
        except Exception:
            self._run = None
            self.animation_state_changed.emit()
            raise

    def _finish(self) -> None:
        self._run = None
        self.animation_state_changed.emit()

    def _entry(self, entry_id: str) -> Entry:
        entry = next((e for e in self.entries() if e.entry_id == entry_id), None)
        if entry is None:
            raise ValueError(f"No entry {entry_id}")
        return entry

    def _on_detected(self, result: dict) -> None:
        run = self._run
        if run is None or run.kind != "detect":
            return
        self._finish()
        self.body_detected.emit(run.entry_id, result)

    def _on_walk_finished(self, _target: Path) -> None:
        run = self._run
        if run is None or run.kind != "walk" or run.derived_id is None:
            return
        self._finish()
        source = self._entry(run.entry_id)
        derived = self._entry(run.derived_id).model_copy(update={
            "outcome": Outcome.succeeded,
            "stats": source.stats,
            "seed": source.seed,
            "options": source.options,
        })
        self.library.update(derived)
        self.entries_changed.emit()
        self.select(derived.entry_id)
        self.walk_added.emit(derived)

    def _on_animation_failed(self, reason: str) -> None:
        run = self._run
        if run is None:
            return
        self._finish()
        if run.derived_id is not None:
            self.library.delete(run.derived_id)
            self.entries_changed.emit()
        self.walk_failed.emit(reason)

    def _on_export_finished(self, target: Path) -> None:
        self.export_state_changed.emit()
        self.export_finished.emit(target)

    def _on_export_failed(self, reason: str) -> None:
        self.export_state_changed.emit()
        self.export_failed.emit(reason)

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
