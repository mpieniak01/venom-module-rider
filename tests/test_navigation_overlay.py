"""Source geometry scales to the actual preview; overlays grant no authority."""

import io

import numpy as np
from PIL import Image

from venom_module_rider.navigation_overlay import render_evidence
from venom_module_rider.navigation_profiles import DEFAULT_BEHAVIOR, parameters
from venom_module_rider.spatial_evidence import assess_maps


def jpeg(size):
    stream = io.BytesIO()
    Image.new("RGB", size, (40, 40, 40)).save(stream, format="JPEG")
    return stream.getvalue()


def spatial():
    floor = np.ones((100, 100), dtype=bool)
    return assess_maps(
        floor,
        ~floor,
        ~floor,
        (0.2, 0.2, 0.8, 0.9),
        parameters({**DEFAULT_BEHAVIOR, "profile_id": "balanced"}),
    )


def test_preview_contains_actual_route_near_guard_and_boundary_with_source_scaling():
    row = spatial()
    result = render_evidence(jpeg((200, 100)), row)
    with Image.open(io.BytesIO(result)) as image:
        assert image.size == (200, 100)
        # x=20 in source becomes x=40 on this stretched model preview.
        assert sum(image.getpixel((40, 40))) > 450
        r, g, b = image.getpixel((80, 21))
        assert g > r and b > r  # cyan boundary, not an invented metric clearance.
    row["guard_clear"] = False
    danger = render_evidence(jpeg((100, 100)), row)
    with Image.open(io.BytesIO(danger)) as image:
        r, g, b = image.getpixel((50, 66))
        assert r > g + 50 and r > b + 50


def test_invalid_evidence_and_bad_preview_never_create_geometry():
    payload = jpeg((100, 100))
    assert render_evidence(payload, {"valid": False}) is payload
    assert render_evidence(payload, {"valid": True}) is payload
    assert render_evidence(b"not-jpeg", spatial()) == b"not-jpeg"
    assert render_evidence(None, spatial()) is None


def test_missing_private_zones_keep_only_verified_boundary():
    row = spatial()
    row.pop("_corridor_mask")
    row.pop("_near_mask")
    assert render_evidence(jpeg((100, 100)), row).startswith(b"\xff\xd8")
    row["boundary"] = []
    assert render_evidence(jpeg((100, 100)), row).startswith(b"\xff\xd8")
