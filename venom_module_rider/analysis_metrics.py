"""Metrics preserve missing truth and distinguish real sequence transitions."""

import math
from collections import Counter


def percentile(values, fraction):
    """Nearest-rank percentile, or N/A when no stage was measured."""
    values = sorted(x for x in values if type(x) in {int, float} and math.isfinite(x))
    return values[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def approved_label(sample):
    """Only the latest label revision can serve as operator-reviewed truth."""
    labels = sample.get("labels") or []
    latest = labels[-1] if labels else {}
    if (
        latest.get("review") == "approved"
        and latest.get("author", {}).get("kind") == "operator"
    ):
        return latest
    return None


def adjacent(first, second, order_source):
    """Temporal scoring requires source order and distinct camera frames."""
    if first.get("input_kind") == second.get("input_kind") == "video":
        delta = second.get("source_time_s", 0) - first.get("source_time_s", 0)
        a, b = first.get("sequence"), second.get("sequence")
        return (
            first.get("run_id") == second.get("run_id")
            and type(first.get("source_index")) is int
            and second.get("source_index") == first["source_index"] + 1
            and type(a) in {int, float}
            and type(b) in {int, float}
            and math.isfinite(a)
            and math.isfinite(b)
            and b == a + 1
            and second.get("source_sequence_gap") in {None, 0}
            and 0 < delta <= 0.6
        )
    if order_source not in {"receipt", "manifest"}:
        return False
    a, b = first.get("sequence"), second.get("sequence")

    def sequence(value):
        return (type(value) is int and value >= 0) or (
            type(value) is float
            and math.isfinite(value)
            and 0 <= value <= 2**53
            and value.is_integer()
        )

    return (
        sequence(a)
        and sequence(b)
        and b == a + 1
        and bool(first.get("run_id"))
        and first.get("run_id") == second.get("run_id")
        and first.get("source_device_id") == second.get("source_device_id")
        and isinstance(first.get("frame_id"), str)
        and bool(first["frame_id"])
        and isinstance(second.get("frame_id"), str)
        and bool(second["frame_id"])
        and first.get("frame_id") != second.get("frame_id")
        and first.get("raw", {}).get("sha256") != second.get("raw", {}).get("sha256")
    )


def summarize(samples, rows, order_source):
    """Score actual results against frozen labels; never treat N/A as zero error."""
    if len(samples) != len(rows):
        raise ValueError("analysis_metric_inputs_mismatch")
    (
        confusion,
        sides,
        side_errors,
        eligible,
        false_go,
        false_stop,
        labels,
        clear_labels,
        obstacle_labels,
    ) = _label_scores(samples, rows)
    pairs, flips = _temporal_scores(samples, rows, order_source)
    stages = {name for row in rows for name in row.get("timings_ms", {})}
    timings = {}
    for name in sorted(stages):
        values = [row.get("timings_ms", {}).get(name) for row in rows]
        timings[name] = {
            "p50": percentile(values, 0.5),
            "p95": percentile(values, 0.95),
        }
    total = len(rows)
    return {
        "frames": total,
        "approved_labels": labels,
        "eligible_labels": eligible,
        "unlabelled_or_unreviewed": total - labels,
        "unknown": sum(row["state"] == "unknown" for row in rows),
        "rejected_inputs": sum(row.get("rejected_input") is True for row in rows),
        "false_go": false_go if obstacle_labels else None,
        "false_stop": false_stop if clear_labels else None,
        "obstacle_labels": obstacle_labels,
        "clear_labels": clear_labels,
        "false_go_rate": false_go / obstacle_labels if obstacle_labels else None,
        "false_stop_rate": false_stop / clear_labels if clear_labels else None,
        "confusion": dict(confusion) if eligible else None,
        "side_errors": side_errors if sides else None,
        "side_labelled": sides,
        "temporal_pairs": pairs,
        "state_flips": flips if pairs else None,
        "flicker_rate": flips / pairs if pairs else None,
        "timings_ms": timings,
        "historical_transport_ms": None,
        "physical_motion_ms": None,
        "distance_measurement": False,
        "drive_supported": False,
    }


class StreamingMetrics:
    """Bounded counters and stage samples; never retain per-frame result rows."""

    def __init__(self, order_source):
        self.order = order_source
        self.counts: Counter[str] = Counter()
        self.confusion: Counter[str] = Counter()
        self.states: Counter[str] = Counter()
        self.timings = {}
        self.previous = None
        self.previous_state = None

    def add(self, sample, row):
        result = summarize([sample], [row], self.order)
        for key in (
            "frames",
            "approved_labels",
            "eligible_labels",
            "unlabelled_or_unreviewed",
            "unknown",
            "rejected_inputs",
            "false_go",
            "false_stop",
            "obstacle_labels",
            "clear_labels",
            "side_errors",
            "side_labelled",
        ):
            self.counts[key] += result[key] or 0
        self.confusion.update(result["confusion"] or {})
        self.states[row["state"]] += 1
        self.states["rejected"] += bool(row.get("rejected_input"))
        if self.previous is not None and adjacent(self.previous, sample, self.order):
            self.counts["temporal_pairs"] += 1
            self.counts["state_flips"] += row["state"] != self.previous_state
        self.previous, self.previous_state = sample, row["state"]
        for name, value in row.get("timings_ms", {}).items():
            self.timings.setdefault(name, []).append(value)

    def finish(self):
        result = {**summarize([], [], self.order), **self.counts}
        for field, total in (
            ("false_go", "obstacle_labels"),
            ("false_stop", "clear_labels"),
        ):
            result[field] = self.counts[field] if result[total] else None
            result[field + "_rate"] = (
                self.counts[field] / result[total] if result[total] else None
            )
        pairs = self.counts["temporal_pairs"]
        result.update(
            confusion=dict(self.confusion) if result["eligible_labels"] else None,
            side_errors=self.counts["side_errors"] if result["side_labelled"] else None,
            state_flips=self.counts["state_flips"] if pairs else None,
            flicker_rate=self.counts["state_flips"] / pairs if pairs else None,
            timings_ms={
                name: {"p50": percentile(values, 0.5), "p95": percentile(values, 0.95)}
                for name, values in sorted(self.timings.items())
            },
            state_counts={
                key: self.states[key]
                for key in ("clear", "unknown", "obstacle", "rejected")
            },
        )
        return result


def _label_scores(samples, rows):
    """Accumulate approved label counts while validating result identity."""
    confusion: Counter[str] = Counter()
    sides = side_errors = eligible = false_go = false_stop = 0
    labels = clear_labels = obstacle_labels = 0
    for sample, result in zip(samples, rows, strict=True):
        if (
            result.get("sample_id") != sample.get("sample_id")
            or result.get("raw_sha256") != sample["raw"]["sha256"]
        ):
            raise ValueError("analysis_metric_inputs_mismatch")
        label = approved_label(sample)
        if label is None:
            continue
        labels += 1
        expected = label.get("expected_state")
        state = result["state"]
        if expected in {"clear", "obstacle"}:
            eligible += 1
            clear_labels += expected == "clear"
            obstacle_labels += expected == "obstacle"
            confusion[expected + "->" + state] += 1
            false_go += expected == "obstacle" and state == "clear"
            false_stop += expected == "clear" and state != "clear"
        side = label.get("obstacle_side", "unknown")
        if expected == "obstacle" and side in {"left", "right", "centre", "both"}:
            sides += 1
            side_errors += result.get("obstacle_side", "unknown") != side
    return (
        confusion,
        sides,
        side_errors,
        eligible,
        false_go,
        false_stop,
        labels,
        clear_labels,
        obstacle_labels,
    )


def _temporal_scores(samples, rows, order_source):
    """Count flips only between adjacent source observations."""
    pairs = flips = 0
    for index in range(1, len(samples)):
        if adjacent(samples[index - 1], samples[index], order_source):
            pairs += 1
            flips += rows[index - 1]["state"] != rows[index]["state"]
    return pairs, flips
