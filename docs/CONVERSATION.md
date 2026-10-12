# Optional Rider conversation tools

Version 0.3.0. Install the module using the host optional-module workflow and its
`backend.conversation_provider_import`. The matching core provides host-owned
conversation context, provider registration and prepare/urgent/handle/finish.
The module remains the owner of all robot-specific tools. Manual operation does
not depend on this option or an AI model.

## Configuration and use

1. Select a server-configured robot in the Rider panel.
2. Configure a private `RIDER_AGENT_TOKEN` on Venom and in Pi's compute/executor
   service environment. A device's `agent_token_env` can reference a different
   server environment variable. Tokens are never returned in configuration.
3. Enable Conversation commands in Perception. This only enables tool access.
   Operator ARM remains the existing global red/green control.
4. Use the existing chat or voice interface. A host chat needs the explicit
   `Rider` or `/rider` prefix. Authenticated Pi voice is already addressed to the
   selected device. Requests must name one bounded action.

| Action | PL example | EN example | DE example | Result |
| --- | --- | --- | --- | --- |
| Status | Rider, stan | Rider, status | Rider, Zustand | Current state/battery; missing data stays missing |
| Observe | Rider, opisz scenę | Rider, describe scene | Rider, beschreibe die Szene | Current camera frame described by shared vision; no navigation clearance |
| Speak | Rider, powiedz dzień dobry | Rider, say hello | Rider, sage guten Tag | Shared TTS, authenticated PCM WAV playback on Pi |
| Face | Rider, buźka friendly | Rider, face friendly | Rider, Gesicht friendly | Play an existing gesture ID; unknown IDs fail |
| Pulse | Rider, do przodu | Rider, forward | Rider, vorwärts | One current-calibration impulse when operator-armed |
| STOP | /rider stop | /rider stop | /rider stop | Independent STOP request; works with the option OFF |

Use a gesture ID present in Face Studio; the example is not a guaranteed built-in
ID. Other directions are backward/left/right and their PL/DE equivalents. A
compound, negated, unbounded or ambiguous motion request is refused. Tools cannot
arm, reset, change height/balance, run actions or shell/GPIO, or plan autonomous
navigation. Ordinary chat is not intercepted. The model cannot supply identities,
operator consent, raw actuator parameters or authorization.

## Host interfaces and lifecycle

`GET /api/v1/rider/conversation/config` returns enabled/revision, selected device,
credential presence and the last result. `POST` accepts exactly enabled/revision
and the existing `X-Rider-Session`; module access/origin/environment guards apply.
Enablement is ephemeral and defaults OFF after host restart or device selection.
Disablement cancels pending work and removes tools from the provider catalog.

The host creates immutable `ConversationContext` for native chat or actual speech
transcription. Correlation IDs are not credentials. Pi compute
`conversation.respond` uses a device-authenticated **voice** lease; panel/CLI
leases cannot impersonate voice. Native voice binds context before ASR. For Pi
voice the host binds it when admitting audio.transcribe, then carries it through
the successful sample's lease/input_id/sequence/captured_at/timestamp_source.
conversation.respond consumes that same single-use scope; missing or mismatched
ASR identity cannot create fresh movement permission. The command text must equal
that successful transcription; a generated/replaced answer cannot become a tool
request. This precedes capability,
model waits and inference queueing. The 30-second host turn deadline
is further bounded by the compute TTL. STOP bypasses model probes and the inference
slot, including queue saturation. It retains normal job correlation/result state.

Manual control invalidates the host intent. Pi additionally rotates a distinct
agent epoch on accepted manual commands, so another Pi client can supersede an
intent without depending on the Venom panel. Old agent rejection never issues a
global STOP or disarms the operator. Device/session selection, module OFF,
deadline and model changes discard stale work; no automatic request replay occurs.

## Pi command and audio contracts

Pi negotiates `rider.safety` v1 for agent state/pulse and STOP. Agent pulse accepts
exactly generation, agent_epoch, seq, ts, ttl_ms, settings_revision, request_id,
intent_id, channel and direction. The server fixes agent source/context, uses the
operator's current direction profile and verifies its canonical HMAC again in
the executor. Agent sequencing is independent of manual sequencing. Test arming
cannot authorize conversation movement. Public generic control rejects agent
impersonation.

Playback negotiates `rider.audio` v1. `POST /api/voice/playback` takes a Bearer
agent credential, raw mono PCM16 WAV (8–48 kHz, at most 15 seconds / 2 MiB),
`X-Rider-Playback-Id` and `X-Rider-Playback-TTL-Ms`. HTTP 200 with state `played`
means software playback completed; it does not prove audible sound. Busy, expiry,
cancelled or unavailable output fails explicitly. The shared audio owner covers
API, CLI and voice; no second TTS engine is started on Pi.
`POST /api/voice/playback/cancel` uses the same credential and playback_id; its
tombstone suppresses cancellation-before-upload races. Cancellation/OFF cannot
revive a late synthesis result. Voice capture rejects entire utterances overlapping
playback, including a generation change between frames and a short drain afterward.

ACK/SDK acceptance does not confirm physical motion or stopping. Scene descriptions
are observations, not obstacle detection or permission to drive. Physical tests
require the operator's separate authorization and are not automatic test fixtures.
