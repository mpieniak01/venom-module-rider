"""Validate local-only API origins before sending operator credentials."""

from urllib.parse import urlsplit


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
