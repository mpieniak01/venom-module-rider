"""Contiguous heading gaps after full circumscribed-footprint projection."""

import math
from typing import Any

import numpy as np


def assess(depth, reference, camera, tolerance, outlier_limit):
    radius = camera.radius_ratio
    step = radius / 4
    along = np.arange(0, camera.lookahead_ratio + radius + step / 2, step)
    lateral = np.linspace(-radius, radius, 9)
    s, t = np.meshgrid(along, lateral)
    # Newly swept capsule only: the robot already occupies its initial disc.
    capsule = (
        s - np.clip(s, 0, camera.lookahead_ratio)
    ) ** 2 + t * t <= radius * radius + 1e-9
    capsule &= s * s + t * t > radius * radius + 1e-9
    s, t = s[capsule], t[capsule]
    if s.size > 2048:
        indices = np.linspace(0, s.size - 1, 2048, dtype=int)
        s, t = s[indices], t[indices]
    headings = np.linspace(-camera.max_heading_deg, camera.max_heading_deg, 49)
    h, w = depth.shape
    py, px = np.mgrid[:h, :w]
    ray = np.stack(
        (
            (px / w - camera.cx) / camera.fx,
            (py / h - camera.cy) / camera.fy,
            np.ones(depth.shape),
        )
    )
    observed = ray * depth
    signed = (
        1 - np.einsum("i,ihw->hw", reference["normal"], observed) / reference["height"]
    )
    ox = np.einsum("i,ihw->hw", reference["right"], observed) / reference["height"]
    oz = np.einsum("i,ihw->hw", reference["forward"], observed) / reference["height"]
    # A recess is blocked at the missing ground location, not only its distant bottom.
    plane_inverse = (
        reference["coefficients"][0] * px / w
        + reference["coefficients"][1] * py / h
        + reference["coefficients"][2]
    )
    # The 35% band belongs only to fitting. A tilted-camera sweep may project
    # above it; keep every visible positive obstacle point. A recess requires
    # an actual ground intersection (sky beyond the horizon is not a hole).
    occupied = (signed > tolerance) | ((signed < -tolerance) & (plane_inverse > 0))
    floor_depth = np.divide(
        1.0, plane_inverse, out=np.zeros_like(depth), where=plane_inverse > 0
    )
    hole = signed < -tolerance
    floor = ray * floor_depth
    ox = np.where(
        hole,
        np.einsum("i,ihw->hw", reference["right"], floor) / reference["height"],
        ox,
    )[occupied]
    oz = np.where(
        hole,
        np.einsum("i,ihw->hw", reference["forward"], floor) / reference["height"],
        oz,
    )[occupied]
    candidates: list[dict[str, Any]] = []
    for angle in headings:
        a = math.radians(float(angle))
        x = s * math.sin(a) + t * math.cos(a)
        z = s * math.cos(a) - t * math.sin(a)
        points = (
            reference["normal"][:, None]
            + reference["right"][:, None] * x
            + reference["forward"][:, None] * z
        ) * reference["height"]
        valid = points[2] > 1e-6
        projected_u = (
            np.divide(points[0], points[2], out=np.zeros_like(x), where=valid)
            * camera.fx
            + camera.cx
        )
        projected_v = (
            np.divide(points[1], points[2], out=np.zeros_like(x), where=valid)
            * camera.fy
            + camera.cy
        )
        h, w = depth.shape
        valid &= (
            (projected_u >= 0)
            & (projected_u < 1)
            & (projected_v >= 0)
            & (projected_v < 1)
        )
        complete = bool(valid.all()) and valid.size > 0
        ix = np.clip((projected_u * w).astype(int), 0, w - 1)
        iy = np.clip((projected_v * h).astype(int), 0, h - 1)
        coefficients = reference["coefficients"]
        residual = 1 - depth[iy, ix] * (
            coefficients[0] * projected_u
            + coefficients[1] * projected_v
            + coefficients[2]
        )
        blocked = abs(residual) > tolerance
        near = s <= radius + min(0.25, camera.lookahead_ratio / 3)
        guard_clear = (
            bool(valid[near].all() and not blocked[near].any()) if near.any() else False
        )
        fraction = float(blocked.mean()) if blocked.size else 1.0
        allowed = math.floor(outlier_limit * blocked.size)
        state = (
            "clear"
            if complete and int(blocked.sum()) <= allowed
            else "obstacle"
            if complete
            else "unknown"
        )
        os = ox * math.sin(a) + oz * math.cos(a)
        ot = ox * math.cos(a) - oz * math.sin(a)
        distances = (os - np.clip(os, 0, camera.lookahead_ratio)) ** 2 + ot * ot
        collisions = (distances <= radius * radius) & (oz >= 0)
        near_distance = (
            os - np.clip(os, 0, min(0.25, camera.lookahead_ratio / 3))
        ) ** 2 + ot * ot
        if ((near_distance <= radius * radius) & (oz >= 0)).any():
            guard_clear = False
        if collisions.any() and complete:
            state = "obstacle"
        clearance = (
            float(min(1.0, np.sqrt(distances[oz >= 0].min()) / radius))
            if (oz >= 0).any()
            else 1.0
        )
        candidates.append(
            {
                "heading_deg": float(angle),
                "state": state,
                "coverage_complete": complete,
                "outlier_fraction": fraction,
                "clear_fraction": float((valid & ~blocked).mean()),
                "clearance": clearance,
                "obstacle_points": int(collisions.sum()),
                "guard_clear": guard_clear,
                "farther_outlier_pixels": int((residual < -tolerance).sum())
                + int((hole[occupied] & collisions).sum()),
            }
        )
    gaps: list[dict[str, Any]] = []
    start = None
    for index, row in enumerate(candidates + [{"state": "end"}]):
        if row["state"] == "clear" and row.get("guard_clear") and start is None:
            start = index
        elif (
            row["state"] != "clear" or not row.get("guard_clear")
        ) and start is not None:
            group = candidates[start:index]
            gaps.append(
                {
                    "start_deg": group[0]["heading_deg"],
                    "end_deg": group[-1]["heading_deg"],
                    "width_deg": group[-1]["heading_deg"] - group[0]["heading_deg"],
                    "samples": len(group),
                }
            )
            start = None
    for row in candidates:
        row["gap_width_deg"] = max(
            (
                gap["width_deg"]
                for gap in gaps
                if gap["start_deg"] <= row["heading_deg"] <= gap["end_deg"]
            ),
            default=0.0,
        )
    paths = {}
    for side, sign in (("left", -1), ("right", 1)):
        choices = [
            row
            for row in candidates
            if row["heading_deg"] * sign > 0
            and row["state"] == "clear"
            and row["guard_clear"]
        ]
        if choices:
            row = min(choices, key=lambda item: abs(item["heading_deg"]))
            paths[side] = {**row, "geometry": "ground_capsule"}
        else:
            paths[side] = {
                "state": "unknown",
                "coverage_complete": False,
                "geometry": "ground_capsule",
            }
    center = candidates[len(candidates) // 2]
    paths["guard"] = {
        "state": "clear" if center["guard_clear"] else "obstacle",
        "coverage_complete": center["coverage_complete"],
        "geometry": "ground_capsule",
    }
    return {
        "_path_overlay": {
            "center": envelope(reference, camera, 0.0),
            **{
                side: envelope(reference, camera, row["heading_deg"])
                for side, row in paths.items()
                if side != "guard" and row.get("state") == "clear"
            },
        },
        "paths": paths,
        "gaps": gaps,
        "heading_candidates": candidates,
        "forward_clear_fraction": center.get("clearance", center["clear_fraction"]),
        "state": center["state"],
        "farther_outlier_pixels": center["farther_outlier_pixels"],
    }


def envelope(reference, camera, heading):
    """Projected capsule outline; missing pixels split strokes at the FOV edge."""
    radius = camera.radius_ratio
    angles = np.linspace(-math.pi / 2, 3 * math.pi / 2, 97)
    x = radius * np.sin(angles)
    z = radius * np.cos(angles) + np.where(
        np.cos(angles) >= 0, camera.lookahead_ratio, 0
    )
    a = math.radians(heading)
    rx = x * math.cos(a) + z * math.sin(a)
    rz = z * math.cos(a) - x * math.sin(a)
    points = (
        reference["normal"][:, None]
        + reference["right"][:, None] * rx
        + reference["forward"][:, None] * rz
    )
    outline: list[tuple[float, float] | None] = []
    for column in points.T:
        if column[2] <= 1e-6:
            outline.append(None)
            continue
        u = column[0] / column[2] * camera.fx + camera.cx
        v = column[1] / column[2] * camera.fy + camera.cy
        outline.append((float(u), float(v)) if 0 <= u < 1 and 0 <= v < 1 else None)
    return outline
