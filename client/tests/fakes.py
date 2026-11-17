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

    def __init__(self, state=EngineState.ready, texture=True, rig=True, rig_reason=None, detailed_face=False,
                 detailed_face_reason="Detailed face needs the TRELLIS.2 generator", voice=False,
                 voice_reason=None, sharper_texture=False,
                 sharper_texture_reason="Sharper texture needs the TRELLIS.2 generator"):
        super().__init__()
        self.state = state
        self.health = Health.model_validate({
            **make_health(ready=state == EngineState.ready, texture=texture, rig=rig, rig_reason=rig_reason,
                          detailed_face=detailed_face, detailed_face_reason=detailed_face_reason),
            "voice": voice,
            "voice_reason": voice_reason,
            "sharper_texture": sharper_texture,
            "sharper_texture_reason": None if sharper_texture else sharper_texture_reason,
        })
        self.failure = None
        self.owned = True
        self.stopped = 0
        self.restarted = 0

    def set_state(self, state):
        self.state = state
        self.state_changed.emit(state)

    def set_voice(self, voice: bool, reason: str | None = None):
        self.health = self.health.model_copy(update={"voice": voice, "voice_reason": reason})
        self.health_changed.emit(self.health)

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


class FakePlayer(QObject):
    """Records what would be played; stands in for AudioPlayer."""

    started = Signal()
    finished = Signal()
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.played: list[Path] = []
        self.stops = 0

    def play(self, path: Path) -> None:
        self.played.append(path)

    def stop(self) -> None:
        self.stops += 1


class FakeRecorder(QObject):
    """Stands in for Recorder: `start` remembers the target, `stop` finishes the take with `content`."""

    level = Signal(float)
    elapsed = Signal(float)
    finished = Signal(object)
    failed = Signal(str)

    content = b"RIFF fake wav"

    def __init__(self):
        super().__init__()
        self.target: Path | None = None

    def start(self, target: Path) -> None:
        self.target = target

    def stop(self) -> None:
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_bytes(self.content)
        self.finished.emit(self.target)
