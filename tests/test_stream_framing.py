"""Native-stream wire framing: fragmented real-sized JPEGs and corrupt peers."""

import pytest

from venom_module_rider.errors import RiderError
from venom_module_rider.stream_framing import (
    MAX_HEADER_BYTES,
    MAX_PART_BYTES,
    MultipartJpegParser,
    boundary_from_type,
)

JPEG = b"\xff\xd8" + b"payload" * 2000 + b"\xff\xd9"


def part(payload=JPEG, extra=b"", length=None):
    return (
        b"--rider\r\nContent-Type: image/jpeg\r\nContent-Length: "
        + str(len(payload) if length is None else length).encode()
        + b"\r\n"
        + extra
        + b"\r\n"
        + payload
        + b"\r\n"
    )


def test_fragmented_frames_have_exact_bytes_and_bounded_partial_state():
    parser = MultipartJpegParser(
        boundary_from_type('multipart/x-mixed-replace; boundary="rider"')
    )
    wire = part(extra=b'X-Rider-Source: {"sequence":1}\r\n') + part() + b"--rider--\r\n"
    received = []
    for offset in range(0, len(wire), 97):
        received.extend(parser.feed(wire[offset : offset + 97]))
        assert len(parser.buffer) <= len(JPEG) + 100
    parser.finish()
    assert [payload for _, payload in received] == [JPEG, JPEG]
    assert received[0][0]["x-rider-source"] == '{"sequence":1}'
    assert parser.finished
    assert parser.feed(b"\r\n") == []
    with pytest.raises(RiderError, match="stream_invalid_boundary"):
        parser.feed(b"unexpected")


@pytest.mark.parametrize(
    "mime",
    [
        "image/jpeg",
        "multipart/x-mixed-replace",
        "multipart/x-mixed-replace; boundary=../rider",
        "multipart/x-mixed-replace; boundary=x; boundary=y",
        "multipart/x-mixed-replace; boundary=" + "x" * 71,
    ],
)
def test_response_must_negotiate_one_bounded_safe_boundary(mime):
    with pytest.raises(RiderError):
        boundary_from_type(mime)


@pytest.mark.parametrize(
    "wire,code",
    [
        (b"not a boundary\r\n", "stream_invalid_boundary"),
        (b"--riderXX", "stream_invalid_boundary"),
        (part(extra=b"Content-Length: 1\r\n"), "stream_invalid_headers"),
        (part(extra=b"Invalid!Header: x\r\n"), "stream_invalid_headers"),
        (part(extra=b"BrokenHeader\r\n"), "stream_invalid_headers"),
        (part(extra=b"X-Test: \xff\r\n"), "stream_invalid_headers"),
        (part(length=MAX_PART_BYTES + 1), "stream_input_size"),
        (part(length=-1), "stream_invalid_length"),
        (part(length="not-a-number"), "stream_invalid_length"),
        (part(length="0000000001"), "stream_input_size"),
        (part(payload=b"broken"), "stream_invalid_jpeg"),
        (part().replace(b"image/jpeg", b"image/png"), "stream_invalid_format"),
        (part()[:-2] + b"XX", "stream_invalid_length"),
        (b"--rider\r\nX: " + b"x" * MAX_HEADER_BYTES, "stream_header_size"),
        (
            b"--rider\r\nX: " + b"x" * MAX_HEADER_BYTES + b"\r\n\r\n",
            "stream_header_size",
        ),
    ],
)
def test_corrupt_parts_fail_explicitly(wire, code):
    with pytest.raises(RiderError, match=code):
        MultipartJpegParser(b"rider").feed(wire)


@pytest.mark.parametrize("wire", [part()[:-30], b"--rid"])
def test_socket_end_cannot_turn_a_partial_part_into_a_frame(wire):
    parser = MultipartJpegParser(b"rider")
    parser.feed(wire)
    with pytest.raises(RiderError, match="stream_truncated"):
        parser.finish()


def test_parser_caps_input_and_rejects_invalid_constructor_boundary():
    with pytest.raises(RiderError, match="stream_input_size"):
        MultipartJpegParser(b"rider").feed(
            b"x" * (MAX_PART_BYTES + MAX_HEADER_BYTES + 1)
        )
    for value in (b"", b"x" * 71):
        with pytest.raises(RiderError, match="stream_invalid_boundary"):
            MultipartJpegParser(value)
