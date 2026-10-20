# Qualified attitude replay v1

Calibration → Motion study uses the existing timeline, selected A/B trial
alignment, dataset revisions, raw plots and one cursor. Two body models show
qualified exact observations. The front is the +X face; body X/Y/Z rotate while
X0/Y0/Z0 remain fixed. The SVG renderer projects rotated 3D vertices with a fixed
isometric camera and sorts body faces by camera depth. It adds no WebGL runtime.

Choose Source frame or set the current observation as a relative reference.
Relative orientation uses inverse(q_reference) * q_current. A reference is
usable only with the same source/convention identity and continuous segment;
a reset, gap or convention change requires choosing it again. The model is
attitude, not a travel path, geographic heading or a position estimate.

## Producer qualification contract

Original IMU records keep their raw values, units, source timestamps and
quality. Optional quality.orientation metadata must contain:

    {
      "convention": "rider-body-zyx-v1",
      "revision": 1,
      "verified": true,
      "qualification_ref": "documented-known-poses-reference",
      "instance_id": "same-boot-id-as-record-and-three-channels",
      "unit": "deg"
    }

The contract means a right-handed body frame X forward, Y left, Z up, with the
sensor mounted aligned to that frame. Positive angles follow the right-hand
rule. Active intrinsic ZYX Euler angles map the body to the declared source
reference: R = Rz(yaw) * Ry(pitch) * Rx(roll). The source must qualify units,
signs, axes, order and mounting against that exact convention. The reference is
arbitrary unless separately established; yaw is not a compass bearing.

The accepted unit is deg or rad, consistently declared by the metadata, record
and each of the three channels. Accepted angle magnitude is <=360 degrees or
2*pi radians. Unsupported conventions, revisions, units or ranges disable
geometry without hiding raw plots. Metadata is a producer declaration with a
bounded qualification reference, not independently authenticated physical proof.
There is no UI checkbox that guesses or upgrades a sensor's convention.

A qualified point additionally requires explicit root/channel ready=true,
stale=false and offline=false, the same known boot identity and finite values.
All three channel observed_at timestamps and record source_time must agree
within 1 microsecond; this is a coherence test, not a clock-accuracy claim.
Simulated or unknown provenance remains visibly labelled. Near pitch +/-90,
Euler singularity is displayed; the quaternion/model remains defined.

Current Rider-Pi sdk_raw records lack this qualification. The delivered UI
correctly shows **3D attitude unqualified** for them. Known fixture/imported
datasets can display geometry now. Enabling physical geometry requires an
independently documented source qualification; this increment does not invent
a conversion or change the Pi driver, mounting, calibration or robot state.

The existing telemetry observer preserves only bounded allowlisted convention,
qualification_ref, instance_id and unit strings plus typed revision/verified.
Unknown fields are omitted. Invalid oversized metadata does not discard the raw
angles. Convention metadata participates in observation identity, preserving
changes at the same source timestamp; records without metadata retain their
original identity. Existing ZIP validation/import/export preserves metadata,
and timeline schema1 passes it through the original quality field.

## Time, gaps and resource bounds

Slider, Home/End, arrows and previous/next select retained observations/events.
The model is cleared at an event with no exact IMU sample and between samples.
It never fills gaps or applies a CSS rotation tween. The quaternion representation
handles +/-180 wrap without an artificial full turn; raw plots retain source
Euler values and units.

Play is explicitly started and never replays device commands. Delays use host
reception intervals, with a minimum100 ms presentation cadence and a maximum
one-second continuous gap. Longer gaps pause; Play advances once to the next
observation. Manual cursor changes, hidden document, reduced-motion preference
changes and component/session unmount stop replay. Reduced-motion users retain
explicit replay and keyboard steps. No interpolation is generated or archived.

Replay and timeline computation do not access Pi, actuators, camera or models.
The surrounding live Calibration workspace may continue its ordinary status
refresh for the manual controls; playback adds no polling. Archive selection
works with the device disconnected.

The shared session limit remains2048 records/2MiB. Plots cap visible marker DOM
to256 representatives plus endpoints/current point; all polyline vertices and
cursor-accessible originals remain intact, including extrema and gap boundaries.
Zoom restores more markers in the smaller time window. Models use six faces,
three body axes and three fixed axes per session, independent of dataset size.

## Verification

make check HOST=<Venom root> covers source quality, strict metadata capture,
archive round-trip, ordered single/composite rotations, relative rotation,
degree/radian agreement, yaw wrap, Euler singularity, gaps and marker bounds.

tests/browser/attitude-replay.js runs only against the isolated Demo ASGI fixture.
It verifies A/B bodies, shared cursor, missing-sample clearing, relative origin,
play/pause, End key, nine language/viewport layouts, reduced motion and raw
fallback, with zero Rider mutations. This is a software acceptance, not a
physical qualification. Deployment changes only the Venom module.
