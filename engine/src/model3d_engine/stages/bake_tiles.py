"""Split a texture bake into tiles, so a large texture never needs all its buffers at once.

Pure numpy: shared by the TRELLIS.2 worker's tiled bake (trellis_bake.py, TRELLIS.2's interpreter)
and the engine's tests. A texture of `size` texels is cut into square tiles of at most `tile`
texels; each tile is rasterized on its own, with the UVs moved and scaled so the tile fills the
rasterizer's [-1, 1] square, and its texels land at the tile's offset in the full texture.
"""

import numpy as np

TILE = 4096  # texels: a 4096 px bake fits next to TRELLIS.2's decoded mesh on a 12 GB card


def tiles(size: int, tile: int = TILE) -> list[tuple[int, int, int]]:
    """(row, column, side) of every tile, rows and columns in texels, covering size x size."""
    side = min(size, tile)
    if size % side:
        raise ValueError(f"texture size {size} is not a multiple of the tile size {side}")
    return [(row, column, side) for row in range(0, size, side) for column in range(0, size, side)]


def tile_clip(uv: np.ndarray, row: int, column: int, side: int, size: int) -> np.ndarray:
    """UVs (0..1, v growing with texture rows, as TRELLIS.2 rasterizes them) moved so the tile at
    (row, column) fills clip space [-1, 1]; points outside the tile fall outside [-1, 1]."""
    scale = size / side
    u = (uv[..., 0] - column / size) * scale
    v = (uv[..., 1] - row / size) * scale
    return np.stack([u * 2 - 1, v * 2 - 1], axis=-1)


def place(full: np.ndarray, part: np.ndarray, row: int, column: int) -> None:
    """Write one tile's texels into the full texture."""
    full[row:row + part.shape[0], column:column + part.shape[1]] = part
