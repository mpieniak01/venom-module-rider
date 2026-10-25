"""Bounded full-map proposals. Diagnostics never authorize movement."""

import json
import math

from .optical_motion import OpticalMotion
from .spatial_evidence import corridor_mask


class ObjectDiagnostics:
    def __init__(self):
        self.motion = OpticalMotion()
        self.reset()

    def reset(self):
        self.motion.reset()
        self.tracks = {}
        self.next_id = 1
        self.tick = 0
        self.identity = None
        self.sequence = None
        self.captured_s = None

    def observe(self, maps, corridor, *, context, sequence, captured_s, params):
        import cv2
        import numpy as np

        if not params.get("object_tracking_enabled", False):
            self.reset()
            return {"diagnostic_only": True, "state": "disabled", "objects": []}
        identity = json.dumps(dict(context), sort_keys=True, allow_nan=False)
        if self.identity is not None and (
            identity != self.identity
            or sequence <= self.sequence
            or captured_s <= self.captured_s
            or (captured_s - self.captured_s) * 1000 > params["max_gap_ms"]
        ):
            self.reset()
        self.identity, self.sequence, self.captured_s = identity, sequence, captured_s
        self.tick += 1
        gray = maps.get("gray")
        if gray is None or not math.isfinite(captured_s):
            self.reset()
            return {"diagnostic_only": True, "state": "unavailable", "objects": []}
        raw = maps["obstacle"]
        if raw.ndim != 2 or raw.dtype != np.bool_ or raw.shape != gray.shape:
            raise ValueError("navigation_object_maps")
        confidence = maps.get("confidence")
        if confidence is not None and (
            confidence.shape != raw.shape or not np.isfinite(confidence).all()
        ):
            raise ValueError("navigation_object_maps")
        route = corridor_mask(maps.get("corridor", corridor), raw.shape)
        h, w = raw.shape
        labels = maps.get("labels")
        excluded = [0, 3, 5, 8, 14, 28]
        if params.get("object_policy_enabled"):
            excluded = [0, 5, 8, 14, *params.get("traversable_labels", (3,))]
        foreground = ~np.isin(labels, excluded) if labels is not None else raw
        foreground = cv2.morphologyEx(
            foreground.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)
        )
        tracking = self.motion.update(gray, params["min_tracking_ratio"])
        transform = tracking.get("_transform")
        if tracking["status"] == "lost":
            self.tracks.clear()
        for tid in list(self.tracks):
            track = self.tracks[tid]
            if transform is not None:
                track["mask"] = cv2.warpAffine(
                    track["mask"].astype(np.uint8),
                    transform,
                    (w, h),
                    flags=cv2.INTER_NEAREST,
                ).astype(bool)
            if (
                self.tick - track["tick"] > params["object_max_gap_frames"] + 1
                or not track["mask"].any()
            ):
                del self.tracks[tid]
        count, parts, stats, _ = cv2.connectedComponentsWithStats(
            foreground, connectivity=8
        )
        indices = sorted(range(1, count), key=lambda i: -stats[i, cv2.CC_STAT_AREA])[
            :16
        ]
        used = set()
        objects = []
        for index in indices:
            area = int(stats[index, cv2.CC_STAT_AREA])
            if area < params["object_min_area_px"]:
                continue
            mask = parts == index
            y, x = np.nonzero(mask)
            box = [int(x.min()), int(y.min()), int(x.max() + 1), int(y.max() + 1)]
            hist = cv2.calcHist([gray], [0], mask.astype(np.uint8), [16], [0, 256])
            cv2.normalize(hist, hist, norm_type=cv2.NORM_L1)
            best, best_score = None, 0.0
            for tid, track in self.tracks.items():
                if tid in used:
                    continue
                overlap = float(
                    (mask & track["mask"]).sum() / max(1, (mask | track["mask"]).sum())
                )
                ratio = area / max(1, int(track["mask"].sum()))
                appearance = 1 - float(
                    cv2.compareHist(hist, track["hist"], cv2.HISTCMP_BHATTACHARYYA)
                )
                score = 0.75 * overlap + 0.25 * appearance
                if (
                    overlap >= params["object_match_iou"]
                    and appearance >= params["object_appearance_min"]
                    and params["object_area_ratio_min"]
                    <= ratio
                    <= params["object_area_ratio_max"]
                    and score > best_score
                ):
                    best, best_score = tid, score
            if best is None:
                best = self.next_id
                self.next_id += 1
                self.tracks[best] = {"history": []}
            track = self.tracks[best]
            history = track["history"]
            observation = {"area": area, "bottom": box[3], "captured_s": captured_s}
            history.append(observation)
            del history[: -int(params["object_trend_frames"])]
            growth = area / history[0]["area"]
            lower = box[3] - history[0]["bottom"]
            approaching = (
                len(history) >= params["object_trend_frames"]
                and growth >= params["object_growth_min"]
                and lower >= params["object_lower_min_px"]
            )
            track.update(mask=mask.copy(), hist=hist, sequence=sequence, tick=self.tick)
            used.add(best)
            objects.append(
                {
                    "id": best,
                    "bbox": box,
                    "area_pixels": area,
                    "median_confidence": float(np.median(confidence[mask]))
                    if confidence is not None
                    else None,
                    "route_pixels": int((mask & route).sum()),
                    "growth": float(growth),
                    "lower_px": lower,
                    "observations": len(history),
                    "approaching_candidate": bool(approaching),
                    "clipped": box[0] == 0 or box[1] == 0 or box[2] == w or box[3] == h,
                }
            )
        if len(self.tracks) > 32:
            keep = sorted(
                self.tracks, key=lambda tid: self.tracks[tid]["tick"], reverse=True
            )[:32]
            self.tracks = {tid: self.tracks[tid] for tid in keep}
        return {
            "diagnostic_only": True,
            "state": "current",
            "scope": "full_semantic_map"
            if labels is not None
            else "available_depth_context",
            "ground_reference_valid": maps.get("valid", True) is True,
            "tracking": tracking["status"],
            "objects": objects,
            **({"_motion": tracking} if params.get("object_policy_enabled") else {}),
        }


def render_objects(payload, diagnostics):
    """Draw on the same analyzed capture; no delayed UI coordinate overlay."""
    import io

    from PIL import Image, ImageDraw

    if diagnostics.get("state") != "current" or not diagnostics.get("objects"):
        return payload
    try:
        with Image.open(io.BytesIO(payload)) as source:
            image = source.convert("RGB")
        draw = ImageDraw.Draw(image)
        for item in diagnostics["objects"][:16]:
            box = item["bbox"]
            draw.rectangle(
                (box[0], box[1], box[2] - 1, box[3] - 1),
                outline=(195, 130, 255),
                width=1,
            )
            lower = item["lower_px"]
            displacement = (
                f"{lower:+.1f}" if isinstance(lower, float) else f"{lower:+d}"
            )
            text = f"T{item['id']} x{item['growth']:.2f} y{displacement}"
            draw.text(
                (box[0], max(0, box[1] - 11)),
                text,
                fill=(220, 175, 255),
                stroke_width=1,
                stroke_fill=(0, 0, 0),
            )
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=85)
        return output.getvalue()
    except (OSError, ValueError):
        return payload
