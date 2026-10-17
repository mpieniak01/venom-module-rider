# Rider operator workspace

The Rider module provides two presentation views: **Manual** and **Automatic**.
Selecting a view never arms the robot, starts a camera, or starts a navigation trial.
The displayed owner comes from Pi; a selected view is not evidence of motor authority.

## Driving
A single directional pad remains mounted while permission or connectivity changes.
Choose **Pulse / hold** or **Continuous drive** in the selector. Changing that choice
ends an existing manual intent and does not start another direction.
The centre STOP brakes manual motion using Pi settings and keeps ARM.
The header emergency STOP stops and disarms. **Stop loop and camera** retains
its separate navigation/resource cleanup behavior.

The manual console stays independent of scrolling camera/configuration content.
Messages and acknowledgements use bounded scroll regions below the directional
targets. Device disconnection and rejected requests cannot insert rows above them.
Phones initially open the full-window module workspace. The expand/restore button
changes only presentation, without browser fullscreen
permissions; it does not affect robot state and is unavailable during active manual input.

Camera pipeline configuration and sample capture are available in the camera's
tools disclosure. Stored sample sessions are under **Perception**.
Manual takeover still stops Venom loop goals, requests manual ARM, and starts or
retains camera preview; the button hint states these effects.

## Local acceptance
Use the existing host dependencies:

    make check-web

With the host UI running, run the hardware-free geometry/flow scenario through
Playwright CLI:

    playwright-cli -s=workspace open http://localhost:3300/rider
    playwright-cli -s=workspace run-code --filename=tests/browser/control-workspace.js

The scenario intercepts every Rider API request. It checks stable direction and
STOP rectangles under success/error/offline messages, ARM, input selection,
manual/automatic view changes, scrolling, and phone/tablet viewports. It also
checks manual brake versus global STOP and never contacts a physical actuator.
This does not replace operator-authorized physical acceptance.
