"""Validate local-only API origins before sending operator credentials."""

import os
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def local_api_tls_context():
    """Verify TLS using system roots or the operator's explicitly selected CA."""
    return ssl.create_default_context(cafile=os.getenv("RIDER_API_CA_FILE") or None)


def local_api_opener(origin=""):
    """Never consult ambient proxy settings or follow a local API redirect."""
    handlers = [urllib.request.ProxyHandler({}), _NoRedirect()]
    if origin.startswith("https://"):
        handlers.append(urllib.request.HTTPSHandler(context=local_api_tls_context()))
    return urllib.request.build_opener(*handlers)


def local_api_origin(value: str) -> str:
    """Return a canonical loopback origin, rejecting ambiguous URLs and proxies."""
    if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ValueError("local_venom_url_required")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("local_venom_url_required")
    except ValueError:
        raise ValueError("local_venom_url_required") from None
    # Never resolve localhost through mutable host/DNS configuration. HTTPS
    # certificates must therefore cover the actual numeric loopback address.
    host = "[::1]" if parsed.hostname == "::1" else "127.0.0.1"
    return parsed.scheme + "://" + host + (f":{port}" if port is not None else "")
