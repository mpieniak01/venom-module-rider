"""The catalog recognizes retained local assets and never installs a model."""

import hashlib
import json
from dataclasses import replace

import pytest

from venom_module_rider import analysis_profiles
from venom_module_rider.analysis_profiles import ProfileCatalog, code_fingerprint
from venom_module_rider.errors import RiderError


def catalog(tmp_path, monkeypatch):
    monkeypatch.delenv("RIDER_DEPTH_MODEL", raising=False)
    root = tmp_path / "module"
    (root / "config").mkdir(parents=True)
    source = {
        "production": {"backend": "da2", "model": "data/depth-small"},
        "profiles": {
            "da3-small-280": {
                "backend": "da3-small",
                "model": "data/da3-research/small",
                "resolution": 280,
                "status": "experimental-offline",
            }
        },
    }
    (root / "config/vision-research.json").write_text(json.dumps(source))
    return ProfileCatalog(root, assets_root=tmp_path / "assets"), source


def da2(profile):
    profile.model.mkdir(parents=True)
    names = {
        "config.json": b"{}",
        "model.safetensors": b"fixture weights",
        "preprocessor_config.json": b"{}",
    }
    for name, payload in names.items():
        (profile.model / name).write_bytes(payload)
    manifest = {
        "files": {
            name: hashlib.sha256(payload).hexdigest() for name, payload in names.items()
        }
    }
    (profile.model / "manifest.json").write_text(json.dumps(manifest))


def test_retained_profiles_share_config_and_model_presence_is_not_inference(
    tmp_path, monkeypatch
):
    configured, _ = catalog(tmp_path, monkeypatch)
    assert set(configured.profiles()) == {"da2-default", "da3-small-280"}
    profile = configured.resolve("da2-default")
    assert profile.describe()["error"] == "analysis_model_unavailable"
    da2(profile)
    presence = profile.describe()
    assert (
        presence["installed"]
        and not presence["loaded"]
        and not presence["content_verified"]
    )
    assert presence["resolution"] == "production_preprocessor"
    assert str(tmp_path) not in json.dumps(presence)
    qualified = profile.qualify()
    assert qualified["content_verified"] and qualified["device"] == "cpu"
    assert qualified["drive_supported"] is False
    assert len(qualified["fingerprint"]) == 64
    assert configured.resolve("da3-small-280").describe()["windows"] == [1, 3, 5]
    with pytest.raises(RiderError, match="analysis_profile_unknown"):
        configured.resolve("https://arbitrary-model")


def test_missing_corrupt_and_wrong_manifest_weights_fail_explicitly(
    tmp_path, monkeypatch
):
    configured, _ = catalog(tmp_path, monkeypatch)
    profile = configured.resolve("da2-default")
    with pytest.raises(RiderError, match="analysis_model_unavailable"):
        profile.qualify()
    da2(profile)
    (profile.model / "model.safetensors").write_bytes(b"changed")
    with pytest.raises(RiderError, match="analysis_model_hash_mismatch"):
        profile.qualify()
    (profile.model / "manifest.json").write_text("invalid JSON")
    with pytest.raises(RiderError, match="analysis_model_invalid"):
        profile.qualify()
    missing = replace(profile, python=tmp_path / "no-python")
    assert missing.describe()["error"] == "analysis_runtime_unavailable"


def test_large_models_and_unretained_profile_configs_cannot_be_selected(
    tmp_path, monkeypatch
):
    configured, source = catalog(tmp_path, monkeypatch)
    source["profiles"]["large"] = {
        "backend": "da3metric-large",
        "model": "data/large",
        "resolution": 504,
        "status": "experimental-offline",
    }
    source["profiles"]["da3-small-280"]["status"] = "removed"
    configured.config.write_text(json.dumps(source))
    assert set(configured.profiles()) == {"da2-default"}


@pytest.mark.parametrize(
    "value", ["/absolute", "data/../outside", "https://weights", "other/path"]
)
def test_configured_assets_stay_within_the_approved_data_root(
    tmp_path, monkeypatch, value
):
    configured, source = catalog(tmp_path, monkeypatch)
    source["production"]["model"] = value
    configured.config.write_text(json.dumps(source))
    with pytest.raises(RiderError, match="analysis_profile_configuration"):
        configured.profiles()


def test_config_errors_are_not_silent_fallback_to_a_different_model(
    tmp_path, monkeypatch
):
    configured, source = catalog(tmp_path, monkeypatch)
    configured.config.write_text("malformed")
    with pytest.raises(RiderError, match="analysis_profile_configuration"):
        configured.profiles()
    source["production"]["backend"] = "other"
    configured.config.write_text(json.dumps(source))
    with pytest.raises(RiderError, match="analysis_profile_configuration"):
        configured.profiles()


def test_da3_verifies_retained_checkpoint_variant_and_sdk(tmp_path, monkeypatch):
    configured, _ = catalog(tmp_path, monkeypatch)
    profile = configured.resolve("da3-small-280")
    profile.model.mkdir(parents=True)
    (profile.model / "config.json").write_text(json.dumps({"model_name": "da3-small"}))
    (profile.model / "model.safetensors").write_bytes(b"tiny fixture")
    profile.python.parent.mkdir(parents=True)
    profile.python.write_text("fixture")
    profile.python.chmod(0o700)
    profile.sdk.mkdir(parents=True)
    with pytest.raises(RiderError, match="analysis_model_hash_mismatch"):
        profile.qualify()
    real_hash = analysis_profiles.file_hash
    monkeypatch.setattr(
        analysis_profiles,
        "file_hash",
        lambda path: (
            analysis_profiles.CHECKPOINT_HASHES["da3-small"]
            if path.name == "model.safetensors"
            else real_hash(path)
        ),
    )
    # Actual git inspection of a non-SDK directory fails as a domain error.
    with pytest.raises(RiderError, match="analysis_sdk_mismatch"):
        profile.qualify()
    monkeypatch.setattr(analysis_profiles, "verify_sdk", lambda path: None)
    metadata = profile.qualify()
    assert metadata["sdk_revision"] == analysis_profiles.SDK_REVISION
    assert metadata["resolution"] == 280
    (profile.model / "config.json").write_text(json.dumps({"model_name": "wrong"}))
    with pytest.raises(RiderError, match="analysis_model_variant_mismatch"):
        profile.qualify()


def test_code_fingerprint_tracks_actual_sources_including_uncommitted_edits(tmp_path):
    (tmp_path / "venom_module_rider").mkdir()
    source = tmp_path / "venom_module_rider/analysis.py"
    source.write_text("first source")
    first = code_fingerprint(tmp_path)
    source.write_text("changed source")
    assert code_fingerprint(tmp_path)["sha256"] != first["sha256"]
    assert first["python_sources"] == 1
    with pytest.raises(RiderError, match="analysis_source_unavailable"):
        code_fingerprint(tmp_path / "not-a-package")
