"""Compact source-aligned classification layers, containing no source RGB texture."""

from __future__ import annotations

import io
import re

import numpy as np
from PIL import Image, ImageDraw

from .depth_detector import draw_stop_border
from .vision_overlay import draw_sectors

PALETTE = (
    (255, 185, 35),
    (35, 210, 100),
    (240, 60, 65),
    (65, 130, 245),
    (255, 255, 255),
    (72, 211, 232),
    (214, 167, 255),
    (80, 170, 255),
    (9, 18, 28),
)
ALPHA = (102, 102, 102, 102, 255, 255, 255, 255, 255)


def classification_layer(
    size,
    assessment,
    corridor,
    settings,
    artifact_name,
    source_sha256,
    *,
    semantic=False,
):
    """Consume private maps and draw frozen geometry into a tiny paletted PNG."""
    width, height = size
    if (
        type(width) is not int
        or type(height) is not int
        or not 1 <= width <= 1280
        or not 1 <= height <= 720
        or not re.fullmatch(r"[a-f0-9]{64}", source_sha256)
    ):
        raise ValueError("analysis_layer_invalid")
    labels = np.zeros((height, width), dtype=np.uint8)
    if semantic:
        maps = assessment.pop("_navigation_maps", None)
        if not isinstance(maps, dict):
            raise ValueError("analysis_layer_unavailable")
        floor, obstacle = maps.get("floor"), maps.get("obstacle")
        if (
            not isinstance(floor, np.ndarray)
            or not isinstance(obstacle, np.ndarray)
            or floor.shape != (height, width)
            or obstacle.shape != (height, width)
            or floor.dtype != np.bool_
            or obstacle.dtype != np.bool_
            or np.any(floor & obstacle)
        ):
            raise ValueError("analysis_layer_invalid")
        labels[floor] = 1
        labels[obstacle] = 2
        source_labels = maps.get("labels")
        if isinstance(source_labels, np.ndarray) and source_labels.shape == (
            height,
            width,
        ):
            from .floor_detector import UNSUPPORTED_GROUND_LABELS

            labels[obstacle & np.isin(source_labels, UNSUPPORTED_GROUND_LABELS)] = 3
    else:
        errors = assessment.pop("_errors", None)
        if not isinstance(errors, np.ndarray) or errors.shape != (height, width):
            raise ValueError("analysis_layer_unavailable")
        finite = np.isfinite(errors)
        if assessment["state"] != "unknown":
            labels[finite & (errors <= settings.residual_limit)] = 1
            labels[finite & (errors > settings.residual_limit)] = 2
    layer = Image.frombytes("P", (width, height), labels.tobytes())
    layer.putpalette(
        [channel for color in PALETTE for channel in color]
        + [0] * (768 - len(PALETTE) * 3)
    )
    draw = ImageDraw.Draw(layer)
    outlines = assessment.pop("_path_overlay", None)
    projected = assessment.get("geometry") == "ground_capsule"
    draw_sectors(
        draw,
        (width, height),
        assessment.get("sectors", {}),
        outlines=outlines,
        projected=projected,
    )
    x0, y0, x1, y1 = corridor
    if semantic:
        draw.rectangle(
            (
                int(x0 * (width - 1)),
                int(y0 * (height - 1)),
                int(x1 * (width - 1)),
                int(y1 * (height - 1)),
            ),
            outline="white",
            width=3,
        )
    elif not projected:
        reference_y0 = assessment.get("plane_reference_corridor", corridor)[1]
        if reference_y0 < y0:
            left, right = int(x0 * (width - 1)), int(x1 * (width - 1))
            top, bottom = int(reference_y0 * (height - 1)), int(y0 * (height - 1))
            for pos in range(left, right, 10):
                draw.line(
                    (pos, top, min(pos + 5, right), top), fill=PALETTE[7], width=2
                )
            for side in (left, right):
                for pos in range(top, bottom, 10):
                    draw.line(
                        (side, pos, side, min(pos + 5, bottom)),
                        fill=PALETTE[7],
                        width=2,
                    )
        draw_stop_border(draw, (height, width), corridor)
    if any(layer.histogram()[len(PALETTE) :]):
        raise ValueError("analysis_layer_palette")
    stream = io.BytesIO()
    layer.save(
        stream,
        format="PNG",
        bits=4,
        transparency=bytes(ALPHA) + bytes(16 - len(ALPHA)),
        optimize=True,
    )
    return stream.getvalue(), {
        "version": 1,
        "artifact": artifact_name,
        "width": width,
        "height": height,
        "palette_version": 1,
        "source_sha256": source_sha256,
    }


def validate_video_layer(row, artifacts, index, raw_sha256):
    """Reject a child result that reintroduces a source RGB copy or wrong layer."""
    import base64

    from .errors import RiderError

    layer = row.get("classification_layer")
    if row.get("rejected_input") is True:
        if layer is not None or artifacts or row.get("artifact_names"):
            raise RiderError("analysis_worker_protocol", 500)
        return
    name = f"{index:04d}-mask.png"
    if (
        not isinstance(layer, dict)
        or layer.get("version") != 1
        or layer.get("palette_version") != 1
        or layer.get("artifact") != name
        or layer.get("source_sha256") != raw_sha256
        or type(layer.get("width")) is not int
        or not 1 <= layer["width"] <= 1280
        or type(layer.get("height")) is not int
        or not 1 <= layer["height"] <= 720
        or len(artifacts) != 1
        or row.get("artifact_names") != [name]
        or artifacts[0].get("name") != name
        or artifacts[0].get("mime") != "image/png"
    ):
        raise RiderError("analysis_worker_protocol", 500)
    try:
        payload = base64.b64decode(artifacts[0]["data"], validate=True)
        with Image.open(io.BytesIO(payload)) as image:
            if (
                image.format != "PNG"
                or image.mode != "P"
                or image.size != (layer["width"], layer["height"])
            ):
                raise ValueError("classification layer")
            image.load()
            palette = image.getpalette()
            if (
                any(image.histogram()[len(PALETTE) :])
                or palette is None
                or palette[: len(PALETTE) * 3]
                != [channel for color in PALETTE for channel in color]
            ):
                raise ValueError("classification palette")
            transparency = image.info.get("transparency")
            if not isinstance(transparency, bytes) or transparency[
                : len(ALPHA)
            ] != bytes(ALPHA):
                raise ValueError("classification alpha")
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise RiderError("analysis_worker_protocol", 500) from exc
