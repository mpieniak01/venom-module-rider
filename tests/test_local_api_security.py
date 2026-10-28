"""Local API clients must not leak operator credentials through URL or redirect."""

import http.server
import json
import threading
import urllib.error

import pytest

from venom_module_rider import analysis_cli
from venom_module_rider.local_api import local_api_origin


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example:8800",
        "http://127.0.0.1@remote.example",
        "http://@localhost",
        "http://localhost:0",
        "http://localhost:65536",
        "http://localhost:not-a-port",
        "https://localhost",
        "http://localhost/api",
        "http://localhost/?key=secret",
        "http://localhost/#part",
        "http://local\nhost",
    ],
)
def test_rejects_nonlocal_or_ambiguous_origins(url):
    with pytest.raises(ValueError, match="local_venom_url_required"):
        local_api_origin(url)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://localhost:8800/", "http://127.0.0.1:8800"),
        ("http://127.0.0.1:9", "http://127.0.0.1:9"),
        ("http://[::1]:8800", "http://[::1]:8800"),
    ],
)
def test_normalizes_loopback_origin(url, expected):
    assert local_api_origin(url) == expected


def test_cli_rejects_redirect_and_ignores_proxy(monkeypatch, capsys):
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get("Authorization")))
            if self.path.endswith("/profiles"):
                self.send_response(302)
                self.send_header("Location", "/credentials-target")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode())

        def do_POST(self):
            size = int(self.headers.get("Content-Length", 0))
            calls.append((self.path, json.loads(self.rfile.read(size))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"id":"fixture-run"}')

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("RIDER_ACCESS_TOKEN", "fixture-only-credential")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            analysis_cli.request(f"http://localhost:{server.server_port}", "/profiles")
        assert error.value.code == 302
        assert calls == [
            ("/api/v1/rider/analysis/profiles", "Bearer fixture-only-credential")
        ]
        origin = f"http://127.0.0.1:{server.server_port}"
        assert analysis_cli.main(["--url", origin, "profiles"]) == 1
        assert json.loads(capsys.readouterr().out) == {
            "error": "analysis_http_error",
            "status": 302,
        }
        assert (
            analysis_cli.main(
                ["--url", origin, "submit", "fixture-session", "--request-id", "intent"]
            )
            == 0
        )
        assert calls[-1] == (
            "/api/v1/rider/analysis/runs",
            {"session_id": "fixture-session", "request_id": "intent", "spec": {}},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
