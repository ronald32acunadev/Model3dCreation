from pathlib import Path

import pytest

from model3d_client.services.animator import RigAnimator

DETECT_OK = (
    'echo "bpy noise line"\n'
    'echo \'MODEL3D_RESULT {"body": "biped", "legs": [["a", "b"]], "arms": [], "up": 2}\'\n'
    "exit 0\n"
)
DETECT_SILENT = "echo 'nothing tagged here'\nexit 0\n"
WALK_OK = 'printf data > "$4"\necho \'MODEL3D_RESULT {"body": "biped", "legs": 2, "arms": 0, "frames": 24}\'\nexit 0\n'
WALK_FAIL = 'printf partial > "$4"\necho "first" >&2\necho "boom" >&2\nexit 2\n'
SLOW = 'sleep 0.5\necho \'MODEL3D_RESULT {"body": "biped"}\'\nexit 0\n'


@pytest.fixture
def fake_python(tmp_path):
    def make(name: str, body: str) -> Path:
        path = tmp_path / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
        return path

    return make


def test_detect_parses_tagged_line_after_noise(qtbot, tmp_path, fake_python):
    animator = RigAnimator(fake_python("ok", DETECT_OK))
    with qtbot.waitSignal(animator.detected, timeout=5000) as blocker:
        animator.detect(tmp_path / "robot.glb")
    assert blocker.args == [{"body": "biped", "legs": [["a", "b"]], "arms": [], "up": 2}]
    assert not animator.busy


def test_detect_exit_zero_without_tagged_line_fails(qtbot, tmp_path, fake_python):
    animator = RigAnimator(fake_python("silent", DETECT_SILENT))
    with qtbot.waitSignal(animator.failed, timeout=5000) as blocker:
        animator.detect(tmp_path / "robot.glb")
    assert blocker.args == ["The script gave no result"]
    assert not animator.busy


def test_walk_success_emits_target(qtbot, tmp_path, fake_python):
    animator = RigAnimator(fake_python("ok", WALK_OK))
    target = tmp_path / "walk.glb"
    with qtbot.waitSignal(animator.walk_finished, timeout=5000) as blocker:
        animator.walk(tmp_path / "robot.glb", target, "quadruped")
    assert blocker.args == [target]
    assert target.read_bytes() == b"data"
    assert not animator.busy


def test_walk_failure_removes_partial_target_and_reports_stderr(qtbot, tmp_path, fake_python):
    animator = RigAnimator(fake_python("fail", WALK_FAIL))
    target = tmp_path / "walk.glb"
    with qtbot.waitSignal(animator.failed, timeout=5000) as blocker:
        animator.walk(tmp_path / "robot.glb", target, "biped")
    assert blocker.args[0] == "first\nboom"
    assert not target.exists()
    assert not animator.busy


def test_walk_failure_without_stderr_reports_exit_code(qtbot, tmp_path, fake_python):
    animator = RigAnimator(fake_python("silentfail", "exit 4\n"))
    with qtbot.waitSignal(animator.failed, timeout=5000) as blocker:
        animator.walk(tmp_path / "robot.glb", tmp_path / "walk.glb", "biped")
    assert blocker.args == ["Animation failed with exit code 4"]


def test_invalid_body_raises(tmp_path, fake_python):
    animator = RigAnimator(fake_python("ok", WALK_OK))
    with pytest.raises(ValueError):
        animator.walk(tmp_path / "robot.glb", tmp_path / "walk.glb", "snake")


def test_busy_start_raises(qtbot, tmp_path, fake_python):
    animator = RigAnimator(fake_python("slow", SLOW))
    animator.detect(tmp_path / "robot.glb")
    assert animator.busy
    with pytest.raises(RuntimeError):
        animator.detect(tmp_path / "robot.glb")
    with qtbot.waitSignal(animator.detected, timeout=5000):
        pass


def test_missing_python_fails_without_starting(qtbot, tmp_path):
    animator = RigAnimator(tmp_path / "missing" / "python")
    with qtbot.waitSignal(animator.failed, timeout=5000) as blocker:
        animator.detect(tmp_path / "robot.glb")
    assert "The engine environment is missing" in blocker.args[0]
    assert not animator.busy
