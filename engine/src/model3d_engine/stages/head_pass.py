"""Head-pass stage runner for the model3d engine."""

import asyncio
import json
import os
import signal
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

RESULT_TAG = "MODEL3D_HEAD_PASS "
STEP_TAG = "[run]  "
OUTPUT_TAIL = 15


@dataclass(frozen=True)
class HeadPassResult:
    applied: bool
    model: Path | None
    reason: str | None


class HeadPassFailed(Exception):
    pass


class HeadPassRunner:
    def __init__(self, python: Path, script: Path, home: Path, engine_url: str) -> None:
        self._python = python
        self._script = script
        self._home = home
        self._engine_url = engine_url
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    @property
    def reason(self) -> str | None:
        if self._script.is_file() and self._python.exists():
            return None
        return "The head-pass tools are not installed"

    async def run(self, job_id: str, on_step: Callable[[str], None]) -> HeadPassResult:
        process = await asyncio.create_subprocess_exec(
            str(self._python),
            str(self._script),
            job_id,
            "--home",
            str(self._home),
            "--engine",
            self._engine_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        self._processes[job_id] = process
        try:
            output: list[str] = []
            result: dict | None = None
            assert process.stdout is not None
            while True:
                line_bytes = await process.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode(errors="replace").rstrip("\n")
                if line.strip():
                    output.append(line.strip())
                if line.startswith(STEP_TAG):
                    on_step(line[len(STEP_TAG):].strip())
                elif line.startswith(RESULT_TAG):
                    payload = line[len(RESULT_TAG):].strip()
                    try:
                        parsed = json.loads(payload)
                        if isinstance(parsed, dict):
                            result = parsed
                    except (json.JSONDecodeError, ValueError):
                        pass
            await process.wait()
            if process.returncode == 0 and result is not None:
                applied = bool(result.get("applied"))
                if applied:
                    return HeadPassResult(True, Path(result["model"]), None)
                return HeadPassResult(False, None, result.get("reason") or "no usable head")
            tail = "\n".join(output[-OUTPUT_TAIL:]) or f"The head pass exited with code {process.returncode}"
            raise HeadPassFailed(tail)
        finally:
            self._processes.pop(job_id, None)

    async def cancel(self, job_id: str) -> None:
        process = self._processes.get(job_id)
        if process is None or process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 30)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
