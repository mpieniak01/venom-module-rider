"""Validate local-only API origins before sending operator credentials."""

import urllib.error
import urllib.request
from urllib.parse import urlsplit


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def local_api_opener():
    """Never consult ambient proxy settings or follow a local API redirect."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def local_api_origin(value: str) -> str:
    """Return a canonical loopback origin, rejecting ambiguous URLs and proxies."""
    if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ValueError("local_venom_url_required")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (
            parsed.scheme != "http"
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
    host = "[::1]" if parsed.hostname == "::1" else "127.0.0.1"
    return "http://" + host + (f":{port}" if port is not None else "")
