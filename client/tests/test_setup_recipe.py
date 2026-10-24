import http.server
import io
import subprocess
import tarfile
import threading
from pathlib import Path

import pytest

from model3d_client.setup.actions import apply_patch, download, extract_tarball, patch_file
from model3d_client.setup.checks import Toolchain
from model3d_client.setup.recipe import HUNYUAN_COMMIT, UNIRIG_COMMIT, Sources, build_recipe
from model3d_client.setup.runner import Command, StepContext

FOLDER = Path("/media/pumba/Disco 2/Modelos 3D")
TOOLCHAIN = Toolchain(nvcc=Path("/usr/local/cuda-13.1/bin/nvcc"), cuda_home=Path("/usr/local/cuda-13.1"),
                      cuda_version=(13, 1), arch_list="12.0", max_jobs=16)


@pytest.fixture
def sources(tmp_path):
    root = tmp_path / "opt"
    (root / "engine").mkdir(parents=True)
    (root / "engine/uv.lock").write_text("lock v1")
    return Sources(app_root=root, uv=root / "bin/uv", app_version="0.1.0")


def commands(step) -> list[Command]:
    return [action for action in step.actions if isinstance(action, Command)]


def by_id(steps):
    return {step.id: step for step in steps}


def test_base_recipe_ids_in_order(sources):
    steps = build_recipe(sources, FOLDER, TOOLCHAIN, ["base"])
    assert [s.id for s in steps] == ["python", "source", "hunyuan", "engine-env", "inpaint", "weights", "verify"]
    assert {s.component for s in steps} == {"base"}


def test_skeletons_appends_unirig_steps(sources):
    steps = build_recipe(sources, FOLDER, TOOLCHAIN, ["base", "skeletons"])
    assert [s.id for s in steps][-3:] == ["unirig-source", "unirig-env", "unirig-weights"]
    assert {s.component for s in steps[-3:]} == {"skeletons"}


def test_pinned_commits():
    assert HUNYUAN_COMMIT == "82920d643c0dc2f7bfd7255f45f62d386edfe60c"
    assert UNIRIG_COMMIT == "6793c6640ff01c8fb389f3993434124bb43d2933"


def test_build_steps_get_toolchain_env(sources):
    steps = by_id(build_recipe(sources, FOLDER, TOOLCHAIN, ["base", "skeletons"]))
    for step_id in ("engine-env", "unirig-env"):
        for command in commands(steps[step_id]):
            assert command.env["CUDA_HOME"] == "/usr/local/cuda-13.1"
            assert command.env["MAX_JOBS"] == "16"
    assert all(c.env["TORCH_CUDA_ARCH_LIST"] == "12.0" for c in commands(steps["engine-env"]))
    flash = [c for c in commands(steps["unirig-env"]) if "flash-attn" in c.argv]
    assert flash and flash[0].env["FLASH_ATTN_CUDA_ARCHS"] == "120"


def test_engine_env_syncs_twice_frozen_in_the_engine_folder(sources):
    syncs = commands(by_id(build_recipe(sources, FOLDER, TOOLCHAIN, ["base"]))["engine-env"])
    assert [c.argv[:2] for c in syncs] == [[str(sources.uv), "sync"], [str(sources.uv), "sync"]]
    assert "--no-install-package" in syncs[0].argv and "custom-rasterizer" in syncs[0].argv
    assert all("--frozen" in c.argv for c in syncs)
    assert all(c.cwd == FOLDER / "engine" for c in syncs)


def test_uv_steps_install_python_into_the_folder(sources):
    steps = build_recipe(sources, FOLDER, TOOLCHAIN, ["base", "skeletons"])
    uv_commands = [c for s in steps for c in commands(s) if c.argv[0] == str(sources.uv)]
    assert uv_commands
    for command in uv_commands:
        assert command.env["UV_PYTHON_INSTALL_DIR"] == str(FOLDER / "python")
        assert command.env["UV_CACHE_DIR"] == str(FOLDER / "data/uv-cache")


def test_paths_with_spaces_stay_single_arguments(sources):
    steps = build_recipe(sources, FOLDER, TOOLCHAIN, ["base", "skeletons"])
    for step in steps:
        for command in commands(step):
            for argument in command.argv:
                if " " in argument and not argument.startswith(("import", "from")):
                    assert str(FOLDER) in argument  # a path, passed whole


def test_lock_change_changes_engine_env_fingerprint(sources):
    before = by_id(build_recipe(sources, FOLDER, TOOLCHAIN, ["base"]))
    (sources.app_root / "engine/uv.lock").write_text("lock v2")
    after = by_id(build_recipe(sources, FOLDER, TOOLCHAIN, ["base"]))
    assert before["engine-env"].fingerprint != after["engine-env"].fingerprint
    assert before["weights"].fingerprint == after["weights"].fingerprint


def test_arch_change_changes_build_fingerprints(sources):
    other = Toolchain(TOOLCHAIN.nvcc, TOOLCHAIN.cuda_home, (13, 1), "8.6", 16)
    first = by_id(build_recipe(sources, FOLDER, TOOLCHAIN, ["base"]))
    second = by_id(build_recipe(sources, FOLDER, other, ["base"]))
    assert first["engine-env"].fingerprint != second["engine-env"].fingerprint
    assert first["hunyuan"].fingerprint == second["hunyuan"].fingerprint


def test_done_checks_read_files_only(sources, tmp_path):
    folder = tmp_path / "install"
    steps = by_id(build_recipe(sources, folder, TOOLCHAIN, ["base"]))
    assert not steps["python"].done_check(folder)
    (folder / "python/cpython-3.11.17-linux-x86_64-gnu").mkdir(parents=True)
    assert steps["python"].done_check(folder)


def context(tmp_path, cancelled=lambda: False) -> StepContext:
    def run(command: Command) -> None:
        subprocess.run(command.argv, cwd=command.cwd, check=True)

    return StepContext(install_dir=tmp_path, log=lambda line: None, cancelled=cancelled, run=run)


def make_tarball(path: Path, files: dict[str, bytes], top="Repo-abc123") -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def test_extract_tarball_strips_the_top_folder(tmp_path):
    archive = tmp_path / "repo.tar.gz"
    make_tarball(archive, {"run.py": b"print(1)", "pkg/a.txt": b"a"})
    target = tmp_path / "vendor/UniRig"
    extract_tarball(archive, target, context(tmp_path))
    assert (target / "run.py").read_bytes() == b"print(1)"
    assert (target / "pkg/a.txt").is_file()


def test_extract_tarball_is_atomic(tmp_path):
    archive = tmp_path / "broken.tar.gz"
    make_tarball(archive, {"run.py": b"x" * 100_000})
    archive.write_bytes(archive.read_bytes()[:200])  # truncated download
    target = tmp_path / "vendor/UniRig"
    with pytest.raises(Exception):
        extract_tarball(archive, target, context(tmp_path))
    assert not target.exists()


def test_extract_tarball_replaces_an_older_copy(tmp_path):
    archive = tmp_path / "repo.tar.gz"
    make_tarball(archive, {"new.txt": b"new"})
    target = tmp_path / "vendor/UniRig"
    (target / "old.txt").parent.mkdir(parents=True)
    (target / "old.txt").write_text("old")
    extract_tarball(archive, target, context(tmp_path))
    assert (target / "new.txt").is_file() and not (target / "old.txt").exists()


class RangeHandler(http.server.BaseHTTPRequestHandler):
    payload = bytes(range(256)) * 400
    ranges: list[str] = []

    def do_GET(self):
        header = self.headers.get("Range")
        RangeHandler.ranges.append(header or "")
        start = int(header.removeprefix("bytes=").split("-")[0]) if header else 0
        body = self.payload[start:]
        self.send_response(206 if header else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    RangeHandler.ranges = []
    yield f"http://127.0.0.1:{httpd.server_address[1]}/file.bin"
    httpd.shutdown()


def test_download_resumes_from_part_file(tmp_path, server):
    target = tmp_path / "downloads/file.bin"
    target.parent.mkdir()
    target.with_name("file.bin.part").write_bytes(RangeHandler.payload[:1000])
    download(server, target, context(tmp_path))
    assert target.read_bytes() == RangeHandler.payload
    assert RangeHandler.ranges == ["bytes=1000-"]
    assert not target.with_name("file.bin.part").exists()


def test_download_skips_a_finished_file(tmp_path, server):
    target = tmp_path / "file.bin"
    target.write_bytes(b"done")
    download(server, target, context(tmp_path))
    assert target.read_bytes() == b"done"
    assert RangeHandler.ranges == []


PATCH = """--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-hello
+hello cuda
"""


def test_apply_patch_twice_is_a_noop(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("hello\n")
    patch = tmp_path / "fix.patch"
    patch.write_text(PATCH)
    apply_patch(patch, repo, context(tmp_path))
    apply_patch(patch, repo, context(tmp_path))
    assert (repo / "a.txt").read_text() == "hello cuda\n"


def test_patch_file_replaces_a_symlink_with_a_patched_copy(tmp_path):
    blob = tmp_path / "blobs/abc"
    blob.parent.mkdir()
    blob.write_text("hello\n")
    link = tmp_path / "snapshot/a.txt"
    link.parent.mkdir()
    link.symlink_to(blob)
    patch = tmp_path / "fix.patch"
    patch.write_text(PATCH)
    patch_file(patch, link, context(tmp_path))
    patch_file(patch, link, context(tmp_path))
    assert not link.is_symlink()
    assert link.read_text() == "hello cuda\n"
    assert blob.read_text() == "hello\n"  # the shared blob stays untouched


def test_unirig_weights_fetch_the_opt_config_the_skeleton_model_loads(sources, tmp_path):
    # UniRig's skeleton model calls AutoConfig.from_pretrained("facebook/opt-350m") with the hub offline.
    folder = tmp_path / "install"
    step = by_id(build_recipe(sources, folder, TOOLCHAIN, ["base", "skeletons"]))["unirig-weights"]
    assert "facebook/opt-350m" in commands(step)[0].argv[-1]
    for name in ("skeleton/articulation-xl_quantization_256/model.ckpt", "skin/articulation-xl/model.ckpt"):
        path = folder / "data/hf/hub/models--VAST-AI--UniRig/snapshots/s" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    assert not step.done_check(folder)  # the checkpoints alone are not enough
    config = folder / "data/hf/hub/models--facebook--opt-350m/snapshots/s/config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}")
    assert step.done_check(folder)


def test_verify_fails_with_gpu_busy_when_no_mode_fits(sources, tmp_path):
    from model3d_client.setup.runner import StepFailed

    verify = by_id(build_recipe(sources, tmp_path, TOOLCHAIN, ["base"]))["verify"].actions[0]
    ran = []

    def run(command: Command) -> None:
        ran.append(command.argv)
        if command.argv[1] == "-c":  # the mode check: no GPU has enough free VRAM
            raise StepFailed("command_failed", "python exited with 1", "")

    ctx = StepContext(install_dir=tmp_path, log=lambda line: None, cancelled=lambda: False, run=run)
    with pytest.raises(StepFailed) as failure:
        verify(ctx)
    assert failure.value.code == "gpu_busy"
    assert not (tmp_path / "data/logs/verify.ok").exists()
    assert ran[0][1].endswith("verify_gpus.py") and ran[1][1] == "-c"


def test_verify_passes_when_a_mode_fits(sources, tmp_path):
    verify = by_id(build_recipe(sources, tmp_path, TOOLCHAIN, ["base"]))["verify"].actions[0]
    ctx = StepContext(install_dir=tmp_path, log=lambda line: None, cancelled=lambda: False, run=lambda command: None)
    verify(ctx)
    assert (tmp_path / "data/logs/verify.ok").is_file()
