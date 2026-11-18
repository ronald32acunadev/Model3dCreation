"""Tests for candidate selection in kit assembly (US-01)."""

from kit import MAX_HEAD_GAP, MIN_VOLUME_RATIO, select_candidate


class FakeField:
    def __init__(self, name: str):
        self.name = name


def test_select_candidate_prefers_ratio_closest_to_one():
    # Candidate 1 has ratio 0.9994 (near perfect)
    # Candidate 2 has ratio 2.75 (bloated)
    c1 = ("head_bust.glb", FakeField("head_bust"), 0.9994)
    c2 = ("head_bust@11.glb", FakeField("head_bust@11"), 2.75)
    best = select_candidate([c1, c2], align_data=None, part="head")
    assert best[0] == "head_bust.glb"
    assert best[2] == 0.9994


def test_select_candidate_prefers_aligned_candidate():
    # Both candidates have acceptable volume, but c2 exceeds MAX_HEAD_GAP
    c1 = ("head_bust.glb", FakeField("head_bust"), 1.05)
    c2 = ("head_bust@11.glb", FakeField("head_bust@11"), 1.02)
    align_data = {
        "head_bust": {"head": {"mean_distance": 0.005, "reverse_mean_distance": 0.005}},  # 1.0 cm gap
        "head_bust@11": {"head": {"mean_distance": 0.02, "reverse_mean_distance": 0.02}},  # 4.0 cm gap > MAX_HEAD_GAP
    }
    best = select_candidate([c1, c2], align_data=align_data, part="head")
    assert best[0] == "head_bust.glb"


def test_select_candidate_empty_list():
    best = select_candidate([], align_data=None, part="head")
    assert best == (None, None, 0.0)


def test_select_candidate_below_min_volume():
    c1 = ("head_bust.glb", FakeField("head_bust"), 0.2)
    best = select_candidate([c1], align_data=None, part="head")
    assert best[0] == "head_bust.glb"
    assert best[2] == 0.2
    assert best[2] < MIN_VOLUME_RATIO
