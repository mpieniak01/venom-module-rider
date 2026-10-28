"""Compact PNGs contain classification/geometry only, never source RGB texture."""

import base64
import io

import numpy as np
import pytest
from PIL import Image

from venom_module_rider.analysis_layers import (
    ALPHA,
    PALETTE,
    classification_layer,
    validate_video_layer,
)
from venom_module_rider.depth_detector import PerceptionSettings
from venom_module_rider.errors import RiderError


def assessment():
    errors = np.full((48, 64), np.nan)
    errors[5, 5] = 0
    errors[6, 6] = 0.2
    return {
        "state": "clear",
        "_errors": errors,
        "sectors": {},
        "plane_reference_corridor": (0.3, 0.5, 0.7, 0.95),
    }


def test_depth_palette_alpha_geometry_source_size_and_no_photo_dependency():
    source = assessment()
    payload, meta = classification_layer(
        (64, 48),
        source,
        (0.3, 0.7, 0.7, 0.95),
        PerceptionSettings(),
        "0000-mask.png",
        "a" * 64,
    )
    assert "_errors" not in source
    with Image.open(io.BytesIO(payload)) as layer:
        assert layer.format == "PNG"
        assert layer.mode == "P"
        assert layer.size == (64, 48)
        assert layer.info["transparency"][: len(ALPHA)] == bytes(ALPHA)
        assert layer.getpixel((0, 0)) == 0
        assert layer.getpixel((5, 5)) == 1
        assert layer.getpixel((6, 6)) == 2
        rgba = layer.convert("RGBA")
        assert rgba.getpixel((5, 5)) == (*PALETTE[1], 102)
        assert rgba.getpixel((18, 32)) == (*PALETTE[4], 255)
    assert meta == {
        "version": 1,
        "artifact": "0000-mask.png",
        "width": 64,
        "height": 48,
        "palette_version": 1,
        "source_sha256": "a" * 64,
    }
    other, other_meta = classification_layer(
        (64, 48),
        assessment(),
        (0.3, 0.7, 0.7, 0.95),
        PerceptionSettings(),
        "0000-mask.png",
        "b" * 64,
    )
    assert other == payload
    assert other_meta["source_sha256"] != meta["source_sha256"]
    assert len(payload) < 2000


def test_unknown_depth_does_not_paint_confident_floor():
    source = assessment()
    source["state"] = "unknown"
    payload, _ = classification_layer(
        (64, 48),
        source,
        (0.3, 0.7, 0.7, 0.95),
        PerceptionSettings(),
        "0000-mask.png",
        "a" * 64,
    )
    with Image.open(io.BytesIO(payload)) as layer:
        assert layer.getpixel((5, 5)) == 0
        assert layer.getpixel((6, 6)) == 0


def test_semantic_layer_keeps_unsupported_ground_blue_without_private_maps():
    floor = np.zeros((48, 64), dtype=bool)
    floor[5, 5] = True
    obstacle = np.zeros_like(floor)
    obstacle[6, 6] = obstacle[7, 7] = True
    labels = np.zeros((48, 64), dtype=np.uint8)
    labels[7, 7] = 28
    source = {
        "state": "clear",
        "_navigation_maps": {
            "floor": floor,
            "obstacle": obstacle,
            "labels": labels,
            "gray": np.ones_like(labels),
        },
    }
    payload, _ = classification_layer(
        (64, 48),
        source,
        (0.3, 0.7, 0.7, 0.95),
        PerceptionSettings(),
        "0000-mask.png",
        "a" * 64,
        semantic=True,
    )
    assert "_navigation_maps" not in source
    with Image.open(io.BytesIO(payload)) as layer:
        assert [
            layer.getpixel(point) for point in ((0, 0), (5, 5), (6, 6), (7, 7))
        ] == [0, 1, 2, 3]


@pytest.mark.parametrize(
    "patch",
    [
        {"source_sha256": "b" * 64},
        {"width": 65},
        {"palette_version": 2},
        {"artifact": "0001-mask.png"},
    ],
)
def test_parent_rejects_wrong_source_or_dimensions_of_layer(patch):
    payload, meta = classification_layer(
        (64, 48),
        assessment(),
        (0.3, 0.7, 0.7, 0.95),
        PerceptionSettings(),
        "0000-mask.png",
        "a" * 64,
    )
    meta.update(patch)
    row = {
        "classification_layer": meta,
        "artifact_names": ["0000-mask.png"],
        "rejected_input": False,
    }
    item = {
        "name": "0000-mask.png",
        "mime": "image/png",
        "data": base64.b64encode(payload).decode(),
    }
    with pytest.raises(RiderError, match="analysis_worker_protocol"):
        validate_video_layer(row, [item], 0, "a" * 64)


def test_parent_rejects_rgb_photo_disguised_as_png_layer():
    stream = io.BytesIO()
    Image.new("RGB", (64, 48), "red").save(stream, "PNG")
    row = {
        "classification_layer": {
            "version": 1,
            "artifact": "0000-mask.png",
            "width": 64,
            "height": 48,
            "palette_version": 1,
            "source_sha256": "a" * 64,
        },
        "artifact_names": ["0000-mask.png"],
    }
    item = {
        "name": "0000-mask.png",
        "mime": "image/png",
        "data": base64.b64encode(stream.getvalue()).decode(),
    }
    with pytest.raises(RiderError, match="analysis_worker_protocol"):
        validate_video_layer(row, [item], 0, "a" * 64)


def test_real_semantic_postprocessing_evidence_flag_preserves_classification():
    from venom_module_rider.perception_runtime import semantic_output

    image = Image.new("RGB", (64, 48), "grey")
    rgb = np.indices((48, 64)).sum(axis=0).astype(np.float32) / 112
    rgb = np.stack((rgb, rgb, rgb), axis=2)
    logits = np.full((1, 150, 4, 4), -8, dtype=np.float32)
    logits[:, 3] = 8
    logits[:, 28, 2:, 2:] = 12
    baseline = semantic_output(logits, (0.3, 0.7, 0.7, 0.95), image, rgb, None, False)
    with_maps = semantic_output(logits, (0.3, 0.7, 0.7, 0.95), image, rgb, None, True)

    def public(value):
        return {key: item for key, item in value.items() if not key.startswith("_")}

    assert public(baseline) == public(with_maps)
    assert "_navigation_maps" in with_maps
    assert "_navigation_maps" not in baseline
