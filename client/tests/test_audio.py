import array

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtMultimedia import QAudioFormat

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


def test_recorder_builds_the_capture_pipeline_on_a_real_input_device(monkeypatch):
    from PySide6.QtMultimedia import QMediaDevices

    devices = QMediaDevices.audioInputs()
    if not devices:
        pytest.skip("the offscreen platform exposes no audio input devices")
    monkeypatch.setattr(recorder_module, "default_input", lambda: devices[0])
    recorder = construct(Recorder)
    recorder._build(devices[0])
    assert recorder._session is not None
    assert recorder._recorder is not None
    assert not hasattr(recorder, "_buffers")


def test_recorder_builds_the_capture_pipeline_without_a_device(qtbot):
    from PySide6.QtMultimedia import QAudioDevice

    recorder = construct(Recorder)
    recorder._build(QAudioDevice())
    assert recorder._recorder is not None
    assert not hasattr(recorder, "_buffers")


def test_rms_level_of_a_constant_int16_buffer_is_scaled_and_capped():
    loud = array.array("h", [8192] * 64).tobytes()
    quiet = array.array("h", [2048] * 64).tobytes()
    assert recorder_module.rms_level(loud, QAudioFormat.SampleFormat.Int16) == 1.0
    assert recorder_module.rms_level(quiet, QAudioFormat.SampleFormat.Int16) == 0.25


def test_rms_level_ignores_a_trailing_partial_sample():
    data = array.array("h", [2048] * 4).tobytes() + b"\x01"
    assert recorder_module.rms_level(data, QAudioFormat.SampleFormat.Int16) == 0.25


def test_rms_level_is_none_for_an_unreadable_sample_format():
    data = array.array("B", [128] * 8).tobytes()
    assert recorder_module.rms_level(data, QAudioFormat.SampleFormat.UInt8) is None
    assert recorder_module.rms_level(b"", QAudioFormat.SampleFormat.Int16) is None


def test_recorder_emits_the_level_of_meter_data(qtbot):
    recorder = construct(Recorder)
    fmt = QAudioFormat()
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    recorder._level_format = fmt
    values = []
    recorder.level.connect(values.append)
    recorder._handle_level_data(array.array("h", [2048] * 16).tobytes())
    assert values == [0.25]
