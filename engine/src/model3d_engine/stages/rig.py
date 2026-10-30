"""The rig stage: UniRig adds a skeleton and skinning weights to a finished model.

UniRig needs its own environment (bpy 4.2, flash_attn, spconv), so each task runs
unirig_job.py with UniRig's interpreter in a new process; nothing stays loaded between tasks.
"""

import asyncio
import collections
import os
import signal
from pathlib import Path

from model3d_engine.stages.protocol import (
    MODEL_OUTPUT,
    RIG_FACES,
    RIG_INPUT,
    ProgressCallback,
    StageError,
    StageResult,
    StageTask,
)

UNIRIG_SCRIPT = Path(__file__).with_name("unirig_job.py")
UNIRIG_CHECKPOINTS = ("skeleton/articulation-xl_quantization_256/model.ckpt", "skin/articulation-xl/model.ckpt")


def rig_availability(unirig_dir: Path, hf_home: Path) -> tuple[bool, str | None]:
    """Whether UniRig's clone, environment, and weights are all in place, and if not, why."""
    if not (unirig_dir / "run.py").is_file():
        return False, "UniRig is not installed in vendor/UniRig (see docs/setup.md)"
    if not (unirig_dir / ".venv/bin/python").exists():
        return False, "UniRig's environment is missing (see docs/setup.md)"
    snapshots = hf_home / "hub/models--VAST-AI--UniRig/snapshots"
    if not any(all((snapshot / name).is_file() for name in UNIRIG_CHECKPOINTS) for snapshot in snapshots.glob("*")):
        return False, "UniRig weights are missing from data/hf (see docs/setup.md)"
    return True, None


def _visible_device(device: str) -> str | None:
    """The CUDA_VISIBLE_DEVICES value that shows only `device` (cuda:N) to the child process."""
    if not device.startswith("cuda:"):
        return None
    index = int(device.split(":", 1)[1])
    visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    if visible:
        # The engine itself sees a subset of the cards; its indices count within that list.
        return visible.split(",")[index].strip()
    return str(index)


class RigRunner:
    """StageRunner for the rig stage; start, unload, and stop only refresh availability."""

    def __init__(
        self,
        unirig_dir: Path,
        hf_home: Path,
        device: str,
        env: dict[str, str] | None = None,
        python: Path | None = None,
        script: Path | None = None,
        task_timeout_s: float = 900.0,
    ):
        self.unirig_dir = unirig_dir
        self.hf_home = hf_home
        self.device = device
        self.env = env or {}
        self.python = python or unirig_dir / ".venv/bin/python"
        self.script = script or UNIRIG_SCRIPT
        self.task_timeout_s = task_timeout_s
        self.last_pid: int | None = None
        self.ready, self.reason = rig_availability(unirig_dir, hf_home)

    async def start(self) -> None:
        self.ready, self.reason = rig_availability(self.unirig_dir, self.hf_home)

    async def unload(self) -> None:
        pass  # nothing stays loaded between tasks

    async def stop(self) -> None:
        pass

    def _child_env(self) -> dict[str, str]:
        env = {**os.environ, **self.env}
        env["PATH"] = f"{self.unirig_dir / '.venv/bin'}{os.pathsep}{env.get('PATH', '')}"
        visible = _visible_device(self.device)
        if visible is not None:
            env["CUDA_VISIBLE_DEVICES"] = visible
        return env

    async def run(self, task: StageTask, on_progress: ProgressCallback) -> StageResult:
        """Rig task.job_dir/RIG_INPUT into task.job_dir/MODEL_OUTPUT."""
        workdir = task.job_dir / "rig"
        workdir.mkdir(exist_ok=True)
        output = task.job_dir / MODEL_OUTPUT
        max_faces = task.options.target_faces or RIG_FACES
        process = await asyncio.create_subprocess_exec(
            str(self.python), str(self.script),
            "--unirig-dir", str(self.unirig_dir),
            "--input", str(task.job_dir / RIG_INPUT),
            "--output", str(output),
            "--workdir", str(workdir),
            "--max-faces", str(max_faces),
            cwd=self.unirig_dir,
            env=self._child_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # its own process group, so a timeout kills UniRig's children too
        )
        self.last_pid = process.pid
        try:
            return await asyncio.wait_for(self._follow(process, task, output, on_progress), self.task_timeout_s)
        except TimeoutError:
            await self._kill(process)
            raise StageError("stage_timeout", f"rig did not finish within {self.task_timeout_s:.0f} s") from None
        except StageError:
            raise
        except BaseException as error:
            # Cancelled (the engine is stopping) or a broken protocol line: never leave UniRig
            # running, since its own session does not receive the engine's signals.
            await self._kill(process)
            if isinstance(error, Exception):
                raise StageError("rig_failed", f"Unexpected output from UniRig: {error}") from error
            raise

    @staticmethod
    async def _kill(process) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await asyncio.shield(process.wait())

    async def _follow(self, process, task: StageTask, output: Path, on_progress: ProgressCallback) -> StageResult:
        result: tuple[int, int] | None = None
        error: tuple[str, str] | None = None
        stderr_tail: collections.deque[str] = collections.deque(maxlen=20)
        with open(task.job_dir / "rig.log", "w") as log:

            async def read_stderr() -> None:
                async for raw in process.stderr:
                    line = raw.decode(errors="replace").rstrip()
                    log.write(line + "\n")
                    if line:
                        stderr_tail.append(line)

            stderr_task = asyncio.create_task(read_stderr())
            async for raw in process.stdout:
                line = raw.decode(errors="replace").rstrip()
                log.write(line + "\n")
                kind, _, rest = line.partition(" ")
                if kind == "PROGRESS":
                    value, _, message = rest.partition(" ")
                    on_progress(float(value), message)
                elif kind == "RESULT":
                    vertices, faces = rest.split()
                    result = int(vertices), int(faces)
                elif kind == "ERROR":
                    code, _, message = rest.partition(" ")
                    error = code, message
            await stderr_task
            await process.wait()
        if error is not None:
            raise StageError(*error)
        if process.returncode != 0 or result is None:
            last = stderr_tail[-1] if stderr_tail else "no output"
            raise StageError("rig_failed", f"UniRig exited with code {process.returncode}: {last}")
        return StageResult(output=output, vertices=result[0], faces=result[1])
