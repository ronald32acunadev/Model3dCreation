import asyncio
import sys
from pathlib import Path

import pytest

from model3d_engine.stages.head_pass import HeadPassFailed, HeadPassResult, HeadPassRunner

pytestmark = pytest.mark.anyio

STUB = Path(__file__).parent / "head_pass_stub.py"


def make_runner(tmp_path: Path) -> HeadPassRunner:
    return HeadPassRunner(Path(sys.executable), STUB, tmp_path, "http://127.0.0.1:1")


async def test_reports_steps_in_order_and_returns_applied_result(tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "applied")
    monkeypatch.setenv("STUB_MODEL", str(tmp_path / "head.glb"))
    runner = make_runner(tmp_path)
    steps: list[str] = []
    result = await runner.run("job1", steps.append)
    assert result == HeadPassResult(applied=True, model=tmp_path / "head.glb", reason=None)
    assert steps == ["step one", "step two"]


async def test_not_applied_carries_the_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "skipped")
    monkeypatch.setenv("STUB_MODEL", str(tmp_path / "head.glb"))
    runner = make_runner(tmp_path)
    result = await runner.run("job1", lambda s: None)
    assert result.applied is False
    assert result.reason == "no usable head"


async def test_exit_without_a_tag_line_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "notag")
    runner = make_runner(tmp_path)
    with pytest.raises(HeadPassFailed):
        await runner.run("job1", lambda s: None)


async def test_failure_carries_the_output_tail(tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "fail")
    runner = make_runner(tmp_path)
    with pytest.raises(HeadPassFailed) as excinfo:
        await runner.run("job1", lambda s: None)
    assert "boom: bad thing" in str(excinfo.value)
    assert "some tool output" in str(excinfo.value)


async def test_cancel_terminates_the_process(tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "sleep")
    runner = make_runner(tmp_path)
    steps: list[str] = []
    task = asyncio.create_task(runner.run("job1", steps.append))
    deadline = asyncio.get_event_loop().time() + 10.0
    while "waiting" not in steps:
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("stub never reported waiting")
        await asyncio.sleep(0.05)
    await runner.cancel("job1")
    with pytest.raises(HeadPassFailed):
        await asyncio.wait_for(task, 10)


async def test_reason_names_missing_tools(tmp_path):
    missing = HeadPassRunner(Path(sys.executable), tmp_path / "missing.py", tmp_path, "http://127.0.0.1:1")
    assert missing.reason == "The head-pass tools are not installed"
    assert make_runner(tmp_path).reason is None
