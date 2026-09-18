import itertools
import math
import tempfile
import unittest
from pathlib import Path

from spatial.core import Engine, Sink
from spatial.pose import Quaternion
from spatial.preferences import Preferences
from spatial.retry import Backoff

DEVICE = "AA:BB:CC:DD:EE:FF"


def populated():
    engine = Engine()
    engine.connect(DEVICE)
    for key, name, kind in [(DEVICE, "WF-1000XM5", "earbud"),
                            ("receiver:7C3A", "7C3A", "optional")]:
        generation = engine.announce(key, name, kind)
        engine.sample(key, generation, 1, [1, 0, 0, 0])
    return engine


class Selection(unittest.TestCase):
    def test_complete_toggle_and_presence_matrix(self):
        for present, optional, earbuds in itertools.product([False, True], repeat=3):
            with self.subTest(present=present, optional=optional, earbuds=earbuds):
                engine = populated()
                engine.set_enabled(DEVICE, earbuds)
                engine.set_enabled("receiver:7C3A", optional)
                if not present:
                    engine.remove("receiver:7C3A", 1)
                expected = "receiver:7C3A" if present and optional else (DEVICE if earbuds else None)
                self.assertEqual(engine.snapshot()["active_id"], expected)

    def test_new_devices_are_opt_in(self):
        self.assertIsNone(populated().snapshot()["active_id"])

    def test_announcement_alone_never_creates_row(self):
        engine = Engine()
        engine.announce("1", "Hardware 1", "optional")
        self.assertEqual(engine.snapshot()["optional_trackers"], [])

    def test_stale_tracker_disappears_and_falls_back(self):
        engine = populated()
        engine.set_enabled(DEVICE, True)
        engine.set_enabled("receiver:7C3A", True)
        engine.advance(0.6)
        engine.sample(DEVICE, 1, 2, [1, 0, 0, 0])
        state = engine.snapshot()
        self.assertEqual(state["optional_trackers"], [])
        self.assertEqual(state["active_id"], DEVICE)

    def test_source_returns_with_identity_preference(self):
        engine = populated()
        engine.set_enabled("receiver:7C3A", True)
        engine.remove("receiver:7C3A", 1)
        generation = engine.announce("receiver:7C3A", "7C3A", "optional")
        engine.sample("receiver:7C3A", generation, 0, [1, 0, 0, 0])
        self.assertEqual(engine.snapshot()["active_id"], "receiver:7C3A")

    def test_device_name_is_verbatim_plain_data(self):
        engine = Engine()
        generation = engine.announce("raw-id", "<b>Real & Device</b>", "optional")
        engine.sample("raw-id", generation, 0, [1, 0, 0, 0])
        self.assertEqual(engine.snapshot()["optional_trackers"][0]["name"], "<b>Real & Device</b>")

    def test_second_optional_requires_explicit_choice(self):
        engine = populated()
        engine.set_enabled("receiver:7C3A", True)
        generation = engine.announce("receiver:2", "2", "optional")
        engine.sample("receiver:2", generation, 0, [1, 0, 0, 0])
        self.assertEqual(engine.select(), "receiver:7C3A")
        engine.set_enabled("receiver:2", True)
        self.assertEqual(engine.select(), "receiver:2")
        self.assertFalse(engine.preferences.optional["receiver:7C3A"])

    def test_disabled_tracker_does_not_get_removed(self):
        engine = populated()
        engine.set_enabled("receiver:7C3A", False)
        self.assertEqual(len(engine.snapshot()["optional_trackers"]), 1)

    def test_toggle_rejects_missing_tracker(self):
        with self.assertRaises(ValueError):
            Engine().set_enabled("missing", True)

    def test_toggle_rejects_stale_tracker(self):
        engine = populated()
        engine.advance(1)
        with self.assertRaises(ValueError):
            engine.set_enabled(DEVICE, True)


class Sessions(unittest.TestCase):
    def test_old_audio_event_cannot_attach_after_reconnect(self):
        engine = populated()
        old = engine.epoch
        engine.disconnect()
        engine.connect(DEVICE)
        sink = Sink(DEVICE, "bluez_output.current", "100", "a2dp-sink", "idle")
        self.assertFalse(engine.observe_sink(old, sink))
        self.assertFalse(engine.snapshot()["audio_ready"])
        self.assertTrue(engine.observe_sink(engine.epoch, sink))
        self.assertEqual(engine.snapshot()["target"], sink.name)

    def test_wrong_device_is_rejected(self):
        engine = populated()
        self.assertFalse(engine.observe_sink(engine.epoch, Sink("OTHER", "HDMI", "1", "a2dp", "running")))

    def test_idle_and_suspended_sink_can_start_playback(self):
        for state in ("running", "idle", "suspended"):
            engine = populated()
            engine.observe_sink(engine.epoch, Sink(DEVICE, "bluez_output.test", "3", "a2dp-sink", state))
            self.assertTrue(engine.snapshot()["audio_ready"])

    def test_bad_profile_and_error_are_not_ready(self):
        for profile, state in [("headset-head-unit", "running"), ("a2dp-sink", "error")]:
            self.assertFalse(Sink(DEVICE, "x", "1", profile, state).usable)

    def test_budslink_and_tracker_delay_do_not_block_static_rendering(self):
        engine = Engine()
        engine.connect(DEVICE)
        engine.observe_sink(engine.epoch, Sink(DEVICE, "bluez_output.test", "3", "a2dp-sink", "idle"))
        engine.capability(engine.epoch, "renderer", True)
        engine.source_mode = "spatial"
        self.assertEqual(engine.snapshot()["render_mode"], "spatial")
        plan = engine.desired_output()
        self.assertIsNone(plan["tracking"])
        self.assertTrue(plan["node.dont-fallback"])
        self.assertTrue(plan["node.dont-reconnect"])

    def test_renderer_crash_leaves_audio_ready(self):
        engine = populated()
        engine.observe_sink(engine.epoch, Sink(DEVICE, "x", "2", "a2dp", "idle"))
        engine.capability(engine.epoch, "renderer", False)
        self.assertTrue(engine.snapshot()["audio_ready"])
        self.assertIsNone(engine.desired_output())

    def test_disconnect_preserves_optional_provider(self):
        engine = populated()
        engine.set_enabled("receiver:7C3A", True)
        engine.disconnect()
        self.assertEqual(len(engine.snapshot()["optional_trackers"]), 1)
        self.assertIsNone(engine.snapshot()["active_id"])
        self.assertNotIn(DEVICE, engine.trackers)

    def test_old_provider_generation_cannot_supply_packets_or_remove_new_tracker(self):
        engine = populated()
        new = engine.announce("receiver:7C3A", "7C3A", "optional")
        self.assertFalse(engine.sample("receiver:7C3A", 1, 90, [1, 0, 0, 0]))
        self.assertFalse(engine.remove("receiver:7C3A", 1))
        self.assertTrue(engine.sample("receiver:7C3A", new, 0, [1, 0, 0, 0]))


class Samples(unittest.TestCase):
    def test_invalid_samples_do_not_refresh_freshness(self):
        for value in ([0, 0, 0, 0], [float("nan"), 0, 0, 0], [float("inf"), 0, 0, 0],
                      [True, 0, 0, 0], [1, 2], ["1", 0, 0, 0]):
            engine = populated()
            engine.advance(1)
            self.assertFalse(engine.sample(DEVICE, 1, 2, value))
            self.assertIsNone(engine.snapshot()["earbud"])

    def test_duplicate_and_reordered_packets_do_not_refresh(self):
        engine = populated()
        engine.advance(1)
        for sequence in (0, 1):
            self.assertFalse(engine.sample(DEVICE, 1, sequence, [1, 0, 0, 0]))
        self.assertIsNone(engine.snapshot()["earbud"])

    def test_recenter_and_handoff_continuity(self):
        engine = populated()
        engine.set_enabled(DEVICE, True)
        engine.select()
        engine.sample(DEVICE, 1, 2, [math.sqrt(0.5), 0, 0, math.sqrt(0.5)])
        before = engine.snapshot()["orientation"]
        engine.sample("receiver:7C3A", 1, 2, [0, 1, 0, 0])
        engine.set_enabled("receiver:7C3A", True)
        after = engine.snapshot()["orientation"]
        self.assertAlmostEqual(abs(sum(a*b for a, b in zip(before, after))), 1)
        engine.recenter_active()
        self.assertEqual(engine.snapshot()["orientation"], [1, 0, 0, 0])

    def test_time_must_be_monotonic(self):
        engine = Engine()
        engine.advance(4)
        for invalid in (3, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                engine.advance(invalid)

    def test_normalization(self):
        self.assertEqual(Quaternion.parse([2, 0, 0, 0]).values(), (1, 0, 0, 0))


class Storage(unittest.TestCase):
    def test_atomic_round_trip_retains_only_preferences(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "prefs.json"
            prefs = Preferences({DEVICE: True}, {"id": False})
            prefs.save(path)
            self.assertEqual(Preferences.load(path), prefs)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(list(Path(root).iterdir())), 1)

    def test_invalid_config_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "prefs.json"
            for value in ('{"schema":2}', '{"schema":1,"optional":{"1":true,"2":true}}',
                          '{"schema":1,"earbuds":{"x":1}}'):
                path.write_text(value)
                with self.assertRaises(ValueError):
                    Preferences.load(path)
                self.assertEqual(path.read_text(), value)

    def test_backoff_is_bounded_and_resettable(self):
        retry = Backoff()
        self.assertEqual([retry.next_delay() for _ in range(9)], [.25, .5, 1, 2, 4, 5, 5, 5, 5])
        retry.reset()
        self.assertEqual(retry.next_delay(), .25)
