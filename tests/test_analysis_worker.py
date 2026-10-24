"""Stored-image runner uses real decoder, geometry and output encoders."""

import base64
import hashlib
import io
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from test_analysis_profiles import catalog, da2

from venom_module_rider import analysis_worker
from venom_module_rider.analysis_store import AnalysisStore
from venom_module_rider.samples_frame import FrameBuffer
from venom_module_rider.samples_store import SampleStore


def job(tmp_path, monkeypatch, *, count=3, dark=False):
    configured, _ = catalog(tmp_path, monkeypatch)
    da2(configured.resolve("da2-default"))
    profile = configured.resolve("da2-default")
    store = SampleStore(tmp_path / "registry")
    session = store.start("session", "history")
    buffer = FrameBuffer()
    for index in range(count):
        rgb = (
            np.zeros((48, 64, 3), dtype=np.uint8)
            if dark
            else np.indices((48, 64)).sum(axis=0).astype(np.uint8) * 2
        )
        image = Image.fromarray(rgb).convert("RGB")
        image.putpixel((index, index), (20 + index, 50, 80))
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        key = buffer.offer(
            "history",
            stream.getvalue(),
            "image/png",
            {
                "sequence": index,
                "frame_id": str(index),
                "run_id": "actual-fixture-sequence",
            },
        )
        store.capture(session["session_id"], str(index), key, buffer.get(key))
    store.end(session["session_id"])
    analysis = AnalysisStore(store)
    row = analysis.create(
        "run", session["session_id"], {}, profile.qualify(), {"sha256": "test"}
    )
    return {
        "id": row["id"],
        "root": str(store.root),
        "context": row["context"],
        "profile": {"backend": "da2", "model": str(profile.model), "resolution": 0},
    }, store


class PlaneModel:
    loaded = 0

    def __init__(self, *args):
        type(self).loaded += 1
        self.metadata = {"backend": "test-plane"}

    def infer(self, image):
        y, _x = np.mgrid[: image.height, : image.width]
        return 1 / np.maximum(3 * y / image.height - 1.7, 0.05)

    def infer_window(self, images):
        return self.infer(images[-1])


def execute(job, monkeypatch):
    messages = []

    def emit(_output, message):
        messages.append(deepcopy(message))
        return {"timings_ms": {"persistence": 1.5, "local_ipc_and_queue": 0.2}}

    monkeypatch.setattr(analysis_worker, "emit", emit)
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(is_initialized=lambda: False),
            set_num_threads=lambda value: None,
            set_num_interop_threads=lambda value: None,
        ),
    )
    PlaneModel.loaded = 0
    analysis_worker.run(job, None, candidate_factory=PlaneModel)
    return messages, messages[-1]["report"]


def test_real_geometry_raw_depth_overlay_and_measured_stage_association(
    tmp_path, monkeypatch
):
    source, store = job(tmp_path, monkeypatch)
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in store.blobs.glob("*")
    }
    messages, report = execute(source, monkeypatch)
    assert PlaneModel.loaded == 1 and report["summary"]["frames"] == 3
    assert report["summary"]["false_go"] is None
    assert [row["state"] for row in report["rows"]] == ["clear"] * 3
    assert report["summary"]["temporal_pairs"] == 2
    for message in messages:
        if message["type"] != "frame":
            continue
        row = message["row"]
        assert row["sample_id"] in [
            sample["sample_id"] for sample in source["context"]["inputs"]
        ]
        assert row["drive_supported"] is False and row["actual_window"] == 1
        assert len(message["artifacts"]) == 3
        for artifact in message["artifacts"]:
            raw = base64.b64decode(artifact["data"])
            if artifact["name"].endswith(".npy"):
                assert np.load(io.BytesIO(raw), allow_pickle=False).shape == (48, 64)
            else:
                with Image.open(io.BytesIO(raw)) as image:
                    assert image.size == (64, 48)
    assert set(report["summary"]["timings_ms"]) == {
        "decode",
        "inference",
        "geometry",
        "render",
        "persistence",
        "local_ipc_and_queue",
    }
    assert {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in store.blobs.glob("*")
    } == before


def test_quality_gate_does_not_load_model_and_probe_is_explicit(tmp_path, monkeypatch):
    source, _store = job(tmp_path, monkeypatch, dark=True)
    _messages, report = execute(source, monkeypatch)
    assert PlaneModel.loaded == 0 and report["model_load_ms"] is None
    assert report["summary"]["rejected_inputs"] == 3
    source["context"]["spec"]["quality_mode"] = "model_probe"
    _messages, report = execute(source, monkeypatch)
    assert PlaneModel.loaded == 1 and report["summary"]["rejected_inputs"] == 0
    assert all(not row["image_quality_passed"] for row in report["rows"])
    assert all(not row["drive_supported"] for row in report["rows"])


def test_joint_window_requires_verified_sequence_no_duplicate_still_padding(
    tmp_path, monkeypatch
):
    source, _store = job(tmp_path, monkeypatch)
    source["profile"]["backend"] = "da3-small"
    source["context"]["spec"]["window"] = 3
    _messages, report = execute(source, monkeypatch)
    assert [row["actual_window"] for row in report["rows"]] == [1, 2, 3]
    assert [row["causal_warmup"] for row in report["rows"]] == [True, True, False]
    source["context"]["session"]["policy"]["order_source"] = "filename-unverified"
    with pytest.raises(ValueError, match="analysis_window_unverified"):
        execute(source, monkeypatch)


def test_changed_model_hash_or_source_bytes_are_not_analysed(tmp_path, monkeypatch):
    source, store = job(tmp_path, monkeypatch)
    model = Path(source["profile"]["model"]) / "model.safetensors"
    original = model.read_bytes()
    model.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="analysis_model_hash_mismatch"):
        execute(source, monkeypatch)
    model.write_bytes(original)
    digest = source["context"]["inputs"][0]["raw"]["sha256"]
    (store.blobs / (digest + ".bin")).write_bytes(b"wrong")
    with pytest.raises(ValueError, match="samples_source_corrupt"):
        execute(source, monkeypatch)


def test_offline_network_denied_for_ip_but_local_socket_family_retained(monkeypatch):
    import socket

    original = socket.socket
    monkeypatch.setattr(socket, "socket", original)
    analysis_worker.deny_network()
    for family in [socket.AF_INET, socket.AF_INET6]:
        with socket.socket(family) as connection:
            with pytest.raises(RuntimeError, match="analysis_network_denied"):
                connection.connect(("127.0.0.1", 9))
            with pytest.raises(RuntimeError, match="analysis_network_denied"):
                connection.connect_ex(("127.0.0.1", 9))
            with pytest.raises(RuntimeError, match="analysis_network_denied"):
                connection.sendto(b"x", ("127.0.0.1", 9))
    left, right = socket.socketpair()
    try:
        left.sendall(b"local")
        assert right.recv(5) == b"local"
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize("payload", [b"", b"{}", b"x" * 40 + b"\n", b"[]\n"])
def test_protocol_rejects_missing_newline_non_object_or_invalid_json(payload):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        analysis_worker.receive(io.BytesIO(payload))


def test_paused_worker_dies_with_parent_and_is_reaped_by_owned_harness():
    import os
    import subprocess
    from pathlib import Path

    parent = r"""
import json,os,signal,subprocess,sys
worker=subprocess.Popen([sys.executable,"-u","-m","venom_module_rider.analysis_worker",str(os.getpid())],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
worker.stdin.write(b"{}\n");worker.stdin.flush()
assert json.loads(worker.stdout.readline())["type"]=="compute"
os.kill(worker.pid,signal.SIGSTOP)
print(worker.pid,flush=True)
os._exit(0)
"""
    harness = r"""
import ctypes,json,os,signal,subprocess,sys,time
assert ctypes.CDLL(None).prctl(36,1,0,0,0)==0
parent=subprocess.Popen([sys.executable,"-c",sys.argv[1]],stdout=subprocess.PIPE,text=True)
identity=int(parent.stdout.readline());parent.wait(timeout=5)
status=None
try:
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        pid,code=os.waitpid(identity,os.WNOHANG)
        if pid:
            status=code;break
        time.sleep(.01)
finally:
    if status is None:
        os.kill(identity,signal.SIGKILL);os.waitpid(identity,0)
assert status is not None and os.WIFSIGNALED(status)
assert os.WTERMSIG(status)==signal.SIGKILL
print(json.dumps({"paused_orphan_killed":True,"reaped":True}))
"""
    import sys

    result = subprocess.run(
        [sys.executable, "-c", harness, parent],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["paused_orphan_killed"]
