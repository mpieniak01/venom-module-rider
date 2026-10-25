"""Drawing data is bounded, source aligned, and independent of model scores."""

import base64
import io
from copy import deepcopy

import numpy as np
import pytest
from PIL import Image

from venom_module_rider.analysis_layers import (
    classification_layer,
    validate_video_layer,
)
from venom_module_rider.depth_detector import (
    PerceptionSettings,
    assess_depth,
)
from venom_module_rider.overlay_geometry import (
    analysis_geometry,
    outline,
    validate_geometry,
    with_evidence,
)
from venom_module_rider.preview_metadata import analysis_header


def geometry():
    return {
        "version": 1,
        "width": 320,
        "height": 240,
        "input_bounds": [0, 96, 320, 240],
    }


def test_mask_v2_contains_only_classification_and_matches_unchanged_assessment():
    y, _ = np.mgrid[:240, :320]
    depth = 1 / np.maximum(0.1, 4 * (y / 240 - 0.5))
    corridor = (0.35, 0.8, 0.65, 0.95)
    settings = PerceptionSettings(engine="depth_plane")
    result = assess_depth(depth, corridor, settings)
    before = {k: v for k, v in result.items() if not k.startswith("_")}
    result["overlay_geometry"] = analysis_geometry((320, 240), result, corridor)
    payload, meta = classification_layer(
        (320, 240), result, corridor, settings, "0000-mask.png", "a" * 64, vector=True
    )
    assert meta["version"] == 2
    with Image.open(io.BytesIO(payload)) as image:
        assert not any(image.histogram()[4:])
    for k, v in before.items():
        assert result[k] == v
    row = {**result, "classification_layer": meta, "artifact_names": ["0000-mask.png"]}
    artifacts = [
        {
            "name": "0000-mask.png",
            "mime": "image/png",
            "data": base64.b64encode(payload).decode(),
        }
    ]
    validate_video_layer(row, artifacts, 0, "a" * 64)
    row["overlay_geometry"]["width"] = 640
    with pytest.raises(Exception, match="analysis_worker_protocol"):
        validate_video_layer(row, artifacts, 0, "a" * 64)


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"width": True},
        {"height": 480},
        {"input_bounds": None},
        {"input_bounds": [0, 0, 321, 240]},
        {"input_bounds": [0, 0, 0, 240]},
        {"paths": {"other": []}},
        {"paths": []},
        {"near": [[1, 240]]},
        {"near": [[False, 1]]},
        {"floor_boundary": [[1]]},
        {"objects": None},
        {"objects": [{"id": True, "bounds": [0, 0, 1, 1], "evidence": "new"}]},
        {"objects": [{"id": 1, "bounds": [0, 0, 321, 240], "evidence": "new"}]},
        {"near": [[1, 1]] * 33},
    ],
)
def test_invalid_geometry_cannot_label_a_different_source(change):
    with pytest.raises(ValueError):
        validate_geometry({**geometry(), **change}, (320, 240))


def test_ground_outlines_keep_gaps_and_are_bounded():
    points = [[i / 100, 0.8] for i in range(100)]
    points[50] = None
    line = outline(points, (320, 240))
    assert None in line and len(line) <= 32
    result = {"geometry": "ground_capsule", "_path_overlay": {"center": points}}
    g = analysis_geometry((320, 240), result, (0.35, 0.8, 0.65, 0.95))
    assert validate_geometry(g, (320, 240)) == g
    assert "score_bounds" not in g and "_path_overlay" not in result


def test_optional_evidence_is_bounded_and_preview_does_not_change_identity():
    near = np.zeros((240, 320), bool)
    near[190:220, 100:200] = True
    g = with_evidence(
        geometry(),
        {
            "boundary": [{"x": i, "y": 180} for i in range(320)],
            "_near_mask": near,
            "guard_clear": True,
        },
        {
            "objects": [
                {"id": i, "bbox": [100, 190, 200, 220], "evidence": "approaching"}
                for i in range(10)
            ]
        },
    )
    validate_geometry(g, (320, 240))
    assert len(g["objects"]) == 4 and len(g["floor_boundary"]) <= 16
    result = {
        "state": "clear",
        "run_id": "run",
        "frame_id": "frame",
        "sequence": 1,
        "config_revision": 1,
        "overlay_geometry": g,
    }
    old = deepcopy(result)
    encoded = analysis_header(result)
    assert len(encoded) <= 4096 and result == old
