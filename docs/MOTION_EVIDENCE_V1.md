# Offline IMU and motion evidence report v1

The report describes evidence already received and archived by Venom through
[the shared evidence v1 contract](DATA_EVIDENCE_V1.md). It opens no device,
transport, camera, model or actuator. It creates no additional Pi polling.
An evidence-only session with zero images is a supported input. Missing evidence
in a legacy archive produces an explicit unavailable report.

## Python interface

```python
from venom_module_rider.motion_evidence import analyze_evidence, analyze_manifest

report = analyze_evidence(records)
report = analyze_manifest(validated_session_manifest)
```

Both functions are deterministic and return JSON-compatible dictionaries. They
leave input untouched. `analyze_manifest` reuses `SampleArchive._validate_manifest`
for the existing session format and dataset-revision hash; corrupted input is an
error, whereas absent telemetry is unavailable. Individual malformed records
passed directly to `analyze_evidence` are rejected using canonical
`validate_evidence`, counted and reported as partial. Duplicate record IDs are
counted, with conflicting payloads explicitly reported; the first payload is
retained for diagnostics. The report does not repair an invalid archive.

The optional second argument is `MotionEvidenceSettings` or a dictionary with
only these fields:

| Field | Default | Meaning |
| --- | --- | --- |
| `max_contiguous_gap_s` | `1.0` | Largest qualified source interval for a yaw derivative |
| `event_duration_s` | `0.15` | Duration used to describe undersampling risk |
| `source_yaw_unit` | `null` | Verified original-source angle unit, `deg` or `rad` |
| `source_yaw_unit_verified` | `false` | Explicit assertion that the unit mapping was validated |

Durations must be finite, positive numbers at most 3600 seconds. Settings change
only offline analysis. They cannot change device calibration or control policy.
Unknown settings, invalid settings or exceeded bounds raise `ValueError`.

## Report fields and reproducibility

The report carries `schema_version`, `analyzer_version`, the exact `settings`,
`status` (`available`, `partial`, `unavailable`) and `reason`. Available means
valid IMU evidence exists; it does not certify physical movement or sensor
accuracy. A partial report exposes rejected rows or conflicting record IDs.
Executor-only input has unavailable IMU analysis, with its executor count retained.

`input` includes received, validated and unique record counts, rejected records,
duplicate IDs and conflicts. `digest_sha256` hashes each input record as sorted,
compact JSON followed by a newline, in input order, including rejected rows.
Python's JSON spelling of NaN/Infinity is used only when hashing invalid direct
inputs; such values never enter accepted records or report numbers.
`validated_unique_digest_sha256` separately identifies normalized retained rows.
Manifest reports also retain `dataset_revision`, `manifest_digest_sha256` and
`collection_status` (including collection loss/limit counters). Neither digest
claims cryptographic authenticity or physically verified data.

`channels` lists each channel and unit separately, distributions and missing-value
counts, with channel quality flags distinct from root/source `quality`. Unknown
units remain `unknown`, and incompatible units are never averaged together.
Distributions use retained unique record IDs, including distinct records with a
repeated source timestamp; this basis is explicit. Counts of distinct source
observations and repeats are reported separately in `clocks.samples`. A source
observation is identified by device, host source session, source instance, clock
domain and source timestamp. Missing timestamps cannot establish uniqueness.

All distributions contain count, min, p50, p95, max and mean; unavailable numbers
are `null`, never zero. Percentiles use linear interpolation between sorted
observations. No source timestamps are reordered to hide reversals or resets.

## Clocks, continuity and sampling coverage

`clocks.source_gaps_s` describes strictly positive consecutive source intervals
in input receipt order. `clocks.host_receipt_gaps_s` independently describes
positive monotonic host-receipt intervals. Source instance/session changes break
both series, and counts expose these discontinuities. Repeats, nonincreasing host
receipts, out-of-order/reset source timestamps, missing source times and unknown
source instances are explicit. A reversal cannot determine whether clock reset,
reordering or corruption caused it. Only the next positive adjacent interval
starts a new diagnostic segment. Unknown instance IDs prevent a yaw derivative.

The source clock is `device-wall-unsynchronised`; it is never subtracted from
host wall or monotonic time. Cross-clock latency and synchronisation uncertainty
remain unknown. Source-record time may aggregate channel timestamps: it is not
proof that every channel was refreshed at that instant.

`clocks.sampling` reports how many observed intervals are at least the configured
event duration. For example, a synthetic median interval near 0.338 seconds can
entirely miss a 150 ms pulse. This describes only the supplied dataset, not a
promised Venom receive rate. Shorter observed intervals also cannot certify
continuous coverage, pulse detection or camera exposure alignment. Event
classification and coverage remain unavailable/unverified; no guessed pulse or
motion labels are emitted. `short_event_may_be_entirely_missed` is `true` when an
observed interval is at least the event duration and `null` otherwise. It never
becomes `false`: shorter retained intervals cannot exclude missed events at
session boundaries, around missing timestamps or in unobserved periods.

## What acceleration and yaw can establish

`acceleration.raw_norm` provides the Euclidean norm only when all three finite
raw acceleration channels share one unit. It keeps unknown raw units separate
and counts missing, mixed-unit and nonfinite derived vectors. The norm includes
gravity, sensor bias and noise. Raw zero or an SDK zero does not prove stillness.

`acceleration.gravity_compensated` and `forward_trend` are unavailable because
physical axes, reference frame, orientation convention, gravity mapping and
calibration are unqualified. Signed acceleration alone does not distinguish
speeding up from slowing down while reversing. There is no undocumented gravity
subtraction or conversion from SDK raw units.

`clocks.yaw_angular_rate` is a source-channel diagnostic, never verified physical
heading rate. It is disabled by default (`calibration_required`). With a validated
`deg`/`rad` source mapping, a derivative additionally requires:

- Original `values.yaw` and matching unit; `sdk_raw` remains unsupported.
- The same device, host session and known source instance, increasing source and
  host receipt times, and an interval within the contiguous limit. If a yaw
  channel explicitly supplies `instance_id`, it must agree with that known
  source instance; disagreement excludes the pair as `source_yaw_instance_mismatch`.
- Source/channel `ready=true`, no stale/offline/simulated flag, and no invalid or
  wrapped-display channel marker. `physical_validation=unverified` is retained
  separately and never promoted to physical verification.
- Increasing, finite original `quality.sensors.yaw.observed_at` values within the
  contiguous limit. The aggregate source-record time cannot replace missing yaw
  timestamps.

The collector preserves original yaw from source sensor quality, not UI-wrapped
angles. The analyzer unwraps consecutive source angle differences to the shortest
arc, converts radians if explicitly mapped, and reports degrees per second. An
exact half revolution is ambiguous and excluded. The result assumes less than
half a revolution between samples; faster/full turns cannot be recovered from
sparse yaw. Excluded pairs carry reason counters. No derivatives cross identity
changes, source-time reversals, repeats, missing yaw times or host-time reversals.

`physical_velocity` (m/s) and `physical_distance` (m) are always unavailable in
v1. No noisy-acceleration integration, zero-velocity update or stillness inference
is performed. Executor acceptance, state or motor output is context only and
cannot establish observed movement or standstill. This report needs independent
qualified measurement before making physical speed/distance assertions.

## Local CLI

```bash
PYTHONPATH=/home/ubuntu/venom:. /home/ubuntu/venom/.venv/bin/python \
  -m venom_module_rider.motion_evidence_cli session.zip --output report.json
```

The source can also be a session manifest JSON file. `--settings settings.json`
accepts the same analysis fields. ZIPs use the existing archive validator,
including path, entry, payload and revision checks. The CLI never extracts ZIPs,
downloads dependencies or contacts a server. Output uses exclusive creation and
refuses existing files and symlinks. Invalid input returns exit code 1 and a small
error JSON; a valid unavailable report returns 0. The output parent must exist.

Bounds: 2048 records / 2 MiB evidence, existing 40 MiB manifest limit, 512 MiB local
ZIP file/expanded payload budget plus the shared manifest allowance, 16 KiB settings
and 1 MiB report. Before serializing any record, a full-session JSON preflight
counts exact compact ASCII-escaped JSON bytes and traversal nodes, rejects cycles
and unsupported non-JSON types, and limits nesting to 32 levels and integers to
4096 bits. Oversized strings and containers are rejected before copying or sorting
them. Shared container references are allowed and charged for every occurrence.
NaN/Infinity rows still contribute their deterministic digest spelling and are
rejected individually by canonical evidence validation. These are limits, not
automatic retention or deletion policies.

Focused checks use the existing environment and synthetic fixtures:

```bash
python -m ruff check venom_module_rider/motion_evidence.py \
  venom_module_rider/motion_evidence_cli.py tests/test_motion_evidence*.py
PYTHONPATH=/home/ubuntu/venom:. python -m pytest -q tests/test_motion_evidence*.py
PYTHONPATH=/home/ubuntu/venom:. python -m mypy \
  venom_module_rider/motion_evidence.py venom_module_rider/motion_evidence_cli.py
```

## Operator report

In Samples and sessions in Venom, select a saved session, expand Motion telemetry
analysis and choose Analyze saved session. This is an explicit local operation,
not a new polling loop. It reads the same stored dataset revision even when Pi
is disconnected. It uses no model, camera or control request.

GET /api/v1/rider/samples/sessions/{session_id}/motion-evidence computes the
bounded report through the existing sample worker. The operator endpoint uses
conservative default settings: no assumed angle calibration or physical velocity.
The library displays sample counts, source-time gaps, raw acceleration norm and
unit qualification. The detailed report retains source quality, loss counters,
settings and input hashes. A report's available status means diagnostic data is
available; it is never permission to move or proof of physical progress.

The local CLI accepts a session ZIP or validated manifest JSON and creates a new
report file without overwriting an existing report:

```sh
python -m venom_module_rider.motion_evidence_cli /absolute/session.zip --output /absolute/new-report.json
```

Use the existing host environment. Optional analysis settings are explicit and
never modify robot configuration. SDK-raw angles remain unqualified until an
actual documented conversion exists; setting a flag alone does not convert them.
