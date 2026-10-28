"""Offline IMU evidence report from a local session ZIP or manifest JSON."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path

from .errors import RiderError
from .motion_evidence import MAX_REPORT_BYTES, analyze_manifest
from .samples_archive import MAX_MANIFEST_BYTES, SampleArchive

MAX_LOCAL_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_SETTINGS_BYTES = 16 * 1024


def _open_local(path: Path, limit: int):
    if path.is_symlink():
        raise ValueError("motion_local_file_invalid")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("motion_local_file_invalid") from exc
    stream = os.fdopen(descriptor, "rb")
    try:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("motion_local_file_invalid")
        if metadata.st_size > limit:
            raise ValueError("motion_local_input_limit")
        return stream
    except BaseException:
        stream.close()
        raise


def _read_json(path: Path, limit: int) -> object:
    with _open_local(path, limit) as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("motion_local_input_limit")
    return json.loads(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Local session ZIP or manifest JSON")
    parser.add_argument(
        "--output", required=True, type=Path, help="New report JSON file"
    )
    parser.add_argument("--settings", type=Path, help="Optional analysis settings JSON")
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("motion_output_exists")
        if args.source.suffix.lower() == ".zip":
            with _open_local(args.source, MAX_LOCAL_ARCHIVE_BYTES) as source:
                manifest = SampleArchive.validate_zip(source, MAX_LOCAL_ARCHIVE_BYTES)
        else:
            manifest = _read_json(args.source, MAX_MANIFEST_BYTES)
        settings = (
            _read_json(args.settings, MAX_SETTINGS_BYTES) if args.settings else None
        )
        if settings is not None and not isinstance(settings, dict):
            raise ValueError("motion_settings_invalid")
        report = analyze_manifest(manifest, settings)
        payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False).encode()
        if len(payload) > MAX_REPORT_BYTES:
            raise ValueError("motion_report_limit")
        # Exclusive creation also protects a dangling symlink/racing writer.
        with args.output.open("xb") as output:
            output.write(payload + b"\n")
        print(json.dumps({"status": report["status"], "output": str(args.output)}))
        return 0
    except (OSError, ValueError, TypeError, RiderError):
        # Do not reflect arbitrary private source content, paths or secrets.
        print(json.dumps({"error": "motion_evidence_cli_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
