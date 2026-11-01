"""Tests for scripts/setup/setup_repo.py: preflight, step plan, dry run, start command, and wrapper rules."""

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from model3d_client.setup import recipe
from model3d_client.setup.runner import Command

REPO_ROOT = Path(__file__).parents[2]
SCRIPT = REPO_ROOT / "scripts/setup/setup_repo.py"
SETUP_DIR = REPO_ROOT / "scripts/setup"


def _load_setup_repo():
    spec = importlib.util.spec_from_file_location("setup_repo", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["setup_repo"] = module
    spec.loader.exec_module(module)
    return module


setup_repo = _load_setup_repo()
GB = 10**9


def fake_probes(*, gpu: bool = True, free: int = 500 * GB, hf_access: bool = True):
    def run(argv: list[str]) -> str:
        if not gpu:
            raise subprocess.CalledProcessError(1, argv)
        return "NVIDIA GeForce RTX 5070\n"

    return setup_repo.Probes(run=run, disk_free=lambda path: free, has_hf_access=lambda repo_id: hf_access)


def write_pyproject(repo: Path, environments: list[str] | None) -> None:
    (repo / "engine").mkdir(parents=True, exist_ok=True)
    text = "[project]\nname = 'engine'\n"
    if environments is not None:
        entries = ", ".join(f'"{entry}"' for entry in environments)
        text += f"\n[tool.uv]\nenvironments = [{entries}]\n"
    (repo / "engine/pyproject.toml").write_text(text, encoding="utf-8")


def step_ids(steps) -> list[str]:
    return [step.id for step in steps]


def test_pins_come_from_the_recipe() -> None:
    assert setup_repo.UNIRIG_COMMIT == recipe.UNIRIG_COMMIT
    assert setup_repo.HUNYUAN_COMMIT == recipe.HUNYUAN_COMMIT


def test_no_driver_stops_before_anything(tmp_path: Path) -> None:
    problems = setup_repo.preflight(tmp_path, "linux", fake_probes(gpu=False))
    assert len(problems) == 1
    assert "NVIDIA driver" in problems[0]


def test_low_disk_stops(tmp_path: Path) -> None:
    problems = setup_repo.preflight(tmp_path, "linux", fake_probes(free=50 * GB))
    assert len(problems) == 1
    assert "100 GB" in problems[0]


def test_ok_preflight_is_empty(tmp_path: Path) -> None:
    assert setup_repo.preflight(tmp_path, "linux", fake_probes()) == []


def test_foreign_engine_env_is_reported_and_kept(tmp_path: Path) -> None:
    foreign = tmp_path / "engine/.venv/Scripts/x"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("windows", encoding="utf-8")
    problems = setup_repo.preflight(tmp_path, "linux", fake_probes())
    assert any("engine/.venv" in problem for problem in problems)
    assert foreign.exists()


def test_unirig_dir_switches_for_a_windows_env(tmp_path: Path) -> None:
    assert setup_repo.unirig_dir(tmp_path, "linux") == tmp_path / "vendor/UniRig"
    windows_env = tmp_path / "vendor/UniRig/.venv/Scripts"
    windows_env.mkdir(parents=True)
    assert setup_repo.unirig_dir(tmp_path, "linux") == tmp_path / "vendor/UniRig-linux"
    assert windows_env.exists()


def test_linux_steps_in_order(tmp_path: Path) -> None:
    steps = setup_repo.build_steps(tmp_path, "linux", setup_repo.Options(), fake_probes())
    assert step_ids(steps) == [
        "cuda-toolkit", "hunyuan-source", "engine-env", "inpaint", "weights",
        "unirig-source", "unirig-env", "unirig-weights",
        "trellis-source", "trellis-env", "trellis-weights",
    ]


def test_no_rig_and_no_trellis_drop_their_steps(tmp_path: Path) -> None:
    options = setup_repo.Options(rig=False, trellis=False)
    steps = setup_repo.build_steps(tmp_path, "linux", options, fake_probes())
    assert step_ids(steps) == ["cuda-toolkit", "hunyuan-source", "engine-env", "inpaint", "weights"]


def test_finished_steps_report_done(tmp_path: Path) -> None:
    (tmp_path / "vendor/Hunyuan3D-2.1/hy3dshape").mkdir(parents=True)
    checkpoint = tmp_path / "vendor/Hunyuan3D-2.1/hy3dpaint/ckpt/RealESRGAN_x4plus.pth"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("weights", encoding="utf-8")
    steps = {step.id: step for step in setup_repo.build_steps(tmp_path, "linux", setup_repo.Options(), fake_probes())}
    assert steps["hunyuan-source"].done() is True
    assert steps["engine-env"].done() is False


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    steps = setup_repo.build_steps(tmp_path, "linux", setup_repo.Options(), fake_probes())
    calls: list[Command] = []

    def runner(command: Command, log) -> None:
        calls.append(command)

    results = setup_repo.run_plan(steps, True, tmp_path, runner, lambda line: None)
    assert calls == []
    assert not (tmp_path / "vendor").exists()
    assert not (tmp_path / "data").exists()
    assert {status for _, status in results} <= {"done", "would run"}
    assert setup_repo.main(["--dry-run"], repo=tmp_path, platform="linux", probes=fake_probes(), runner=runner) == 0
    assert not (tmp_path / "vendor").exists()


def test_windows_stops_at_engine_while_linux_only(tmp_path: Path) -> None:
    write_pyproject(tmp_path, ["sys_platform == 'linux'"])
    steps = setup_repo.build_steps(tmp_path, "windows", setup_repo.Options(), fake_probes())
    assert step_ids(steps) == ["hunyuan-source", "weights-only", "engine-blocked"]
    blocked = steps[-1]
    with pytest.raises(setup_repo.Blocked) as error:
        blocked.run(setup_repo.Context(repo=tmp_path, log=lambda line: None, runner=lambda c, log: None))
    assert "cross-platform-native" in str(error.value)


def test_windows_runs_engine_when_allowed(tmp_path: Path) -> None:
    write_pyproject(tmp_path, ["sys_platform == 'linux'", "sys_platform == 'win32'"])
    steps = setup_repo.build_steps(tmp_path, "windows", setup_repo.Options(), fake_probes())
    assert step_ids(steps) == [
        "hunyuan-source", "weights-only", "engine-env", "inpaint",
        "unirig-source", "unirig-env", "unirig-weights",
    ]


def test_macos_has_no_steps(tmp_path: Path) -> None:
    assert setup_repo.build_steps(tmp_path, "macos", setup_repo.Options(), fake_probes()) == []


def test_missing_dino_access_skips_and_continues(tmp_path: Path) -> None:
    probes = fake_probes(hf_access=False)
    trellis_weights = next(
        step for step in setup_repo.build_steps(tmp_path, "linux", setup_repo.Options(), probes)
        if step.id == "trellis-weights"
    )
    commands: list[Command] = []

    def runner(command: Command, log) -> None:
        commands.append(command)

    lines: list[str] = []
    later_ran: list[str] = []
    steps = [
        setup_repo.Step("before", "Before", lambda: False, lambda ctx: later_ran.append("before")),
        trellis_weights,
        setup_repo.Step("after", "After", lambda: False, lambda ctx: later_ran.append("after")),
    ]
    results = setup_repo.run_plan(steps, False, tmp_path, runner, lines.append)
    assert ("trellis-weights", "skipped") in results
    assert later_ran == ["before", "after"]
    assert any("huggingface-cli login" in line for line in lines)


def test_start_command(tmp_path: Path) -> None:
    xdg = tmp_path / "xdg"
    (xdg / "model3d").mkdir(parents=True)
    (xdg / "model3d/config.json").write_text(json.dumps({"install_dir": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "vendor/UniRig/.venv/Scripts").mkdir(parents=True)
    env = {"XDG_CONFIG_HOME": str(xdg)}
    command = setup_repo.start_command(tmp_path, env, "linux", setup_repo.Options(), trellis_ready=False)
    assert "XDG_CONFIG_HOME=" in command
    assert "MODEL3D_UNIRIG_DIR=$PWD/vendor/UniRig-linux" in command
    assert command.endswith("client/.venv/bin/python -m model3d_client")

    plain = setup_repo.start_command(tmp_path / "other", {}, "linux", setup_repo.Options(), trellis_ready=False)
    assert "XDG_CONFIG_HOME=" not in plain
    assert "MODEL3D_UNIRIG_DIR=" not in plain


def test_ubuntu_script_uses_sudo_only_for_apt() -> None:
    lines = (SETUP_DIR / "setup-ubuntu.sh").read_text(encoding="utf-8").splitlines()
    sudo_lines = [line.strip() for line in lines if re.search(r"(^|[\s;(!])sudo\s", line)]
    assert sudo_lines == [
        "if ! sudo -v; then",
        "sudo apt-get update",
        "sudo apt-get install -y \"${missing[@]}\"",
    ]
    assert "sudo" not in SCRIPT.read_text(encoding="utf-8")


def test_unirig_env_keeps_an_existing_venv(tmp_path: Path) -> None:
    python = tmp_path / "vendor/UniRig/.venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    (tmp_path / "vendor/UniRig/requirements.txt").write_text("numpy\n", encoding="utf-8")
    commands: list[Command] = []
    env_step = next(step for step in setup_repo.rig_steps(tmp_path, "linux", {}) if step.id == "unirig-env")
    env_step.run(setup_repo.Context(repo=tmp_path, log=lambda line: None, runner=lambda c, log: commands.append(c)))
    assert commands
    assert not any("venv" in command.argv for command in commands)
    assert "torch==2.9.1" in commands[0].argv


def test_unirig_env_creates_a_missing_venv(tmp_path: Path) -> None:
    (tmp_path / "vendor/UniRig").mkdir(parents=True)
    (tmp_path / "vendor/UniRig/requirements.txt").write_text("numpy\n", encoding="utf-8")
    commands: list[Command] = []
    env_step = next(step for step in setup_repo.rig_steps(tmp_path, "linux", {}) if step.id == "unirig-env")
    env_step.run(setup_repo.Context(repo=tmp_path, log=lambda line: None, runner=lambda c, log: commands.append(c)))
    assert commands[0].argv[:3] == ["uv", "venv", "--python"]


def test_wrappers_keep_dev_packages_and_skip_sync_on_dry_run() -> None:
    for name in ("setup-ubuntu.sh", "setup-macos.sh", "setup-windows.ps1"):
        text = (SETUP_DIR / name).read_text(encoding="utf-8")
        sync_lines = [line for line in text.splitlines() if "uv sync" in line]
        assert sync_lines, name
        assert all("--inexact" in line for line in sync_lines), name
        assert "--dry-run" in text, name


def test_scripts_have_the_right_line_endings() -> None:
    for script in SETUP_DIR.glob("*.sh"):
        assert b"\r\n" not in script.read_bytes(), script.name
