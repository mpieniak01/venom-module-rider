"""Label the exact assessed image regions or projected paths; no inference here."""

import math
from functools import lru_cache

SIDES = (
    ("left", "L", (72, 211, 232)),
    ("center", "C", (255, 255, 255)),
    ("right", "R", (214, 167, 255)),
)


@lru_cache(maxsize=8)
def _font(size):
    from PIL import ImageFont

    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _label(draw, size, point, text, color, occupied=None):
    """Use a compact, opaque badge; scale with the source image, not the viewport."""
    w, h = size
    font = _font(max(10, round(min(size) / 20)))
    box = draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0] + 6, box[3] - box[1] + 6
    x = max(0, min(w - tw, round(point[0]) - (tw // 2 if occupied is not None else 0)))
    y = max(0, min(h - th, round(point[1]) - th - 3))
    original = (x, y)
    if occupied is not None:
        for dx, dy in ((0, 0), (-tw - 4, 0), (tw + 4, 0), (0, -th - 4), (0, th + 4)):
            px, py = (
                max(0, min(w - tw, original[0] + dx)),
                max(0, min(h - th, original[1] + dy)),
            )
            if all(
                px + tw + 2 < a or px > c + 2 or py + th + 2 < b or py > d + 2
                for a, b, c, d in occupied
            ):
                x, y = px, py
                break
        occupied.append((x, y, x + tw, y + th))
        if (x, y) != original:
            draw.line((point, (x + tw / 2, y + th)), fill=color, width=1)
    draw.rectangle((x, y, x + tw, y + th), fill=(9, 18, 28), outline=color)
    draw.text((x + 3 - box[0], y + 3 - box[1]), text, font=font, fill=color)


def _dashed(draw, start, end, color, width):
    length = max(abs(end[0] - start[0]), abs(end[1] - start[1]))
    if length == 0:
        return
    for at in range(0, math.ceil(length), 8 * width):
        stop = min(length, at + 4 * width)
        draw.line(
            (
                (
                    start[0] + (end[0] - start[0]) * at / length,
                    start[1] + (end[1] - start[1]) * at / length,
                ),
                (
                    start[0] + (end[0] - start[0]) * stop / length,
                    start[1] + (end[1] - start[1]) * stop / length,
                ),
            ),
            fill=color,
            width=width,
        )


def draw_sectors(draw, size, sectors, *, outlines=None, projected=False):
    """Geometry comes from this assessment, before camera-to-command mirroring.

    L/C/R identify the analysis side. A question mark means unknown/incomplete,
    or that the actual projection is unavailable. Never invent a projected ROI.
    """
    w, h = size
    line_width = max(1, round(min(size) / 160))
    labels = []
    for side, code, color in SIDES:
        row = sectors.get(side, {})
        uncertain = (
            row.get("state") not in {"clear", "obstacle"}
            or row.get("coverage_complete") is not True
        )
        label = code + ("?" if uncertain else "")
        if projected:
            points = (outlines or {}).get(side, [])
            visible = [p for p in points if p is not None]
            previous = None
            for point in points:
                current = (point[0] * w, point[1] * h) if point is not None else None
                if previous is not None and current is not None:
                    draw.line((previous, current), fill=color, width=line_width)
                previous = current
            if visible:
                point = min(visible, key=lambda p: p[1])
                labels.append((side, (point[0] * w, point[1] * h), label, color))
            else:
                # This is an availability badge, not an invented region boundary.
                labels.append(
                    (
                        side,
                        ({"left": 12, "center": w / 2, "right": w - 12}[side], 22),
                        code + "?",
                        color,
                    )
                )
            continue
        bounds = row.get("corridor")
        if not bounds:
            continue
        x0, y0, x1, y1 = bounds
        left, top = max(0, math.ceil(x0 * w)), max(0, math.ceil(y0 * h))
        right, bottom = (
            min(w - 1, math.ceil(x1 * w) - 1),
            min(h - 1, math.ceil(y1 * h) - 1),
        )
        if left > right or top > bottom:
            continue
        # The central STOP border is drawn separately outside its scored pixels.
        if side != "center":
            if row.get("coverage_complete") is True:
                draw.rectangle(
                    (left, top, right, bottom), outline=color, width=line_width
                )
            else:
                for start, end in (
                    ((left, top), (right, top)),
                    ((right, top), (right, bottom)),
                    ((right, bottom), (left, bottom)),
                    ((left, bottom), (left, top)),
                ):
                    _dashed(draw, start, end, color, line_width)
        _label(draw, size, (left + 3, top), label, color)
    occupied: list[tuple[int, int, int, int]] = []
    for _side, point, label, color in sorted(
        labels, key=lambda entry: entry[0] != "center"
    ):
        _label(draw, size, point, label, color, occupied)


def preview_sectors(result):
    """Bounded source-space metadata for the JPEG, independent of planner keys."""
    keys = ("state", "coverage_complete", "corridor", "heading_deg", "geometry")
    return {
        side: {key: row[key] for key in keys if key in row}
        for side, row in result.get("sectors", {}).items()
        if side in {"left", "center", "right"}
    }
