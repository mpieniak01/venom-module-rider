# Host loop timing v1

Calibration includes a Frequencies and resources panel. It configures one shared
Venom-host profile, independent of the selected device and motion calibration.
Values are delays/TTL in milliseconds; their reciprocal Hz is a nominal target,
not a measured sensor rate.

| Setting | Default | Bounds | Meaning |
| --- | --- | --- | --- |
| control_ms | 500 | 500–1000 | Control/vision status delay after completion |
| calibration_ms | 1000 | 500–2000 | Calibration status delay after completion |
| overview_ms | 2000 | 1000–5000 | Overview status delay after completion |
| idle_ms | 5000 | 2000–10000 | Resources/face/compute status delay |
| telemetry_ms | 2000 | 500–5000 | Existing /state cache TTL, demand-driven |
| preview_ms | 500 | 500–2000 | Fallback JPEG start-to-start interval |

Bounds constrain host demand; they are not qualified robot performance presets.
Slower preview may expire the displayed image before the next frame; freshness
limits remain unchanged. Native MJPEG, camera source FPS, sensor polling, motion
tick, command TTL, watchdog and hold renewal are unchanged. Calibration does not
add telemetry reads. Existing demand-driven readers are reused, without a poller.
Control reads still bypass cache. Browser control freshness retains its original
budget even when display polling is slowed.

GET /api/v1/rider/loop-timing returns schema_version=1, scope=venom-host, revision,
effective, fields, persistent, warning and reads. PUT on the same endpoint
requires the current X-Rider-Session and exactly {revision, values}. All values
are required integers on 100 ms steps from the published minimum within the bounds. Stale revisions return 409,
invalid profiles 422, persistence failure 503. The normal module mutation guard
and origin checks apply. Successful persistence precedes the effective revision.
Changes apply to subsequent cycles and reads, without restarting processes or
dispatching robot commands. The response confirms server configuration; clients
already in a scheduled wait adopt it on their next successful status read.

When RIDER_SELECTION_FILE is configured (as in the standard router lifespan),
the profile is atomically saved as loop-timing.json beside that file. A service
without a selection path uses process-local state. Invalid stored configuration
falls back to defaults with a visible warning; saving explicitly replaces it.
One service process owns the file; multi-process writes are not supported.

Read diagnostics instrument existing GET transports only: requests, errors,
cache_hits, coalesced, duration_ms and start_gaps_ms. Distributions retain at most
64 samples per fixed category, reporting count/p50/p95/max. Counters span this
process lifetime and selected devices; they are not per-device experiments.
Durations include failed/cancelled attempts, gaps are between HTTP starts,
not unique IMU measurements or firmware sampling. Missing values are null.
No payloads, URLs, credentials, extra HTTP requests or device clock differences
are stored. Status snapshots carry a diagnostic snapshot taken before their own
reads; the next status or explicit GET exposes those measurements.

For comparative motion experiments, record the host profile revision/effective
values alongside the independently revisioned Pi motion profile. Automatic
attachment to archived trials and Pi timing instrumentation remain future work.
No CPU, UART jitter, physical response latency or motion improvement is claimed.

## Isolated component regression

Build tests/browser/loop-timing-fixture.tsx with the existing host esbuild,
aliasing @ to the host web-next directory and using automatic JSX.
Serve the resulting JS/CSS with a temporary local HTML containing #root.
Execute tests/browser/loop-timing.js through Playwright run-code --filename
against that fixture, never a physical-device panel. The fixture supplies a
memory-only client; it tests all three languages, widths 1440/768/390, invalid
drafts, revision conflicts and saving. API tests independently verify transport,
persistence, cache and the absence of device mutations. This component fixture
does not certify the entire deployed host or physical motion.
