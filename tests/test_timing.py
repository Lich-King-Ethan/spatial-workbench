"""Prediction limits are safety bounds, not hardware-latency acceptance claims."""
import copy
import math
import unittest
from dataclasses import replace

from spatial.core import Sink
from spatial.pose import IDENTITY, Quaternion
from spatial.timing import Predictor, reported_sink_latency


START = 1_000_000_000
STEP = 20_000_000
MAC = "AA:BB:CC:DD:EE:FF"


def rotation(degrees, axis=(0, 0, 1)):
    half = math.radians(degrees) / 2
    length = math.hypot(*axis)
    return Quaternion(math.cos(half), *(math.sin(half) * value / length for value in axis))


def sample(index, **changes):
    result = {"clock": "host-monotonic", "captured_ns": START + index * STEP,
              "received_ns": START + index * STEP, "sequence": index,
              "angular_velocity": None, "discontinuity": 0, "raw_report_hex": "0100"}
    result.update(changes)
    return result


def warm(*, speed=90, gyro=None, base=IDENTITY, axis=(0, 0, 1)):
    predictor = Predictor()
    for index in range(4):
        pose = base * rotation(speed * index * STEP / 1e9, axis)
        predictor.observe(pose, sample(index, angular_velocity=gyro), "session")
    return predictor, pose


class Prediction(unittest.TestCase):
    def assertPose(self, actual, expected, places=8):
        # Quaternion sign has no physical significance.
        sign = 1 if sum(a*b for a, b in zip(actual.values(), expected.values())) >= 0 else -1
        for actual_value, expected_value in zip(actual.values(), expected.values()):
            self.assertAlmostEqual(actual_value * sign, expected_value, places=places)

    def test_default_keeps_observed_pose_and_no_sample_is_explicit(self):
        predictor = Predictor()
        pose, detail = predictor.predict(START, 211_000_000, enabled=True)
        self.assertIsNone(pose)
        self.assertEqual(detail["reason"], "no_sample")
        predictor, actual = warm()
        for enabled in (False, 1, "true", None):
            pose, detail = predictor.predict(START + 3 * STEP, 211_000_000, enabled=enabled)
            self.assertEqual(pose, actual)
            self.assertFalse(detail["applied"])
            self.assertEqual(detail["reason"], "disabled")

    def test_three_new_consistent_intervals_are_required(self):
        predictor = Predictor()
        for index in range(4):
            pose = rotation(index * 1.8)
            self.assertTrue(predictor.observe(pose, sample(index), "session"))
            for _ in range(5):
                self.assertFalse(predictor.observe(pose, sample(index), "session"))
            _, detail = predictor.predict(START + index * STEP, 211_000_000, enabled=True)
            self.assertEqual(detail["applied"], index == 3)

    def test_body_velocity_predicts_from_nonidentity_orientation(self):
        base = rotation(53, (1, 2, 3))
        axis = (1, 0, 0)
        predictor, _ = warm(base=base, axis=axis)
        pose, detail = predictor.predict(START + 3 * STEP + 5_000_000, 20_000_000, enabled=True)
        # The head rotates around its own X axis, not the fixed world's X axis.
        self.assertPose(pose, base * rotation(90 * .085, axis))
        self.assertEqual(detail["velocity_source"], "quaternion_delta")
        self.assertAlmostEqual(detail["effective_horizon_ms"], 25)

    def test_quaternion_sign_changes_do_not_reverse_velocity(self):
        predictor = Predictor()
        for index in range(4):
            pose = rotation(index * 1.8)
            if index % 2:
                pose = Quaternion(*(-value for value in pose.values()))
            predictor.observe(pose, sample(index), "session")
        pose, detail = predictor.predict(START + 3 * STEP, 40_000_000, enabled=True)
        self.assertTrue(detail["applied"])
        self.assertPose(pose, rotation(9))

    def test_zero_gyro_uses_bounded_fallback_not_full_bluetooth_queue(self):
        predictor, _ = warm(gyro=(0, 0, 0))
        pose, detail = predictor.predict(START + 3 * STEP, 210_666_666, enabled=True)
        self.assertEqual(detail["velocity_source"], "quaternion_delta")
        self.assertAlmostEqual(detail["requested_horizon_ms"], 210.666666)
        self.assertEqual(detail["effective_cap_ms"], 40)
        self.assertEqual(detail["effective_horizon_ms"], 40)
        self.assertPose(pose, rotation(9))

    def test_verified_gyro_and_configured_and_angle_limits(self):
        for limit, expected in ((80, 80), (30, 30), (500, 150)):
            predictor, _ = warm(speed=30, gyro=(0, 0, math.radians(30)))
            _, detail = predictor.predict(START + 3 * STEP, 211_000_000,
                                          enabled=True, max_prediction_ms=limit)
            self.assertEqual(detail["velocity_source"], "validated_gyro")
            self.assertEqual(detail["effective_horizon_ms"], expected)
            self.assertLessEqual(detail["configured_cap_ms"], 150)
        predictor, _ = warm(speed=600, gyro=(0, 0, math.radians(600)))
        _, detail = predictor.predict(START + 3 * STEP, 211_000_000, enabled=True)
        self.assertAlmostEqual(detail["correction_degrees"], 12)
        self.assertAlmostEqual(detail["effective_horizon_ms"], 20)

    def test_wrong_axis_or_scale_gyro_cannot_get_longer_horizon(self):
        for gyro in ((math.pi / 2, 0, 0), (0, 0, -math.pi / 2), (0, 0, math.pi)):
            with self.subTest(gyro=gyro):
                predictor, _ = warm(gyro=gyro)
                _, detail = predictor.predict(START + 3 * STEP, 211_000_000, enabled=True)
                self.assertEqual(detail["velocity_source"], "quaternion_delta")
                self.assertLessEqual(detail["effective_horizon_ms"], 40)

    def test_invalid_timing_clears_prediction_and_requires_new_history(self):
        corruptions = ({"clock": "device"}, {"captured_ns": START + 100 * STEP},
                       {"captured_ns": True}, {"received_ns": -1}, {"sequence": None},
                       {"discontinuity": -1}, {"raw_report_hex": "xyz"},
                       {"raw_report_hex": "00" * 257}, {"angular_velocity": [0, 0]},
                       {"angular_velocity": [0, 0, float("nan")]},
                       {"angular_velocity": [False, 0, 0]}, {"captured_ns": 2**80})
        for corruption in corruptions:
            with self.subTest(corruption=corruption):
                predictor, _ = warm()
                self.assertFalse(predictor.observe(rotation(7.2), sample(4, **corruption), "session"))
                _, detail = predictor.predict(START + 4 * STEP, 211_000_000, enabled=True)
                self.assertFalse(detail["applied"])
                self.assertEqual(detail["reason"], "invalid_timing")
                predictor.observe(rotation(9), sample(5), "session")
                _, detail = predictor.predict(START + 5 * STEP, 211_000_000, enabled=True)
                self.assertEqual(detail["reason"], "warming_up")

    def test_invalid_orientation_never_reuses_old_velocity(self):
        predictor, previous = warm()
        self.assertFalse(predictor.observe(Quaternion(0, 0, 0, 0), sample(4), "session"))
        pose, detail = predictor.predict(START + 4 * STEP, 211_000_000, enabled=True)
        self.assertEqual(pose, previous)
        self.assertEqual(detail["reason"], "invalid_orientation")

    def test_conflicting_duplicate_cannot_replace_or_reactivate_sample(self):
        for changes in ({"raw_report_hex": "0101"}, {"angular_velocity": [0, 0, 1]},
                        {"received_ns": START + 3 * STEP + 1}):
            with self.subTest(changes=changes):
                predictor, previous = warm()
                self.assertFalse(predictor.observe(previous, sample(3, **changes), "session"))
                self.assertFalse(predictor.observe(previous, sample(3), "session"))
                pose, detail = predictor.predict(START + 3 * STEP, 211_000_000, enabled=True)
                self.assertEqual(pose, previous)
                self.assertEqual(detail["reason"], "conflicting_duplicate")
                predictor.observe(rotation(7.2), sample(4), "session")
                _, detail = predictor.predict(START + 4 * STEP, 211_000_000, enabled=True)
                self.assertEqual(detail["reason"], "warming_up")

    def test_old_packets_do_not_roll_back_pose(self):
        predictor, previous = warm()
        self.assertFalse(predictor.observe(rotation(1.8), sample(1), "session"))
        pose, detail = predictor.predict(START + 3 * STEP, 211_000_000, enabled=True)
        self.assertEqual(pose, previous)
        self.assertEqual(detail["reason"], "out_of_order")

    def test_recenter_handoff_and_discontinuity_start_new_history(self):
        for reference, changes, reason in (("new session", {}, "reference_changed"),
                                            ("session", {"discontinuity": 1, "sequence": 0}, "discontinuity")):
            with self.subTest(reason=reason):
                predictor, _ = warm()
                predictor.observe(IDENTITY, sample(4, **changes), reference)
                pose, detail = predictor.predict(START + 4 * STEP, 211_000_000, enabled=True)
                self.assertEqual(pose, IDENTITY)
                self.assertEqual(detail["reason"], reason)
                for step in range(1, 4):
                    timing = sample(4 + step, **changes)
                    if changes:
                        timing["sequence"] = step
                    predictor.observe(rotation(step * 1.8), timing, reference)
                pose, detail = predictor.predict(START + 7 * STEP, 40_000_000, enabled=True)
                self.assertTrue(detail["applied"])
                self.assertPose(pose, rotation(9))

    def test_stale_history_is_cleared_even_while_disabled(self):
        for enabled in (False, True):
            predictor, previous = warm()
            now = START + 3 * STEP + 101_000_000
            pose, detail = predictor.predict(now, 211_000_000, enabled=enabled)
            self.assertEqual(pose, previous)
            self.assertEqual(detail["reason"], "stale")
            # A delayed new report has a short enough interval to have reused the
            # old velocity if predict() had only hidden, rather than cleared it.
            timing = sample(4, captured_ns=now - 10_000_000, received_ns=now)
            predictor.observe(rotation(13.59), timing, "session")
            _, detail = predictor.predict(now, 211_000_000, enabled=True)
            self.assertEqual(detail["reason"], "warming_up")

    def test_clock_reversal_or_future_observation_clears_history(self):
        predictor, _ = warm()
        now = START + 3 * STEP
        predictor.predict(now, 211_000_000, enabled=True)
        _, detail = predictor.predict(now - 1, 211_000_000, enabled=True)
        self.assertEqual(detail["reason"], "clock_reversed")
        predictor.observe(rotation(7.2), sample(4), "session")
        _, detail = predictor.predict(START + 4 * STEP, 211_000_000, enabled=True)
        self.assertEqual(detail["reason"], "warming_up")
        predictor, _ = warm()
        _, detail = predictor.predict(now - 1, 211_000_000, enabled=True)
        self.assertEqual(detail["reason"], "invalid_clock")

    def test_gaps_drop_estimate(self):
        for timing, reason in ((sample(5), "sequence_gap"),
                               (sample(4, captured_ns=START + 4 * STEP + 100_000_000,
                                       received_ns=START + 4 * STEP + 100_000_000), "sample_gap")):
            predictor, _ = warm()
            predictor.observe(rotation(7.2), timing, "session")
            _, detail = predictor.predict(timing["received_ns"], 211_000_000, enabled=True)
            self.assertEqual(detail["reason"], reason)

    def test_stop_reversal_braking_and_direction_change_return_observed_pose(self):
        cases = ((rotation(5.4), "stopped"), (rotation(3.6), "reversal"),
                 (rotation(6.3), "braking"),
                 (rotation(5.4) * rotation(1.8, (1, 0, 1)), "changing_direction"))
        for current, reason in cases:
            with self.subTest(reason=reason):
                predictor, _ = warm()
                predictor.observe(current, sample(4), "session")
                pose, detail = predictor.predict(START + 4 * STEP, 211_000_000, enabled=True)
                self.assertEqual(pose, current)
                self.assertEqual(detail["reason"], reason)
                self.assertFalse(detail["applied"])

    def test_measured_gyro_stop_blocks_still_moving_quaternion(self):
        predictor, _ = warm(gyro=(0, 0, math.pi / 2))
        current = rotation(7.2)
        predictor.observe(current, sample(4, angular_velocity=(0, 0, 0)), "session")
        pose, detail = predictor.predict(START + 4 * STEP, 211_000_000, enabled=True)
        self.assertEqual(pose, current)
        self.assertEqual(detail["reason"], "gyro_stopped")

    def test_mild_braking_and_aging_reduce_horizon(self):
        predictor, _ = warm()
        predictor.observe(rotation(5.4 + 1.7), sample(4), "session")
        _, detail = predictor.predict(START + 4 * STEP, 211_000_000, enabled=True)
        self.assertEqual(detail["reason"], "braking_reduced_horizon")
        self.assertGreater(detail["effective_horizon_ms"], 0)
        self.assertLess(detail["effective_horizon_ms"], 40)
        predictor, _ = warm()
        _, detail = predictor.predict(START + 3 * STEP + 90_000_000, 211_000_000, enabled=True)
        self.assertEqual(detail["reason"], "aging_sample_reduced_horizon")
        self.assertLess(detail["effective_horizon_ms"], 6)

    def test_acceleration_uncertainty_suppresses_prediction(self):
        predictor = Predictor()
        current = IDENTITY
        predictor.observe(current, sample(0), "session")
        for index, speed in enumerate((90, 130, 180), 1):
            current = current * rotation(speed * STEP / 1e9)
            predictor.observe(current, sample(index), "session")
        pose, detail = predictor.predict(START + 3 * STEP, 211_000_000, enabled=True)
        self.assertEqual(pose, current)
        self.assertEqual(detail["reason"], "uncertain_motion")

    def test_unknown_latency_invalid_limit_and_zero_horizon_do_not_guess(self):
        for latency in (None, -1, True, 4.5, 2**80):
            predictor, previous = warm()
            pose, detail = predictor.predict(START + 3 * STEP, latency, enabled=True)
            self.assertEqual(pose, previous)
            self.assertEqual(detail["reason"], "unknown_latency")
        for limit in (0, -1, True, None, float("inf"), float("nan")):
            predictor, _ = warm()
            _, detail = predictor.predict(START + 3 * STEP, 211_000_000,
                                          enabled=True, max_prediction_ms=limit)
            self.assertEqual(detail["reason"], "invalid_limit")
        predictor, _ = warm()
        _, detail = predictor.predict(START + 3 * STEP, 0, enabled=True)
        self.assertEqual(detail["reason"], "zero_horizon")


def graph():
    return [{"id": 10, "type": "PipeWire:Interface:Device", "info": {"props": {
                "device.api": "bluez5", "api.bluez5.address": MAC}}},
            {"id": 20, "type": "PipeWire:Interface:Node", "info": {
                "state": "running", "props": {"device.id": 10, "media.class": "Audio/Sink",
                    "object.serial": 100, "node.name": "bluez_output.test",
                    "api.bluez5.profile": "a2dp-sink", "api.bluez5.codec": "ldac"},
                "params": {"Latency": [{"direction": "Input", "minQuantum": 0.0,
                    "maxQuantum": 0.0, "minRate": 0, "maxRate": 0,
                    "minNs": 210666666, "maxNs": 210666666}]}}}]


SINK = Sink(MAC, "bluez_output.test", "100", "a2dp-sink", "running", "ldac")


class SinkLatency(unittest.TestCase):
    def test_exact_report_is_evidence_not_acoustic_measurement(self):
        latency, evidence = reported_sink_latency(graph(), SINK)
        self.assertEqual(latency, 210666666)
        self.assertEqual(evidence["state"], "reported")
        self.assertFalse(evidence["measured_acoustically"])
        self.assertEqual(evidence["source"], "PipeWire Latency Input")

    def test_wrong_device_name_serial_profile_or_virtual_sink_is_unknown(self):
        variants = (replace(SINK, device="11:22:33:44:55:66"), replace(SINK, serial="101"),
                    replace(SINK, name="bluez_output.other"), replace(SINK, profile="hfp"),
                    replace(SINK, name="spatial.eq"), None)
        for sink in variants:
            with self.subTest(sink=sink):
                latency, detail = reported_sink_latency(graph(), sink)
                self.assertIsNone(latency)
                self.assertEqual(detail["state"], "unknown")

    def test_duplicate_name_or_serial_and_device_ids_are_ambiguous(self):
        for duplicate in ("name", "serial", "device"):
            objects = graph()
            extra = copy.deepcopy(objects[0 if duplicate == "device" else 1])
            if duplicate != "device":
                extra["id"] = 21
                extra["info"]["props"]["object.serial" if duplicate == "name" else "node.name"] = (
                    101 if duplicate == "name" else "bluez_output.other")
            objects.append(extra)
            latency, detail = reported_sink_latency(objects, SINK)
            self.assertIsNone(latency)
            self.assertEqual(detail["state"], "unknown")

    def test_unknown_or_nonzero_quantum_and_rate_are_never_converted(self):
        for key in ("minQuantum", "maxQuantum", "minRate", "maxRate"):
            for value in (None, True, 1, "0", float("nan")):
                with self.subTest(key=key, value=value):
                    objects = graph()
                    record = objects[1]["info"]["params"]["Latency"][0]
                    if value is None:
                        record.pop(key)
                    else:
                        record[key] = value
                    latency, detail = reported_sink_latency(objects, SINK)
                    self.assertIsNone(latency)
                    self.assertEqual(detail["reason"], "quantum_or_rate_requires_verified_clock")

    def test_latency_requires_one_input_exact_nanosecond_value(self):
        for change in ({"minNs": 200000000}, {"minNs": -1, "maxNs": -1},
                       {"maxNs": None}, {"minNs": 0.0, "maxNs": 0.0},
                       {"minNs": True, "maxNs": True}, {"direction": "Output"}):
            objects = graph()
            objects[1]["info"]["params"]["Latency"][0].update(change)
            self.assertIsNone(reported_sink_latency(objects, SINK)[0])
        objects = graph()
        records = objects[1]["info"]["params"]["Latency"]
        records.append(copy.deepcopy(records[0]))
        self.assertIsNone(reported_sink_latency(objects, SINK)[0])

    def test_absent_or_malformed_graph_is_unknown(self):
        for objects in (None, {}, [None], [{"id": True}], [{"id": 1, "type": "PipeWire:Interface:Node",
                                                            "info": {"props": []}}]):
            with self.subTest(objects=objects):
                self.assertIsNone(reported_sink_latency(objects, SINK)[0])
        objects = graph()
        objects[1]["info"].pop("params")
        self.assertIsNone(reported_sink_latency(objects, SINK)[0])


if __name__ == "__main__":
    unittest.main()
