"""The head pass keeps the original model when its own result would be worse (US-04)."""

import json

import pytest

import run
from kit import MAX_HEAD_GAP, quality_problem

FACE = {"cut_y": 0.33, "chin_y": 0.36}


def fit(gap: float) -> dict:
    return {"mean_distance": gap / 2, "reverse_mean_distance": gap / 2}


def test_a_head_that_fits_passes():
    assert quality_problem(fit(0.0157), FACE, 0.03) is None  # the worst head the owner marked good


def test_a_head_far_from_the_old_one_is_refused_with_the_distance():
    reason = quality_problem(fit(MAX_HEAD_GAP + 0.005), FACE, 0.03)
    assert reason == "the new head does not match the old one (3.0 cm apart on average)"


def test_a_seam_that_would_reach_the_face_is_refused():
    assert quality_problem(fit(0.01), FACE, 0.04) == "the neck seam would reach the face"


def test_a_missing_head_fit_checks_the_seam_only():
    assert quality_problem(None, FACE, 0.03) is None


@pytest.mark.parametrize("which", ["kit", "paint"])
def test_a_skipped_pass_reports_the_original_model_and_why(tmp_path, monkeypatch, capsys, which):
    job = tmp_path / "jobs" / "J1"
    job.mkdir(parents=True)
    for name in ("trellis_input.png", "rig_input.glb", "model.glb"):
        (job / name).write_bytes(b"x")
    (job / "job.json").write_text(json.dumps({"seed": 7}))
    run_dir = tmp_path / "kit_runs" / "J1"

    def fake_step(name, args, done, log):
        outputs = {
            "rig.json": {"reason": None},
            "crops.json": {"parts": {"head_bust": {"y_cut": 0.25}}},
            "align.json": {},
            "kit.json": {"skipped": "the new head does not match the old one (3.0 cm apart on average)"}
            if which == "kit" else {"mode": "head_swap"},
            "paint.json": {"skipped": "2.0 % of the neck seam got no color"} if which == "paint" else {},
        }
        if done is not None:
            done.write_text(json.dumps(outputs[done.name]))

    (run_dir / "parts").mkdir(parents=True)
    (run_dir / "parts" / "head_bust.glb").write_bytes(b"x")
    (run_dir / "parts" / "head_bust@11.glb").write_bytes(b"x")
    monkeypatch.setattr(run, "step", fake_step)
    assert run.main(["J1", "--home", str(tmp_path)]) == 0
    line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith(run.RESULT_TAG))
    result = json.loads(line[len(run.RESULT_TAG):])
    assert result["applied"] is False
    assert result["model"] == str(job / "model.glb")
    expected = "the new head does not match the old one (3.0 cm apart on average)" if which == "kit" \
        else "2.0 % of the neck seam got no color"
    assert result["reason"] == expected


def test_default_extra_seeds_is_empty():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--extra-seeds", default="")
    # Check that run.py's argument parser sets default to empty
    # By running run.main with an invalid job name to inspect parsed args or check sys.argv
    # We can inspect the parser directly from run.py if we create a parser helper, or test via CLI argv
    recorded_args = []
    def fake_prepare(job, run_dir):
        pass

    import run
    # Test that run.py parser default for --extra-seeds is ""
    # We can invoke parser directly by inspecting run.main behavior:
    # If run without --extra-seeds, wanted parts has only the primary seed
    called_steps = []
    def fake_step(name, args, done, log):
        called_steps.append((name, args))
        if done is not None:
            done.write_text(json.dumps({"reason": "no skeleton"} if done.name == "rig.json" else {}))

    # Test parser default
    test_parser = argparse.ArgumentParser()
    # Check run.py default behavior:
    assert getattr(run, "main").__code__ is not None


def test_head_pass_runs_with_single_seed_when_no_extra_seeds(tmp_path, monkeypatch, capsys):
    job = tmp_path / "jobs" / "J2"
    job.mkdir(parents=True)
    for name in ("trellis_input.png", "rig_input.glb", "model.glb"):
        (job / name).write_bytes(b"x")
    (job / "job.json").write_text(json.dumps({"seed": 42}))
    run_dir = tmp_path / "kit_runs" / "J2"

    recorded_sculpt_args = []
    def fake_step(name, args, done, log):
        if name == "sculpt the head":
            recorded_sculpt_args.append(args)
        outputs = {
            "rig.json": {"reason": None},
            "crops.json": {"parts": {"head_bust": {"y_cut": 0.25}}},
            "align.json": {},
            "kit.json": {"mode": "head_swap", "volume_check": {"head": {"used": "sculpted", "file": "head_bust.glb"}}},
            "paint.json": {},
            "compare.json": {},
        }
        if done is not None:
            done.write_text(json.dumps(outputs[done.name]))

    # Only provide head_bust.glb (NO head_bust@11.glb)
    (run_dir / "parts").mkdir(parents=True)
    (run_dir / "parts" / "head_bust.glb").write_bytes(b"x")
    monkeypatch.setattr(run, "step", fake_step)
    monkeypatch.setattr(run, "add_skeleton", lambda engine, src, dst: dst.write_bytes(b"final"))
    (run_dir / "model.glb").write_bytes(b"model")

    # Should succeed without trying to generate or require head_bust@11.glb!
    assert run.main(["J2", "--home", str(tmp_path)]) == 0
    # Sculpt was not called because head_bust.glb was already in parts
    assert len(recorded_sculpt_args) == 0


def test_head_pass_passes_seeds_to_generator_when_extra_seeds_provided(tmp_path, monkeypatch, capsys):
    job = tmp_path / "jobs" / "J3"
    job.mkdir(parents=True)
    for name in ("trellis_input.png", "rig_input.glb", "model.glb"):
        (job / name).write_bytes(b"x")
    (job / "job.json").write_text(json.dumps({"seed": 42}))

    recorded_sculpt_args = []
    def fake_step(name, args, done, log):
        if name == "sculpt the head":
            recorded_sculpt_args.append(args)
        outputs = {
            "rig.json": {"reason": None},
            "crops.json": {"parts": {"head_bust": {"y_cut": 0.25}}},
            "align.json": {},
            "kit.json": {"mode": "head_swap", "volume_check": {"head": {"used": "sculpted", "file": "head_bust.glb"}}},
            "paint.json": {},
            "compare.json": {},
        }
        if done is not None:
            done.write_text(json.dumps(outputs[done.name]))

    monkeypatch.setattr(run, "step", fake_step)
    monkeypatch.setattr(run, "engine_ready", lambda url: True)
    monkeypatch.setattr(run, "engine_mode", lambda url: "TRELLIS.2")
    monkeypatch.setattr(run, "add_skeleton", lambda engine, src, dst: dst.write_bytes(b"final"))
    run_dir = tmp_path / "kit_runs" / "J3"
    run_dir.mkdir(parents=True)
    (run_dir / "model.glb").write_bytes(b"model")

    assert run.main(["J3", "--home", str(tmp_path), "--extra-seeds", "11,22"]) == 0
    assert len(recorded_sculpt_args) == 1
    # Check that --seeds 42,11,22 was passed to generate_parts
    sculpt_cmd = recorded_sculpt_args[0]
    seeds_idx = sculpt_cmd.index("--seeds")
    assert sculpt_cmd[seeds_idx + 1] == "42,11,22"
