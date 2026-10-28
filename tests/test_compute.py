import asyncio
import io
import json
import time
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from venom_module_rider.api.routes import router
from venom_module_rider.compute import ComputeService
from venom_module_rider.compute_contract import JobRequest, validate_media
from venom_module_rider.compute_demo import DemoEngines
from venom_module_rider.config import Device, configured_devices
from venom_module_rider.errors import RiderError
from venom_module_rider.service import RiderService


def request(service, *, device="pi", boot="boot", client="cli", **changes):
    lease = service.open_session(device, boot, client)
    return JobRequest.model_validate(
        {
            "protocol_version": 1,
            "executor_session": service.executor_session,
            "session_id": lease["session_id"],
            "device_session": boot,
            "job_id": "job",
            "input_id": "frame",
            "stream_id": "stream",
            "task": "text.respond",
            "captured_at": time.time() - 1,
            "input_age_ms": 1000.0,
            "ttl_ms": 3000,
            "sequence": 0,
            **changes,
        }
    )


async def finish(service, device, job):
    for _ in range(100):
        result = await service.observe(device, job)
        if result["state"] not in {"queued", "running"}:
            return result
        await asyncio.sleep(0.002)
    raise AssertionError("job did not finish")


class BlockingEngines(DemoEngines):
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.version = "demo-v1"
        self.calls = 0

    async def revision(self):
        return self.version

    async def execute(self, *args):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return await super().execute(*args)


async def test_whole_text_observation_keeps_source_time_and_no_idle_work():
    service = ComputeService(DemoEngines())
    job = request(service)
    queued = await service.submit("pi", job, b"hello", "text/plain")
    assert queued["state"] == "queued"
    result = await finish(service, "pi", "job")
    assert result["data"]["text"] == "Demo: hello"
    assert result["captured_at"] == job.captured_at
    assert result["quality"]["simulated"] is True
    assert result["metrics"]["source_age_ms"] >= 1000
    assert not service.jobs["job"].payload
    await asyncio.sleep(0.01)
    assert service.worker.done() and not service.pending
    await service.close()
    assert not service.jobs and not service.leases


async def test_shared_identity_has_distinct_goal_leases():
    service = ComputeService(DemoEngines())
    voice = request(service, client="voice", job_id="voice")
    vision = request(service, client="vision", job_id="vision")
    assert voice.session_id != vision.session_id
    assert service.open_session("pi", "boot", "voice")["session_id"] == voice.session_id
    await service.submit("pi", voice, b"hello", "text/plain")
    await service.submit("pi", vision, b"hello", "text/plain")
    service.close_session("pi", voice.session_id)
    assert (await finish(service, "pi", "vision"))["state"] == "succeeded"
    with pytest.raises(RiderError, match="compute_job_missing"):
        await service.observe("pi", "voice")
    await service.close()


async def test_new_boot_invalidates_all_old_goals_and_cannot_return():
    service = ComputeService(DemoEngines())
    old = request(service, client="voice")
    service.open_session("pi", "newboot", "voice")
    with pytest.raises(RiderError, match="compute_stale_session"):
        await service.submit("pi", old, b"old", "text/plain")
    with pytest.raises(RiderError, match="compute_stale_session"):
        service.open_session("pi", "boot", "vision")
    await service.close()


async def test_abrupt_goal_restart_replaces_lease_and_old_off_cannot_cancel_new():
    service = ComputeService(DemoEngines())
    old = service.open_session("pi", "boot", "voice", "oldprocess")
    other = service.open_session("pi", "boot", "vision", "visionprocess")
    old_job = request(service, device="pi", client="cli", job_id="old")
    old_job = old_job.model_copy(update={"session_id": old["session_id"]})
    await service.submit("pi", old_job, b"old", "text/plain")
    new = service.open_session("pi", "boot", "voice", "newprocess")
    assert old["session_id"] != new["session_id"]
    assert (
        service.open_session("pi", "boot", "voice", "newprocess")["session_id"]
        == new["session_id"]
    )
    assert service.lease("pi", other["session_id"])
    with pytest.raises(RiderError, match="compute_stale_session"):
        service.close_session("pi", old["session_id"])
    new_job = old_job.model_copy(
        update={"session_id": new["session_id"], "job_id": "new"}
    )
    await service.submit("pi", new_job, b"new", "text/plain")
    assert (await finish(service, "pi", "new"))["data"]["text"] == "Demo: new"
    await service.close()


async def test_duplicate_order_identity_and_cross_device_read_are_rejected():
    service = ComputeService(DemoEngines())
    job = request(service)
    await service.submit("pi", job, b"a", "text/plain")
    for changes, code in [
        ({}, "compute_duplicate_job"),
        ({"job_id": "older"}, "compute_out_of_order"),
        ({"executor_session": "old"}, "compute_stale_session"),
    ]:
        with pytest.raises(RiderError, match=code):
            await service.submit(
                "pi", job.model_copy(update=changes), b"a", "text/plain"
            )
    with pytest.raises(RiderError, match="compute_job_missing"):
        await service.observe("other", "job")
    await service.close()


async def test_retired_boot_cache_is_bounded_without_rejecting_new_boots():
    service = ComputeService(DemoEngines())
    try:
        for index in range(40):
            session = service.open_session("pi", f"boot{index}")
        assert len(service.retired_boots["pi"]) == 32
        assert "boot0" not in service.retired_boots["pi"]
        with pytest.raises(RiderError, match="compute_stale_session"):
            service.open_session("pi", "boot38")
        assert service.lease("pi", session["session_id"]).device_session == "boot39"
    finally:
        await service.close()


def test_demo_configuration_with_omitted_executor_is_controlled():
    from venom_module_rider.demo import DemoPi, envelope
    from venom_module_rider.demo_compute import compute_response

    state = DemoPi()
    result = compute_response(
        state, "/api/compute/config", {"revision": state.compute_revision}, envelope
    )
    assert result.status_code == 200 and state.compute_executor is None


async def test_saturation_is_explicit_and_does_not_change_manual_state():
    engines = BlockingEngines()
    service = ComputeService(engines)
    await service.submit("pi", request(service), b"a", "text/plain")
    await engines.entered.wait()
    for i in range(1, 5):
        await service.submit(
            "pi", request(service, job_id=f"j{i}", sequence=i), b"a", "text/plain"
        )
    with pytest.raises(RiderError, match="compute_saturated") as error:
        await service.submit(
            "pi", request(service, job_id="last", sequence=5), b"a", "text/plain"
        )
    assert error.value.status == 429
    assert service.counters["rejected"] == 1 and len(service.pending) == 4
    engines.release.set()
    await service.close()


async def test_latest_image_supersedes_old_running_result():
    engines = BlockingEngines()
    service = ComputeService(engines)
    data = io.BytesIO()
    Image.new("RGB", (10, 10)).save(data, "PNG")
    old = request(service, task="vision.describe")
    await service.submit("pi", old, data.getvalue(), "image/png")
    await engines.entered.wait()
    new = old.model_copy(
        update={"job_id": "new", "input_id": "newframe", "sequence": 1}
    )
    await service.submit("pi", new, data.getvalue(), "image/png")
    assert (await service.observe("pi", "job"))["state"] == "superseded"
    engines.release.set()
    assert (await finish(service, "pi", "new"))["input_id"] == "newframe"
    await service.close()


async def test_cancel_and_deadline_prevent_late_output():
    for expiry in (False, True):
        engines = BlockingEngines()
        service = ComputeService(engines)
        await service.submit("pi", request(service, ttl_ms=100), b"a", "text/plain")
        await engines.entered.wait()
        if expiry:
            await asyncio.sleep(0.12)
        else:
            service.cancel("pi", "job")
        result = await finish(service, "pi", "job")
        assert result["state"] == ("expired" if expiry else "cancelled")
        assert not result["data"] and not result["output_available"]
        await service.close()


async def test_successful_speech_expires_before_output_can_be_used():
    service = ComputeService(DemoEngines())
    await service.submit(
        "pi",
        request(service, task="audio.synthesize", ttl_ms=100),
        b"hello",
        "text/plain",
    )
    assert (await finish(service, "pi", "job"))["state"] == "succeeded"
    await asyncio.sleep(0.12)
    result = await service.observe("pi", "job")
    assert result["state"] == "expired" and not result["data"]
    with pytest.raises(RiderError, match="compute_output_unavailable"):
        await service.output("pi", "job")
    await service.close()


async def test_goal_off_during_revision_resolution_does_not_run_inference():
    engines = BlockingEngines()
    entered, release = asyncio.Event(), asyncio.Event()

    async def revision():
        entered.set()
        await release.wait()
        return "demo-v1"

    engines.revision = revision
    service = ComputeService(engines)
    job = request(service)
    await service.submit("pi", job, b"a", "text/plain")
    await entered.wait()
    service.close_session("pi", job.session_id)
    release.set()
    await service.worker
    assert engines.calls == 0 and not service.jobs
    await service.close()


async def test_parallel_admission_keeps_session_memory_bounded():
    engines = DemoEngines()
    service = ComputeService(engines)
    request(service)
    lease = next(iter(service.leases.values()))
    lease.seen = {f"old{i}" for i in range(4095)}
    lease.sequences = {("text.respond", f"s{i}"): 0 for i in range(31)}
    gate = asyncio.Event()
    reached = 0
    original = engines.capabilities

    async def capabilities():
        nonlocal reached
        reached += 1
        if reached == 2:
            gate.set()
        await gate.wait()
        return await original()

    engines.capabilities = capabilities
    results = await asyncio.gather(
        *[
            service.submit(
                "pi",
                request(service, job_id=f"new{i}", stream_id=f"new{i}"),
                b"a",
                "text/plain",
            )
            for i in range(2)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(row, RiderError) for row in results) == 1
    assert len(lease.seen) == 4096 and len(lease.sequences) == 32
    await service.close()


async def test_model_switch_invalidates_running_and_completed_results():
    engines = BlockingEngines()
    service = ComputeService(engines)
    await service.submit("pi", request(service), b"a", "text/plain")
    await engines.entered.wait()
    engines.version = "new"
    engines.release.set()
    result = await finish(service, "pi", "job")
    assert result["state"] == "expired" and result["error"] == "compute_runtime_changed"
    await service.close()


async def test_engine_failure_is_not_mock_success():
    engines = DemoEngines()

    async def fail(*_):
        raise ValueError("failure")

    engines.execute = fail
    service = ComputeService(engines)
    await service.submit("pi", request(service), b"a", "text/plain")
    assert (await finish(service, "pi", "job"))["state"] == "failed"
    await service.close()


@pytest.mark.parametrize(
    "task,data,mime",
    [
        ("text.respond", b"", "text/plain"),
        ("text.respond", b"\xff", "text/plain"),
        ("text.respond", b"x", "application/json"),
        ("text.respond", b"x" * 4097, "text/plain"),
        ("audio.transcribe", b"RIFFjunk", "audio/wav"),
        ("audio.transcribe", b"x", "text/plain"),
        ("vision.describe", b"x", "image/png"),
        ("vision.describe", b"x", "image/gif"),
    ],
)
def test_invalid_binary_inputs_are_bounded(task, data, mime):
    service = ComputeService(DemoEngines())
    with pytest.raises(RiderError):
        validate_media(request(service, task=task), data, mime)


async def test_audio_transcribe_and_tts_return_pcm16_wav():
    service = ComputeService(DemoEngines())
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 800)
    await service.submit(
        "pi", request(service, task="audio.transcribe"), stream.getvalue(), "audio/wav"
    )
    assert (await finish(service, "pi", "job"))["data"]["text"]
    await service.submit(
        "pi",
        request(service, task="audio.synthesize", job_id="tts", sequence=1),
        b"hello",
        "text/plain",
    )
    assert (await finish(service, "pi", "tts"))["output_available"]
    data, mime = await service.output("pi", "tts")
    with wave.open(io.BytesIO(data)) as audio:
        assert audio.getframerate() == 16000 and audio.getsampwidth() == 2
    assert mime == "audio/wav"
    service.cancel("pi", "tts")
    with pytest.raises(RiderError, match="compute_output_unavailable"):
        await service.output("pi", "tts")
    await service.close()


def host(monkeypatch):
    monkeypatch.setenv("RIDER_COMPUTE_TOKEN", "fixture-secret")
    monkeypatch.delenv("RIDER_ACCESS_TOKEN", raising=False)
    device = Device("demo", "Demo", "http://127.0.0.1", "demo")
    app = FastAPI()
    app.state.rider = RiderService((device,))
    app.state.rider_compute = ComputeService(DemoEngines())
    app.include_router(router)
    return app


def test_http_contract_authentication_and_no_browser_device_job(monkeypatch):
    app = host(monkeypatch)
    headers = {"X-Rider-Device": "demo", "Authorization": "Bearer fixture-secret"}
    with TestClient(app) as client:
        assert client.get("/api/v1/rider/compute/capabilities").status_code == 403
        assert (
            client.get(
                "/api/v1/rider/compute/capabilities",
                headers={"X-Rider-Device": "demo", b"Authorization": b"Bearer \xff"},
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/api/v1/rider/compute/capabilities",
                headers={**headers, "Origin": "http://evil.invalid"},
            ).status_code
            == 403
        )
        caps = client.get("/api/v1/rider/compute/capabilities", headers=headers).json()
        assert caps["protocol_version"] == 1 and len(caps["tasks"]) == 5
        assert caps["tasks"]["conversation.respond"]["available"] is False
        session = client.post(
            "/api/v1/rider/compute/sessions",
            headers=headers,
            json={
                "protocol_version": 1,
                "device_session": "boot",
                "client_id": "voice",
            },
        ).json()
        job = request(app.state.rider_compute, device="demo", client="voice")
        response = client.post(
            "/api/v1/rider/compute/jobs",
            content=b"hello",
            headers={
                **headers,
                "Content-Type": "text/plain",
                "X-Rider-Job": job.model_dump_json(),
            },
        )
        assert response.status_code == 202
        assert (
            client.delete(
                "/api/v1/rider/compute/sessions/" + session["session_id"],
                headers=headers,
            ).status_code
            == 200
        )
        assert client.get("/api/v1/rider/config").json()["devices"] == [
            {"id": "demo", "label": "Demo", "mode": "demo"}
        ]


def test_private_compute_token_reference_not_in_public_configuration():
    configured = configured_devices(
        json.dumps(
            [
                {
                    "id": "a",
                    "label": "A",
                    "url": "http://localhost",
                    "compute_token_env": "PI_TOKEN",
                }
            ]
        )
    )
    assert configured[0].compute_token_env == "PI_TOKEN"
    assert "compute_token_env" not in configured[0].public()
    with pytest.raises(RiderError):
        configured_devices(
            json.dumps(
                [
                    {
                        **configured[0].public(),
                        "url": "http://localhost",
                        "compute_token_env": "secret value",
                    }
                ]
            )
        )


def test_http_job_observation_output_cancel_and_invalid_inputs(monkeypatch):
    app = host(monkeypatch)
    headers = {"X-Rider-Device": "demo", "Authorization": "Bearer fixture-secret"}
    base = "/api/v1/rider/compute"
    with TestClient(app) as client:
        lease = client.post(
            base + "/sessions",
            headers=headers,
            json={"protocol_version": 1, "device_session": "boot"},
        ).json()
        job = request(app.state.rider_compute, device="demo", task="audio.synthesize")
        meta = {
            **headers,
            "Content-Type": "text/plain; charset=utf-8",
            "X-Rider-Job": job.model_dump_json(),
        }
        assert (
            client.post(base + "/jobs", content=b"hello", headers=meta).status_code
            == 202
        )
        for _ in range(100):
            observed = client.get(base + "/jobs/job", headers=headers).json()
            if observed["state"] == "succeeded":
                break
            time.sleep(0.002)
        output = client.get(base + "/jobs/job/output", headers=headers)
        assert output.status_code == 200 and output.content.startswith(b"RIFF")
        assert (
            client.delete(base + "/jobs/job", headers=headers).json()["state"]
            == "cancelled"
        )
        assert client.get(base + "/jobs/job/output", headers=headers).status_code == 409
        assert (
            client.post(
                base + "/sessions", headers=headers, json={"protocol_version": 2}
            ).status_code
            == 400
        )
        assert (
            client.post(
                base + "/jobs",
                headers={**headers, "X-Rider-Job": "x" * 4097},
                content=b"x",
            ).status_code
            == 400
        )
        assert (
            client.post(
                base + "/jobs", headers={**headers, "X-Rider-Job": "{}"}, content=b"x"
            ).status_code
            == 400
        )
        assert (
            client.post(
                base + "/sessions",
                headers={**headers, "Content-Length": "99999"},
                content=b"{}",
            ).status_code
            == 413
        )
        assert (
            client.delete(
                base + "/sessions/" + lease["session_id"], headers=headers
            ).status_code
            == 200
        )


def test_operator_compute_status_and_selection_use_only_pi_authority(monkeypatch):
    from test_rider import domain

    service, pi = domain()
    app = host(monkeypatch)
    app.state.rider = service
    with TestClient(app) as client:
        session = service.session
        client.post(
            "/api/v1/rider/config",
            headers={"X-Rider-Session": session},
            json={"device_id": "demo"},
        )
        session = service.session
        status = client.get("/api/v1/rider/status?section=compute").json()
        assert status["compute"]["config"]["selected_executor"] is None
        revision = status["compute"]["config"]["revision"]
        result = client.post(
            "/api/v1/rider/compute/executor",
            headers={"X-Rider-Session": session},
            json={"executor_id": "venom", "revision": revision},
        )
        assert result.status_code == 200
        status = client.get("/api/v1/rider/status?section=compute").json()
        assert status["compute"]["config"]["selected_executor"] == "venom"
        assert pi.state == "DISARMED" and not pi.camera_on
        assert (
            client.post(
                "/api/v1/rider/compute/executor",
                headers={"X-Rider-Session": session},
                json={"url": "http://other.invalid"},
            ).status_code
            == 400
        )
        client.post(
            "/api/v1/rider/goal/s12_venom_vision",
            headers={"X-Rider-Session": session},
            json={"enabled": True},
        )
        assert (
            client.post(
                "/api/v1/rider/compute/executor",
                headers={"X-Rider-Session": session},
                json={"executor_id": None, "revision": revision + 1},
            ).status_code
            == 409
        )
        client.post(
            "/api/v1/rider/goal/s12_venom_vision",
            headers={"X-Rider-Session": session},
            json={"enabled": False},
        )
        assert not pi.camera_on and pi.state == "DISARMED"


async def test_status_invalidates_old_model_result_and_history_is_bounded():
    engines = BlockingEngines()
    engines.release.set()
    service = ComputeService(engines)
    await service.submit("pi", request(service), b"hi", "text/plain")
    await finish(service, "pi", "job")
    original = engines.capabilities

    async def changed():
        caps = await original()
        caps["config_revision"] = "new"
        return caps

    engines.capabilities = changed
    result = await service.status("pi")
    assert result["jobs"][0]["state"] == "expired" and result["jobs"][0]["data"] == {}
    assert not result["jobs"][0]["output_available"]
    await service.close()


async def test_unavailable_closed_oversized_output_and_history_retention():
    engines = DemoEngines()
    service = ComputeService(engines)
    for i in range(35):
        await service.submit(
            "pi", request(service, job_id=f"j{i}", sequence=i), b"hi", "text/plain"
        )
        await finish(service, "pi", f"j{i}")
    await service.status()
    assert len(service.jobs) == 32
    for job in service.jobs.values():
        job.finished -= 61
    await service.status()
    assert not service.jobs

    async def oversized(*args):
        result = await DemoEngines().execute(*args)
        result.binary = b"x" * (2 * 1024 * 1024 + 1)
        return result

    engines.execute = oversized
    await service.submit(
        "pi", request(service, job_id="big", sequence=36), b"hi", "text/plain"
    )
    assert (await finish(service, "pi", "big"))["error"] == "compute_output_size"
    await service.close()
    with pytest.raises(RiderError, match="compute_closed"):
        await service.capabilities()


async def test_external_worker_cancellation_propagates_after_job_cleanup():
    engines = BlockingEngines()
    service = ComputeService(engines)
    try:
        job = request(service)
        await service.submit("pi", job, b"hello", "text/plain")
        await asyncio.wait_for(engines.entered.wait(), 2)
        worker = service.worker
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
        assert service.jobs["job"].state == "cancelled"
        assert service.jobs["job"].payload == b""
        assert service.running is None
        assert service.inference is None
    finally:
        await service.close()
