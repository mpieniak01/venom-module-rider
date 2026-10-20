"""Local history import uses the same durable capture domain, never Pi."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

from PIL import Image
from pydantic import ValidationError

from .errors import RiderError
from .evidence_store import canonical as evidence_canonical
from .samples_archive import MAX_MANIFEST_BYTES, SampleArchive, canonical
from .samples_contract import LabelValues


class SampleImporter:
    def __init__(self, registry):
        self.registry = registry
        self.store = registry.store

    def inspect(self, source: Path):
        if source.is_symlink():
            raise RiderError("samples_import_path", 400)
        if source.is_file():
            manifest = SampleArchive.validate_zip(
                source, self.store.limits.session_bytes
            )
            return self.preflight(
                {
                    "kind": "zip",
                    "path": source,
                    "manifest": manifest,
                    "digest": manifest["dataset_revision"],
                    "items": manifest["samples"],
                }
            )

        if not source.is_dir():
            raise RiderError("samples_import_path", 400)
        manifest_file = source / "manifest.json"
        if manifest_file.exists():
            if (
                manifest_file.is_symlink()
                or manifest_file.stat().st_size > MAX_MANIFEST_BYTES
            ):
                raise RiderError("samples_import_path", 400)
            try:
                manifest = json.loads(manifest_file.read_bytes())
                SampleArchive._validate_manifest(manifest)
            except (ValueError, TypeError, KeyError) as exc:
                raise RiderError("samples_import_invalid", 400) from exc
            catalog = {
                "kind": "directory",
                "path": source,
                "manifest": manifest,
                "digest": manifest["dataset_revision"],
                "items": manifest["samples"],
            }
        else:
            files = sorted(
                path
                for path in source.iterdir()
                if path.suffix.lower() in {".jpg", ".jpeg", ".png"}
            )
            if not files or len(files) > self.store.limits.session_samples:
                raise RiderError("samples_import_size", 413)
            items = []
            for path in files:
                actual = SampleArchive.safe_file(source, path.name)
                if not 0 < actual.stat().st_size <= 512 * 1024:
                    raise RiderError("samples_import_size", 413)
                payload = actual.read_bytes()
                with Image.open(io.BytesIO(payload)) as image:
                    mime = {"JPEG": "image/jpeg", "PNG": "image/png"}.get(
                        image.format or ""
                    )
                if mime is None:
                    raise RiderError("samples_image_invalid", 415)
                items.append(
                    {
                        "file_name": path.name,
                        "raw": {
                            "file": path.name,
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "bytes": len(payload),
                            "format": mime,
                        },
                    }
                )
            catalog = {
                "kind": "history",
                "path": source,
                "manifest": None,
                "items": items,
                "digest": hashlib.sha256(canonical(items)).hexdigest(),
            }
        return self.preflight(catalog)

    def preflight(self, catalog):
        evidence_bytes = sum(
            len(evidence_canonical(row).encode())
            for row in (catalog["manifest"] or {})
            .get("evidence", {})
            .get("records", [])
        )
        if (
            evidence_bytes + sum(item["raw"]["bytes"] for item in catalog["items"])
            > self.store.limits.session_bytes
        ):
            raise RiderError("samples_import_size", 413)
        for index in range(len(catalog["items"])):
            self.read(catalog, index)  # validate before creating any import session
        return catalog

    def read(self, catalog, index):
        item = catalog["items"][index]
        raw = item["raw"]
        if catalog["kind"] == "zip":
            with zipfile.ZipFile(catalog["path"]) as archive:
                payload = archive.read(raw["file"])
        else:
            path = SampleArchive.safe_file(catalog["path"], raw["file"])
            if path.stat().st_size != raw["bytes"]:
                raise RiderError("samples_import_hash", 400)
            payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != raw["sha256"]:
            raise RiderError("samples_import_hash", 400)
        if not isinstance(item.get("source_context", {}), dict):
            raise RiderError("samples_import_invalid", 400)
        context = dict(item.get("source_context") or {})
        session = (catalog["manifest"] or {}).get("session", {})
        context.update(
            {
                "frame_id": item.get("frame_id"),
                "run_id": item.get("run_id"),
                "sequence": item.get("sequence"),
                "source_session": item.get("source_session"),
                "source_timestamp": item.get("source_timestamp"),
                "timestamp_source": item.get("timestamp_source"),
                "clock_domain": item.get("clock_domain"),
                "clock_sync_uncertainty_ms": item.get("clock_sync_uncertainty_ms"),
                "transform": item.get("transform"),
                "calibration": item.get("calibration"),
                "telemetry": item.get("telemetry"),
                "commands": item.get("commands"),
                "association": item.get("association", "unmeasured"),
                "prediction": item.get("prediction"),
                "input_kind": "historical-import",
                "stale": True,
                "received_event": "import-read",
                "import_origin": {
                    "dataset_revision": catalog["digest"],
                    "sample_id": item.get("sample_id"),
                    "session_id": session.get("session_id"),
                    "frame_key": item.get("frame_key"),
                    "received_at": item.get("received_at"),
                    "admitted_at": item.get("admitted_at"),
                    "source_device_id": item.get("source_device_id"),
                    "original_file_name": item.get("file_name"),
                    "original_session_started_at": session.get("started_at"),
                    "original_session_ended_at": session.get("ended_at"),
                },
            }
        )
        from .samples_frame import CapturedFrame
        from .samples_store import encode

        checked = CapturedFrame(
            "validation",
            "history",
            payload,
            raw["format"],
            raw["sha256"],
            self.store.wall(),
            0.0,
            encode(context).encode(),
        )
        self.store.quality(checked)
        for position, label in enumerate(item.get("labels") or [], 1):
            try:
                LabelValues.model_validate(
                    {
                        key: value
                        for key, value in label.items()
                        if key in LabelValues.model_fields
                    }
                )
                if label["revision"] != position or label["author"]["kind"] not in {
                    "operator",
                    "auto",
                }:
                    raise ValueError("label provenance")
                if (
                    label["author"]["kind"] == "auto"
                    and label.get("review") != "pending"
                ):
                    raise ValueError("automatic review")
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                raise RiderError("samples_import_invalid", 400) from exc
        return payload, raw["format"], context

    async def run(self, source: Path, request_id: str, *, device_id=None):
        catalog = await self.registry.read(self.inspect, source)
        original_policy = (catalog["manifest"] or {}).get("session", {}).get(
            "policy"
        ) or {}
        source_device = (
            device_id
            or (catalog["manifest"] or {}).get("session", {}).get("source_device_id")
            or "history"
        )
        policy = {
            "kind": "import",
            "scene_group": original_policy.get("scene_group", source.name[:128]),
            "dataset_split": original_policy.get("dataset_split", "unassigned"),
            "import_digest": catalog["digest"],
            "order_source": original_policy.get("order_source", "manifest")
            if catalog["manifest"]
            else "filename-unverified",
        }
        session = await self.registry.start(request_id, source_device, policy)
        sid = session["session_id"]
        if session["state"] != "collecting":
            return session  # idempotent completed import, not another archive
        failed = False
        try:
            evidence = (catalog["manifest"] or {}).get("evidence")
            if evidence is not None:
                await self.registry.read(
                    self.store.evidence.append, sid, evidence["records"], imported=True
                )
                await self.registry.read(
                    self.store.evidence.status, sid, evidence["status"]
                )
            for index, item in enumerate(catalog["items"]):
                payload, mime, context = await self.registry.read(
                    self.read, catalog, index
                )
                key = self.registry.offer(source_device, payload, mime, context)
                if key is None:
                    raise RiderError("samples_import_invalid", 400)
                job_id = hashlib.sha256(f"{request_id}:{index}".encode()).hexdigest()
                self.registry.capture(sid, job_id, key, allow_historical=True)
                # This lookup follows the admitted capture in the same bounded
                # writer lane. Await actual completion, not a polling deadline.
                result = await self.registry.read(self.store.request, job_id)
                if result["state"] != "saved":
                    raise RiderError("samples_import_incomplete", 409)
                for label in item.get("labels") or []:
                    values = {
                        key: value
                        for key, value in label.items()
                        if key in LabelValues.model_fields
                    }
                    await self.registry.read(
                        self.store.label,
                        result["sample_id"],
                        values,
                        expected_revision=label["revision"] - 1,
                        author_kind=label["author"]["kind"],
                        origin={
                            "dataset_revision": catalog["digest"],
                            "sample_id": item.get("sample_id"),
                            "label": label,
                        },
                    )
        except BaseException:
            failed = True
            raise
        finally:
            session = await self.registry.end(sid, incomplete=failed)
        return session
