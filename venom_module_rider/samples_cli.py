"""Local archive tools. They do not configure or contact any robot."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from pydantic import ValidationError

from .errors import RiderError
from .samples_archive import SampleArchive
from .samples_contract import LabelValues
from .samples_import import SampleImporter
from .samples_registry import SamplesRegistry, default_root


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--root", type=Path, default=None)
    commands = root.add_subparsers(dest="action", required=True)
    commands.add_parser("list", help="List durable sessions without Pi")
    commands.add_parser("status")
    sample = commands.add_parser("sample")
    sample.add_argument("id")
    session = commands.add_parser("session")
    session.add_argument("id")
    session.add_argument("--offset", type=int, default=0)
    session.add_argument("--limit", type=int, default=24)
    request = commands.add_parser("request")
    request.add_argument("id")
    imported = commands.add_parser("import")
    imported.add_argument("source", type=Path)
    imported.add_argument("--request-id", default=None)
    imported.add_argument("--device-id", default=None)
    exported = commands.add_parser("export")
    exported.add_argument("id")
    exported.add_argument("output", type=Path)
    label = commands.add_parser("label")
    label.add_argument("id")
    label.add_argument("--expected-revision", type=int, required=True)
    label.add_argument(
        "--expected-state", choices=["clear", "obstacle", "unknown"], default="unknown"
    )
    label.add_argument(
        "--obstacle-side",
        choices=["left", "right", "centre", "both", "none", "unknown"],
        default="unknown",
    )
    label.add_argument("--class-name", default="unknown")
    label.add_argument("--scene", default="")
    label.add_argument(
        "--review", choices=["pending", "approved", "rejected"], default="pending"
    )
    label.add_argument("--note", default="")
    return root


def copy_export(source, destination):
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise RiderError("samples_export_exists", 409)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            with source.open("rb") as original:
                shutil.copyfileobj(original, stream, length=1024 * 1024)
            stream.flush()
            os.fsync(stream.fileno())
        # Linking cannot overwrite a destination created concurrently.
        os.link(temporary, destination)
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return str(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def execute(args):
    registry = SamplesRegistry(args.root or default_root())
    store = registry.store
    try:
        if args.action == "list":
            return await registry.read(store.sessions)
        if args.action == "status":
            return await registry.status()
        if args.action == "sample":
            return await registry.read(store.sample, args.id)
        if args.action == "session":
            return await registry.read(
                store.page, args.id, offset=args.offset, limit=args.limit
            )
        if args.action == "request":
            return await registry.request(args.id)
        if args.action == "import":
            return await SampleImporter(registry).run(
                args.source,
                args.request_id or uuid.uuid4().hex,
                device_id=args.device_id,
            )
        if args.action == "label":
            values = LabelValues(
                expected_state=args.expected_state,
                obstacle_side=args.obstacle_side,
                class_name=args.class_name,
                scene=args.scene,
                review=args.review,
                note=args.note,
            ).model_dump()
            return await registry.read(
                store.label, args.id, values, expected_revision=args.expected_revision
            )
        archive = SampleArchive(store)
        source, revision = await registry.read(archive.export, args.id)
        try:
            path = await registry.read(copy_export, source, args.output)
            return {"output": path, "dataset_revision": revision}
        finally:
            source.unlink(missing_ok=True)
    finally:
        await registry.close()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = asyncio.run(execute(args))
    except RiderError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "status": exc.status}))
        return 1
    except (OSError, ValidationError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "samples_local_operation_failed",
                    "error_type": type(exc).__name__,
                }
            )
        )
        return 1
    print(json.dumps({"ok": True, "data": result}, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
