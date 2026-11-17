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
