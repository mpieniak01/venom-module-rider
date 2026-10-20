# Rider sample sessions v1

The image collector runs in the existing Venom module process. Pi remains the
camera/actuator terminal. Collection reuses frames already received by the RAW
preview or vision loop; it does not create another camera producer, Pi service,
model, stream, archive or motion command. JPEG/PNG receipts are not a video file.

## Operator flow

1. Select a configured device and explicitly show the camera preview. This uses
   the existing S2 preview goal. RAW collection requires neither a model nor ARM.
   Opening the page, library or session never activates the camera.
2. Next to the camera, start a manual collection or a 10-second sequence. The UI
   sequence admits at most 50 receipts, at intervals of at least 200 ms. API limits
   are 30 seconds, 150 receipts and a minimum 150 ms interval.
3. Save a sample of the displayed image. Its host receipt ticket pins the original
   RAW bytes, including when an overlay is displayed. PROC images with no matching
   RAW ticket cannot be captured. An expired ticket fails; no next frame substitutes.
4. `Queued` is not `Saved`. A saved response includes the immutable sample ID after
   the source blob and SQLite manifest commit. For a lost HTTP result, check the
   same capture request ID. Reload retains only this data request, never movement.
5. End collection. This ends the data lease, not motors or the existing S2 camera
   owner. The loop STOP releases loop/preview resources through its existing path;
   the manual pad STOP brakes motors. Explicitly disable preview resources when
   no longer needed. Hidden/unmounted collection is interrupted; abandoned manual
   collections expire after 30 minutes. Accepted queued writes finish on close.
6. Open **Samples and sessions in Venom**, choose a session, inspect the original,
   label the scene and obstacle side, and export ZIP. This works with Pi disconnected.
   Labels describe data and have no actuator effect. No label is inferred as human
   approval from a filename or prediction. Operator approval is an explicit label
   revision; automatic labels remain pending.

Sequence order is admission order. Original run/frame/sequence, source times and
RAW SHA-256 remain in each record. Repeated content remains present under distinct
sample IDs; identical blobs share storage. Frame gaps, missed writes and missing
input can mark a sequence incomplete. A sequence uses actual receipt times and
never invents constant FPS. A dim image is still collectable and retains quality
flags; blur is explicitly unmeasured. Source quality does not grant driving GO.

## Storage and bounds

Set private server `RIDER_SAMPLE_ROOT` to an absolute directory **outside all Git
checkouts**. The default is `$XDG_DATA_HOME/venom/rider/samples` (or
`~/.local/share/venom/rider/samples`). Demo-only hosts use `demo-samples` instead.
Use the same root after process/worktree replacement. Do not point production and
an isolated fixture at the same root. Private home images and exports never enter Git.

- RAM: at most 8 MiB / 32 receipts, TTL 30 seconds; one source at most 512 KiB.
- Images: JPEG/PNG, at most 1280×720, decoded and checked before persistence.
- IO: one worker, four admitted jobs, no unbounded write or read queue. IO does not
  run in the event loop or actuator lane. Four concurrent collection sessions.
- Disk: 2 GiB total, 128 MiB/session, 256 MiB reserved free space; 1000 samples,
  2000 requests per session, 500 sessions. SQLite/WAL/exports count toward usage.
- Labels: 32 immutable revisions per sample. Conflicting revisions fail explicitly.
- Export cache: two bounded temporary ZIPs, ten-minute cleanup on subsequent export.
  HTTP deletes its owned temporary export after delivery. RAW originals are retained.

Quota exhaustion refuses new data. It never deletes originals to make space,
changes movement, cancels STOP, or claims success. Archive/prune sessions explicitly
before reaching the quota. There is no automatic archival retention policy in v1.
A storage initialization failure disables data routes, not control/config/STOP.
Only one active collector process may own a root. Readers can browse without
interrupting it. Restart recovery marks interrupted jobs/sessions; it does not
resume a camera, drive intent, collection or infer a completed write.

A source blob is written to a temporary file, fsynced and atomically renamed;
its directory is fsynced before the immutable SQLite manifest is committed.
After an interrupted write an orphan blob may remain and is counted. It is never
presented as a saved sample without its committed manifest. Original reads verify
size/hash and reject missing/corrupt/symlink sources.

## Data provenance

A receipt timestamp is Venom receipt time, not camera exposure time. Unavailable
pose, calibration, transform, telemetry and command association stay null or
`unmeasured`. Pi wall timestamps are unsynchronised; timestamp source and clock
domain are preserved. No timestamp subtraction implies network latency without
a known clock relationship. Relative source age plus host receipt age is only a
lower bound. Replay retains recorded time and is historical. A live demo is marked
`device_mode: demo`, never treated as physical evidence.

When vision is active, prediction metadata records the actual input descriptor,
model revision and decision. It remains separate from versioned ground-truth
labels. A queued capture freezes its metadata; later predictions cannot rewrite it.
Saving a historical/stopped image is explicit and retains age/history flags.

## API

Existing module access/origin/environment guards apply. `/api/v1/rider/samples`:

| Method | Path | Result |
|---|---|---|
| GET | `/status`, `/sessions` | Bounds/usage; available sessions |
| POST | `/sessions` | Idempotent collection start: request_id, source_device_id, policy |
| GET | `/sessions/{id}?offset=0&limit=24` | Session and a bounded page (maximum 50) |
| POST | `/sessions/{id}/end` | End data only; optional interrupted boolean |
| POST | `/capture` | 202 admission: session_id, capture_request_id, frame_key, allow_historical |
| GET | `/requests/{id}` | Same request outcome after HTTP loss/restart |
| GET | `/items/{id}`, `/items/{id}/original` | Metadata/label revisions; verified RAW |
| POST | `/items/{id}/labels` | expected_revision and values; conflict returns 409 |
| GET | `/sessions/{id}/export` | ZIP plus X-Rider-Dataset-Revision |

RAW/vision preview returns `X-Rider-Capture-Key` only when an exact original is
available in the bounded host cache. The UI binds capture to a decoded displayed
image. Request IDs refer to one source and session; reusing them for another source
returns conflict. A saved retry remains idempotent after source-cache expiry.
Data errors include `samples_frame_expired`, `samples_queue_full`,
`samples_registry_busy`, `samples_quota_reached`, `samples_disk_full`,
`samples_source_corrupt` and `samples_storage_unavailable`. No error fetches another
frame, arms Pi or silently falls back to a simulated source.

## Local import and CLI

Use the existing Venom virtualenv and the same module/root:

```sh
python -m venom_module_rider.samples_cli --root /absolute/durable/rider-samples status
python -m venom_module_rider.samples_cli --root /absolute/durable/rider-samples import /absolute/images --request-id historical-set-1
python -m venom_module_rider.samples_cli --root /absolute/durable/rider-samples list
python -m venom_module_rider.samples_cli --root /absolute/durable/rider-samples session SESSION_ID
python -m venom_module_rider.samples_cli --root /absolute/durable/rider-samples export SESSION_ID /absolute/new-session.zip
```

Import accepts a v1 export ZIP/directory or a flat local JPEG/PNG set. Validate the
whole source before admitting the session: paths, symlinks, duplicate ZIP entries,
size bounds, formats and hashes. No `extractall`, network fetching or overwrite of
existing export targets. Historical import uses the same capture registry/store.
Original files are untouched; absent exposure/pose/labels stay unknown. Legacy
filename ordering is explicitly `filename-unverified`, unsuitable for temporal
continuity claims. A repeated request/dataset returns the same session; a different
dataset with that request ID conflicts. Export fixes sample and label revisions in
one SQLite snapshot and includes a dataset revision hash for offline analysis.

## Acceptance and rollback

Software verification uses the real module API/UI with a loopback Pi fixture,
source hash checks, lost-result/idempotency, corruption/quota/restart and blocked-IO
STOP cases. It is not physical movement or environmental acceptance. Morning
operator testing remains pending. Keep this PR unmerged until that review.

Roll back the module code to its recorded parent revision, leaving the sample root
intact. The prior module simply does not expose sample routes. Do not delete the
root or overwrite its SQLite database to roll back. Export sessions before any
future incompatible migration. This increment introduces schema version 1 only.

Derived analysis files retained by a newer optional feature also count toward the shared byte quota after rolling back to this registry version. Temporary hardlinks count once; unsafe symlinks are rejected. Sources and derived files are never deleted automatically to make room.

## Host evidence from existing responses

An explicitly opened collection also admits bounded IMU and executor observations
already returned by the existing Venom reads of /state and /api/control/state.
This adds no polling, UART access, camera reads, inference or device storage.
Telemetry-only collections are valid and can be exported with zero RAW images.

Source samples are identified by device, the selected-device host session,
producer instance, original observation timestamp and source values. Host receipt
time and changes in cache-age/stale metadata do not create another sample. A
duplicate retains its first admitted receipt even after its RAM window expires.
Raw yaw is copied from sensor_quality.sensors.yaw; display wrapping is not applied.
Unknown, stale and physically unverified quality remains explicit.

Evidence uses the existing process lease and single disk writer. At most one
evidence job occupies its bounded queue. Saturation drops evidence immediately;
manual capture and STOP do not wait for evidence admission. Each record is at most
8 KiB, the recent window is 64 records / 64 KiB / 30 seconds, and each collection
admits at most 2048 distinct records / 2 MiB within the shared 2 GiB and per-session
128 MiB archive quotas. The per-collection deduplication index is separately
bounded by those 2048 admitted identities. There is no automatic retry that
changes the original receipt.

Frames may reference up to eight recent durable records in source_context.evidence.
Telemetry and executor snapshots carry the association
host-reception-window-unmatched-exposure and unknown clock synchronization
uncertainty. This is a host receipt window, not proof that telemetry matches image
exposure. Existing camera source references and exact image associations remain
unchanged. Failed/dropped evidence never creates a dangling frame reference.

Collection end, STOP, source switch and registry close retire the old evidence
binding and pending evidence. They clear the recent window; recording requires a
new explicit collection. A restart does not resume a collection. Closing one
collection leaves other explicitly opened collections eligible for the same source.
The session summary exposes saved, dropped, failed and bytes plus storage limits.
The active storage status also exposes bounded RAM usage. Durable summaries remain
available when browsing without a connected Pi.

Exports optionally contain evidence={schema_version:1,records:[...],status:{...}}.
All evidence participates in the same consistent SQLite snapshot and dataset
revision hash as RAW manifests and labels. Import validates every record and
evidence bounds before creating a session; telemetry-only archives preserve
original source and receipt clocks. Legacy archives without the extension remain
valid and do not fabricate telemetry. See DATA_EVIDENCE_V1.md for the shared
record contract.
