"""Exercise executable module entrypoints using only local inert fixtures."""

import hashlib
import io
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def cli(module, *arguments, stdin=None):
    """Run the actual python -m entrypoint with isolated child settings."""
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "CUDA_VISIBLE_DEVICES": "",
    }
    environment.pop("RIDER_ACCESS_TOKEN", None)
    return subprocess.run(
        [sys.executable, "-m", module, *map(str, arguments)],
        cwd=ROOT,
        env=environment,
        input=stdin,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_analysis_cli_profiles_uses_executable_dispatch():
    """A real fixture HTTP response must survive the executable CLI dispatcher."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            """Respond only to the inert profiles fixture endpoint."""
            requests.append(self.path)
            encoded = json.dumps({"profiles": [{"id": "fixture"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            """Keep fixture HTTP diagnostics out of test output."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = cli(
            "venom_module_rider.analysis_cli",
            "--url",
            f"http://127.0.0.1:{server.server_port}",
            "profiles",
        )
        assert result.returncode == 0, result.stderr.decode()
        assert json.loads(result.stdout) == {"profiles": [{"id": "fixture"}]}
        assert requests == ["/api/v1/rider/analysis/profiles"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_analysis_worker_executable_reaches_cuda_admission_without_models():
    """The worker must report its CUDA-only admission error, not startup NameError."""
    result = cli(
        "venom_module_rider.analysis_worker",
        os.getpid(),
        stdin=b"{}\n{}\n",
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error"] == "vision_cuda_unavailable"


def test_video_extract_executable_preserves_committed_raw_bytes(tmp_path):
    """The executable extraction path must validate and export one committed frame."""
    stream = io.BytesIO()
    Image.new("RGB", (64, 48), (70, 90, 100)).save(stream, "JPEG")
    payload = stream.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    recording = tmp_path / "recording"
    recording.mkdir()
    source = {
        "contract_version": 1,
        "camera_session": "fixture-camera",
        "sequence": 1,
        "transport_epoch": 2,
        "pipeline_revision": 3,
        "frame_id": "fixture-camera-1",
        "sha256": digest,
        "frame_age_ms": 10.0,
        "captured_monotonic_ns": 100000000,
        "encoded_width": 64,
        "encoded_height": 48,
        "image_transform": {"rotation": 0, "flip_h": False, "flip_v": False},
    }
    (recording / "recording.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "rider-test-video",
                "state": "stream_ended",
                "frames": 1,
            }
        )
    )
    (recording / "capture.mjpeg").write_bytes(payload)
    (recording / "frames.jsonl").write_text(
        json.dumps(
            {
                "index": 0,
                "offset": 0,
                "bytes": len(payload),
                "sha256": digest,
                "source": source,
            }
        )
        + "\n"
    )
    output = tmp_path / "extracted"
    result = cli(
        "venom_module_rider.video_capture",
        "extract",
        recording,
        "--output",
        output,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout) == {"output": str(output)}
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["samples"][0]["raw"]["sha256"] == digest
    assert (output / "raw" / f"{digest}.bin").read_bytes() == payload
