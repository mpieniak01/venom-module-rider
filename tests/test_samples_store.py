"""Durability, identity and failure paths use the actual SQLite registry."""

import hashlib
import io
from dataclasses import replace

import pytest
from PIL import Image

from venom_module_rider.errors import RiderError
from venom_module_rider.samples_frame import FrameBuffer
from venom_module_rider.samples_store import SampleStore, StorageLimits, encode


def frame(colour="black", device="robot", context=None):
    image = io.BytesIO()
    Image.new("RGB", (32, 24), colour).save(image, "PNG")
    buffer = FrameBuffer(wall=lambda: 1000.0, clock=lambda: 0.0)
    return buffer.get(buffer.offer(device, image.getvalue(), "image/png", context))


def setup(tmp_path, *, limits=None):
    store = SampleStore(tmp_path / "registry", wall=lambda: 1000.0, limits=limits)
    session = store.start("create-1", "robot")
    return store, session["session_id"]


def save(store, session, source, request="capture-1", **kwargs):
    return store.capture(session, request, source.key, source, **kwargs)


def test_exact_source_durable_idempotent_and_labels_do_not_modify_input(tmp_path):
    store, session = setup(tmp_path)
    source = frame(context={"sequence": 17, "source_timestamp": None})
    result = save(store, session, source)
    assert result["state"] == "saved"
    original, mime = store.original(result["sample_id"])
    assert original == source.payload and mime == "image/png"
    assert hashlib.sha256(original).hexdigest() == source.sha256
    sample = store.sample(result["sample_id"])
    assert sample["sequence"] == 17 and sample["source_timestamp"] is None
    assert sample["telemetry"] is None and sample["commands"] is None
    assert "dim" in sample["quality"]["flags"]
    label = store.label(
        sample["sample_id"], {"expected_state": "obstacle"}, expected_revision=0
    )
    auto = store.label(
        sample["sample_id"],
        {"review": "approved"},
        expected_revision=1,
        author_kind="auto",
    )
    assert label["revision"] == 1 and auto["review"] == "pending"
    with pytest.raises(RiderError, match="samples_label_conflict"):
        store.label(sample["sample_id"], {}, expected_revision=0)
    with pytest.raises(RiderError, match="samples_label_invalid"):
        store.label(sample["sample_id"], {}, expected_revision=2, author_kind="other")
    # Idempotency still works after the source has been evicted from RAM.
    again = store.capture(session, "capture-1", source.key, None)
    assert again == result
    assert store.original(sample["sample_id"])[0] == original
    assert len(store.samples(session)) == 1
    assert store.status()["samples"] == 1
    assert store.session(session)["counts"]["saved"] == 1


def test_same_raw_hash_is_stored_once_but_distinct_receipts_keep_order(tmp_path):
    store, session = setup(tmp_path)
    first, second = frame(), frame()
    assert first.key != second.key
    one = save(store, session, first, "one")
    two = save(store, session, second, "two")
    assert one["sample_id"] != two["sample_id"]
    assert [row["ordinal"] for row in store.samples(session)] == [0, 1]
    assert store.total_bytes == len(first.payload)
    assert len(list(store.blobs.glob("*.bin"))) == 1


def test_request_ids_cannot_silently_change_source_or_session(tmp_path):
    store, session = setup(tmp_path)
    source = frame()
    assert store.start("create-1", "robot")["session_id"] == session
    with pytest.raises(RiderError, match="samples_request_conflict"):
        store.start("create-1", "different")
    with pytest.raises(RiderError, match="samples_request_conflict"):
        store.start("create-1", "robot", {"different": True})
    save(store, session, source)
    with pytest.raises(RiderError, match="samples_request_conflict"):
        save(store, session, frame("white"))
    assert len(store.sessions()) == 1


@pytest.mark.parametrize(
    "failure,code",
    [
        ("expired", "samples_frame_expired"),
        ("mismatch", "samples_source_mismatch"),
        ("wrong-key", "samples_frame_expired"),
        ("historical", "samples_historical_consent_required"),
        ("bad-image", "samples_image_invalid"),
        ("wrong-mime", "samples_image_invalid"),
        ("closed", "samples_session_closed"),
        ("old-session", "samples_session_expired"),
    ],
)
def test_failed_capture_is_never_saved(tmp_path, failure, code):
    store, session = setup(tmp_path)
    source = frame(
        device="different" if failure == "mismatch" else "robot",
        context={"stale": failure == "historical"},
    )
    key = source.key
    if failure == "expired":
        source = None
    elif failure == "wrong-key":
        key = "absent"
    elif failure == "bad-image":
        source = replace(source, payload=b"not an image")
    elif failure == "wrong-mime":
        source = replace(source, mime="image/jpeg")
    elif failure == "closed":
        store.end(session)
    result = store.capture(
        session,
        "bad",
        key,
        source,
        admitted_at=9999 if failure == "old-session" else 1001,
    )
    assert result["state"] == "failed" and result["error"] == code
    assert store.samples(session) == []
    assert store.status()["samples"] == 0


def test_explicit_historical_sample_preserves_age_and_source(tmp_path):
    store, session = setup(tmp_path)
    result = save(
        store, session, frame("white"), receipt_age_ms=5000, allow_historical=True
    )
    sample = store.sample(result["sample_id"])
    assert sample["historical"] is True and sample["receipt_age_ms"] == 5000
    assert sample["clock_sync_uncertainty_ms"] is None
    assert sample["quality"]["mean_luma"] == 255


@pytest.mark.parametrize(
    "limits",
    [
        StorageLimits(total_bytes=1),
        StorageLimits(session_bytes=1),
        StorageLimits(session_samples=0),
    ],
)
def test_quota_failure_preserves_existing_sources(tmp_path, limits):
    store, session = setup(tmp_path, limits=limits)
    result = save(store, session, frame())
    assert result["state"] == "failed" and result["error"] == "samples_quota_reached"
    assert not store.samples(session)


def test_disk_failure_and_restart_never_acknowledge_partial_sample(
    tmp_path, monkeypatch
):
    store, session = setup(tmp_path)
    source = frame()

    def unavailable(_):
        raise OSError("simulated full disk")

    monkeypatch.setattr(store, "_blob", unavailable)
    assert save(store, session, source)["error"] == "samples_disk_full"
    restarted = SampleStore(store.root, wall=lambda: 1001.0, recover=True)
    assert restarted.session(session)["state"] == "interrupted"
    assert restarted.request("capture-1")["state"] == "failed"
    assert restarted.samples(session) == []


def test_abrupt_crash_after_blob_before_manifest_recovers_as_failed(
    tmp_path, monkeypatch
):
    store, session = setup(tmp_path)
    source = frame()
    original = store._blob

    class AbruptCrash(BaseException):
        pass

    def crash(frame):
        original(frame)
        raise AbruptCrash()

    monkeypatch.setattr(store, "_blob", crash)
    with pytest.raises(AbruptCrash):
        save(store, session, source)
    assert store.request("capture-1")["state"] == "running"
    recovered = SampleStore(store.root, wall=lambda: 1001.0, recover=True)
    assert recovered.request("capture-1")["error"] == "samples_interrupted"
    assert recovered.total_bytes == len(source.payload)
    assert recovered.samples(session) == []


def test_corruption_and_symlinks_are_not_served_as_originals(tmp_path):
    store, session = setup(tmp_path)
    source = frame()
    result = save(store, session, source)
    path = store.blobs / (source.sha256 + ".bin")
    path.write_bytes(b"corrupt")
    with pytest.raises(RiderError, match="samples_source_corrupt"):
        store.original(result["sample_id"])
    assert save(store, session, frame(), "second")["error"] == "samples_source_corrupt"
    path.unlink()
    target = tmp_path / "outside"
    target.write_bytes(source.payload)
    path.symlink_to(target)
    with pytest.raises(RiderError, match="samples_source_corrupt"):
        store.original(result["sample_id"])
    assert save(store, session, frame(), "third")["error"] == "samples_source_corrupt"


def test_roots_and_unknown_records_fail_explicitly(tmp_path):
    with pytest.raises(RiderError, match="samples_root_must_be_absolute"):
        SampleStore(tmp_path.__class__("relative"))
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    with pytest.raises(RiderError, match="samples_root_inside_checkout"):
        SampleStore(checkout / "data")
    store, _ = setup(tmp_path)
    for method in (store.session, store.sample, store.request):
        with pytest.raises(RiderError):
            method("unknown")
    with pytest.raises(RiderError, match="samples_invalid_identifier"):
        store.start("", "robot")
    with pytest.raises(RiderError, match="samples_metadata_invalid"):
        encode({"bad": float("nan")})


def test_session_and_request_count_limits_are_finite(tmp_path):
    store, session = setup(
        tmp_path, limits=StorageLimits(sessions=1, session_requests=1)
    )
    with pytest.raises(RiderError, match="samples_quota_reached"):
        store.start("second", "robot")
    save(store, session, frame())
    with pytest.raises(RiderError, match="samples_quota_reached"):
        save(store, session, frame(), "second")


def test_reading_an_archive_does_not_interrupt_a_live_session(tmp_path):
    store, session = setup(tmp_path)
    reader = SampleStore(store.root)
    assert reader.session(session)["state"] == "collecting"
    assert store.session(session)["state"] == "collecting"


def test_queued_capture_admitted_before_close_may_finish(tmp_path):
    store, session = setup(tmp_path)
    store.end(session)
    result = save(store, session, frame(), admitted_at=1000)
    assert result["state"] == "saved"
    assert store.session(session)["state"] == "closed"


@pytest.mark.parametrize("bad", [{"large": "x" * (32 * 1024)}, {"object": object()}])
def test_metadata_rejects_unbounded_or_unserializable_inputs(bad):
    with pytest.raises(RiderError, match="samples_metadata_invalid"):
        encode(bad)


def test_unsupported_schema_and_symlink_storage_fail_without_recovery(tmp_path):
    store, session = setup(tmp_path)
    with store.db() as db:
        db.execute("PRAGMA user_version=2")
    with pytest.raises(RiderError, match="samples_schema_unsupported"):
        SampleStore(store.root)
    with store.db() as db:
        db.execute("PRAGMA user_version=1")
    root = tmp_path / "symlinked"
    root.mkdir()
    (root / "blobs").symlink_to(store.blobs, target_is_directory=True)
    with pytest.raises(RiderError, match="samples_root_invalid"):
        SampleStore(root)
