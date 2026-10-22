"""Quality levels map to shape settings and to the face count the paint stage keeps."""

import pytest

from model3d_contracts.jobs import JobOptions, Quality
from model3d_engine.stages.hunyuan import QUALITY_PRESETS, paint_face_target


def test_every_quality_has_shape_settings():
    assert set(QUALITY_PRESETS) == {q.value for q in Quality}
    assert QUALITY_PRESETS["high"] == QUALITY_PRESETS["final"]  # shape is already at its best in final


@pytest.mark.parametrize(
    ("quality", "faces"), [(Quality.draft, 40_000), (Quality.final, 40_000), (Quality.high, 150_000)]
)
def test_paint_keeps_the_faces_of_each_level(quality, faces):
    assert paint_face_target(JobOptions(quality=quality)) == faces


def test_target_faces_overrides_the_level_for_textured_jobs():
    # Upstream's paint stage always cut the mesh to 40000 faces, ignoring target_faces.
    assert paint_face_target(JobOptions(quality=Quality.final, target_faces=100_000)) == 100_000
    assert paint_face_target(JobOptions(quality=Quality.high, target_faces=5_000)) == 5_000
