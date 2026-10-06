"""Real policy and OSC codec with a recording transport; no hardware claims."""
import copy
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from spatial.audio_runtime import RendererTelemetry, renderer_pose
from spatial.core import Engine, Sink
from spatial.pose import IDENTITY, Quaternion
from spatial.runtime import Runtime
from spatial.settings import Settings
from spatial.trackers.common import EngineSource

ADDRESS = 'AA:BB:CC:DD:EE:FF'
SINK = Sink(ADDRESS, 'bluez_output.timing', '101', 'a2dp-sink', 'running')


def yaw(degrees):
    angle = math.radians(degrees) / 2
    return Quaternion.parse((math.cos(angle), 0, math.sin(angle), 0))


def metadata(captured, sequence, counter=0):
    return {'captured_ns': captured, 'received_ns': captured + 100_000,
            'sequence': sequence, 'clock': 'host-monotonic',
            'angular_velocity': [0, 0, 0], 'discontinuity': counter,
            'raw_report_hex': '01' + '00' * 13}


def graph():
    return [{'id': 1, 'type': 'PipeWire:Interface:Node', 'info': {
        'state': 'running', 'props': {'media.class': 'Audio/Sink',
            'api.bluez5.address': ADDRESS, 'api.bluez5.profile': 'a2dp-sink',
            'node.name': SINK.name, 'object.serial': SINK.serial},
        'params': {'Latency': [{'direction': 'Input', 'minQuantum': 0., 'maxQuantum': 0.,
            'minRate': 0, 'maxRate': 0, 'minNs': 210_666_666, 'maxNs': 210_666_666}]}}}]


class Consumer:
    def __init__(self):
        self.telemetry = RendererTelemetry()
        self.sent = []
        self.telemetry.connection_made(SimpleNamespace(sendto=lambda *args: self.sent.append(args)))
        self.telemetry.target = ('127.0.0.1', 9999)
        self.poses = []

    def set_pose(self, pose):
        self.poses.append(pose)
        return self.telemetry.send('/omniphony/control/head/quat',
                                   *renderer_pose(pose or IDENTITY))

    def status(self):
        return {'running': True}


class TimingPolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.consumer = Consumer()
        self.runtime = Runtime(Settings(head_prediction_enabled=True), audio=self.consumer,
                               preferences_path=Path(self.tmp.name) / 'preferences.json')
        self.engine = self.runtime.engine
        self.engine.connect(ADDRESS)
        self.engine.observe_sink(self.engine.epoch, SINK)
        self.generation = self.engine.announce(ADDRESS, 'Head tracker', 'earbud')
        self.engine.preferences.earbuds[ADDRESS] = True
        self.runtime.desktop = SimpleNamespace(pipewire_objects=graph(), pipewire_observed_ns=1_000_000_000)

    def feed(self, sequence, degrees, *, counter=0, start=1_000_000_000):
        captured = start + sequence * 20_000_000
        now = captured + 200_000
        self.engine.advance(now / 1e9)
        self.assertTrue(self.engine.sample(ADDRESS, self.generation, sequence, yaw(degrees).values(),
                                          timing=metadata(captured, sequence, counter)))
        self.engine.select()
        with patch('time.monotonic_ns', return_value=now), patch('time.monotonic', return_value=now / 1e9):
            result = self.runtime._render_pose(self.consumer, 'media')
        return result, now

    def test_runtime_predicts_only_after_consistent_motion_and_records_actual_sends(self):
        for sequence in range(1, 6):
            pose, now = self.feed(sequence, sequence * 2)
        self.assertNotEqual(pose, self.engine.pose)
        with patch('time.monotonic_ns', return_value=now):
            snapshot = self.runtime.timing_snapshot()
        self.assertTrue(snapshot['prediction_status']['applied'])
        self.assertEqual(snapshot['prediction_status']['velocity_source'], 'quaternion_delta')
        self.assertLessEqual(snapshot['prediction_status']['effective_horizon_ms'], 40)
        self.assertEqual(len(snapshot['samples']), 5)
        self.assertEqual(len(snapshot['applications']), len(self.consumer.sent))
        self.assertEqual(snapshot['applications'][-1]['sent_ns'], now)
        self.assertEqual(snapshot['applications'][-1]['source_sequence'], 5)
        json.dumps(snapshot, allow_nan=False)
        snapshot['samples'][0]['timing']['sequence'] = -1
        self.assertEqual(self.runtime._timing_samples[0]['timing']['sequence'], 1)

    def test_missing_or_stale_latency_never_predicts_or_claims_current_latency(self):
        self.runtime.desktop.pipewire_observed_ns = None
        for sequence in range(1, 6):
            pose, now = self.feed(sequence, sequence * 2)
        self.assertEqual(pose, self.engine.pose)
        self.assertFalse(self.runtime._prediction_status['applied'])
        self.runtime.desktop.pipewire_observed_ns = now - 2_000_000_001
        latency, evidence = self.runtime._reported_latency(now)
        self.assertIsNone(latency)
        self.assertEqual(evidence['state'], 'unknown')
        self.assertIsNone(evidence['latency_ns'])

    def test_recenter_and_disconnect_cannot_reuse_motion_prediction(self):
        for sequence in range(1, 6):
            _, now = self.feed(sequence, sequence * 2)
        self.engine.recenter_active()
        with patch('time.monotonic_ns', return_value=now), patch('time.monotonic', return_value=now / 1e9):
            pose = self.runtime._render_pose(self.consumer, 'media')
            self.assertEqual(pose, IDENTITY)
            self.assertFalse(self.runtime._prediction_status['applied'])
            self.engine.disconnect()
            self.assertIsNone(self.runtime._render_pose(self.consumer, 'media'))
            self.assertFalse(self.runtime._prediction_status['applied'])

    def test_reference_counter_change_preserves_orientation_and_resets_prediction(self):
        for sequence in range(1, 6):
            self.feed(sequence, sequence * 2)
        before = self.engine.pose
        pose, _ = self.feed(6, 130, counter=1)
        self.assertFalse(self.runtime._prediction_status['applied'])
        for a, b in zip(pose.values(), before.values()):
            self.assertAlmostEqual(a, b, places=12)

    def test_old_default_keeps_observed_pose_and_cached_consumers_create_no_send_fact(self):
        self.runtime.settings = Settings()
        self.consumer.set_pose = lambda pose: None
        for sequence in range(1, 8):
            pose, _ = self.feed(sequence, sequence * 2)
            self.assertEqual(pose, self.engine.pose)
        self.assertEqual(list(self.runtime._timing_applications), [])

    def test_each_accepted_selected_packet_is_observed_between_render_ticks(self):
        source = EngineSource(self.engine, self.runtime.notify)
        # Six actual provider callbacks arrive between two 100 Hz renderer ticks.
        # Preserve their capture intervals; do not reconstruct motion from only
        # the final pose or turn notification callbacks into pretend OSC sends.
        for sequence in range(1, 7):
            captured = 1_000_000_000 + sequence * 2_000_000
            now = captured + 200_000
            with patch('time.monotonic_ns', return_value=now), patch('time.monotonic', return_value=now / 1e9):
                self.assertTrue(source.sample(ADDRESS, 'Head tracker', 'earbud', yaw(sequence * .18),
                                              timing=metadata(captured, sequence)))
                self.runtime.notify()  # Unrelated notifications are idempotent.
        self.assertEqual([row['timing']['sequence'] for row in self.runtime._timing_samples], list(range(1, 7)))
        self.assertEqual(list(self.runtime._timing_applications), [])
        self.assertEqual(self.consumer.sent, [])
        with patch('time.monotonic_ns', return_value=now), patch('time.monotonic', return_value=now / 1e9):
            self.runtime._render_pose(self.consumer, 'media')
            self.assertTrue(self.runtime._prediction_status['applied'])
            self.runtime._render_pose(self.consumer, 'live')
            self.assertTrue(self.runtime._prediction_status['applied'])
        self.assertEqual(len(self.runtime._timing_samples), 6)
        self.assertEqual(len(self.runtime._timing_applications), 2)
        self.assertEqual([row['consumer'] for row in self.runtime._timing_applications], ['media', 'live'])
        self.assertEqual([row['source_sequence'] for row in self.runtime._timing_applications], [6, 6])

    def test_packet_notifications_never_retain_unselected_or_disabled_tracker_reports(self):
        source = EngineSource(self.engine, self.runtime.notify)
        now = 1_050_000_000
        with patch('time.monotonic_ns', return_value=now), patch('time.monotonic', return_value=now / 1e9):
            self.assertTrue(source.sample('receiver:1', 'Body tracker', 'optional', yaw(45),
                                          timing=metadata(now - 200_000, 1)))
            self.assertEqual(list(self.runtime._timing_samples), [])
            self.assertTrue(source.sample(ADDRESS, 'Head tracker', 'earbud', yaw(10),
                                          timing=metadata(now - 100_000, 1)))
            self.assertEqual(len(self.runtime._timing_samples), 1)
            self.engine.set_enabled(ADDRESS, False)
            self.runtime.notify()
        now += 2_000_000
        with patch('time.monotonic_ns', return_value=now), patch('time.monotonic', return_value=now / 1e9):
            self.assertTrue(source.sample(ADDRESS, 'Head tracker', 'earbud', yaw(12),
                                          timing=metadata(now - 100_000, 2)))
        self.assertEqual(len(self.runtime._timing_samples), 1)
        self.assertEqual(list(self.runtime._timing_applications), [])

    def test_engine_rejects_bad_timestamp_without_overwriting_latest_pose(self):
        self.feed(1, 0)
        before = copy.deepcopy(self.engine.trackers[ADDRESS])
        valid = metadata(1_030_000_000, 2)
        self.engine.advance(1.04)
        for changed in ({'captured_ns': -1}, {'captured_ns': 1_041_000_000},
                        {'received_ns': 1_041_000_000}, {'captured_ns': 1},
                        {'sequence': 1}, {'captured_ns': 10**1000},
                        {'received_ns': 10**1000}, {'sequence': 10**1000},
                        {'clock': 'device-wall-clock'}, {'angular_velocity': [10**1000, 0, 0]},
                        {'angular_velocity': [float('nan'), 0, 0]}, {'discontinuity': 256}):
            with self.subTest(changed=changed):
                self.assertFalse(self.engine.sample(ADDRESS, self.generation, 2, yaw(20).values(),
                                                  timing={**valid, **changed}))
                self.assertEqual(self.engine.trackers[ADDRESS], before)


class TelemetryTiming(unittest.TestCase):
    def test_observed_renderer_pose_is_receipt_timed_bounded_and_session_scoped(self):
        telemetry = RendererTelemetry()
        message = json.dumps({'binaural': {'headPose': dict(zip(('w','x','y','z'), IDENTITY.values()))}})
        with patch('time.monotonic_ns', return_value=123):
            for _ in range(600):
                telemetry.accept('/omniphony/state/renderer', [message])
        self.assertEqual(len(telemetry.observed_poses), 512)
        self.assertEqual(telemetry.observed_poses[-1]['received_ns'], 123)
        telemetry.accept('/omniphony/state/head_pose', [0, 0, 0, 0])
        self.assertEqual(len(telemetry.observed_poses), 512)
        telemetry.invalidate()
        self.assertEqual(list(telemetry.observed_poses), [])
