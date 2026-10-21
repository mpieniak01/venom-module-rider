# CPU/RAM history continuity

The overview retains the last successful system observation when a system read fails. It shows the read error and lets the observation age on the browser monotonic clock. A cached response with the same API instance and sequence never refreshes the source age. Older sequences cannot roll history backwards. Device and module-session changes discard unrelated history.

Rider-Pi supplies the additive `rider.system` v1 metadata: `observation` contains boot/API identity, sampler generation, sample sequence, state, monotonic source time/age and sanitized error counters. `hist_mono`, `hist_generation` and `hist_seq` align with the legacy arrays. Legacy firmware remains supported with timestamp-based gap detection.

Both panels use a fixed 60-second window and break curves on missing channel values, source generation changes, sequence discontinuities and timestamp gaps. RAM remains valid when CPU has no delta. CPU peak means the aggregate maximum across subintervals, not a single-core measurement. Confirmed host, API and sampler identity changes have separate event labels; gaps alone do not imply reboots.

Demand-driven sampling and hidden-tab pauses remain supported. This increment does not tune polling, timeouts, SSH or actuator safety. A failed Venom tunnel does not imply that a direct LAN panel failed. Transport root causes require separate evidence; diagnostic readouts never arm or move the robot.

Roll out the paired Rider-Pi change to obtain full monotonic diagnostics. No dependency or hardware configuration changes are required.
