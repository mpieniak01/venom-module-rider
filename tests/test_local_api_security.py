"""Local API clients must not leak operator credentials through URL or redirect."""

import asyncio
import http.server
import ipaddress
import json
import ssl
import threading
import urllib.error
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from venom_module_rider import analysis_cli, video_capture
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
        "https://remote.example",
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
        ("https://localhost:8800/", "https://127.0.0.1:8800"),
        ("https://127.0.0.1:8800", "https://127.0.0.1:8800"),
        ("https://[::1]:8800", "https://[::1]:8800"),
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


def test_local_https_verifies_certificate_and_hostname_for_both_clients(
    tmp_path, monkeypatch
):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            True,
        )
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.headers.get("Authorization"))
            if self.path.endswith("/camera/pipeline"):
                payload = {
                    "state": "active",
                    "camera_session": "fixture",
                    "transport_epoch": 1,
                    "revision": 1,
                    "profile": {"remote_transport": "mjpeg"},
                }
            else:
                payload = {"ok": True}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("RIDER_ACCESS_TOKEN", "fixture-token")
    monkeypatch.delenv("RIDER_API_CA_FILE", raising=False)
    origin = f"https://localhost:{server.server_port}"
    try:
        with pytest.raises(urllib.error.URLError):
            analysis_cli.request(origin, "/profiles")
        with pytest.raises(httpx.ConnectError):
            asyncio.run(
                video_capture.run_record(SimpleNamespace(url=origin, action="check"))
            )
        assert received == []
        monkeypatch.setenv("RIDER_API_CA_FILE", str(cert_file))
        # A trusted certificate for localhost is not sufficient for the
        # numeric address used on the wire.
        with pytest.raises(urllib.error.URLError):
            analysis_cli.request(origin, "/profiles")
        with pytest.raises(httpx.ConnectError):
            asyncio.run(
                video_capture.run_record(SimpleNamespace(url=origin, action="check"))
            )
        assert received == []
        leaf = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fixture-api")])
            )
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                False,
            )
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
                False,
            )
            .sign(key, hashes.SHA256())
        )
        leaf_file = tmp_path / "leaf.pem"
        leaf_file.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
        context.load_cert_chain(leaf_file, key_file)
        assert analysis_cli.request(origin, "/profiles") == {"ok": True}
        result = asyncio.run(
            video_capture.run_record(SimpleNamespace(url=origin, action="check"))
        )
        assert result["navigation"] == {"ok": True}
        assert received == ["Bearer fixture-token"] * 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
