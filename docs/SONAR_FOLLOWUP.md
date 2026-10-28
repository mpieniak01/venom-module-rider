# Rider Sonar follow-up: trust boundaries

Baseline: module `main` at `3cc6cd2`. The main analysis had nine open
security-impact issues, eleven reliability-impact issues and one new-code
maintainability issue. These categories can overlap. A green PR gate does not
mean all baseline issues are resolved.

## Security review

| Finding | Boundary and current assessment |
| --- | --- |
| `S8703` `run_demo.free_port` | The local operator chooses a validated port; the destination address is fixed to IPv4 loopback. Availability checks still have a port-binding race; this is not remote HTTP input. |
| `S8703` `analysis_cli.request`, `video_capture.run_record` | The CLI accepts loopback HTTP/HTTPS only, blocks proxies and redirects, and verifies configured TLS. The operator controls the URL and local HTTP remains unencrypted by design. A local service that occupies the chosen port is not cryptographically authenticated over HTTP. |
| `S8707` `replay_navigation_policy` (three findings) | Input/output paths are explicitly chosen by the local operator. Outputs are exclusively created. Map names must resolve within the manifest directory; map bytes are read once using a bounded regular-file descriptor, checked and hashed from the same bytes. The manifest digest is computed from the bytes processed. Parent directories must be trusted; no guarantee is made against hostile changes to ancestor directories. |
| `S8707` `navigation_archive.export_evidence` | The output path is explicitly selected by the operator; the export directory is newly created. Source artifacts are allowlisted by name, bounded and hashed. Parent directories must be trusted. |
| `S8707` `mapping_research.publish` (two findings) | The operator selects an absolute destination outside Git. Ancestors are opened one by one with `O_NOFOLLOW`, publication uses a temporary file and exclusive hard link relative to the pinned directory descriptor, and replacement of the named target directory is detected. A hostile process with write access to an ancestor can still rename a pinned directory after the final check; parent directories therefore remain a trust boundary, not a sandbox. |

CLI findings must be assessed against the actual caller and directory trust;
no remote input is inferred solely from the use of argparse. Do not mark a
finding false positive merely to pass the quality gate.

## Reliability review

The three `S7497` findings involve intentional cancellation of child operations
or invalidated conversation scopes; the domain translates those conditions to
superseded/expired results, while external worker cancellation is propagated.
The eight `S7503` findings include async/Promise interfaces consumed by callers
and test doubles. Do not remove `async` solely because a body currently has no
`await`; confirm the interface and cancellation contract first.

The compute queue isolates child execution, worker cancellation and drain. Test
both cancellation during inference and during drain, queued job disposition,
deadlines, supersession and urgent STOP independently of physical hardware.
