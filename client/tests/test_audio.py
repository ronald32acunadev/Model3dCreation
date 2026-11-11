import pytest
from PySide6.QtCore import QCoreApplication

from model3d_client.services import recorder as recorder_module
from model3d_client.services.audio import AudioPlayer
from model3d_client.services.recorder import Recorder


def construct(factory):
    try:
        return factory()
    except Exception as error:  # noqa: BLE001 - any platform failure means the backend is unusable here
        pytest.skip(f"QtMultimedia cannot be constructed on this platform: {error}")


def test_player_reports_a_missing_file(qtbot, tmp_path):
    assert QCoreApplication.instance() is not None
    player = construct(AudioPlayer)
    assert not player.playing
    with qtbot.waitSignal(player.failed, timeout=2000) as blocker:
        player.play(tmp_path / "missing.wav")
    assert "missing.wav" in blocker.args[0]
    assert not player.playing


def test_recorder_without_a_microphone_fails(qtbot, tmp_path, monkeypatch):
    class NoDevice:
        @staticmethod
        def isNull():
            return True

    monkeypatch.setattr(recorder_module, "default_input", lambda: NoDevice())
    recorder = construct(Recorder)
    with qtbot.waitSignal(recorder.failed, timeout=2000) as blocker:
        recorder.start(tmp_path / "take.wav")
    assert blocker.args == ["No microphone found"]
    assert not recorder.recording
    assert not (tmp_path / "take.wav").exists()
