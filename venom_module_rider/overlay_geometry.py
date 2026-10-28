"""Bounded source-pixel drawing instructions, never perception or motion evidence."""

import math

SIDES = {"left", "center", "right"}


def bounds(corridor, size):
    """Convert an assessed rectangle to its exact half-open source-pixel bounds."""
    w, h = size
    a, b, c, d = corridor
    return [
        max(0, math.ceil(a * w)),
        max(0, math.ceil(b * h)),
        min(w, math.ceil(c * w)),
        min(h, math.ceil(d * h)),
    ]


def _outline_break(result):
    """Append a gap only after an existing observed stroke."""
    if result and result[-1] is not None:
        result.append(None)


def outline(points, size, count=25):
    """Bound curves without joining strokes across unobserved source pixels."""
    if not points:
        return []
    w, h = size
    indices = sorted({round(i * (len(points) - 1) / (count - 1)) for i in range(count)})
    result: list[list[int] | None] = []
    previous = None
    for index in indices:
        point = points[index]
        if previous is not None and any(
            p is None for p in points[previous : index + 1]
        ):
            _outline_break(result)
        if point is None:
            _outline_break(result)
        else:
            result.append(
                [
                    min(w - 1, max(0, round(point[0] * w))),
                    min(h - 1, max(0, round(point[1] * h))),
                ]
            )
        previous = index
    return result if len(result) <= 32 else []


def analysis_geometry(size, assessment, corridor, input_bounds=None):
    """Extract bounded display geometry in the decoded source coordinate system."""
    w, h = size
    result = {
        "version": 1,
        "width": w,
        "height": h,
        "input_bounds": list(input_bounds or (0, 0, w, h)),
    }
    if assessment.get("geometry") == "ground_capsule":
        paths = assessment.pop("_path_overlay", {})
        result["paths"] = {
            side: outline(points, size)
            for side, points in paths.items()
            if side in SIDES
        }
    else:
        result["score_bounds"] = bounds(corridor, size)
        result["paths"] = {}
        for side, sector in assessment.get("sectors", {}).items():
            region = sector.get("corridor")
            if (
                side not in SIDES
                or not region
                or not 0 <= region[0] < region[2] <= 1
                or not 0 <= region[1] < region[3] <= 1
            ):
                continue
            a, b, c, d = bounds(region, size)
            result["paths"][side] = [
                [a, b],
                [c - 1, b],
                [c - 1, d - 1],
                [a, d - 1],
                [a, b],
            ]
        reference = assessment.get("plane_reference_corridor")
        if reference is not None:
            result["reference_bounds"] = bounds(reference, size)
    return result


def source_geometry(result, payload, corridor):
    """Retain the exact decoded-source coordinate system for legacy adapters."""
    if "overlay_geometry" not in result and "_preview" in result:
        import io

        from PIL import Image

        try:
            with Image.open(io.BytesIO(payload)) as image:
                result["overlay_geometry"] = analysis_geometry(
                    image.size, result, corridor
                )
        except OSError:
            # Display metadata must never revoke a legacy adapter decision.
            pass
    return result


def with_evidence(geometry, spatial, objects=None):
    """Attach bounded visual diagnostics without altering perception evidence."""
    result = dict(geometry)
    w, h = result["width"], result["height"]
    boundary = spatial.get("boundary", [])
    if boundary:
        stride = max(1, math.ceil(len(boundary) / 16))
        result["floor_boundary"] = [
            [int(p["x"]), int(p["y"])] for p in boundary[::stride]
        ]
    near = spatial.get("_near_mask")
    if near is not None:
        import cv2
        import numpy as np

        contours, _ = cv2.findContours(
            near.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if contours:
            polygon = cv2.approxPolyDP(
                max(contours, key=cv2.contourArea), 1.5, True
            ).reshape(-1, 2)
            points = [(float(x) / w, float(y) / h) for x, y in polygon]
            if len(points) >= 2:
                result["near"] = outline([*points, points[0]], (w, h), count=13)
                result["guard_clear"] = spatial.get("guard_clear") is True
    if objects:
        result["objects"] = [
            {
                "id": item["id"],
                "bounds": item["bbox"],
                "evidence": item.get("evidence", "transient"),
            }
            for item in objects.get("objects", [])[:4]
        ]
    return result


def _validate_geometry_box(box, width, height):
    """Require integer pixel bounds inside their immutable source frame."""
    if (
        not isinstance(box, (list, tuple))
        or len(box) != 4
        or any(type(point) is not int for point in box)
    ):
        raise ValueError("vision_overlay_geometry_invalid")
    if not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height:
        raise ValueError("vision_overlay_geometry_invalid")


def _validate_geometry_points(points, width, height):
    """Keep every optional path vertex bounded without joining missing pixels."""
    if not isinstance(points, list) or len(points) > 32:
        raise ValueError("vision_overlay_geometry_invalid")
    for point in points:
        if point is not None and (
            not isinstance(point, list)
            or len(point) != 2
            or any(type(value) is not int for value in point)
            or not 0 <= point[0] < width
            or not 0 <= point[1] < height
        ):
            raise ValueError("vision_overlay_geometry_invalid")


def _validate_geometry_object(item, size):
    """Validate an object identity before applying the shared bounds contract."""
    if (
        not isinstance(item, dict)
        or type(item.get("id")) is not int
        or not isinstance(item.get("evidence"), str)
    ):
        raise ValueError("vision_overlay_geometry_invalid")
    width, height = size
    validate_geometry(
        {
            "version": 1,
            "width": width,
            "height": height,
            "input_bounds": item.get("bounds"),
        },
        size,
    )


def validate_geometry(value, size):
    """A visual descriptor must fit its own immutable source frame."""
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or type(value.get("version")) is not int
    ):
        raise ValueError("vision_overlay_geometry_invalid")
    w, h = size
    if (
        type(value.get("width")) is not int
        or type(value.get("height")) is not int
        or (value["width"], value["height"]) != (w, h)
    ):
        raise ValueError("vision_overlay_geometry_invalid")
    for key in ("input_bounds", "score_bounds", "reference_bounds"):
        if key not in value:
            continue
        _validate_geometry_box(value[key], w, h)
    if "input_bounds" not in value:
        raise ValueError("vision_overlay_geometry_invalid")
    paths = value.get("paths", {})
    if not isinstance(paths, dict) or set(paths) - SIDES:
        raise ValueError("vision_overlay_geometry_invalid")
    for points in [
        *paths.values(),
        *[value[k] for k in ("near", "floor_boundary") if k in value],
    ]:
        _validate_geometry_points(points, w, h)
    objects = value.get("objects", [])
    if not isinstance(objects, list) or len(objects) > 4:
        raise ValueError("vision_overlay_geometry_invalid")
    for item in objects:
        _validate_geometry_object(item, size)
    return value
