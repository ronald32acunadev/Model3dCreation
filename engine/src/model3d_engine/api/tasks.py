"""In-memory runner for the engine's CPU tool scripts."""

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

RESULT_TAG = "MODEL3D_RESULT "
ERROR_LINES = 5


@dataclass(frozen=True)
class ToolResult:
    code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ToolRun:
    state: str
    error: str | None = None


class ToolBusy(Exception):
    """A tool already runs for this entry."""


def parse_result(stdout: str) -> dict | None:
    result = None
    for line in stdout.splitlines():
        if not line.startswith(RESULT_TAG):
            continue
        rest = line[len(RESULT_TAG):].strip()
        try:
            obj = json.loads(rest)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            result = obj
    return result


def failure_reason(stderr: str, code: int, fallback: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return fallback
    return "\n".join(lines[-ERROR_LINES:])


class ToolTasks:
    def __init__(self, python: str | Path, tools_dir: Path) -> None:
        self._python = python
        self._tools_dir = tools_dir
        self._busy: set[str] = set()
        self._runs: dict[tuple[str, str], ToolRun] = {}
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}

    def is_busy(self, entry_id: str) -> bool:
        return entry_id in self._busy

    async def _execute(self, script: str, args: list[str]) -> ToolResult:
        cmd = [str(self._python), str(self._tools_dir / script), *args]
        env = dict(os.environ)
        env["CUDA_VISIBLE_DEVICES"] = ""
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            return ToolResult(-1, "", str(exc))
        stdout_bytes, stderr_bytes = await process.communicate()
        await process.wait()
        return ToolResult(
            process.returncode,
            stdout_bytes.decode(errors="replace"),
            stderr_bytes.decode(errors="replace"),
        )

    async def run(self, entry_id: str, script: str, args: list[str]) -> ToolResult:
        if entry_id in self._busy:
            raise ToolBusy()
        self._busy.add(entry_id)
        try:
            return await self._execute(script, args)
        finally:
            self._busy.discard(entry_id)

    def start(
        self,
        entry_id: str,
        key: str,
        script: str,
        args: list[str],
        finish: Callable[[ToolResult], str | None],
    ) -> None:
        if entry_id in self._busy:
            raise ToolBusy()
        self._busy.add(entry_id)
        run_key = (entry_id, key)
        self._runs[run_key] = ToolRun("running")

        async def _task() -> None:
            try:
                result = await self._execute(script, args)
                try:
                    message = finish(result)
                except Exception as exc:
                    self._runs[run_key] = ToolRun("failed", error=str(exc))
                    return
                if message is None:
                    self._runs[run_key] = ToolRun("succeeded")
                else:
                    self._runs[run_key] = ToolRun("failed", error=message)
            finally:
                self._busy.discard(entry_id)

        task = asyncio.get_running_loop().create_task(_task())
        self._tasks[run_key] = task

    def status(self, entry_id: str, key: str) -> ToolRun | None:
        return self._runs.get((entry_id, key))

    async def wait(self, entry_id: str, key: str) -> None:
        task = self._tasks.get((entry_id, key))
        if task is not None:
            await task
