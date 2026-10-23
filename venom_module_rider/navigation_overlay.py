"""Draw only current source-space evidence on the model preview."""

import io


def render_evidence(payload, spatial):
    """White route, amber/red near guard and cyan floor boundary; no distances."""
    if not isinstance(payload, bytes) or spatial.get("valid") is not True:
        return payload
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw

    dimensions = spatial.get("source_dimensions", {})
    width, height = dimensions.get("width", 0), dimensions.get("height", 0)
    if not width or not height:
        return payload
    try:
        with Image.open(io.BytesIO(payload)) as source:
            image = source.convert("RGB")
    except (OSError, ValueError):
        return payload
    sx, sy = image.width / width, image.height / height
    draw = ImageDraw.Draw(image)
    for name, color in (
        ("_corridor_mask", "white"),
        ("_near_mask", "#fbbf24" if spatial.get("guard_clear") is True else "#ff3b30"),
    ):
        mask = spatial.get(name)
        if mask is None:
            continue
        contours, _ = cv2.findContours(
            mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        for contour in contours[:4]:
            polygon = cv2.approxPolyDP(contour, 1.5, True).reshape(-1, 2)
            points = [
                (round(float(x) * sx), round(float(y) * sy)) for x, y in polygon[:128]
            ]
            if len(points) >= 2:
                draw.line([*points, points[0]], fill=color, width=2)
    boundary = spatial.get("boundary", [])
    points = [(round(row["x"] * sx), round(row["y"] * sy)) for row in boundary[:64]]
    if len(points) >= 2:
        draw.line(points, fill="#22d3ee", width=2)
    stream = io.BytesIO()
    image.save(stream, format="JPEG", quality=85)
    return stream.getvalue()
