# DA2 input region and source-aligned drawing

The model input region is independent of the assessed corridor and the visual
books calibration. Changing display lines never changes inference or policy.
Changing the input region does change inference and requires the usual explicit,
revision-checked operator application while control is inactive.

`perception.input_region = {"version":1,"bounds":[0,0.4,1,1]}` selects the
recommended bottom 60% of the source, at full width. Bounds are normalized
left/top/right/bottom, with a half-open pixel rectangle (floor start, ceil end).
An omitted field preserves the legacy whole-frame input and serialized contract.
A newly selected DA2 identity receives the recommended region when image geometry
is in use and it covers the existing floor reference. Otherwise the default input
is full frame; an enabled ground calibration likewise retains full-frame input. Merely reading an
existing configuration does not migrate it. The operator can choose the whole
frame, the recommended region or edit all four edges in percent in model tuning.
New DA2 offline forms start with the recommended region; importing Rider settings
preserves the actual applied region, including legacy whole-frame omission.

The crop must contain both the STOP assessment rectangle and the original floor
reference extending up to 30% of source height above its lower edge. Crops smaller
than 32 pixels per dimension are rejected. Cropped depth returns to original
source coordinates with NaN outside the observed region. Unseen side paths remain
unknown and cannot authorize motion. Objects touching the crop boundary are
marked clipped. Cropped input is supported only for DA2, one frame, image geometry;
full-frame input remains available with ground geometry. Semantic and DA3 inputs
do not inherit this setting.

Both peers must advertise `input_region_version: 1`. Venom exposes the combined
`input_region_supported` capability, and Rider checks host support during stream
preparation, before motion admission. An older peer cannot silently ignore it.
The region is included in configuration identity, inference cache keys and frozen
study specifications. Historical studies are never reinterpreted automatically.

New previews carry bounded `overlay_geometry` v1 instructions in source pixels:
input, assessment, floor-reference boundaries, observed path outlines and optional
spatial/object diagnostics. Classification stays rasterized; geometry is rendered
with SVG using the same 1.5 CSS-pixel, non-scaling strokes as the books/robot guide.
Solid and dashed strokes and colors distinguish meanings without changing their
coordinates. The renderer checks source dimensions and never connects gaps in
unobserved paths. Optional diagnostic drawing can be omitted to retain the 4096
character preview limit; identities and timing are retained.

New video classification layers are version 2 (palette version 1): indices 0–3
contain classification only, and the matching row carries vector geometry.
Version 1 archives retain their original burned-in annotations and remain
readable. RAW hashes, source indices and dimensions must match before composition.
Stored photo overlays likewise use separately rendered source geometry.

## Evidence and limits

The recommended region comes from the yellow-bag reference film (696 frames,
41.01 seconds): full-width lower context outperformed narrow crops in that
experiment. The local production-path comparison evaluated both full and lower
inputs on all 696 immutable frames: zero raw-state, raw-reason or temporal-action
mismatches against the experiment; maximum plane-slope difference 4.92e-7.
This is implementation parity, not an independent accuracy or collision-safety
qualification. The experiment also contains unknown-floor/orientation episodes;
STOP in those episodes is not proof of recognizing the bag.

This change is local only. Live configuration and robot services are not migrated
or restarted. Deployment and physical acceptance remain separate actions.
