from pathlib import Path

import pytest

from model3d_client.services.converter import ModelConverter

OK = 'printf data > "$3"\nexit 0\n'
FAIL = 'printf partial > "$3"\necho boom >&2\nexit 3\n'
SILENT = "exit 0\n"
SLOW = 'sleep 0.5\nprintf data > "$3"\nexit 0\n'
ARGS = 'echo "$1 $2 $3" > "$3"\nexit 0\n'


@pytest.fixture
def fake_python(tmp_path):
    def make(name: str, body: str) -> Path:
        path = tmp_path / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
        return path

    return make


def test_success_emits_finished_with_target(qtbot, tmp_path, fake_python):
    converter = ModelConverter(fake_python("ok", OK))
    source, target = tmp_path / "robot.glb", tmp_path / "robot.fbx"
    with qtbot.waitSignal(converter.finished, timeout=5000) as blocker:
        converter.start(source, target)
    assert blocker.args == [target]
    assert target.read_bytes() == b"data"
    assert not converter.busy


def test_failure_emits_stderr_and_removes_partial_target(qtbot, tmp_path, fake_python):
    converter = ModelConverter(fake_python("fail", FAIL))
    target = tmp_path / "robot.fbx"
    with qtbot.waitSignal(converter.failed, timeout=5000) as blocker:
        converter.start(tmp_path / "robot.glb", target)
    assert "boom" in blocker.args[0]
    assert not target.exists()
    assert not converter.busy


def test_obj_failure_removes_sibling_material(qtbot, tmp_path, fake_python):
    body = 'printf x > "$3"\nprintf x > "${3%.obj}.mtl"\nexit 1\n'
    converter = ModelConverter(fake_python("objfail", body))
    target = tmp_path / "robot.obj"
    with qtbot.waitSignal(converter.failed, timeout=5000):
        converter.start(tmp_path / "robot.glb", target)
    assert not target.exists()
    assert not (tmp_path / "robot.mtl").exists()


def test_exit_zero_without_file_is_a_failure(qtbot, tmp_path, fake_python):
    converter = ModelConverter(fake_python("silent", SILENT))
    target = tmp_path / "robot.stl"
    with qtbot.waitSignal(converter.failed, timeout=5000) as blocker:
        converter.start(tmp_path / "robot.glb", target)
    assert blocker.args[0] == "Conversion failed with exit code 0"


def test_missing_python_fails_without_starting_a_process(qtbot, tmp_path):
    converter = ModelConverter(tmp_path / "missing" / "python")
    with qtbot.waitSignal(converter.failed, timeout=5000) as blocker:
        converter.start(tmp_path / "robot.glb", tmp_path / "robot.fbx")
    assert "The engine environment is missing" in blocker.args[0]
    assert not converter.busy


def test_unsupported_suffix_raises(tmp_path, fake_python):
    converter = ModelConverter(fake_python("ok", OK))
    with pytest.raises(ValueError):
        converter.start(tmp_path / "robot.glb", tmp_path / "robot.png")


def test_second_start_while_busy_raises(qtbot, tmp_path, fake_python):
    converter = ModelConverter(fake_python("slow", SLOW))
    target = tmp_path / "robot.fbx"
    converter.start(tmp_path / "robot.glb", target)
    assert converter.busy
    with pytest.raises(RuntimeError):
        converter.start(tmp_path / "robot.glb", tmp_path / "other.fbx")
    with qtbot.waitSignal(converter.finished, timeout=5000):
        pass
    assert target.exists()


def test_arguments_are_script_source_target(qtbot, tmp_path, fake_python):
    script = tmp_path / "convert_model.py"
    converter = ModelConverter(fake_python("args", ARGS), script=script)
    source, target = tmp_path / "robot.glb", tmp_path / "robot.obj"
    with qtbot.waitSignal(converter.finished, timeout=5000):
        converter.start(source, target)
    assert target.read_text() == f"{script} {source} {target}\n"
