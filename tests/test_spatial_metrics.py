"""Exercise the acoustic gate with analytic PCM and deliberate broken paths.

These signals are an independent, simple directional-response model, not an
HRTF or evidence of a working renderer. They verify that the *gate* accepts
geometry-consistent signals and rejects channel collapse and pose mistakes.
The integration job remains responsible for actual rendered captures.
"""
import importlib.util
import math
from pathlib import Path
import random
import tempfile
import unittest

import numpy as np


path = Path(__file__).resolve().parents[1] / "tools/ci/spatial-metrics.py"
spec = importlib.util.spec_from_file_location("spatial_metrics", path)
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


# Independent geometric truth table: azimuth positive right, elevation up.
# Do not calculate it with production pose adapters or renderer routines.
DIRECTIONS = {
    "position_fl": (-45, 0), "position_fr": (45, 0), "position_fc": (0, 0),
    "position_rl": (-135, 0), "position_rr": (135, 0),
    "pose_neutral": (0, 0), "pose_yaw_plus90": (90, 0),
    "pose_yaw_minus90": (-90, 0), "pose_yaw_180": (180, 0),
    "pose_pitch_plus45": (0, -45), "pose_pitch_minus45": (0, 45),
    "pose_roll_plus45": (0, 0), "pose_roll_minus45": (0, 0),
    "pose_pitch_plus45_roll_plus90": (-45, 0),
    "pose_pitch_minus45_roll_plus90": (45, 0),
    "pose_neutral_repeat": (0, 0), "pose_recentered": (0, 0),
    "pose_recentered_yaw_plus90": (90, 0),
}


def signal(azimuth, elevation, *, phase=0, gain=1):
    """Periodic colored noise with ear gain/delay from a source direction."""
    generator = random.Random(4844)
    noise = np.array([generator.uniform(-0.01, 0.01) for _ in range(4800)])
    azimuth, elevation = map(math.radians, (azimuth, elevation))
    leftness = -math.sin(azimuth) * math.cos(elevation)
    frontness = math.cos(azimuth) * math.cos(elevation)
    height = math.sin(elevation)
    frequencies = np.fft.rfftfreq(len(noise), 1 / 48000)
    # Smooth common coloration distinguishes front/back and up/down while
    # preserving coherent low-frequency interaural delay. At identical
    # relative directions it is exactly identical, even across pose routes.
    relative_frequency = frequencies / 16000
    color_db = (-6 * (1 - frontness) * np.sqrt(relative_frequency)
                + 12 * height * relative_frequency ** 1.2)
    mono = np.fft.irfft(np.fft.rfft(noise) * 10 ** (color_db / 20))
    delay = round(28 * leftness)
    left = np.roll(mono, max(0, -delay)) * 10 ** (4 * leftness / 20)
    right = np.roll(mono, max(0, delay)) * 10 ** (-4 * leftness / 20)
    period = np.roll(np.column_stack((left, right)), phase, axis=0)
    return (gain * np.tile(period, (10, 1))).astype("<f4")


class SpectrumTests(unittest.TestCase):
    def test_every_cyclic_stimulus_phase_has_the_same_spectrum(self):
        # A full 4800-phase sweep catches the old two-phase Hann average;
        # adding more repeated periods cannot fix that estimator's bias.
        period = signal(-45, 0)[:4800].astype(float)
        reference = metrics._spectrum(period, 48000)
        maximum = max(metrics._distance(reference, metrics._spectrum(
            np.roll(period, phase, axis=0), 48000)) for phase in range(4800))
        self.assertLess(maximum, 1e-11)

    def test_absolute_gain_and_capture_period_count_do_not_change_spectrum(self):
        samples = signal(45, 0).astype(float)
        np.testing.assert_allclose(metrics._spectrum(samples, 48000),
                                   metrics._spectrum(samples[:14400] * 0.17, 48000),
                                   atol=1e-12)

    def test_delay_sign_and_common_source_shift(self):
        for azimuth, sign in ((-90, 1), (90, -1)):
            samples = signal(azimuth, 0).astype(float)
            delay, coherence = metrics._interaural_delay(samples, 48000)
            shifted_delay, _ = metrics._interaural_delay(np.roll(samples, 973, axis=0), 48000)
            self.assertAlmostEqual(delay, sign * 28 * 1e6 / 48000, delta=2)
            self.assertAlmostEqual(delay, shifted_delay, delta=2)
            self.assertGreater(coherence, 0.99)


class AcousticGateControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="spatial-metrics-test-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.windows = {}
        for index, (name, direction) in enumerate(DIRECTIONS.items()):
            target = Path(cls.directory.name) / (name + ".f32le")
            # All captures start at different source phases and gains.
            signal(*direction, phase=index * 193, gain=0.7 + index / 100).tofile(target)
            cls.windows[name] = target

    def assert_rejected(self, windows, expected_check):
        with self.assertRaises(metrics.SpatialMetricsError) as raised:
            metrics.analyze_windows(windows)
        failures = [entry["name"] for entry in raised.exception.report["checks"]
                    if not entry["passed"]]
        self.assertIn(expected_check, failures)

    def test_geometry_consistent_phase_shifted_controls_pass(self):
        report = metrics.analyze_windows(self.windows)
        self.assertEqual(report["status"], "passed")
        self.assertTrue(all(check["passed"] for check in report["checks"]))

    def test_front_rear_stereo_collapse_is_rejected(self):
        windows = dict(self.windows, position_rl=self.windows["position_fl"],
                       position_rr=self.windows["position_fr"])
        self.assert_rejected(windows,
            "position_rl: distinct-directional-spectrum-from-position_fl")
        self.assert_rejected(windows,
            "position_rr: distinct-directional-spectrum-from-position_fr")

    def test_no_op_head_tracking_is_rejected(self):
        windows = {name: self.windows["pose_neutral"] if name.startswith("pose_") else path
                   for name, path in self.windows.items()}
        self.assert_rejected(windows, "pose_yaw_plus90: expected-right-ear-leads-and-is-louder")

    def test_yaw_sign_inversion_is_rejected(self):
        windows = dict(self.windows,
                       pose_yaw_plus90=self.windows["pose_yaw_minus90"],
                       pose_yaw_minus90=self.windows["pose_yaw_plus90"])
        self.assert_rejected(windows, "pose_yaw_plus90: expected-right-ear-leads-and-is-louder")

    def test_pitch_sign_inversion_is_rejected_by_compound_pose(self):
        windows = dict(self.windows,
            pose_pitch_plus45=self.windows["pose_pitch_minus45"],
            pose_pitch_minus45=self.windows["pose_pitch_plus45"],
            pose_pitch_plus45_roll_plus90=self.windows["pose_pitch_minus45_roll_plus90"],
            pose_pitch_minus45_roll_plus90=self.windows["pose_pitch_plus45_roll_plus90"])
        self.assert_rejected(windows,
            "pose_pitch_plus45_roll_plus90: expected-left-ear-leads-and-is-louder")

    def test_no_op_roll_is_rejected_by_off_axis_compound_pose(self):
        windows = dict(self.windows,
            pose_pitch_plus45_roll_plus90=self.windows["pose_pitch_plus45"],
            pose_pitch_minus45_roll_plus90=self.windows["pose_pitch_minus45"])
        self.assert_rejected(windows,
            "pose_pitch_plus45_roll_plus90: matches-position_fl")

    def test_roll_moving_an_on_axis_front_source_is_rejected(self):
        windows = dict(self.windows, pose_roll_plus45=self.windows["pose_pitch_plus45"])
        self.assert_rejected(windows, "pose_roll_plus45: matches-pose_neutral")

    def test_same_direction_with_wrong_spectrum_is_rejected(self):
        # Lateral signs/delays alone cannot distinguish front from rear.
        windows = dict(self.windows,
            pose_pitch_plus45_roll_plus90=self.windows["position_rl"])
        self.assert_rejected(windows, "pose_pitch_plus45_roll_plus90: matches-position_fl")

    def test_no_op_recenter_is_rejected(self):
        windows = dict(self.windows, pose_recentered=self.windows["position_fr"])
        self.assert_rejected(windows, "pose_recentered: matches-pose_neutral")

    def test_swapped_headphone_channels_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="spatial-metrics-swap-") as directory:
            windows = {}
            for name, original in self.windows.items():
                target = Path(directory) / original.name
                np.fromfile(original, dtype="<f4").reshape(-1, 2)[:, ::-1].tofile(target)
                windows[name] = target
            self.assert_rejected(windows, "position_fl: expected-left-ear-leads-and-is-louder")


if __name__ == "__main__":
    unittest.main()
