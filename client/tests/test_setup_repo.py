"""Tests for scripts/setup/setup_repo.py: preflight, step plan, dry run, start command, and wrapper rules."""

import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable
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
DINO_REPO = "facebook/dinov3-vitl16-pretrain-lvd1689m"
DINO_URL = f"https://huggingface.co/{DINO_REPO}"


def fake_probes(
    *,
    gpu: bool = True,
    free: int = 500 * GB,
    hf_access: bool | Callable[[str], bool] = True,
    token_valid: Callable[[str], bool] = lambda token: False,
):
    def run(argv: list[str]) -> str:
        if not gpu:
            raise subprocess.CalledProcessError(1, argv)
        return "NVIDIA GeForce RTX 5070\n"

    access = hf_access if callable(hf_access) else (lambda repo_id: hf_access)
    return setup_repo.Probes(
        run=run, disk_free=lambda path: free, has_hf_access=access, token_valid=token_valid
    )


def write_pyproject(repo: Path, environments: list[str] | None) -> None:
    (repo / "engine").mkdir(parents=True, exist_ok=True)
    text = "[project]\nname = 'engine'\n"
    if environments is not None:
        entries = ", ".join(f'"{entry}"' for entry in environments)
        text += f"\n[tool.uv]\nenvironments = [{entries}]\n"
    (repo / "engine/pyproject.toml").write_text(text, encoding="utf-8")


def step_ids(steps) -> list[str]:
    return [step.id for step in steps]


def fake_prompter(secrets: list[str], answers: list[str] | None = None, interactive: bool = True):
    secrets_left = list(secrets)
    answers_left = list(answers or [])
    out_lines: list[str] = []
    asked: list[str] = []

    def secret(prompt: str) -> str:
        asked.append(prompt)
        if not secrets_left:
            raise AssertionError("unexpected prompt")
        return secrets_left.pop(0)

    def ask(prompt: str) -> str:
        asked.append(prompt)
        if not answers_left:
            raise AssertionError("unexpected prompt")
        return answers_left.pop(0)

    prompter = setup_repo.Prompter(
        interactive=lambda: interactive, secret=secret, ask=ask, out=out_lines.append
    )
    return prompter, out_lines, asked


def write_addon_manifest(repo: Path, version: str) -> None:
    (repo / "blender/model3d").mkdir(parents=True, exist_ok=True)
    (repo / "blender/model3d/blender_manifest.toml").write_text(
        f'id = "model3d"\nversion = "{version}"\n', encoding="utf-8"
    )


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
        "cuda-toolkit", "hunyuan-source", "engine-env", "inpaint", "dev-tools", "weights",
        "unirig-source", "unirig-env", "unirig-weights",
        "trellis-source", "trellis-env", "trellis-weights",
        "blender", "blender-addon",
    ]


def test_no_rig_and_no_trellis_drop_their_steps(tmp_path: Path) -> None:
    options = setup_repo.Options(rig=False, trellis=False)
    steps = setup_repo.build_steps(tmp_path, "linux", options, fake_probes())
    assert step_ids(steps) == [
        "cuda-toolkit", "hunyuan-source", "engine-env", "inpaint", "dev-tools", "weights",
        "blender", "blender-addon",
    ]


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
    packages_line = next(line for line in lines if line.strip().startswith("PACKAGES=("))
    tokens = re.findall(r"[\w.+-]+", packages_line)
    for package in (
        "python3-pyside6.qtwidgets", "python3-pyside6.qtwebenginewidgets",
        "python3-pyside6.qtwebsockets", "python3-pyside6.qttest", "python3-pydantic",
        "libgl1", "libsm6", "libx11-6", "libxext6", "libxfixes3", "libxi6", "libxrender1",
    ):
        assert package in tokens


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


def test_dev_tools_syncs_every_project_keeping_packages(tmp_path: Path) -> None:
    env = {"UV_PYTHON": "3.11", "PATH": "/usr/bin"}
    step = setup_repo.dev_tools_step(tmp_path, env)
    commands: list[Command] = []
    step.run(setup_repo.Context(repo=tmp_path, log=lambda line: None, runner=lambda c, log: commands.append(c)))
    assert step.id == "dev-tools"
    assert [(command.argv, command.cwd, command.env) for command in commands] == [
        (["uv", "sync", "--frozen", "--inexact"], tmp_path / project, env)
        for project in ("contracts", "engine", "client", "blender", "packaging")
    ]


def test_dev_tools_done_only_when_every_dev_package_is_present(tmp_path: Path) -> None:
    for project in ("contracts", "engine", "client", "blender", "packaging"):
        names = ['"pytest"', '"pytest-qt>=4.4"'] if project == "client" else ['"pytest"']
        deps = ", ".join(names)
        (tmp_path / project).mkdir(parents=True)
        (tmp_path / project / "pyproject.toml").write_text(
            f'[project]\nname = "{project}"\n\n[dependency-groups]\ndev = [{deps}]\n', encoding="utf-8"
        )
        site = tmp_path / project / ".venv/lib/python3.11/site-packages"
        site.mkdir(parents=True)
        (site / "pytest-8.3.0.dist-info").mkdir()
    step = setup_repo.dev_tools_step(tmp_path, {})
    assert step.done() is False
    (tmp_path / "client/.venv/lib/python3.11/site-packages/pytest_qt-4.4.0.dist-info").mkdir()
    assert step.done() is True


def test_dev_tools_done_finds_packages_for_any_python(tmp_path: Path) -> None:
    for project in ("contracts", "engine", "client", "blender", "packaging"):
        deps = '"pytest", "pytest-qt"' if project == "client" else '"pytest"'
        (tmp_path / project).mkdir(parents=True)
        (tmp_path / project / "pyproject.toml").write_text(
            f'[project]\nname = "{project}"\n\n[dependency-groups]\ndev = [{deps}]\n', encoding="utf-8"
        )
        python = "3.14" if project == "client" else "3.11"
        site = tmp_path / project / f".venv/lib/python{python}/site-packages"
        site.mkdir(parents=True)
        if project == "client":
            (site / "pytest-9.1.1.dist-info").mkdir()
            (site / "pytest_qt-4.4.0.dist-info").mkdir()
        else:
            (site / "pytest-8.3.0.dist-info").mkdir()
    assert setup_repo.dev_tools_step(tmp_path, {}).done() is True


def test_token_prompt_saves_a_valid_token_privately(tmp_path: Path) -> None:
    token_path = tmp_path / "secrets/hf_token"
    probes = fake_probes(hf_access=True, token_valid=lambda token: token == "hf_good")
    prompter, out_lines, asked = fake_prompter(["hf_good"])
    assert setup_repo.request_hf_access(DINO_REPO, probes, prompter, token_path) is True
    assert token_path.read_text(encoding="utf-8") == "hf_good"
    assert stat.S_IMODE(os.stat(token_path).st_mode) == 0o600
    assert all("hf_good" not in line for line in out_lines + asked)


def test_rejected_token_is_asked_again_at_most_three_times(tmp_path: Path) -> None:
    token_path = tmp_path / "secrets/hf_token"
    prompter, out_lines, asked = fake_prompter(["bad1", "bad2", "bad3"])
    assert setup_repo.request_hf_access(DINO_REPO, fake_probes(), prompter, token_path) is False
    assert len(asked) == 3
    assert out_lines.count("Hugging Face rejected that token.") == 3
    assert not token_path.exists()


def test_empty_token_skips(tmp_path: Path) -> None:
    token_path = tmp_path / "secrets/hf_token"
    prompter, _, asked = fake_prompter([""])
    assert setup_repo.request_hf_access(DINO_REPO, fake_probes(), prompter, token_path) is False
    assert len(asked) == 1
    assert not token_path.exists()


def test_valid_token_without_access_offers_to_check_again_or_skip(tmp_path: Path) -> None:
    token_path = tmp_path / "secrets/hf_token"
    access = iter([False, False, True])
    probes = fake_probes(
        hf_access=lambda repo_id: next(access), token_valid=lambda token: token == "hf_good"
    )
    prompter, out_lines, asked = fake_prompter(["hf_good"], ["", ""])
    assert setup_repo.request_hf_access(DINO_REPO, probes, prompter, token_path) is True
    assert len(asked) == 3
    assert any(DINO_URL in line for line in out_lines)


def test_skip_answer_stops_waiting_for_access(tmp_path: Path) -> None:
    token_path = tmp_path / "secrets/hf_token"
    probes = fake_probes(hf_access=False, token_valid=lambda token: token == "hf_good")
    prompter, _, asked = fake_prompter(["hf_good"], ["s"])
    assert setup_repo.request_hf_access(DINO_REPO, probes, prompter, token_path) is False
    assert len(asked) == 2


def test_saved_valid_token_is_not_asked_again(tmp_path: Path) -> None:
    token_path = tmp_path / "secrets/hf_token"
    token_path.parent.mkdir(parents=True)
    token_path.write_text("hf_saved", encoding="utf-8")
    probes = fake_probes(hf_access=True, token_valid=lambda token: token == "hf_saved")
    prompter, _, asked = fake_prompter([])
    assert setup_repo.request_hf_access(DINO_REPO, probes, prompter, token_path) is True
    assert asked == []


def test_no_terminal_never_prompts(tmp_path: Path) -> None:
    prompter, _, asked = fake_prompter([], interactive=False)
    probes = fake_probes(hf_access=False)
    assert setup_repo.request_hf_access(DINO_REPO, probes, prompter, tmp_path / "secrets/hf_token") is False
    assert asked == []


def test_dry_run_never_asks_for_a_token(tmp_path: Path) -> None:
    prompter, _, asked = fake_prompter([])
    result = setup_repo.main(
        ["--dry-run"],
        repo=tmp_path,
        platform="linux",
        probes=fake_probes(hf_access=False),
        runner=lambda command, log: None,
        prompter=prompter,
    )
    assert result == 0
    assert asked == []


def test_save_token_tightens_an_existing_file(tmp_path: Path) -> None:
    token_path = tmp_path / "hf_token"
    token_path.write_text("hf_old", encoding="utf-8")
    token_path.chmod(0o644)
    setup_repo.save_token(token_path, "hf_new")
    assert token_path.read_text(encoding="utf-8") == "hf_new"
    assert stat.S_IMODE(os.stat(token_path).st_mode) == 0o600


def test_blender_done_needs_the_pinned_release(tmp_path: Path) -> None:
    write_addon_manifest(tmp_path, "0.1.0")
    blender = setup_repo.blender_steps(tmp_path, setup_repo.Options(blender_config=tmp_path / "cfg"))[0]
    assert blender.id == "blender"
    assert blender.done() is False
    binary = tmp_path / "vendor/blender/blender"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    marker = tmp_path / "vendor/blender/.model3d-release"
    marker.write_text("4.5.14", encoding="utf-8")
    assert blender.done() is False
    marker.write_text("4.5.15", encoding="utf-8")
    assert blender.done() is True


def test_blender_checksum_mismatch_deletes_only_the_archive(tmp_path: Path) -> None:
    write_addon_manifest(tmp_path, "0.1.0")
    downloads = tmp_path / "data/downloads"
    downloads.mkdir(parents=True)
    archive = downloads / "blender-4.5.15-linux-x64.tar.xz"
    archive.write_bytes(b"not blender")
    keep = downloads / "keep.txt"
    keep.write_text("keep", encoding="utf-8")
    blender = setup_repo.blender_steps(tmp_path, setup_repo.Options(blender_config=tmp_path / "cfg"))[0]
    with pytest.raises(setup_repo.CommandFailed):
        blender.run(setup_repo.Context(repo=tmp_path, log=lambda line: None, runner=lambda c, log: None))
    assert not archive.exists()
    assert keep.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "vendor").exists()


def test_blender_addon_builds_then_installs(tmp_path: Path) -> None:
    write_addon_manifest(tmp_path, "0.1.0")
    addon = setup_repo.blender_steps(tmp_path, setup_repo.Options(blender_config=tmp_path / "cfg"))[1]
    commands: list[Command] = []
    addon.run(setup_repo.Context(repo=tmp_path, log=lambda line: None, runner=lambda c, log: commands.append(c)))
    blender = str(tmp_path / "vendor/blender/blender")
    assert [command.argv for command in commands] == [
        [
            blender, "--command", "extension", "build",
            "--source-dir", str(tmp_path / "blender/model3d"),
            "--output-dir", str(tmp_path / "data/downloads"),
        ],
        [
            blender, "--command", "extension", "install-file", "-r", "user_default",
            "-e", str(tmp_path / "data/downloads" / "model3d-0.1.0.zip"),
        ],
    ]


def test_blender_addon_done_matches_the_source_version(tmp_path: Path) -> None:
    write_addon_manifest(tmp_path, "0.1.0")
    addon = setup_repo.blender_steps(tmp_path, setup_repo.Options(blender_config=tmp_path / "cfg"))[1]
    installed = tmp_path / "cfg/4.5/extensions/user_default/model3d/blender_manifest.toml"
    installed.parent.mkdir(parents=True)
    installed.write_text('id = "model3d"\nversion = "0.0.9"\n', encoding="utf-8")
    assert addon.done() is False
    installed.write_text('id = "model3d"\nversion = "0.1.0"\n', encoding="utf-8")
    assert addon.done() is True
