"""A large texture bake split into tiles lands every texel exactly where the whole bake would."""

import numpy as np
import pytest

from model3d_engine.stages.bake_tiles import place, tile_clip, tiles


def test_an_8192_texture_is_four_4096_tiles():
    assert tiles(8192) == [(0, 0, 4096), (0, 4096, 4096), (4096, 0, 4096), (4096, 4096, 4096)]


def test_a_texture_no_larger_than_a_tile_is_one_tile():
    assert tiles(4096) == [(0, 0, 4096)]
    assert tiles(2048) == [(0, 0, 2048)]


def test_a_size_that_tiles_do_not_cover_exactly_is_refused():
    with pytest.raises(ValueError):
        tiles(6000)


def rasterize_points(clip: np.ndarray, side: int) -> list[tuple[int, int]]:
    """Texel (row, column) a rasterizer of `side` texels gives each clip-space point inside [-1, 1)."""
    inside = np.all((clip >= -1) & (clip < 1), axis=1)
    pixel = np.floor((clip[inside] + 1) / 2 * side).astype(int)
    return [(int(r), int(c)) for c, r in pixel]


def test_each_texel_lands_where_the_whole_bake_puts_it():
    size, tile = 64, 32
    rng = np.random.default_rng(0)
    uv = (rng.integers(0, size, (200, 2)) + 0.5) / size  # texel centers
    whole = {tuple(p) for p in rasterize_points(uv * 2 - 1, size)}
    tiled = set()
    for row, column, side in tiles(size, tile):
        for r, c in rasterize_points(tile_clip(uv, row, column, side, size), side):
            tiled.add((row + r, column + c))
    assert tiled == whole


def test_place_writes_a_tile_at_its_offset():
    full = np.zeros((4, 4), int)
    place(full, np.ones((2, 2), int), 2, 0)
    assert full.tolist() == [[0, 0, 0, 0], [0, 0, 0, 0], [1, 1, 0, 0], [1, 1, 0, 0]]
