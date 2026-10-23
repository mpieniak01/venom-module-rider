# Rider operator workspace

The camera and the manual keypad are the primary driving surface. Manual,
Automatic (10 s) and Automat2 views reuse those same mounted controls. Selecting
a view changes presentation only; Pi reports the actual motor owner.

## Camera and manual control

The camera toolbar selects raw image or analysis and provides an explicit
**Switch off camera** action at the source. Hiding preview only hides the image.
The manual keypad remains available according to Pi's existing control contract,
independently of the selected vision model or navigation calibration.

The centre STOP brakes an owned manual intent. It pauses compatible automatic
control or ends an active/preparing journey. From a different panel it sends a
global STOP rather than an unauthorized pause or release. Header STOP always
stops and disarms. A direction gesture on an intervention-v1 Pi supplies explicit
manual consent and ends the old automatic intent. Releasing it never resumes
that intent.

**Take manual control** pauses an automatic session owned by this panel. Taking
over a different panel uses the existing disarm/read/manual-ARM sequence. An
already owned paused session does not repeat that handoff.

## Settings below the camera

Preparation, model/provider selection, caution, per-model tuning, camera
configuration, recording and diagnostics are full-width disclosures below the
driving surface. Opening them does not overlay or shrink the camera. There is
one page scroll area; small landscape screens retain readable images and touch
targets through scrolling. Header emergency STOP remains accessible.

Current operator messages use a stable inline area below the keypad. Detailed
acknowledgements, error codes and repeated instructions belong in diagnostics.
Tips appear after a short hover or keyboard focus and close on a pointer press.

## Preparing automatic drive

After changing the model or critical settings, **Prepare automatic drive** opens
the explicit observation/acceptance controls. Start analysis, inspect a fresh
image for the saved revision, and confirm the image/path. Automat2 also needs its
existing swept-path acceptance and avoidance settings. Stale, mismatched or
unclear frames cannot be accepted. Successful acceptance displays its saved
configuration revision.

This flow does not arm or start driving. The separate Start/Resume action and
Pi's motion admission remain authoritative. Opening the panel, reconnecting,
changing caution or accepting a preview never restores an earlier drive intent.

## Hardware-free acceptance

Use the existing host dependencies:

    make check-web

With the local UI available, run Playwright CLI scenarios. They intercept every
Rider request, including all simulated motion and ARM acknowledgements:

    playwright-cli -s=workspace open http://localhost:3300/rider
    playwright-cli -s=workspace run-code --filename=tests/browser/operator-workspace.js
    playwright-cli -s=workspace run-code --filename=tests/browser/control-workspace.js
    playwright-cli -s=workspace run-code --filename=tests/browser/operator-priority.js
    playwright-cli -s=workspace run-code --filename=tests/browser/operator-journey-priority.js
    playwright-cli -s=workspace run-code --filename=tests/browser/journey-acceptance.js
    playwright-cli -s=workspace run-code --filename=tests/browser/camera-lifecycle.js
    playwright-cli -s=workspace run-code --filename=tests/browser/ui-consistency.js

These cover readable camera/keypad geometry, settings placement, stable messages,
foreign-panel takeover and STOP, delayed replies, camera power and explicit
acceptance. They do not replace operator-authorized physical acceptance.

See [Journey v1](JOURNEY_V1.md) for the paired journey contract and limits.
