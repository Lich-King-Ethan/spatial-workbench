"""Bounded pose prediction and read-only evidence of physical sink latency.

Prediction is opt-in. Reported sink latency is not an acoustic measurement, and
the deliberately short prediction horizon does not compensate a whole Bluetooth
queue. This module performs no I/O and changes no device or stream settings.
"""
from __future__ import annotations

import math

from .pose import Quaternion


STALE_NS = 100_000_000
MAX_CONFIG_MS = 150.0
FALLBACK_CAP_MS = 40.0
MAX_CORRECTION_RAD = math.radians(12)
MIN_SPEED = math.radians(2)
MAX_SPEED = math.radians(720)


def _vector(value):
    if (not isinstance(value, (tuple, list)) or len(value) != 3
            or any(type(v) not in (int, float) or not -MAX_SPEED <= v <= MAX_SPEED for v in value)):
        return None
    result = tuple(map(float, value))
    return result if math.hypot(*result) <= MAX_SPEED else None


def _cosine(a, b):
    denominator = math.hypot(*a) * math.hypot(*b)
    return sum(x*y for x, y in zip(a, b)) / denominator if denominator else 0.0


def _body_velocity(before, current, interval_ns):
    delta = before.inverse() * current
    w, x, y, z = delta.values()
    if w < 0:  # q and -q describe the same orientation; take the short arc.
        w, x, y, z = -w, -x, -y, -z
    sine = math.hypot(x, y, z)
    if sine < 1e-12:
        return (0.0, 0.0, 0.0)
    scale = 2 * math.atan2(sine, w) / sine / (interval_ns / 1e9)
    return (x * scale, y * scale, z * scale)


def _gyro_agrees(gyro, measured):
    if gyro is None or math.hypot(*measured) < MIN_SPEED:
        return False
    ratio = math.hypot(*gyro) / math.hypot(*measured)
    return .7 <= ratio <= 1.3 and _cosine(gyro, measured) >= math.cos(math.radians(15))


class Predictor:
    """Predict head-to-world orientation using angular velocity in head axes.

    ``reference`` must change on recenter, tracker handoff, or provider session
    replacement. Repeated observation of the identical sample is idempotent.
    Three consistent new intervals are required after every reset. An absent or
    unusable gyro can use independently consistent quaternion motion, limited to
    40 ms. No history crosses a discontinuity or reference change.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self._pose = None
        self._sample = None
        self._reference = None
        self._intervals = []
        self._blocked = "no_sample"
        self._last_now_ns = None

    def observe(self, pose: Quaternion, timing: dict, reference):
        """Accept a sample, returning whether it became the latest observation."""
        try:
            current = Quaternion.parse(pose.values())
        except (AttributeError, OverflowError, TypeError, ValueError):
            self._sample = None
            self._intervals.clear()
            self._blocked = "invalid_orientation"
            return False
        try:
            hash(reference)
            if not isinstance(timing, dict) or timing.get("clock") != "host-monotonic":
                raise ValueError
            captured, received, sequence, discontinuity = (
                timing.get(key) for key in ("captured_ns", "received_ns", "sequence", "discontinuity"))
            if (any(type(value) is not int or not 0 <= value <= 2**63 - 1
                    for value in (captured, received, sequence, discontinuity))
                    or captured > received):
                raise ValueError
            raw = timing.get("raw_report_hex")
            if raw is not None and (not isinstance(raw, str) or len(raw) > 512):
                raise ValueError
            if raw is not None:
                raw = bytes.fromhex(raw)
            gyro = _vector(timing.get("angular_velocity"))
            if timing.get("angular_velocity") is not None and gyro is None:
                raise ValueError
        except (TypeError, ValueError):
            self._pose, self._sample = current, None
            self._intervals.clear()
            self._blocked = "invalid_timing"
            return False
        sample = {"captured_ns": captured, "received_ns": received,
                  "sequence": sequence, "discontinuity": discontinuity,
                  "gyro": gyro, "raw": raw}
        previous, old_pose = self._sample, self._pose
        reset_reason = None
        if previous is not None and reference != self._reference:
            reset_reason = "reference_changed"
        elif previous is not None and discontinuity != previous["discontinuity"]:
            reset_reason = "discontinuity"
        elif previous is not None:
            if (captured == previous["captured_ns"] and sequence == previous["sequence"]
                    and current == old_pose and received == previous["received_ns"]
                    and sample["gyro"] == previous["gyro"] and raw == previous["raw"]):
                return False
            if captured == previous["captured_ns"] or sequence == previous["sequence"]:
                self._intervals.clear()
                self._blocked = "conflicting_duplicate"
                return False
            if (captured <= previous["captured_ns"] or sequence <= previous["sequence"]
                    or received < previous["received_ns"]):
                self._intervals.clear()
                self._blocked = "out_of_order"
                return False  # Never roll the displayed pose back to an older packet.
        self._pose, self._sample, self._reference = current, sample, reference
        if previous is None or reset_reason is not None or self._blocked in (
                "out_of_order", "conflicting_duplicate", "invalid_orientation",
                "clock_reversed", "invalid_clock", "stale"):
            self._intervals.clear()
            self._blocked = reset_reason or "warming_up"
            return True
        interval = captured - previous["captured_ns"]
        if sequence != previous["sequence"] + 1:
            self._intervals.clear()
            self._blocked = "sequence_gap"
            return True
        if not 1_000_000 <= interval <= STALE_NS:
            self._intervals.clear()
            self._blocked = "sample_gap"
            return True
        velocity = _body_velocity(old_pose, current, interval)
        speed = math.hypot(*velocity)
        reason = None
        if speed < MIN_SPEED:
            reason = "stopped"
        elif speed > MAX_SPEED:
            reason = "implausible_motion"
        elif self._intervals:
            last = self._intervals[-1]
            previous_speed = math.hypot(*last["velocity"])
            if _cosine(velocity, last["velocity"]) <= 0:
                reason = "reversal"
            elif _cosine(velocity, last["velocity"]) < math.cos(math.radians(25)):
                reason = "changing_direction"
            elif speed < .85 * previous_speed:
                reason = "braking"
            elif speed > 1.6 * previous_speed:
                reason = "changing_speed"
            elif (_gyro_agrees(last["gyro"], last["velocity"])
                  and sample["gyro"] is not None and math.hypot(*sample["gyro"]) < MIN_SPEED):
                reason = "gyro_stopped"
        if reason:
            self._intervals.clear()
            self._blocked = reason
            return True
        self._intervals.append({"velocity": velocity, "gyro": sample["gyro"], "interval_ns": interval})
        self._intervals = self._intervals[-3:]
        self._blocked = "warming_up" if len(self._intervals) < 3 else ""
        return True

    def predict(self, now_ns, latency_ns, enabled=False, max_prediction_ms=80.0):
        """Return the bounded prediction (or observed pose) and diagnostic facts."""
        details = {"enabled": enabled is True, "applied": False, "reason": "disabled",
                   "velocity_source": "none", "sample_age_ms": None,
                   "reported_latency_ms": None, "requested_horizon_ms": None,
                   "effective_horizon_ms": 0.0, "effective_cap_ms": 0.0,
                   "configured_cap_ms": None, "correction_degrees": 0.0,
                   "angle_cap_degrees": 12.0}

        def observed(reason):
            details["reason"] = reason
            return self._pose, details

        if type(now_ns) is not int or not 0 <= now_ns <= 2**63 - 1:
            self._intervals.clear()
            self._blocked = "invalid_clock"
            return observed("invalid_clock")
        if self._last_now_ns is not None and now_ns < self._last_now_ns:
            self._intervals.clear()
            self._blocked = "clock_reversed"
            return observed("clock_reversed")
        self._last_now_ns = now_ns
        if self._sample is None:
            return observed(self._blocked)
        age = now_ns - self._sample["captured_ns"]
        details["sample_age_ms"] = age / 1e6
        if age < 0 or now_ns < self._sample["received_ns"]:
            self._intervals.clear()
            self._blocked = "invalid_clock"
            return observed("invalid_clock")
        if age > STALE_NS:
            self._intervals.clear()
            self._blocked = "stale"
            return observed("stale")
        if enabled is not True:
            return self._pose, details
        if type(latency_ns) is not int or not 0 <= latency_ns <= 2**63 - 1:
            return observed("unknown_latency")
        if (type(max_prediction_ms) not in (int, float)
                or not 0 < max_prediction_ms < float("inf")):
            return observed("invalid_limit")
        cap_ms = float(min(max_prediction_ms, MAX_CONFIG_MS))
        details.update(configured_cap_ms=cap_ms, reported_latency_ms=latency_ns / 1e6,
                       requested_horizon_ms=(age + latency_ns) / 1e6)
        if self._blocked or len(self._intervals) < 3:
            return observed(self._blocked or "warming_up")
        velocities = [sample["velocity"] for sample in self._intervals]
        speeds = [math.hypot(*velocity) for velocity in velocities]
        if (max(speeds) > 1.5 * min(speeds)
                or any(_cosine(a, b) < math.cos(math.radians(20))
                       for a in velocities for b in velocities)):
            return observed("uncertain_motion")
        gyro_valid = all(_gyro_agrees(sample["gyro"], sample["velocity"])
                         for sample in self._intervals)
        if gyro_valid:
            velocity = self._intervals[-1]["gyro"]
            details["velocity_source"] = "validated_gyro"
            reason = "bounded_gyro_prediction"
        else:
            velocity = velocities[-1]
            cap_ms = min(cap_ms, FALLBACK_CAP_MS)
            details["velocity_source"] = "quaternion_delta"
            reason = "gyro_missing_or_inconsistent_quaternion_fallback"
        details.update(effective_cap_ms=cap_ms, body_angular_velocity_rad_s=list(velocity))
        horizon = min((age + latency_ns) / 1e9, cap_ms / 1000)
        # Mild deceleration shortens the horizon; pronounced braking already
        # discarded history in observe(). Never average old fast motion into a stop.
        ratio = speeds[-1] / speeds[-2]
        if ratio < .98:
            horizon *= max(0.0, (ratio - .85) / (.98 - .85))
            reason = "braking_reduced_horizon"
        fresh_window = max(20_000_000, self._intervals[-1]["interval_ns"] * 1.5)
        if age > fresh_window:
            horizon *= max(0.0, (STALE_NS - age) / max(1, STALE_NS - fresh_window))
            reason = "aging_sample_reduced_horizon"
        speed = math.hypot(*velocity)
        horizon = min(horizon, MAX_CORRECTION_RAD / speed)
        if horizon <= 0:
            return observed("zero_horizon")
        angle = speed * horizon
        scale = math.sin(angle / 2) / speed
        increment = Quaternion.parse((math.cos(angle / 2), *(value * scale for value in velocity)))
        predicted = self._pose * increment
        details.update(applied=True, reason=reason, effective_horizon_ms=horizon * 1000,
                       correction_degrees=math.degrees(angle))
        return predicted, details


def reported_sink_latency(objects, sink):
    """Return ``(nanoseconds | None, evidence)`` for one exact physical sink.

    Only an explicit, unambiguous Input nanosecond value is used. Nonzero or
    missing quantum/rate terms cannot be converted without a verified clock.
    """
    evidence = {"source": "PipeWire Latency Input", "measured_acoustically": False}

    def unknown(reason):
        return None, {**evidence, "state": "unknown", "reason": reason}

    try:
        from .pipewire import parse_sinks  # Local import keeps the core free of a cycle.
        if sink is None or not sink.usable or not sink.name.startswith("bluez_output."):
            return unknown("no_usable_physical_sink")
        if (not isinstance(objects, list)
                or any(not isinstance(obj, dict) or type(obj.get("id")) is not int
                       or obj["id"] < 0 for obj in objects)
                or len({obj["id"] for obj in objects}) != len(objects)):
            return unknown("invalid_graph_or_sink")
        matching = [value for value in parse_sinks(objects, sink.device)
                    if (value.device, value.name, value.serial) == (sink.device, sink.name, sink.serial)
                    and value.usable]
        nodes = [obj for obj in objects if isinstance(obj, dict)
                 and obj.get("type") == "PipeWire:Interface:Node"
                 and ((obj.get("info") or {}).get("props", {}).get("node.name") == sink.name
                      or str((obj.get("info") or {}).get("props", {}).get("object.serial")) == sink.serial)]
        if len(matching) != 1 or len(nodes) != 1:
            return unknown("physical_sink_identity_missing_or_ambiguous")
        records = nodes[0]["info"].get("params", {}).get("Latency")
        if not isinstance(records, list):
            return unknown("latency_not_reported")
        inputs = [record for record in records if isinstance(record, dict) and record.get("direction") == "Input"]
        if len(inputs) != 1:
            return unknown("input_latency_missing_or_ambiguous")
        record = inputs[0]
        for key in ("minQuantum", "maxQuantum", "minRate", "maxRate"):
            if type(record.get(key)) not in (int, float) or record[key] != 0:
                return unknown("quantum_or_rate_requires_verified_clock")
        minimum, maximum = record.get("minNs"), record.get("maxNs")
        if (type(minimum) is not int or type(maximum) is not int
                or not 0 <= minimum == maximum <= 2**63 - 1):
            return unknown("nanosecond_latency_missing_or_ambiguous")
    except (AttributeError, KeyError, TypeError, ValueError):
        return unknown("invalid_graph_or_sink")
    return minimum, {**evidence, "state": "reported", "latency_ns": minimum,
                     "reason": "exact_sink_explicit_nanoseconds"}
