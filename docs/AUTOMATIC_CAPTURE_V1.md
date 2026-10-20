# Automatic sample capture v1

Automatic capture is an explicit, default-off mode of the existing sample
registry. Start a manual or sequence collection beside the camera, keep an
existing live analysis running, then choose **Enable automatic capture**.
A saved automatic sample appears in the same library as operator photos.
The mode never starts a camera, analysis model, navigation or motion.

## Events and limits

The configurable profile offers a new obstacle, a change in path assessment,
uncertainty and a new scene. Persistence requires distinct source frame IDs,
a minimum frame count and duration. A perceptual image hash, recent-scene
deduplication, cooldown, future-frame burst spacing, duration and attempt limits
bound the collector. A static or flashing result does not create an unlimited
archive. Defaults: 3 frames / 250 ms persistence, 3 s cooldown, one image,
50 attempts, 300 s duration and a 1.5 s maximum analysis gap.

The current depth provider supplies geometry/path states, not named-object
recognition. There is no chair detector or calibrated probability. Confidence is
null, class is unknown, and every automatic label is **pending operator review**.
Only a subsequent approved operator label can be evaluation ground truth.
Changing the profile requires stopping and explicitly enabling a new run.

## Source and storage contract

The hook receives the exact RAM capture ticket after VisionLoop annotates its
completed result. It performs no extra camera read, inference or file operation.
One automatic job at most is admitted to the existing bounded sample IO writer.
Manual capture keeps the other writer slots. RAWs use the existing content
addressed blobs, quota and collector lease; no data archive is added on Pi.

Source device/host session, run, sequence, frame ID, configuration revision,
model hash, engine, trigger/source identity, profile revision and event reasons
are recorded. The decision's original remaining lifetime is frozen before
queueing and rechecked after queue delay/decoding. Expired, historical,
diagnostic preview or foreign results cannot be promoted to fresh events.
Bursts select future distinct frames, not copies of the trigger.

Automatic capture skips a RAW already saved in that collection. An explicit
manual capture retains its own request identity, even for the same RAW.
Existing operator labels are never overwritten by the automatic collector.
Queued, saved, skipped and failed events are distinct. If bytes are saved but
annotation fails, the event reports failure and retains the sample ID.

## Stop and recovery

**Stop automatic capture** invalidates the enable epoch immediately, then waits
for the ordered IO marker. Already admitted work may finish before this response;
after it returns no new automatic samples are written. Ending the collection
uses the same boundary. Neither action stops motors or owns the camera.

Global hardware STOP, camera/source stop, configuration or device change
invalidate automatic capture without waiting for filesystem IO. A write already
in progress may finish; hardware STOP must not be delayed by a stalled disk.
Feed loss, expired input, model/run/configuration change, quota or write failure
also stop automatic capture. A new explicit enable is required. Application
restart recovers unfinished metadata as interrupted; no automatic resume.

## API

All paths inherit the existing Rider access/origin/environment policy.

- GET /api/v1/rider/samples/automatic: current runtime state/counters.
- POST /api/v1/rider/samples/automatic: explicit enable with session_id,
  unique request_id, optional profile, and current X-Rider-Session.
- POST /api/v1/rider/samples/automatic/stop with {}: ordered data stop.
- GET /api/v1/rider/samples/automatic/runs/{id}: durable run and last 20 events.

A lost enable response is recovered by reading status, not replaying a new
request. Retrying the identical request ID never creates a second enable epoch.
Restarted/finished requests remain stopped. The library and originals remain
available while Pi is disconnected.

Rollback to manual-only collection leaves source samples and label revisions
intact. Additive automatic metadata uses the existing registry schema version;
it does not authorize motion or require a Pi deployment.

## Passive host evidence

Explicit sample collection also records bounded observations from existing
/state and /api/control/state responses in Venom. Automatic event capture does
not start another telemetry poll, camera reader or model for this evidence.
The evidence extension and telemetry-only export/import are described in
SAMPLES_V1.md and DATA_EVIDENCE_V1.md.

Event frames can reference recent durable evidence with an explicitly unmatched
exposure association. A recent host receipt cannot qualify stale sensor data,
establish a physical unit or verify motion. Exact image source provenance is
preserved independently. STOP/source teardown retires pending evidence recording;
neither an event nor subsequent telemetry resumes the old collection binding.
