"""Resolve the retained model configuration; no arbitrary model URL/download."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .depth_candidates import (
    CHECKPOINT_HASHES,
    SDK_REVISION,
    file_hash,
    verify_sdk,
)
from .errors import RiderError
from .samples_archive import canonical

_MODEL_WEIGHTS_FILENAME = "model.safetensors"
_MODEL_CONFIG_FILENAME = "config.json"


@dataclass(frozen=True)
class Profile:
    """Server-only paths accompany public model and SDK provenance."""

    name: str
    backend: str
    model: Path
    python: Path
    sdk: Path | None
    resolution: int

    def availability(self):
        """Presence is not a claim of loaded or verified inference."""
        required = [_MODEL_CONFIG_FILENAME, _MODEL_WEIGHTS_FILENAME]
        if self.backend == "da2":
            required += ["manifest.json", "preprocessor_config.json"]
        if not all((self.model / item).is_file() for item in required):
            return "analysis_model_unavailable"
        if (
            not self.python.is_file()
            or not os.access(self.python, os.X_OK)
            or (self.sdk is not None and not self.sdk.is_dir())
        ):
            return "analysis_runtime_unavailable"
        return None

    def describe(self):
        """Expose IDs/capabilities without leaking server paths into the panel."""
        error = self.availability()
        return {
            "id": self.name,
            "backend": self.backend,
            "resolution": "production_preprocessor"
            if self.backend == "da2"
            else self.resolution,
            "installed": error is None,
            "loaded": False,
            "content_verified": False,
            "error": error,
            "device": "cuda",
            "drive_supported": False,
            "distance_measurement": False,
            "windows": [1] if self.backend == "da2" else [1, 3, 5],
        }

    def qualify(self):
        """Return a stable error for malformed or unavailable local model assets."""
        try:
            return self._qualify()
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RiderError("analysis_model_invalid", 409) from exc

    def _qualify(self):
        """Freeze actual hashes before a run; never load/unload live models."""
        error = self.availability()
        if error:
            raise RiderError(error, 503)
        names = [_MODEL_CONFIG_FILENAME, _MODEL_WEIGHTS_FILENAME]
        if self.backend == "da2":
            names.append("preprocessor_config.json")
        files = {name: file_hash(self.model / name) for name in names}
        if self.backend == "da2":
            manifest = json.loads((self.model / "manifest.json").read_text())
            if manifest.get("files") != files:
                raise RiderError("analysis_model_hash_mismatch", 409)
        else:
            if files[_MODEL_WEIGHTS_FILENAME] != CHECKPOINT_HASHES[self.backend]:
                raise RiderError("analysis_model_hash_mismatch", 409)
            if (
                json.loads((self.model / _MODEL_CONFIG_FILENAME).read_text()).get(
                    "model_name"
                )
                != self.backend
            ):
                raise RiderError("analysis_model_variant_mismatch", 409)
            assert self.sdk is not None
            try:
                verify_sdk(self.sdk.parent)
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                raise RiderError("analysis_sdk_mismatch", 409) from exc
        metadata = {
            "id": self.name,
            "backend": self.backend,
            "files": files,
            "model_sha256": hashlib.sha256(
                json.dumps(files, sort_keys=True).encode()
            ).hexdigest(),
            "resolution": self.resolution
            if self.backend != "da2"
            else "production_preprocessor",
            "sdk_revision": SDK_REVISION if self.sdk is not None else None,
            "device": "cuda",
            "content_verified": True,
            "drive_supported": False,
            "distance_measurement": False,
        }
        return {
            **metadata,
            "fingerprint": hashlib.sha256(canonical(metadata)).hexdigest(),
        }


@dataclass(frozen=True)
class SemanticProfile(Profile):
    """Server paths and qualification reuse the host-owned live catalog."""

    live_catalog: Any

    def availability(self):
        error = self.live_catalog.describe(self.name)["reason"]
        if error:
            return error
        if not self.python.is_file() or not os.access(self.python, os.X_OK):
            return "analysis_runtime_unavailable"
        return None

    def describe(self):
        error = self.availability()
        return {
            "id": self.name,
            "backend": "semantic",
            "task": "semantic",
            "perception_engine": "segformer",
            "resolution": "native_preprocessor",
            "installed": error is None,
            "loaded": False,
            "content_verified": False,
            "error": error,
            "device": "cuda",
            "drive_supported": False,
            "distance_measurement": False,
            "windows": [1],
        }

    def qualify(self):
        error = self.availability()
        if error:
            raise RiderError(error, 409)
        artifact = self.live_catalog.ready(self.name)
        definition = self.live_catalog.definition(self.name)
        metadata = {
            "id": self.name,
            "backend": artifact["backend"],
            "task": "semantic",
            "files": artifact["files"],
            "model_sha256": artifact["sha256"],
            "revision": definition["revision"],
            "resolution": "native_preprocessor",
            "sdk_revision": None,
            "device": "cuda",
            "content_verified": True,
            "drive_supported": False,
            "distance_measurement": False,
        }
        return {
            **metadata,
            "fingerprint": hashlib.sha256(canonical(metadata)).hexdigest(),
        }


class ProfileCatalog:
    """One config supplies production baseline and retained offline options."""

    def __init__(self, module_root=None, *, assets_root=None, config=None):
        self.module = (
            Path(module_root) if module_root else Path(__file__).resolve().parent.parent
        )
        self.assets = Path(
            assets_root or os.getenv("RIDER_VISION_ASSETS", str(self.module / "data"))
        )
        self.module = self.module.resolve()
        self.assets = self.assets.resolve()
        self.live_assets = Path(
            os.getenv(
                "RIDER_MODEL_ROOT",
                str(self.assets / "perception")
                if assets_root
                else str(Path.home() / ".local/share/venom/rider/models"),
            )
        ).resolve()
        self.config = (
            Path(config).resolve()
            if config
            else self.module / "config/vision-research.json"
        )

    def _asset(self, value):
        path = Path(value)
        if (
            path.is_absolute()
            or not path.parts
            or path.parts[0] != "data"
            or ".." in path.parts
        ):
            raise RiderError("analysis_profile_configuration", 503)
        return self.assets.joinpath(*path.parts[1:])

    def profiles(self):
        """Only supported retained configurations may become executable profiles."""
        try:
            config = json.loads(Path(self.config).read_text())
            baseline = config["production"]
            if baseline["backend"] != "da2":
                raise ValueError("unsupported production baseline")
            profiles = {
                "da2-default": Profile(
                    "da2-default",
                    "da2",
                    Path(
                        os.getenv(
                            "RIDER_DEPTH_MODEL", str(self._asset(baseline["model"]))
                        )
                    ).resolve(),
                    Path(sys.executable),
                    None,
                    0,
                )
            }
            for name, values in config["profiles"].items():
                if (
                    values["backend"] != "da3-small"
                    or values["status"] != "experimental-offline"
                    or values["resolution"] != 280
                ):
                    continue
                research = self.assets / "da3-research"
                profiles[name] = Profile(
                    name,
                    values["backend"],
                    self._asset(values["model"]),
                    research / "venv/bin/python",
                    research / "sdk/src",
                    280,
                )
            return profiles
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RiderError("analysis_profile_configuration", 503) from exc

    def live(self):
        """Host-owned model inventory shared by live and offline callers."""
        from .perception_catalog import LiveCatalog

        return LiveCatalog(self)

    def research_profiles(self):
        """Extend the retained depth list without a second model registry."""
        from .perception_catalog import FAST, SEGFORMER, YOLO

        result: dict[str, Profile] = dict(self.profiles())
        live = self.live()
        for name in (SEGFORMER, YOLO, FAST):
            result[name] = SemanticProfile(
                name, "semantic", live.path(name), Path(sys.executable), None, 0, live
            )
        return result

    def resolve(self, name):
        """Resolve an ID, never a caller-supplied path or shell fragment."""
        from .perception_catalog import FAST, SEGFORMER, YOLO

        if name in {SEGFORMER, YOLO, FAST}:
            return self.research_profiles()[name]
        result = self.profiles().get(name)
        if result is None:
            raise RiderError("analysis_profile_unknown", 404)
        return result


def code_fingerprint(module=None):
    """Digest actual Python sources, also in an uncommitted or installed checkout."""
    module = Path(module) if module else Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    files = sorted(
        [
            *module.joinpath("venom_module_rider").rglob("*.py"),
            *module.joinpath("scripts").glob("*.py"),
        ]
    )
    if not files:
        raise RiderError("analysis_source_unavailable", 503)
    for path in files:
        digest.update(str(path.relative_to(module)).encode() + b"\0")
        digest.update(path.read_bytes())
    return {"sha256": digest.hexdigest(), "python_sources": len(files)}
