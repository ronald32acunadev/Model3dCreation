"""Seam colors: a texel the mold bake missed never stays black."""

import numpy as np

from paint import combine

SKIN = [0.8, 0.6, 0.5, 1.0]
SHIRT = [0.1, 0.2, 0.6, 1.0]
MISS = [0.0, 0.0, 0.0, 0.0]


def image(rows):
    return np.array(rows, np.float32)


def test_a_texel_only_the_part_reached_takes_the_part_color_at_any_weight():
    mold = image([[MISS, SHIRT]])
    part = image([[SKIN, SKIN]])
    weight = np.array([[0.0, 0.0]], np.float32)  # the cut plane: the blend gives the part nothing
    color, unreached = combine(mold, part, weight)
    assert np.allclose(color[0, 0], SKIN)
    assert np.allclose(color[0, 1], SHIRT)
    assert not unreached.any()


def test_where_both_bakes_reached_the_weight_blends_them():
    mold = image([[SHIRT]])
    part = image([[SKIN]])
    color, _ = combine(mold, part, np.array([[0.25]], np.float32))
    assert np.allclose(color[0, 0, :3], 0.25 * np.array(SKIN[:3]) + 0.75 * np.array(SHIRT[:3]))


def test_a_texel_no_bake_reached_copies_its_nearest_reached_neighbor():
    mold = image([[SHIRT, MISS, MISS, MISS]])
    part = image([[MISS, MISS, MISS, SKIN]])
    color, unreached = combine(mold, part, np.ones((1, 4), np.float32))
    assert unreached.tolist() == [[False, True, True, False]]
    assert np.allclose(color[0, 1, :3], SHIRT[:3])
    assert np.allclose(color[0, 2, :3], SKIN[:3])
    assert color[0, :, :3].max(axis=1).min() > 0.1  # nothing left black


def test_without_a_part_the_mold_colors_stay():
    mold = image([[SHIRT, SKIN]])
    color, unreached = combine(mold, None, None)
    assert np.allclose(color, mold)
    assert not unreached.any()
