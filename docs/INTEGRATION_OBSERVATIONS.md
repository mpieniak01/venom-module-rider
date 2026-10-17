# Central integration inventory (263R)

Newer Venom cores can read this module through the optional
`backend.integration_provider_import` entry in `module.json`:
`venom_module_rider.integrations:observe_integration`.
The existing `/rider` panel and `/api/v1/rider` interfaces do not depend on this
hook. Older cores can ignore it; the router and package do not import the hook,
and its return value is plain data, without a new core-schema dependency.

## Passive data only

The asynchronous hook reads the already initialized `app.state.rider` service.
It does not create a service or HTTP client, select/restore a device, call
`status()`, start polling, load a model or change a camera, session, ARM, motion
or data collection. There is no hook-specific worker or hardware cleanup.
If a service is not present it only validates the server's existing device
configuration. An invalid configuration returns an incomplete inventory with
`device_configuration_invalid`; an empty configuration is a complete zero,
with `device_not_configured`. It never fabricates a robot to represent an error.

All configured targets are listed. Only the selected target can reuse current
connection evidence; another target stays `unverified` with
`device_not_selected`, even when its URL equals the selected device's URL.
The configured ID remains stable within this module. Core namespaces the ID
before exposing the central inventory. URLs, credential references, token values,
private cache payloads and control sessions are not included. Public labels are
bounded; pasted URLs, addresses and credential-like labels use `Rider-Pi`.

## Evidence and freshness

Ordinary panel reads record only local completion times and success/failure.
The existing status aggregation retains passive connection evidence from actual
reads, without adding transport work. Cache hits do not renew the evidence.
Changing selection or closing the service clears the previous evidence.

The wrapper `observed_at` is the inventory read time. Each resource's timestamp
is the last cached connection observation, including a failed observation for
`offline`; it is not a new ping or the device's potentially different clock.
The service's existing `last_seen` remains the last successful contact and is
not renewed on failures. Age is measured using the host monotonic clock.
Evidence older than 60 seconds becomes `stale`; connectivity then becomes
`unknown`. Missing evidence never becomes online from configuration alone.
Demo resources retain `mode=demo` and `demo_observation`; this is not physical
hardware acceptance. A mixed fresh status result is reported as degraded.

The normal configuration permits up to 16 devices. The hook also bounds any
injected service inventory to 64 entries; truncation is explicitly incomplete.
Core owns its shared hook timeout, single-flight and result-cache policy.

## Capabilities are not permission

`supported_capabilities` describes the module's interfaces: manual control,
camera, vision, samples and navigation. `reported_capabilities` lists only known
explicit facts from fresh existing device cache: the negotiated manual-control
capability and camera-pipeline contract. It is `null` when no such evidence is
available; the list is not an exhaustive device capability negotiation.

`available_capabilities` remains `null`: contact and feature presence do not
prove current ownership, calibration, model readiness or Pi motion admission.
The existing Pi/panel contracts continue to decide whether an operation may
run. Online does not grant ARM or movement, and the integration map contains
no actuator controls.

## Validation

`tests/test_integrations.py` covers online, offline, stale, unselected targets,
empty/invalid configuration, direct cache reads, cached-hit timestamps,
capability evidence, redaction, no side effects, older router import and the
new core's schema/normalization. Tests use in-process fixtures and mock clients.
Run the repository checks with the existing host environment:

```sh
make HOST=/path/to/venom check
```

The companion core integration overview must be deployed before expecting this
hook's richer observations in the map. The Rider-Pi firmware needs no change.
