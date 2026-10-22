from pathlib import Path

from model3d_engine.stages import unirig_job


def test_classify_failure_spots_out_of_memory():
    assert unirig_job.classify_failure("torch.OutOfMemoryError: CUDA out of memory.") == "gpu_out_of_memory"
    assert unirig_job.classify_failure("RuntimeError: CUDA out of memory. Tried to allocate") == "gpu_out_of_memory"
    assert unirig_job.classify_failure("KeyError: 'joints'") == "rig_failed"


def test_step_commands_follow_unirig_launch_scripts(tmp_path):
    work = tmp_path / "work"
    steps = unirig_job.step_commands("/venv/python", Path("/in/model.glb"), tmp_path / "out.glb", work, "T")
    names = [name for name, _command, _output in steps]
    assert names == ["extract", "skeleton", "extract skin", "skin", "merge"]
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
    assert f"--output={tmp_path / 'out.glb'}" in commands["merge"]
    # run.py goes through the bootstrap that allowlists box.Box for torch.load.
    assert commands["skeleton"][1] == "-c" and "add_safe_globals" in commands["skeleton"][2]
    assert all("pip" not in part for _name, command, _output in steps for part in command)
    outputs = {name: output for name, _command, output in steps}
    assert outputs["skeleton"] == work / "skeleton.fbx"
    assert outputs["merge"] == tmp_path / "out.glb"
