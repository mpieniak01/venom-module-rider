"""Exact ROI/projection annotations, with no model or device connection."""

import io

import numpy as np
import pytest
from PIL import Image

from venom_module_rider.depth_detector import PerceptionSettings, render_depth
from venom_module_rider.directional import sector_regions
from venom_module_rider.vision_overlay import draw_sectors, preview_sectors

ROI = (0.35, 0.8, 0.65, 0.95)


class Drawing:
    def __init__(self):
        self.rectangles, self.lines, self.labels = [], [], []

    def rectangle(self, box, **kwargs):
        self.rectangles.append((box, kwargs))

    def line(self, points, **kwargs):
        self.lines.append(points)

    def textbbox(self, _xy, text, **_kwargs):
        return (0, 0, len(text) * 7, 10)

    def text(self, _xy, text, **_kwargs):
        self.labels.append(text)


def sectors(roi=ROI):
    return {
        side: {
            "corridor": bounds,
            "state": "clear",
            "coverage_complete": 0 <= bounds[0] < bounds[2] <= 1,
        }
        for side, bounds in sector_regions(roi).items()
    }


def test_image_boxes_use_assessed_bounds_not_thirds_of_the_image():
    draw = Drawing()
    draw_sectors(draw, (320, 240), sectors())
    bounds = [box for box, options in draw.rectangles if "fill" not in options]
    assert bounds == [(16, 192, 111, 227), (208, 192, 303, 227)]
    assert draw.labels == ["L", "C", "R"]


def test_clipped_or_unknown_side_is_dashed_and_labelled_uncertain():
    draw, rows = Drawing(), sectors((0.1, 0.8, 0.4, 0.95))
    rows["right"]["state"] = "unknown"
    draw_sectors(draw, (320, 240), rows)
    assert draw.labels == ["L?", "C", "R?"]
    assert draw.lines
    assert all(0 <= p[0] < 320 and 0 <= p[1] < 240 for line in draw.lines for p in line)


def test_projected_strokes_keep_fov_gaps_and_use_actual_points():
    draw = Drawing()
    points = [(0.2, 0.6), (0.25, 0.7), None, (0.8, 0.7), (0.9, 0.8)]
    draw_sectors(
        draw,
        (320, 240),
        {"left": {"state": "clear", "coverage_complete": True}},
        outlines={"left": points},
        projected=True,
    )
    assert draw.lines == [((64, 144), (80, 168)), ((256, 168), (288, 192))]
    assert draw.labels == ["L", "C?", "R?"]


def test_absent_projection_cannot_create_a_fake_region():
    draw = Drawing()
    draw_sectors(draw, (320, 240), {}, projected=True)
    assert draw.lines == []
    assert len(draw.rectangles) == 3  # Only small availability badges.
    assert draw.labels == ["L?", "C?", "R?"]
    assert all(box[2] - box[0] < 30 for box, _ in draw.rectangles)


def test_invalid_or_empty_visible_image_region_draws_no_boundary():
    draw = Drawing()
    draw_sectors(draw, (320, 240), {"left": {"corridor": (1.1, 0.8, 1.3, 0.9)}})
    assert draw.labels == [] and draw.lines == [] and draw.rectangles == []


def test_preview_metadata_is_small_and_does_not_publish_planner_grids():
    rows = sectors()
    rows["left"].update(heading_candidates=list(range(1000)), occupancy_trend="growing")
    result = preview_sectors({"sectors": {**rows, "guard": rows["center"]}})
    assert set(result) == {"left", "center", "right"}
    assert set(result["left"]) == {"state", "coverage_complete", "corridor"}


@pytest.mark.parametrize("size", [(320, 240), (640, 360), (1280, 720)])
def test_render_preserves_dimensions_and_central_scored_pixels(size):
    from venom_module_rider.depth_detector import assess_depth

    w, h = size
    y, x = np.mgrid[:h, :w]
    inverse = np.maximum(0.1 * x / w + 3.5 * y / h - 2, 0.1)
    depth = 1 / inverse
    settings = PerceptionSettings(engine="depth_plane")
    row = assess_depth(depth, ROI, settings)
    assert row["state"] == "clear"
    image = Image.new("RGB", size, (80, 80, 80))
    encoded = render_depth(image, depth, row, ROI, settings)
    decoded = Image.open(io.BytesIO(encoded))
    assert decoded.size == size
    # Annotation is sparse; the scored central floor retains its green mask.
    px = np.asarray(decoded)
    central = px[round(h * 0.83) : round(h * 0.92), round(w * 0.38) : round(w * 0.62)]
    assert (central[:, :, 1] > central[:, :, 0] + 30).mean() > 0.99


def test_font_fallback_is_bounded_and_cached(monkeypatch):
    from PIL import ImageFont

    from venom_module_rider.vision_overlay import _font

    marker, calls = object(), []

    def unavailable(*args):
        raise OSError("no system font")

    monkeypatch.setattr(ImageFont, "truetype", unavailable)
    monkeypatch.setattr(ImageFont, "load_default", lambda: calls.append(1) or marker)
    _font.cache_clear()
    try:
        assert _font(13) is marker and _font(13) is marker
        assert calls == [1]
    finally:
        _font.cache_clear()


@pytest.mark.parametrize("protocol", [2, 3])
async def test_preview_keeps_image_sides_when_the_command_planner_mirrors_them(
    protocol,
):
    from venom_module_rider.vision_contract import VisionFrame
    from venom_module_rider.vision_loop import VisionLoop

    class Detector:
        def detect(self, *_args):
            rows = sectors()
            rows["left"]["state"] = "obstacle"
            return {
                "state": "obstacle",
                "reason": "depth_ground_deviation",
                "sectors": rows,
                "_preview": b"exact-jpeg",
            }

        def status(self):
            return {"sha256": "a" * 64}

    loop = VisionLoop()
    loop.depth_detector = Detector()
    frame = VisionFrame.model_validate(
        {
            "protocol_version": protocol,
            "run_id": "run",
            "frame_id": "capture-7",
            "sequence": 7,
            "config_revision": 39,
            "corridor": ROI,
            "age_ms": 0.0,
            "deadline_ms": 300,
            "mode": "observe",
            "perception": {"engine": "depth_plane"},
            "avoidance": {
                "enabled": True,
                "protocol_version": protocol,
                "mirrored": True,
            },
        }
    )
    result = await loop.decide("fixture", frame, b"fixture-input")
    assert result["action"] == "stop"
    assert result["sectors"]["left"]["state"] == "clear"  # Command mapping.
    cached = loop.preview("fixture", run_id="run")
    assert cached is not None and cached[1] == b"exact-jpeg"
    assert cached[2]["sectors"]["left"]["state"] == "obstacle"  # Image mapping.
    assert cached[2]["sector_space"] == "image"
    assert cached[2]["frame_id"] == "capture-7"


@pytest.mark.parametrize("ground", [False, True])
def test_quality_rejection_still_labels_unknown_regions_without_loading_model(ground):
    from venom_module_rider.depth_detector import DepthDetector
    from venom_module_rider.ground_config import GroundGeometry

    detector = DepthDetector()

    def forbidden():
        raise AssertionError("Quality rejection must not load a model")

    detector._load = forbidden
    image = Image.new("RGB", (320, 240), (1, 1, 1))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    geometry = GroundGeometry() if ground else None
    result = detector._detect(
        stream.getvalue(), ROI, PerceptionSettings(engine="depth_plane"), geometry
    )
    assert result["state"] == "unknown" and result["reason"] == "vision_image_quality"
    assert all(row["state"] == "unknown" for row in result["sectors"].values())
    preview = np.asarray(Image.open(io.BytesIO(result["_preview"])))
    assert (preview.max(axis=2) > 100).sum() > 100  # Visible uncertainty badges.


async def test_projected_metadata_remains_small_in_the_real_preview_response():
    import json
    from types import SimpleNamespace

    from test_adaptive_ground import CAMERA, SETTINGS, scene

    from venom_module_rider.api.vision_preview import processed_camera
    from venom_module_rider.vision_contract import VisionFrame
    from venom_module_rider.vision_loop import VisionLoop

    class Detector:
        def detect_ground(self, *_args):
            from venom_module_rider.adaptive_ground import assess

            image = Image.new("RGB", (128, 96), (80, 80, 80))
            depth = scene()
            row = assess(depth, ROI, SETTINGS, CAMERA)
            row["_preview"] = render_depth(image, depth, row, ROI, SETTINGS, CAMERA)
            row.pop("_errors", None)
            return row

        def status(self):
            return {"sha256": "a" * 64}

    loop = VisionLoop()
    loop.depth_detector = Detector()
    frame = VisionFrame.model_validate(
        {
            "protocol_version": 3,
            "run_id": "run",
            "frame_id": "capture",
            "sequence": 1,
            "config_revision": 39,
            "corridor": ROI,
            "age_ms": 0.0,
            "deadline_ms": 300,
            "mode": "observe",
            "perception": {"engine": "depth_plane"},
            "avoidance": {
                "protocol_version": 3,
                "enabled": True,
                "geometry": CAMERA.model_dump(),
            },
        }
    )
    await loop.decide("fixture", frame, b"fixture")

    class Domain:
        def transport(self):
            return SimpleNamespace(device=SimpleNamespace(id="fixture"))

        async def read(self, path, *_args):
            return (
                {"data": {"corridor": ROI, "revision": 39}}
                if path.endswith("config")
                else {"data": {"running": True, "stale": False, "run_id": "run"}}
            )

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(rider=Domain(), rider_vision=loop))
    )
    response = await processed_camera(request)
    header = response.headers["x-rider-analysis-json"]
    assert len(header) <= 4096
    row = json.loads(header)
    assert row["sector_space"] == "projected_paths"
    assert set(row["sectors"]) == {"left", "center", "right"}
    assert "_path_overlay" not in row and "heading_candidates" not in row
