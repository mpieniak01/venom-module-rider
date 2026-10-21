# Rider compute protocol v1

Venom performs inference. Pi owns capture, playback, goals, calibration and all
actuation. Compute results are observations or speech, never robot commands.
The manual control service and STOP use separate connections and queues.
Optional R3 conversation.respond uses a separate typed tool path before returning
its textual result. It cannot supply raw actuator parameters; see
[Conversation tools](CONVERSATION.md).

## Transport and identity

Base path: `/api/v1/rider/compute`. HTTP JSON control, bounded binary job bodies;
no base64 image/audio transport. Every device request supplies `X-Rider-Device`
and `Authorization: Bearer <device compute token>`. Tokens are configured only
on the two endpoints, never returned to the browser. The isolated demo uses a
separate loopback fixture identity. The module rejects unconfigured devices.

- `GET /capabilities`: protocol_version=1, executor_id="venom", executor_session,
  server_time, tasks and limits. This reads status; it does not load models.
- `POST /sessions`: JSON `{protocol_version:1, device_session:"boot-id", client_id:"voice", client_instance:"process-uuid"}`.
  Returns `{protocol_version:1, executor_id, executor_session, session_id,
  device_id, device_session, client_id, client_instance, server_time, limits}`.
  A new device session invalidates its old jobs. Repeating the same live
  device_session/client_id/client_instance returns that
  client's lease. Clients are voice, vision, cli and panel; closing one goal does
  not cancel a different goal's lease.
  Repeated handshakes from the same client_instance are idempotent. A new
  instance replaces that goal's old lease, even after an abrupt process kill;
  its segment counter can restart and an old DELETE cannot cancel the new lease.
- `DELETE /sessions/{session_id}` cancels that lease and all its jobs.
- `POST /jobs`: raw UTF-8, JPEG/PNG or PCM16 WAV. `X-Rider-Job` is a JSON
  header (at most 4096 bytes) with protocol_version, executor_session, session_id,
  device_session, job_id (unique), input_id, task, captured_at (Unix seconds),
  input_age_ms (age at send), ttl_ms (remaining budget), sequence (integer),
  stream_id and params. Age is never replaced with server receipt time.
  timestamp_source is capture, file_mtime, capture_read_boundary, operator_input
  or unknown. It remains in quality; file time is not claimed as sensor time.
  Content-Type must match the task. Returns 202 and a job observation.
- `GET /jobs/{job_id}` returns the job observation. Only its device can read it.
- `DELETE /jobs/{job_id}` cancels the result; already-running inference drains
  before a shared synchronous model may be used again.
- `GET /jobs/{job_id}/output` returns TTS WAV only for a succeeded live job.

Identifiers: `[A-Za-z0-9_-]{1,64}`. Finite timestamps and numeric limits only.
Max input: 2 MiB; image: 1920×1080; WAV: mono PCM16, 8–48 kHz, at most 15 s;
text: 4096 UTF-8 bytes. TTL: 100–60000 ms; max input age: 60000 ms. Queue:
one running job and at most four waiting jobs per executor. One pending image
per device stream: a newer frame supersedes the older one. Audio segments are
ordered by monotonically increasing sequence per stream; duplicate or old
segments are rejected. No retry of POST and no persistent/replayed jobs.
Completed results are kept at most 60 s / 32 jobs; expired leases/results cannot
be used. HTTP 429 explicitly reports saturation, 503 reports unavailable engine,
409 reports stale session/order, 413 reports oversized input.

## Tasks and observations

| Task | Input | Params | Result |
| --- | --- | --- | --- |
| text.respond | text/plain; charset=utf-8 | language (pl/en/de) | data.text |
| audio.transcribe | audio/wav | language (pl/en/de) | data.text; empty transcription is silence, not connection failure |
| audio.synthesize | text/plain; charset=utf-8 | language (pl/en/de) | binary mono PCM16 audio/wav, data.sample_rate |
| vision.describe | image/jpeg or image/png | language (pl/en/de), prompt (optional, max 512 chars) | data.text: scene description, explicitly not an obstacle detector or navigation clearance |
| conversation.respond | text/plain; charset=utf-8 from authenticated voice ASR | language (pl/en/de) | optional shared conversation tools, data.text and speech_played; default OFF |

Every observation includes protocol_version, job_id, task, device_id,
device_session, executor_session, session_id, input_id, stream_id, sequence,
captured_at, received_at, finished_at, state, error, runtime, quality, metrics,
data and output_available. States: queued, running, succeeded, failed,
cancelled, expired, superseded. Runtime/model identity is the one actually used;
configuration changes invalidate pending/results from the previous revision.
conversation.respond consumes the same successful ASR sample's lease, input_id,
sequence, captured_at, timestamp_source and exact text. Its safety context was
bound before transcription, not refreshed after a queued model call. Authenticated
STOP uses the separate safety path and bypasses the inference queue even with
the conversation option OFF.
Metrics distinguish transfer bytes, queue_ms, inference_ms and source_age_ms.
Fixture results carry quality.simulated=true; real engine failure never becomes
mock success. Installed, configured and loaded/ready are distinct capabilities.

## Lifecycle on Pi

One persisted executor selection on Pi controls all remote tasks. Only explicit
operator configuration changes it; a heartbeat cannot change its address.
Goals start the existing camera/microphone owner and a lightweight client;
OFF closes HTTP connections and media subscriptions. The client never opens
V4L2/UART/LCD directly and never starts local AI on a remote error. Camera
frames use the existing stream's capture metadata. Missing capture time remains
unavailable, not relabelled as fresh. Voice collects bounded ordered utterances,
uses Venom transcription/text/TTS and Pi's existing playback function.
The client works with the browser closed. Restart, OFF and executor/session
change cancel in-flight intentions and drop late results. Operator ARM is not
changed by inference health. Legacy PC configuration remains a rollback path;
its heartbeat is isolated from the selected Venom executor.

## Trusted LAN and persistent peer identity

The passive core `GET /api/v1/transport/identity` is separate from authenticated
compute capabilities: `{protocol_version:1,peer_id,service_role:"venom"}`. The Pi
companion binds its explicit executor URL/profile to this persistent peer before
sending a compute token; an executor session still identifies the running compute
instance. DNS/DHCP changes do not select an executor. Job/session TTL, cancellation,
no POST replay and bearer authentication remain unchanged. The module's opposite
Pi connection uses `service_role:"rider_pi"` and its private expected peer.
Configure matching Pi/core changes; see [HTTP LAN](TRANSPORT_HTTP_LAN.md) /
[opis po polsku](TRANSPORT_HTTP_LAN_PL.md). SSH is a service/rollback path.
