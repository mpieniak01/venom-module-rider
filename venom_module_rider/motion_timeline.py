"""Versioned, bounded archive timeline. Host reception is not physical latency."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict

from .angle_analysis import enrich, semantics
from .evidence_contract import validate_evidence
from .motion_evidence import analyze_manifest

CHANNELS = ("accel_x", "accel_y", "accel_z", "roll", "pitch", "yaw")


def number(value):
    return (
        float(value) if type(value) in {int, float} and math.isfinite(value) else None
    )


def elapsed(value, origin):
    result = value - origin
    return result if math.isfinite(result) else None


def build_timeline(manifest):
    analysis = analyze_manifest(manifest)
    evidence = manifest.get("evidence", {})
    groups = defaultdict(list)
    for value in evidence.get("records", []):
        row = validate_evidence(value)
        groups[(row["device_id"], row["source_session"])].append(row)
    streams = []
    for (device, session), rows in groups.items():
        commands = [row for row in rows if row["kind"] == "command"]
        starts = [
            row["received_mono"]
            for row in commands
            if row["host"]["phase"] == "requested"
            and row["host"]["intent"]["action"] == "move"
        ]
        origin = min(starts or [row["received_mono"] for row in rows])
        points, events, outputs, profiles = [], [], [], {}
        previous = None
        segment = 0
        skipped_times = 0
        for row in rows:
            t = elapsed(row["received_mono"], origin)
            if t is None:
                skipped_times += 1
                continue
            if row["kind"] == "profile":
                profiles[row["record_id"]] = row["host"]
            elif row["kind"] == "command":
                events.append({"id": row["record_id"], "t": t, **row["host"]})
            elif row["kind"] == "executor":
                output = row.get("executor") or {}
                outputs.append(
                    {
                        "id": row["record_id"],
                        "t": t,
                        "instance_id": row["source_instance_id"],
                        "source_time": row["source_observed_at"],
                        "target": {
                            key: number(output.get("target", {}).get(key))
                            for key in ("lx", "az")
                        },
                        "output": {
                            key: number(output.get("output", {}).get(key))
                            for key in ("lx", "az")
                        },
                        "state": output.get("state"),
                        "quality": row["quality"],
                    }
                )
            elif row["kind"] == "imu":
                q = row["quality"]
                valid = q.get("ready") is True and not any(
                    q.get(flag) is True for flag in ("stale", "offline")
                )
                source = row["source_observed_at"]
                contiguous = False
                if previous is not None and valid:
                    prior, prior_valid = previous
                    source_gap = (
                        elapsed(source, prior["source_observed_at"])
                        if source is not None
                        and prior["source_observed_at"] is not None
                        else None
                    )
                    host_gap = elapsed(row["received_mono"], prior["received_mono"])
                    contiguous = bool(
                        prior_valid
                        and row["source_instance_id"] is not None
                        and row["source_instance_id"] == prior["source_instance_id"]
                        and source_gap is not None
                        and 0 < source_gap <= 1
                        and host_gap is not None
                        and 0 < host_gap <= 1
                    )
                if not contiguous:
                    segment += 1
                points.append(
                    {
                        "id": row["record_id"],
                        "t": t,
                        "segment": segment,
                        "valid": valid,
                        "instance_id": row["source_instance_id"],
                        "source_time": source,
                        "values": {key: row["values"].get(key) for key in CHANNELS},
                        "units": {
                            key: row["units"].get(key, "unknown") for key in CHANNELS
                        },
                        "quality": q,
                        "angle_semantics": semantics(row),
                    }
                )
                previous = row, valid
        diagnostics = enrich(points, events)
        times = [row["t"] for row in [*points, *events, *outputs]]
        streams.append(
            {
                "id": hashlib.sha256((device + ":" + session).encode()).hexdigest(),
                "device_id": device,
                "source_session": session,
                "clock": "host-reception-monotonic",
                "origin": "first_move_request" if starts else "first_receipt",
                "range": [min(times), max(times)] if times else [0.0, 0.0],
                "points": points,
                "events": events,
                "outputs": outputs,
                "profiles": profiles,
                "skipped_times": skipped_times,
                "sampling_diagnostics": diagnostics,
            }
        )
    report = {
        "schema_version": 1,
        "dataset_revision": manifest["dataset_revision"],
        "session_id": manifest["session"]["session_id"],
        "context": manifest["session"].get("policy", {}).get("scene_group"),
        "streams": streams,
        "collection_status": evidence.get("status", {}),
        "counts": analysis["counts"],
        "sampling": analysis["clocks"]["sampling"],
        "source_gaps_s": analysis["clocks"]["source_gaps_s"],
        "physical_velocity": None,
        "physical_distance": None,
        "cross_clock_latency_s": None,
    }
    if len(json.dumps(report, allow_nan=False).encode()) > 4 * 1024**2:
        raise ValueError("motion_timeline_size")
    return report
