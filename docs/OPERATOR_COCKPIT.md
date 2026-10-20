# Rider operator cockpit

Manual, Automatic (bounded trial) and Automatic2 (journey) share one mounted
camera and one manual input surface. Switching the view does not arm the robot,
start observation, change authority, or recreate an active pointer handler.

Automatic start and pause/end controls occupy a reserved command region outside
scrollable settings. A persistent reason line shows the current blocking reason;
**Settings and diagnostics** opens the full requirements, saved calibration,
preparation result, configuration and decision details. The drawer covers only
the observation column; manual directions and both motor/emergency stops stay
available. Its disclosure can be closed using the same button or its summary.

Camera controls, displayed-frame sectors and capture actions use the same slots
in all three views. The image uses `object-fit: contain`; neither orientation nor
field of view is changed. Stale/offline warnings remain visible on the image.
Technical metadata and explanations are available under camera details.

**Collect** opens a sample collection; **Photo** saves the displayed source frame
in that collection. **Sequence · 10 s** samples frames with the existing bounded
policy. This is a frame sequence, not a video recording. **End collection** only
ends collection. Capture actions do not change motion permission or steering.
Sample outcomes, errors, automatic capture and evidence remain under Sample details.

Short desktop and tablet/phone viewports open the focused cockpit automatically.
Restoring the surrounding host navigation explicitly reduces observation space;
manual intervention retains priority. Expand the cockpit again for simultaneous
camera and control on a small screen. Other Rider sections retain their content.

UI changes do not alter speeds, motion pulse durations, freshness budgets,
calibration requirements, detector settings or API contracts. End journey,
automatic pause/end, motor STOP and emergency disarm retain their separate meanings.
