# Explicit trusted HTTP LAN transport

[Polski](TRANSPORT_HTTP_LAN_PL.md)

The default `transport_profile="secure"` preserves existing behavior: credential
requests require HTTPS or loopback (including an administrator-managed local
transport). Existing device entries keep their IDs, selection and behavior.
`trusted_lan` is an explicit choice for one configured peer on a trusted network;
it does not enable arbitrary private-address destinations or disable tokens.
HTTP does not encrypt a bearer token. `peer_id` is a continuity check, not
cryptographic authentication. Use HTTPS when the network is not trusted.

Configure this only in private server-side `RIDER_DEVICES_JSON`, for example:

```json
[{"id":"robot","label":"Rider-Pi","url":"http://rider.example:8080","mode":"device","transport_profile":"trusted_lan","expected_peer_id":"operator_configured_peer_id"}]
```

The hostname and ID above are examples. Both `transport_profile` and
`expected_peer_id` are validated, and trusted LAN requires an explicit peer ID.
A browser still supplies only an allowlisted device ID, never a URL or token.
Keep `RIDER_AGENT_TOKEN` and the compute token in their existing private backend
configuration. Provision the expected peer out of band; discovery does not select
a robot. The companion Pi exposes passive, unauthenticated
`GET /api/v1/transport/identity`:

```json
{"protocol_version":1,"peer_id":"operator_configured_peer_id","service_role":"rider_pi"}
```

The expected peer identifier is `[A-Za-z0-9_-]{1,64}` and persists across API boots
and DHCP changes. A hostname must resolve from the Venom host; changing addresses
in a browser does not establish name resolution. The module resolves only the
configured name, with a bounded DNS lookup and no subnet scan. It pins the result
for the current transport generation while preserving the configured canonical
HTTP Host and TLS SNI. Separate JSON, urgent STOP and media pools use this same
verified target. Unique A/AAAA candidates are tried in resolver order after a
transport failure, with a separate identity check before using each target.
A responding wrong/invalid peer or redirect ends the attempt without trying to
hide it behind another candidate. IPv6 addresses requiring an interface scope
are explicitly unsupported and skipped, never retried with the scope removed;
provide an unscoped A/AAAA target. No usable target reports `pi_name_unresolved`.
A server may close each TCP response; keep-alive is not required.

Identity is checked before the first read/media request of a generation and
freshly before each mutation or credential-bearing request. The preflight never
contains authorization. A different identity, unsupported role/protocol, oversized
identity or redirect prevents the command/token request. Redirects remain disabled.
This also detects an old IP reassigned to another responding API without an
intervening network error. No pending POST is automatically retried.

An individual passive JSON GET pool/connect/read timeout still reports its error,
but does not retire independent MJPEG while the shared hub has a fresh frame from
that exact verified upstream and generation. The evidence requires the existing
hash, source authority, monotonic sequence and conservative age checks; it expires
when that frame reaches the unchanged 500 ms admission limit. Raw bytes and
replayed frames cannot extend it. The normal connect limit remains 500 ms.
Identity/command/media failures, explicit Refresh, other JSON network failures and
timeouts without this fresh proof retain full generation invalidation. POST is
never replayed and
urgent STOP keeps its separate lane and identity preflight.

After connection loss the old pools close. Ordinary demand-driven reads retry
with jittered exponential backoff (roughly 1/2/4/8/16/30 seconds, capped at 30),
re-resolve the configured name and verify the peer. Normal UI polling cadence is
unchanged. The urgent STOP lane bypasses transient retry cooldown and ordinary
resolver contention, but retains its own bounded identity verification; it cannot
send a command to an unverified target. DNS lookup is bounded to 750 ms and urgent
identity to 500 ms, including a 100 ms pool wait limit. One shared 1.25 s urgent
connection budget includes admission, DNS and all candidate preflights (2.25 s
for ordinary connections); another DNS result does not renew that budget. TCP
connect remains capped at 500 ms. Resource cleanup and the subsequent STOP request
are separate; these limits are not a measured physical braking guarantee.

The header **Refresh** coalesces concurrent requests, retires old observations,
commands and media handles, bypasses backoff once and obtains current Pi state.
For trusted LAN it never invokes `reconnect_command` or SSH, even if a legacy
helper remains in the private entry. Secure-profile recovery retains its existing
helper flow. Refresh does not change `device_id`, saved Demo/Prod selection, panel
session, Pi permissions or calibration. A new transport generation rejects late
responses and browser gestures from the previous generation. A Pi API restart
still supplies its own safety/executor instance; the transport generation does
not replace that device-owned identity.

Reconnect restores passive reads/subscriptions only. It does not replay ARM,
motion, camera start, recording, inference or navigation requests. Existing leases,
TTLs, source sequence/epoch, capture age and bounded shared MJPEG fan-out remain
in force. Connection loss invalidates pending collection and conversation intents.
STOP, camera/loop stop and disarm retain their distinct meanings.

The UI reports `connection.transport`: profile, state, generation,
`identity_verified`, error and retry delay. Errors distinguish
`pi_name_unresolved`, `pi_connect_failed`, `pi_connection_timeout`,
`pi_identity_mismatch`, `pi_identity_invalid` and `pi_identity_unavailable`.
Authenticated API rejection is still an API/permission error, rather than a DNS
failure. Unknown operation outcome remains explicit. Existing CPU/RAM history is
retained with its age/error markers, while old control permission is discarded
at a generation change. Diagnostics and conversation readiness are translated
into PL, EN and DE.

The browser uses the Venom origin and relative API/media paths. Request IDs use
Venom's public `createBrowserUuid` helper, which preserves UUID v4 and uses
`getRandomValues` when HTTP lacks `randomUUID`. Opening a page does not grant
control or start hardware. Browser microphone capture still requires a secure
context; Pi audio and browser microphone availability are separate paths.

Pi-to-Venom compute uses its independently configured executor URL, expected
Venom peer (`service_role="venom"`) and unchanged compute bearer/session contract.
The module does not rewrite that choice from heartbeat data. Configure the
matching Pi/core transport changes together. SSH remains available for service
work; a tunnel is an explicit rollback profile, not part of trusted LAN recovery.
Rollback restores the previous profile/URL and requires fresh sessions/intents.

`make check HOST=<matching-core-checkout>` covers backend/frontend contracts.
`tests/test_lan_transport.py` uses only isolated mock/loopback servers, including
Connection-close, same-IP peer replacement, resolver changes, concurrent Refresh,
urgent STOP and pool cleanup. `tests/test_lan_media_isolation.py` additionally
covers fresh-media isolation, proof expiry, peer replacement and no replay.
Fixture tests do not establish actual DNS, phone,
physical media quality, braking or CPU savings. Those require separate acceptance
with the pinned Pi/module/core revisions before environment rollout.
