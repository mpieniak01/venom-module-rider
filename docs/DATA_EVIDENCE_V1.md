# Host-owned sample evidence v1

Evidence is collected only in Venom, during an explicitly opened sample session.
It observes responses already delivered to RiderService.read. It creates no
Pi polling, camera reader, inference, actuator request or device archive.

The session-export extension is optional `evidence = {schema_version: 1,
records: [...], status: {...}}`. Records use EvidenceRecord from
venom_module_rider.evidence_contract: kind imu/executor, stable record_id,
device_id, host source_session, source_instance_id, source_observed_at,
source_clock_domain, host received_mono/received_at, values, units, quality,
and optional executor snapshot. Preserve raw yaw from sensor quality rather
than silently replacing it with the wrapped display channel. Unknown, stale,
simulated or physically unverified input remains explicitly qualified.

The sample context may reference records through `evidence.record_ids`.
Its association is host-reception-window-unmatched-exposure; clock synchronisation
uncertainty is unknown. A recent host receipt does not prove matching camera
exposure or fresh source data. Original RAW/source references remain unchanged.

One bounded RAM window: 64 records / 64 KiB / 30 seconds. Maximum record 8 KiB.
Per session: at most 2048 evidence records / 2 MiB, within the existing shared
archive quota and single IO writer. Full queues or limits drop data with visible
counters; they never delay STOP or grant GO. No silent deletion or collection
restart after process restart. Changing source retires the prior source window.

Legacy archives without evidence remain usable, with unavailable telemetry.
The extension participates in dataset hashes, export/import validation and quotas.
Offline analysis reads this exact contract; it never contacts the robot.
