# Motion study v1

The Calibration view places the manual trial at the upper right, with height and
balance below it. Direction and ramp numeric fields retain the Pi descriptors,
bounds and revision validation. A dirty draft is never recorded as an executed
profile; manual commands continue to use saved Pi settings.

## Recording and analysis

Enter a surface/context and choose **Start study recording**. This starts an
ordinary sample collection with policy scene_group=motion-study-v1:<context>.
It does not arm, move, start a camera or change calibration. While this explicit
collection is active, Calibration status includes telemetry through the existing
shared cache and refresh interval. It creates no independent poller or UART
reader. Closing the view ends its collection; STOP retires telemetry admission.
Start a new collection after retirement.

Select saved collections A/B, an optional device/source-session stream, and the
motion request used as time zero for each. A shared cursor, sample/event steps
and time window drive three raw angle plots, acceleration plots and sparse SDK
output points. Select an archive while the Pi is disconnected using Calibration;
import remains available through the existing Samples view. Export uses the
same ZIP archive, storage, limits and dataset revision as other sample analysis.

Trial details expose request direction, mode, normalized intensity, requested
pulse duration, result, saved Pi motion profile/revision, height/balance snapshot
and host timing values/revision. A profile is request-revision-match only when
the cached Pi revision matches the command revision and device settings were
requested. Otherwise intensity/duration remain unknown. Cache age describes the
first snapshot of a deduplicated profile. A settings ACK is not physical proof.

A/B aligns host receipt times of selected move requests. Different directions
or unknown/different surface context are called out. Observed angle ranges are
reported only with at least three contiguous valid points within the requested
pulse duration, with consistent units. These are retained sample ranges, not
the full physical response. Source gaps may entirely miss a 150 ms impulse.
Actual actuation duration, physical latency, velocity and distance are unknown.

## Evidence and timeline interfaces

GET /api/v1/rider/samples/sessions/{id}/motion-timeline is an archive-only,
bounded worker operation. It performs no Pi calls. Timeline schema v1 carries
dataset_revision, collection status, context and device/source-session streams:
points, command events, sparse executor outputs and recorded profile references.
The origin is the first move request (or first receipt for old archives).
Host monotonic receive time is never subtracted from a device timestamp.
Original source timestamps and boot identity remain separate.

Evidence extension v2 permits original v1 IMU/executor records plus strict v2
host-monotonic command and profile records. Both online writes and imports
use the same validation; extension v1 rejects v2 records. Old v1 archives remain
readable with absent commands/profiles explicitly unknown. Commands contain an
allowlist of intent fields; arbitrary request bodies, credentials and URLs are
not copied. Each HTTP intent has a distinct operation ID (hold renewals remain
distinct); request/panel/generation/sequence preserve caller correlation.

Requested/result events are admitted without IO before/after the existing
control operation. Admission captures the original source identity, including
for STOP and cancelled requests. Recording faults cannot block control. The
existing single writer drains an additional bounded 64-record/64 KiB command
queue; the telemetry window remains independently bounded at 64 records/64 KiB.
All records share the session quota of 2048 records / 2 MiB and the existing
disk quota. Drops/failures remain explicit. A result arriving after collection
closure is omitted; a request without a result has unknown outcome.

Lines break on missing units, invalid/stale/offline state, source reset,
unknown/changed boot identity, nonpositive time or gaps over one second. No
interpolation fills gaps. sdk_raw is preserved, never relabelled as degrees.
The [3D view](ATTITUDE_REPLAY_V1.md) consumes this same timeline and requires its qualified producer convention.

## Verification and deployment

make check HOST=<canonical Venom root> runs the module gates. The isolated
tests/browser/motion_study_server.py and bundled
tests/browser/motion-study-fixture.tsx expose synthetic ASGI Demo data only;
tests/browser/motion-study.js verifies layouts, translations, A/B, cursor,
draft validation, zoom and capture while asserting zero actuator requests.
Build fixture assets in ignored docs_dev/_reports/motion-study-browser using
the host esbuild dependency. Never point that scenario at a production robot.

This increment changes the Venom module only. Deploy its merged revision into
the canonical Venom checkout, regenerate optional-module mirrors and restart
the module API through the existing launcher. Rider-Pi firmware needs no change.
