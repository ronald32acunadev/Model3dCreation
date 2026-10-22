"""Test doubles shared by view model and window tests."""

from pathlib import Path

from model3d_contracts.health import Health
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from fake_engine import make_health
from model3d_client.services.supervisor import EngineState


class FakeSupervisor(QObject):
    state_changed = Signal(object)
    health_changed = Signal(object)

    def __init__(self, state=EngineState.ready, texture=True, rig=True, rig_reason=None):
        super().__init__()
        self.state = state
        self.health = Health.model_validate(
            make_health(ready=state == EngineState.ready, texture=texture, rig=rig, rig_reason=rig_reason)
        )
        self.failure = None
        self.owned = True
        self.stopped = 0
        self.restarted = 0

    def set_state(self, state):
        self.state = state
        self.state_changed.emit(state)

    def stop(self, on_stopped):
        self.stopped += 1
        on_stopped()

    def restart(self):
        self.restarted += 1


class StubViewer(QWidget):
    """Stands in for ViewerWidget, which needs WebGL."""

    load_failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.shown: Path | None = None
        self.textured = True
        self.resets = 0

    def show_model(self, path: Path) -> None:
        self.shown = path

    def clear(self) -> None:
        self.shown = None

    def set_textured(self, textured: bool) -> None:
        self.textured = textured

    def reset_camera(self) -> None:
        self.resets += 1
