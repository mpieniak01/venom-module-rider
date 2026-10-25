"""Session video API uses durable host files and never contacts hardware."""

from test_samples_api import BASE, rig
from test_session_video import jpeg


async def test_download_endpoint_keeps_recordings_separate_and_never_arms(tmp_path):
    service, pi, registry, app, client = await rig(tmp_path)
    expected = {}
    try:
        for index in range(2):
            session = await registry.start(
                f"video-{index}", "demo", {"record_video": True}
            )
            sid = session["session_id"]
            frame = jpeg(index, 0)
            await registry.read(registry.store.video.append, sid, frame)
            active = await client.get(BASE + f"/samples/sessions/{sid}/video")
            assert active.status_code == 409
            await registry.end(sid)
            expected[sid] = frame.payload
        listing = (await client.get(BASE + "/samples/sessions")).json()["sessions"]
        assert {row["session_id"] for row in listing} == set(expected)
        for sid, payload in expected.items():
            response = await client.get(BASE + f"/samples/sessions/{sid}/video")
            assert response.status_code == 200
            assert response.content == payload
            assert sid in response.headers["content-disposition"]
            assert response.headers["cache-control"] == "no-store"
        assert pi.state == "DISARMED" and not pi.camera_on
    finally:
        await client.aclose()
        await registry.close()
        await service.close()
