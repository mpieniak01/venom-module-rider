# Stored session analysis v1

Analysis is an explicit step after collection. Open the Rider sample library,
choose a stored session, select a retained profile and click **Analyse session**.
The Pi can be disconnected. Collection, analysis and future mapping are separate
operations. Analysis never selects a device, starts a camera, arms, or moves it.

## Inputs and models

The sample registry remains the only source of original bytes and labels.
Legacy sample runs freeze the selected offset/count (at most 200 frames), source hashes,
order/provenance, current label revisions, parameters, model hashes, SDK revision
and Python source digest. Parameters and labels changed later cannot overwrite
an existing result. At most 50 runs are retained; there is no automatic deletion.

Video runs select `spec.input_source="video"` and process the entire finalized
session source directly; offset/count do not truncate video inputs. A compact
immutable source descriptor pins video/index hashes and frame count. RAW source
bytes are not copied into the analysis directory. New runs can judge the same
recording differently; the UI keeps saved model/configuration distinct from the
current settings draft. Replay and source-frame GET endpoints never invoke a
model. Whole-video results are chunked. Per frame, a compact transparent classification
PNG retains only the model mask and frozen geometry outlines. No RAW texture,
full RGB overlay JPEG or depth.npy/depth.png copy is stored. The layer identifies
its source SHA256 and dimensions; the browser composites it with that exact
original frame only during inspection. A green/yellow/red histogram
counts frame classifications and is not ground-truth accuracy or a robot driving
verdict. Rejected inputs are counted separately and cannot appear as clear.

Profiles come from `config/vision-research.json`:

- `da2-default`: production DA2 weights and preprocessing, evaluated offline on CUDA.
- `da3-small-280`: retained experimental DA3 Small at resolution 280, CUDA required,
  using its existing research interpreter and qualified SDK.

No weights, SDK or dependencies are downloaded. Installed means files/runtime are
present; qualification verifies actual hashes before admission and again in the
worker. Presence never means loaded. DA2 remains the live default. Optional
`RIDER_VISION_ASSETS` selects the server-owned asset root; `RIDER_DEPTH_MODEL`
retains its existing DA2 meaning. Neither path is accepted from the browser.

The live decoder, quality gate, depth adapters, geometry and overlay are reused.
The default offline geometry is explicitly `image`; choose `ground` and provide
actual normalized camera/footprint calibration to evaluate the existing adaptive
method. Missing calibration yields UNKNOWN. Camera height/tilt are not assumed
constant or converted to measured centimetres. Stored capture transformations
are preserved in the input snapshot; changing projection parameters requires a
new run. These assessments are diagnostic, not motion permissions.

Production quality rejects dark/low-contrast images before loading a model.
An all-rejected run can finish with no inference: model/load timing is N/A.
The explicit offline `model_probe` option bypasses this image quality gate only;
it never qualifies driving.

DA2 supports one view. DA3 supports causal windows 1/3/5. Multi-view requires
verified receipt/manifest ordering, consecutive source sequences, matching runs,
distinct frame IDs and source hashes. Filename order and repeated stills do not
become video. Short initial windows are reported with their actual size; frames
are never duplicated to fill them. A rejected frame resets the image window.

## Execution and failure states

The existing host task manager/queue and StateManager track jobs independently
of chat. In llm_off the same queue classes and host state are used without
starting a chat model; analysis API cancellation remains available.

One owned low-priority analysis child executes at a time; at most four analyses can
be queued/in progress. It has at most two CPU threads/cores. Existing live vision
preempts this child's compute; new compute waits for two quiet seconds. The
worker never owns a database transaction: original captures and derived outputs
share the registry's single IO executor and process writer lease. Same-host
collection can continue. No live model is unloaded and no control deadline is
relaxed. The child cannot open IP connections.

The manager polls its child's RSS (2.5 GiB limit) and available host memory
(512 MiB reserve), with a 15-minute total timeout for legacy sample windows. Whole-video runs use
`RIDER_VIDEO_ANALYSIS_TIMEOUT_S` (default 21600 seconds, accepted 60..86400).
Video analysis accepts at most 10000 indexed frames within the existing 128 MiB
source-session budget; exceeding a limit produces an explicit error. These are monitored process
limits, not a performance or real-time guarantee. A parent-death signal kills
the owned child even if it is paused. No unrelated process is signalled.

States are queued, running, succeeded, failed or cancelled. Progress comes from
actual loading/decoding/inference/geometry/save barriers. Cancel requests stay
pending until the owned process stops and prior admitted writes are ordered.
A host restart marks lost executions interrupted and never resumes them. If
another registry writer/executor owns startup recovery, list metadata reports
`recovery_pending`; new work must acquire ownership first.

Every derived artifact is immutable, fsynced and committed before it is served.
Failed/cancelled runs can retain partial outputs; only a complete committed
matching report qualifies success. Source bytes, labels and earlier outputs
survive failures, full disk, missing weights and cancellation. Original blobs,
exports, DB metadata and derived artifacts share the same quota. Maximum artifact
size is 8 MiB and maximum derived bytes per run 256 MiB; limits can reject a large
run before its requested frame count completes.

## Reports and comparisons

Per frame: original sample ID/hash, actual quality, relative depth array
(`.npy`, float32, no pickle), depth PNG, the production geometry overlay, state,
reason, sector diagnostics, actual window and measured stage timings.
Rejected-quality frames have no fabricated depth/overlay.

Only the latest operator-approved label revision is ground truth. Automatic,
pending and rejected labels never score errors. False GO requires labelled
obstacles; false STOP requires labelled clear inputs. Missing denominators are
JSON null / UI N/A, never zero errors. Side errors need reviewed side labels.
Flicker requires verified adjacent distinct frames. Historical transport latency,
metric distances and physical motor timing remain N/A.

Reports separate model loading, queue delay and per-frame decode, inference,
geometry, render, persistence, and local IPC/IO queue timing. Persistence covers
frame artifacts; local IPC includes serialization/transfer and waiting for the
single writer. `admission_to_report_ms` ends before the final report fsync and
terminal state transition. CPU peak RSS and zero GPU allocation are explicit.
Do not compare these offline timings directly to a live GPU/control deadline.

Comparison aligns the exact selected input snapshots, including frozen labels
and transforms. Different parameters/models are intentional. A mismatched input
digest suppresses numerical quality deltas. Unlabelled data remains N/A even
when both runs finish successfully.

## API and CLI

All endpoints use the module's existing authorization, origin and environment
mutation policy at `/api/v1/rider/analysis`:

- GET `/profiles`, `/runs?session_id=ID`.
- POST `/runs` with `{request_id, session_id, spec}`.
- GET `/requests/{request_id}`: recover an unknown submission outcome.
- GET `/runs/{id}`: compact status; `?context=true` reads its frozen inputs.
- POST `/runs/{id}/cancel` with `{}`.
- GET `/runs/{id}/artifacts` and `/artifacts/{name}`.
- GET `/compare?first=ID&second=ID`.

A request ID is idempotent for the same session/spec, including after labels
change or weights become unavailable. A different intent with that ID conflicts.
HTTP disconnect does not cancel an accepted server-owned admission. Reload
recovery reads the prior ID and never automatically enqueues a new analysis.

From the module checkout, using the existing host Python environment:

```sh
python -m venom_module_rider.analysis_cli profiles
python -m venom_module_rider.analysis_cli submit SESSION --request-id MY-INTENT
python -m venom_module_rider.analysis_cli recover MY-INTENT
python -m venom_module_rider.analysis_cli show RUN
python -m venom_module_rider.analysis_cli report RUN
python -m venom_module_rider.analysis_cli cancel RUN
python -m venom_module_rider.analysis_cli compare FIRST SECOND
```

Global `--url` defaults to the existing localhost API on port 8800. It accepts
only an HTTP loopback origin and does not follow redirects or environment proxies,
so the configured access token cannot be forwarded to another host.
`submit --spec FILE` reads validated AnalysisSpec JSON. It cannot provide model
paths/URLs, actuator settings, a shell command or a new provider.
`RIDER_ACCESS_TOKEN`, if configured, supplies the existing authorization header.
The CLI is an API client, not another inference service.

## Review and rollback

This increment is additive to the sample registry schema; originals and label
history remain v1-readable. Its dependency includes derived-byte accounting so
rolling back the analysis feature still counts preserved artifacts in quota.
Rollback uses the previous code commit and the same data root; do not delete or
rewrite the registry to roll back code. Completed reports remain on disk.

Software acceptance uses recorded inputs and owned local processes. Operator
acceptance on the real robot remains a separate pending step; successful JSON
responses or offline model output do not demonstrate physical motion.
