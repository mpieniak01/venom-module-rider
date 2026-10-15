# Remote floor decisions and bounded movement

This feature is an experimental indoor floor-perception loop, not a certified
collision avoidance system. Segmentation does not measure distance, infer drop
edges reliably, or guarantee passage through transparent objects.

## Ownership

Pi owns persisted policy at data/navigation_config.json: mode, corridor, speed,
decision budget, maximum pulse, calibration identity and validated model hash.
Both panels use the same revisioned Pi API. Saving never arms, starts a goal,
loads a model, opens a camera, or changes LCD orientation.

Venom owns an independent lazy ONNX floor model. Active chat model switches do
not choose this model. Requests are Pi-authenticated over the existing TLS or
SSH tunnel, bounded to 512 KiB, and never queued behind general LLM work.

## Provisioning

Install declared vision and vision_export extras in the existing host virtualenv.
The runtime requires ONNX Runtime, NumPy and Pillow; Torch/Transformers are export
tools only. Provision explicitly:

    python scripts/export_floor_model.py --revision PINNED_MODEL_COMMIT_SHA --output /models/rider-floor

Use the resulting RIDER_VISION_MODEL and RIDER_VISION_SHA256 settings on the host.
The source is nvidia/segformer-b0-finetuned-ade-512-512. Review its upstream
license (https://github.com/NVlabs/SegFormer/blob/master/LICENSE).
Weights and generated files are not committed.

## Run contract

Use observation first. For drive, characterize the actual stopping zone at the
configured speed, camera mounting and posture, lighting and floor conditions.
Record its calibration_id and exact validated model hash. This is an operator
acceptance record, not a proof supplied by the model. Changes require a stopped
run and a new policy revision.

Navigation uses separate, time-limited owner=navigation consent and the existing
actuator capability gates. It cannot borrow a manual ARM. No auto-ARM, no
automatic restart, no local inference fallback. Opening either panel has no
hardware side effects.

Every result echoes run, frame, sequence and policy revision. Three distinct,
fresh confident floor observations are required before GO. Any non-floor or
uncertain pixel in the configured rectangular stopping corridor means STOP.
After movement begins, STOP, timeout or invalid result latches the run off.
Local executor pulse duration independently limits motion if the loop or host
dies. Fresh heartbeats do not renew permission. Manual takeover supersedes
navigation authority.

API:
- GET/POST /api/v1/rider/navigation/config proxies the authoritative Pi policy.
- GET /api/v1/rider/compute/vision/status is a device-authenticated model snapshot.
- POST /api/v1/rider/compute/vision/decision accepts JPEG/PNG and X-Rider-Vision metadata.

No device movement, deployment, energy benefit or calibrated stopping distance
is claimed by fixture tests. Physical acceptance is separate from software CI.

## Processed operator preview

The camera panel offers raw frames, **Venom vision** and legacy Pi processing
as separate views. GET /api/v1/rider/camera/venom produces a JPEG overlay:
green is confident floor, red another class, amber uncertain classification,
and a white rectangle identifies the analysed corridor. Headers report the
classification and inference time. The overlay is diagnostic and always
observation-only: it cannot arm, start the camera or submit motion.

Fresh cached decision overlays are reused to avoid duplicate inference during
a running loop. Expired or failed inference cannot display the old mask.
If an older Pi returns 404 for the new policy API, the preview explicitly uses
a default diagnostic corridor; this fallback cannot authorize driving and
does not persist a second configuration.
