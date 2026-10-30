from pathlib import Path
from types import SimpleNamespace

import pytest

from model3d_engine.stages import unirig_job


def _rig_with_fake_unirig(monkeypatch, tmp_path, smooth_outcome):
    """rig() with prepare_mesh and every UniRig command faked; the smooth step returns smooth_outcome(produced)."""
    monkeypatch.setattr(unirig_job, "prepare_mesh", lambda source, work, max_faces: source)
    real_step_commands = unirig_job.step_commands
    steps_by_command = {}
    kept = []

    def step_commands(python, mesh, output, work, stamp):
        steps = real_step_commands(python, mesh, output, work, stamp)
        kept.extend(steps)  # keeps the command lists alive so their ids stay unique
        for name, command, produced in steps:
            steps_by_command[id(command)] = (name, produced)
        return steps

    def run(command, cwd, capture_output, text):
        name, produced = steps_by_command[id(command)]
        if name != "smooth":
            produced.parent.mkdir(parents=True, exist_ok=True)
            produced.write_bytes(b"glb")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return smooth_outcome(produced)

    monkeypatch.setattr(unirig_job, "step_commands", step_commands)
    monkeypatch.setattr(unirig_job.subprocess, "run", run)
    return unirig_job.rig(tmp_path / "unirig", tmp_path / "in.glb", tmp_path / "out.glb", tmp_path / "work", 150000)


def test_classify_failure_spots_out_of_memory():
    assert unirig_job.classify_failure("torch.OutOfMemoryError: CUDA out of memory.") == "gpu_out_of_memory"
    assert unirig_job.classify_failure("RuntimeError: CUDA out of memory. Tried to allocate") == "gpu_out_of_memory"
    assert unirig_job.classify_failure("KeyError: 'joints'") == "rig_failed"


def test_step_commands_follow_unirig_launch_scripts(tmp_path):
    work = tmp_path / "work"
    steps = unirig_job.step_commands("/venv/python", Path("/in/model.glb"), tmp_path / "out.glb", work, "T")
    names = [name for name, _command, _output in steps]
    assert names == ["extract", "skeleton", "extract skin", "skin", "merge", "smooth"]
    commands = {name: command for name, command, _output in steps}
    assert commands["extract"][:3] == ["/venv/python", "-m", "src.data.extract"]
    assert "--faces_target_count=50000" in commands["extract"]
    assert "--input=/in/model.glb" in commands["extract"]
    assert "--task=configs/task/quick_inference_skeleton_articulationxl_ar_256.yaml" in commands["skeleton"]
    assert "--seed=12345" in commands["skeleton"]
    assert f"--output={work / 'skeleton.fbx'}" in commands["skeleton"]
    assert f"--input={work / 'skeleton.fbx'}" in commands["extract skin"]
    assert "--task=configs/task/quick_inference_unirig_skin.yaml" in commands["skin"]
    assert "--data_name=raw_data.npz" in commands["skin"]
    assert f"--source={work / 'skin.fbx'}" in commands["merge"]
    assert "--target=/in/model.glb" in commands["merge"]
    assert f"--output={work / 'merged.glb'}" in commands["merge"]
    assert commands["smooth"] == [
        "/venv/python",
        str(unirig_job.SMOOTH_SCRIPT),
        str(work / "merged.glb"),
        str(tmp_path / "out.glb"),
    ]
    # run.py goes through the bootstrap that allowlists box.Box for torch.load.
    assert commands["skeleton"][1] == "-c" and "add_safe_globals" in commands["skeleton"][2]
    assert all("pip" not in part for _name, command, _output in steps for part in command)
    outputs = {name: output for name, _command, output in steps}
    assert outputs["skeleton"] == work / "skeleton.fbx"
    assert outputs["merge"] == work / "merged.glb"
    assert outputs["smooth"] == tmp_path / "out.glb"


def test_progress_only_rises_and_reaches_the_end(tmp_path):
    steps = unirig_job.step_commands("/venv/python", Path("/in/model.glb"), tmp_path / "out.glb", tmp_path / "work", "T")
    names = [name for name, _command, _output in steps]
    assert all(name in unirig_job.STEP_PROGRESS for name in names)
    progress = [unirig_job.STEP_PROGRESS[name][0] for name in names]
    assert all(later > earlier for earlier, later in zip(progress, progress[1:]))
    assert progress[-1] == 1.0


def test_failed_smoothing_fails_the_job_by_name(monkeypatch, tmp_path):
    def smooth_fails(produced):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    with pytest.raises(unirig_job.JobError) as raised:
        _rig_with_fake_unirig(monkeypatch, tmp_path, smooth_fails)
    assert raised.value.code == "rig_failed"
    assert "smooth" in raised.value.message
    assert not (tmp_path / "out.glb").exists()


def test_smoothing_without_a_file_fails_the_job(monkeypatch, tmp_path):
    def smooth_writes_nothing(produced):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with pytest.raises(unirig_job.JobError) as raised:
        _rig_with_fake_unirig(monkeypatch, tmp_path, smooth_writes_nothing)
    assert raised.value.code == "rig_failed"
    assert "smooth" in raised.value.message
    assert not (tmp_path / "out.glb").exists()


def test_extract_outputs_follow_unirig_path_rule(tmp_path):
    """UniRig saves to os.path.join(npz_dir, input without suffix): beside an absolute input."""
    work = tmp_path / "work"
    steps = unirig_job.step_commands("/venv/python", tmp_path / "job/rig_input.glb", tmp_path / "out.glb", work, "T")
    outputs = {name: output for name, _command, output in steps}
    assert outputs["extract"] == tmp_path / "job/rig_input/raw_data.npz"
    assert outputs["extract skin"] == work / "skeleton/raw_data.npz"
