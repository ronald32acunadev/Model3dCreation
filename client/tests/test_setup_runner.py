import os
import sys
import threading
import time

import pytest

from model3d_client.setup.runner import Command, Step, StepFailed, StepRunner, pending_steps
from model3d_client.setup.state import InstallState, load_state


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "Disco 2" / "Modelos 3D"
    path.mkdir(parents=True)
    return path


def py(code: str) -> Command:
    return Command([sys.executable, "-c", code])


def touch_step(step_id: str, fingerprint: str = "f1", code: str | None = None) -> Step:
    """A step that writes <folder>/<id>.done through a real subprocess."""
    code = code or "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('ok')"
    return Step(
        id=step_id, title=step_id, component="base", size_bytes=0, fingerprint=fingerprint,
        actions=[_writer(step_id, code)],
        done_check=lambda folder, step_id=step_id: (folder / f"{step_id}.done").exists(),
    )


def _writer(step_id: str, code: str):
    def action(ctx):
        command = Command([sys.executable, "-c", code, str(ctx.install_dir / f"{step_id}.done")])
        ctx.run(command)

    return action


def runner(folder, steps, log=None):
    return StepRunner(folder, steps, app_version="0.1.0", components=["base"],
                      on_progress=lambda *args: None, on_log=log or (lambda line: None))


def test_runs_steps_in_order_and_records_them(folder):
    order = []
    steps = [touch_step("a"), touch_step("b")]
    StepRunner(folder, steps, "0.1.0", ["base"], on_progress=lambda step_id, title, value: order.append(step_id),
               on_log=lambda line: None).run()
    assert order == ["a", "b", ""]  # the empty id is the final 100% report
    state = load_state(folder)
    assert list(state.steps) == ["a", "b"]
    assert state.steps["a"].fingerprint == "f1"
    assert state.components == ["base"] and state.app_version == "0.1.0"


def test_failed_step_leaves_later_steps_unrecorded(folder):
    steps = [touch_step("a"), touch_step("bad", code="import sys; sys.exit(3)"), touch_step("c")]
    with pytest.raises(StepFailed) as failure:
        runner(folder, steps).run()
    assert failure.value.step_id == "bad"
    assert failure.value.code == "command_failed"
    assert list(load_state(folder).steps) == ["a"]
    assert not (folder / "c.done").exists()


def test_resume_skips_recorded_steps(folder):
    with pytest.raises(StepFailed):
        runner(folder, [touch_step("a"), touch_step("b", code="import sys; sys.exit(1)")]).run()
    (folder / "a.done").write_text("kept")
    runner(folder, [touch_step("a"), touch_step("b")]).run()
    assert (folder / "a.done").read_text() == "kept"  # not run again
    assert list(load_state(folder).steps) == ["a", "b"]


def test_changed_fingerprint_reruns_the_step(folder):
    runner(folder, [touch_step("a", "f1")]).run()
    (folder / "a.done").write_text("old")
    steps = [touch_step("a", "f2")]
    assert [s.id for s in pending_steps(steps, load_state(folder), folder)] == ["a"]
    runner(folder, steps).run()
    assert (folder / "a.done").read_text() == "ok"
    assert load_state(folder).steps["a"].fingerprint == "f2"


def test_failed_done_check_reruns_a_recorded_step(folder):
    runner(folder, [touch_step("a")]).run()
    (folder / "a.done").unlink()
    assert [s.id for s in pending_steps([touch_step("a")], load_state(folder), folder)] == ["a"]


def test_cancel_kills_a_running_command(folder):
    started = threading.Event()
    steps = [Step(id="slow", title="slow", component="base", size_bytes=0, fingerprint="f",
                  actions=[py("import os, sys, time; print(os.getpid(), flush=True); time.sleep(60)")],
                  done_check=lambda folder: False)]
    lines = []

    def on_log(line):
        lines.append(line)
        if line.strip().isdigit():
            started.set()

    run = runner(folder, steps, log=on_log)
    errors = []
    thread = threading.Thread(target=lambda: _capture(run.run, errors))
    begin = time.monotonic()
    thread.start()
    assert started.wait(20)
    run.cancel()
    thread.join(20)
    assert not thread.is_alive()
    assert time.monotonic() - begin < 30
    assert errors and errors[0].code == "cancelled"
    pid = int(next(line for line in lines if line.strip().isdigit()))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert load_state(folder) is None or "slow" not in load_state(folder).steps


def _capture(function, errors):
    try:
        function()
    except StepFailed as error:
        errors.append(error)


def test_no_space_output_maps_to_no_space(folder):
    steps = [touch_step("full", code="import sys; print('write error: No space left on device'); sys.exit(1)")]
    with pytest.raises(StepFailed) as failure:
        runner(folder, steps).run()
    assert failure.value.code == "no_space"


def test_no_space_oserror_in_an_action_maps_to_no_space(folder):
    def full(ctx):
        raise OSError(28, "No space left on device")

    steps = [Step(id="full", title="full", component="base", size_bytes=0, fingerprint="f",
                  actions=[full], done_check=lambda folder: False)]
    with pytest.raises(StepFailed) as failure:
        runner(folder, steps).run()
    assert failure.value.code == "no_space"


def test_log_file_receives_command_output(folder):
    steps = [Step(id="say", title="say", component="base", size_bytes=0, fingerprint="f",
                  actions=[py("print('hello from step')")], done_check=lambda folder: True)]
    seen = []
    runner(folder, steps, log=seen.append).run()
    assert any("hello from step" in line for line in seen)
    assert "hello from step" in (folder / "data/logs/install.log").read_text()


def test_command_env_and_cwd_are_applied(folder):
    steps = [Step(id="env", title="env", component="base", size_bytes=0, fingerprint="f",
                  actions=[Command([sys.executable, "-c", "import os; print(os.environ['M3D_X'], os.getcwd())"],
                                   cwd=folder, env={"M3D_X": "value"})],
                  done_check=lambda folder: True)]
    seen = []
    runner(folder, steps, log=seen.append).run()
    assert any(line.strip() == f"value {folder}" for line in seen)


def test_state_keeps_records_of_steps_outside_this_run(folder):
    runner(folder, [touch_step("a")]).run()
    runner(folder, [touch_step("b")]).run()
    assert set(load_state(folder).steps) == {"a", "b"}
    assert isinstance(load_state(folder), InstallState)
