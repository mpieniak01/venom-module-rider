# Rider operator workspace

The Rider module provides **Manual**, **Automatic (10 s)** and
**Automat2 — journey** presentation views.
Selecting a view never arms the robot, starts a camera, or starts a navigation trial.
The displayed owner comes from Pi; a selected view is not evidence of motor authority.

## Driving
A single directional pad remains mounted while permission or connectivity changes.
Choose **Pulse / hold** or **Continuous drive** in the selector. Changing that choice
ends an existing manual intent and does not start another direction.
The centre STOP brakes manual motion using Pi settings and keeps manual ARM.
During Automat2 preparation or a running journey it sends global STOP and
revokes the pending or active journey authority.
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
The optional Take manual button stops Venom loop goals, requests manual ARM,
and starts or retains camera preview. On a Pi advertising intervention v1, a
direction gesture itself supplies fresh manual consent without that button.

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

## Continuous journey

Automat2 adds a separate explicit Start journey and Stop journey. A journey has
no overall ten-second limit; all existing short motor-pulse and decision
watchdogs remain active. Pi and Venom must both support journey v1. Missing
avoidance, swept-path acceptance or calibration is explained next to Start.
Image and calibrated ground geometry use their existing rules. No settings or
operator acceptance are fabricated by selecting the view.

A versioned direction gesture atomically ends the old journey and establishes
new manual authority, including from normal DISARMED after a fault. The optional
Take manual button retains its STOP/read/ARM sequence; it is not a prerequisite
for the arrows. The journey does not use the ten-second trial's timed resume.
A new explicit Start can reuse a compatible ready observer with a new run and
fresh qualification. Stop journey keeps observation; Stop loop and camera
releases its resources. Reconnect and page refresh never start another journey.

See [Journey v1](JOURNEY_V1.md) for the paired contract, per-obstacle budgets and
qualification limits. Hardware-free journey flow:

    playwright-cli -s=workspace run-code --filename=tests/browser/journey.js
