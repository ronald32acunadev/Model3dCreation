"""Where a sculpted head's face ends, read from horizontal sections of the head alone.

The subject faces +Z (glTF, y up). At each height, the head's section is the connected solid
region, in that horizontal slice, around the head's vertical axis (the middle of its crown).
Armour, collars or hair beside the head form separate regions and are left out.

Along the middle of the face, the region's frontmost point is the profile: it peaks at the nose
(or snout), falls back under the jaw to the throat, and comes forward again at the chest or a
collar. The nose is the profile's peak in the middle of the head (between NOSE_ABOVE and
NOSE_BELOW of the head's height above the joint) and above the first section, going down, that suddenly grows
by JUMP past the head's width (the head meeting a collar or armour). Going down from the nose, the throat is the profile's lowest
point before it comes forward again by more than RETURN of its depth below the nose (an open
mouth's lower jaw comes forward less); the chin is the lowest height above the throat where the
profile reaches halfway between the throat and the nose.
"""

from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy import ndimage

STEP = 0.004  # glTF units between sections (body about 1.0 tall)
CELL = 0.003  # section raster cell
REACH = 0.3  # sections are read this far around the head's axis
CROWN = 0.03  # the crown, which locates the head's axis, is this far below the top
FACE_BAND = 0.015  # half-width of the face's middle, where the profile is read
NOSE_ABOVE = 0.1  # the nose is searched above the joint by this share of the head's height...
NOSE_BELOW = 0.65  # ...and below this share of it: a quiff of hair juts out past the nose higher up
RETURN = 0.5  # coming forward by this share of the depth below the nose is the chest or a collar
JUMP = 1.5  # a section this much wider than the one above it, and than the head, has met armour
BOTTOM = 0.02  # the throat is not searched this close to `lowest`, where a bust's cut edge recedes


@dataclass(frozen=True)
class Face:
    nose_y: float
    chin_y: float
    throat_y: float
    chin_z: float  # front of the face at the chin, for heights of tilted planes
    axis: tuple[float, float]  # (x, z) of the head's vertical axis
    alone_from_y: float  # below this height the head's sections join a collar or armour


@dataclass(frozen=True)
class Sections:
    heights: np.ndarray
    area: np.ndarray  # area of the head's region at each height (0 where none)
    front: np.ndarray  # its frontmost z within FACE_BAND of the axis (nan where none)
    radius: np.ndarray  # its farthest point from the axis (0 where none)
    axis: tuple[float, float]
    regions: list | None = None  # with keep_regions, the region's cells at each height (bool grid)
    solids: list | None = None  # with keep_regions, every solid cell at each height (bool grid)


def head_sections(field: Callable[[np.ndarray], np.ndarray], lowest: float, top: float,
                  axis: tuple[float, float] | None = None, keep_regions: bool = False) -> Sections:
    """The head's solid region at each height from `lowest` to `top`; `field` is negative inside.

    The head's axis is the middle of the crown unless given."""
    s = np.arange(-REACH, REACH + CELL / 2, CELL)
    gx, gz = np.meshgrid(s, s, indexing="ij")
    size = len(s)
    middle = size // 2

    def solid(y: float, axis: tuple[float, float]) -> np.ndarray:
        points = np.column_stack([gx.ravel() + axis[0], np.full(gx.size, y), gz.ravel() + axis[1]])
        return (field(points) < 0).reshape(size, size)

    axis = _crown_axis(field, top) if axis is None else axis
    heights = np.arange(lowest + STEP / 2, top, STEP)
    area = np.zeros(len(heights))
    front = np.full(len(heights), np.nan)
    radius = np.zeros(len(heights))
    regions = [np.zeros((size, size), bool) for _ in heights] if keep_regions else None
    solids = [np.zeros((size, size), bool) for _ in heights] if keep_regions else None
    for i, y in enumerate(heights):
        cells = solid(float(y), axis)
        if solids is not None:
            solids[i] = cells
        labels, count = ndimage.label(cells)
        if count == 0:
            continue
        own = labels[middle, middle]
        if own == 0:  # the axis falls outside every region: take the nearest one
            filled = np.argwhere(labels > 0)
            nearest = filled[np.argmin(((filled - middle) ** 2).sum(axis=1))]
            own = labels[nearest[0], nearest[1]]
        region = np.argwhere(labels == own)
        if regions is not None:
            regions[i] = labels == own
        area[i] = len(region) * CELL**2
        radius[i] = float(np.sqrt(s[region[:, 0]] ** 2 + s[region[:, 1]] ** 2).max()) + CELL
        middle_cells = region[np.abs(s[region[:, 0]]) <= FACE_BAND]
        if len(middle_cells):
            front[i] = axis[1] + s[middle_cells[:, 1].max()]
    return Sections(heights, area, front, radius, axis, regions, solids)


def _crown_axis(field: Callable[[np.ndarray], np.ndarray], top: float) -> tuple[float, float]:
    """(x, z) middle of the solid just below the top, searched over a wide square around the origin."""
    s = np.arange(-1.0, 1.0, CELL * 2)
    gx, gz = np.meshgrid(s, s, indexing="ij")
    for depth in np.arange(CROWN, 10 * CROWN, CROWN / 2):
        points = np.column_stack([gx.ravel(), np.full(gx.size, top - depth), gz.ravel()])
        inside = field(points) < 0
        if inside.any():
            return float(points[inside, 0].mean()), float(points[inside, 2].mean())
    raise ValueError("the head has no solid crown")


def find_face(field: Callable[[np.ndarray], np.ndarray], lowest: float, top: float, head_joint_y: float) -> Face:
    """Nose, chin and throat of a sculpted head or bust, searching between `lowest` and `top`."""
    s = head_sections(field, lowest, top)
    top = solid_top(s)  # a mesh field's box reaches past the head
    start = alone_from(s, head_joint_y, top)
    upper = np.flatnonzero((s.heights >= head_joint_y + NOSE_ABOVE * (top - head_joint_y))
                           & (s.heights <= head_joint_y + NOSE_BELOW * (top - head_joint_y))
                           & (np.arange(len(s.heights)) >= start) & ~np.isnan(s.front))
    if len(upper) == 0:
        raise ValueError("the head has no face above its joint")
    nose = int(upper[np.argmax(s.front[upper])])
    throat = nose
    for i in range(nose - 1, -1, -1):
        if np.isnan(s.front[i]) or s.heights[i] < lowest + BOTTOM:
            break
        if s.front[i] > s.front[throat] + RETURN * (s.front[nose] - s.front[throat]):
            break
        if s.front[i] <= s.front[throat]:
            throat = i
    if throat == nose:
        raise ValueError("the head has no chin below its nose")
    halfway = s.front[throat] + 0.5 * (s.front[nose] - s.front[throat])
    chin = throat + 1 + int(np.argmax(s.front[throat + 1:nose + 1] >= halfway))
    return Face(float(s.heights[nose]), float(s.heights[chin]), float(s.heights[throat]), float(s.front[chin]), s.axis,
                float(s.heights[start] - STEP / 2) if start else lowest)


def solid_top(s: Sections) -> float:
    present = np.flatnonzero(s.area > 0)
    if len(present) == 0:
        raise ValueError("the head has no solid")
    return float(s.heights[present.max()] + STEP / 2)


def alone_from(s: Sections, head_joint_y: float, top: float) -> int:
    """Index of the lowest section that still holds the head alone.

    Going down, a section that suddenly grows by JUMP, to more than JUMP times the widest section
    of the head's upper half, has met a collar, armour or the shoulders (the crown grows fast from
    nothing, but never past the head's own width)."""
    crown = (s.heights >= (head_joint_y + top) / 2) & (s.area > 0)
    if not crown.any():
        raise ValueError("the head has no solid above its joint")
    wide = JUMP * s.area[crown].max()
    for i in range(len(s.heights) - 1, 0, -1):
        if s.area[i - 1] > JUMP * s.area[i] and s.area[i - 1] > wide:
            return i
    return 0


def head_members(field: Callable[[np.ndarray], np.ndarray], axis: tuple[float, float], bottom: float, top: float,
                 head_joint_y: float, points: np.ndarray, grow: int = 2) -> np.ndarray:
    """Which of `points` (between `bottom` and `top`) lie on the head itself, grown by `grow` cells.

    The head is the solid connected in 3D, over the sections that hold it alone, to the most cells
    of the head's region in each section: ears, a nose tip or the crown, which a single section
    can cut off, stay with it, and a collar or pauldrons beside it do not. Below those sections,
    where the head has joined a collar or armour, the lowest one that holds it alone stands in.
    """
    s = head_sections(field, bottom, top, axis, keep_regions=True)
    start = alone_from(s, head_joint_y, solid_top(s)) if (s.heights >= (head_joint_y + top) / 2).any() else 0
    labels, count = ndimage.label(np.stack(s.solids[start:]))
    own = np.stack(s.regions[start:])
    if count == 0 or not (labels[own] > 0).any():
        return np.zeros(len(points), bool)
    head = labels == np.bincount(labels[own][labels[own] > 0]).argmax()
    grown = ndimage.binary_dilation(head, iterations=grow)
    size = grown.shape[1]
    index = np.clip(np.floor((points[:, 1] - bottom) / STEP).astype(int), 0, len(s.heights) - 1)
    layer = np.maximum(index, start) - start
    ix = np.round((points[:, 0] - axis[0] + REACH) / CELL).astype(int)
    iz = np.round((points[:, 2] - axis[1] + REACH) / CELL).astype(int)
    inside = (ix >= 0) & (ix < size) & (iz >= 0) & (iz < size) & (points[:, 1] >= bottom) & (points[:, 1] <= top)
    members = np.zeros(len(points), bool)
    members[inside] = grown[layer[inside], ix[inside], iz[inside]]
    return members


def head_radius(field: Callable[[np.ndarray], np.ndarray], axis: tuple[float, float], bottom: float, top: float,
                head_joint_y: float) -> float:
    """Farthest reach of a head from `axis` between `bottom` and `top`, over the sections that hold
    the head alone (a collar or armour beside it is left out)."""
    s = head_sections(field, bottom, top, axis)
    top = solid_top(s)
    start = alone_from(s, head_joint_y, top) if (s.heights >= (head_joint_y + top) / 2).any() else 0
    return float(s.radius[start:].max()) if len(s.radius[start:]) else 0.0


def plane_height(plane: dict, x: float, z: float) -> float:
    """Height of a plane ({point, normal}) above the point (x, z)."""
    point, normal = np.asarray(plane["point"], float), np.asarray(plane["normal"], float)
    return float(point[1] - (normal[0] * (x - point[0]) + normal[2] * (z - point[2])) / normal[1])
