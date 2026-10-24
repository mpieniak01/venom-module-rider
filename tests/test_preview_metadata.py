"""Producer/consumer contract regressions with production-shaped temporal output."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from venom_module_rider.api.vision_preview import processed_camera

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/preview-metadata-v1.json").read_text()
)


@pytest.mark.parametrize("diagnostic", [False, True])
async def test_snapshot_projects_full_temporal_diagnostics_for_the_browser(diagnostic):
    result = dict(FIXTURE["result"])
    result["preview_only"] = diagnostic
    expected = {**FIXTURE["preview"], "preview_only": diagnostic}

    class Domain:
        def transport(self):
            return SimpleNamespace(device=SimpleNamespace(id="fixture"))

        async def read(self, path, *_args, **_kwargs):
            return {
                "data": (
                    {
                        "corridor": [0.35, 0.8, 0.65, 0.95],
                        "revision": result["config_revision"],
                    }
                    if path.endswith("config")
                    else {"running": True, "stale": False, "run_id": result["run_id"]}
                )
            }

    class Loop:
        def clock(self):
            return 10.1

        async def wait_for_preview(self, *_args, **_kwargs):
            return 10.0, b"jpeg-fixture", result, 48.0

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(rider=Domain(), rider_vision=Loop()))
    )
    assert len(json.dumps(result, separators=(",", ":"))) > 4096
    response = await processed_camera(request)
    header = response.headers["x-rider-analysis-json"]
    assert len(header) <= 4096
    assert json.loads(header) == expected
    assert response.body == b"jpeg-fixture"
    assert response.headers["x-rider-vision-action"] == "stop"
    assert float(response.headers["x-rider-frame-age-ms"]) == pytest.approx(100)


async def test_native_mjpeg_uses_the_same_small_contract():
    from test_stream_browser import Browser, setup, unpack

    from venom_module_rider.api.pipeline_routes import browser_stream

    pi, domain, hub, loop, state = await setup()
    jpeg = b"\xff\xd8fixture\xff\xd9"
    loop.detector.detect = lambda *_args: {**FIXTURE["result"], "_preview": jpeg}
    iterator = None
    try:
        iterator = (await browser_stream("venom", Browser(state))).body_iterator
        headers, image = unpack(await anext(iterator))
        assert image == jpeg
        assert len(headers["x-rider-analysis-json"]) <= 4096
        analysis = json.loads(headers["x-rider-analysis-json"])
        source = json.loads(headers["x-rider-source-json"])
        assert analysis["source"]["frame_id"] == source["frame_id"] == "session-1"
        assert analysis["source"]["sha256"] == source["sha256"]
        assert analysis["sector_space"] == "image"
        assert analysis["sectors"] == FIXTURE["preview"]["sectors"]
        assert analysis["action"] == headers["x-rider-vision-action"] == "stop"
        assert "temporal" not in analysis and "paths" not in analysis
        assert pi.snapshot_requests == 0
    finally:
        if iterator:
            await iterator.aclose()
        await hub.close()
        await loop.close()
        await domain.close()


def test_projection_keeps_identity_and_model_fields_without_mutating_decision():
    import copy

    from venom_module_rider.preview_metadata import analysis_header

    result = copy.deepcopy(FIXTURE["result"])
    result.update(
        model_id="segformer-b0-ade20k",
        artifact_sha256="d" * 64,
        valid_for_ms=150,
        action="go",
        journey={"id": "real-motion-journey"},
    )
    original = copy.deepcopy(result)
    projected = json.loads(analysis_header(result))
    assert projected["source"] == result["source"]
    assert projected["model_id"] == result["model_id"]
    assert projected["artifact_sha256"] == result["artifact_sha256"]
    assert projected["model_sha256"] == result["model_sha256"]
    assert projected["config_revision"] == result["config_revision"]
    assert projected["action"] == "stop"
    assert not {"valid_for_ms", "journey", "paths", "temporal"} & projected.keys()
    assert result == original


@pytest.mark.parametrize("invalid", ["x" * 4097, float("nan"), object()])
def test_invalid_or_excessive_metadata_is_rejected_before_sending(invalid):
    from venom_module_rider.errors import RiderError
    from venom_module_rider.preview_metadata import analysis_header

    with pytest.raises(RiderError, match="vision_preview_metadata_invalid"):
        analysis_header({**FIXTURE["result"], "reason": invalid})


def test_planner_diagnostics_cannot_grow_the_preview_header():
    from venom_module_rider.preview_metadata import (
        MAX_PREVIEW_ANALYSIS_CHARS,
        analysis_header,
    )

    result = {
        **FIXTURE["result"],
        "temporal": {"spatial": {"regions": ["diagnostic"] * 10000}},
        "paths": {"left": ["planner"] * 10000},
        "future_diagnostic": "details" * 10000,
    }
    header = analysis_header(result)
    assert json.loads(header) == FIXTURE["preview"]
    assert len(header) < MAX_PREVIEW_ANALYSIS_CHARS
