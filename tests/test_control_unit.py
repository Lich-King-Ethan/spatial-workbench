import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spatial.core import Engine

try:
    from dbus_next import DBusError
    from spatial.desktop import Control
    HAVE_DBUS = True
except ImportError:
    HAVE_DBUS = False


@unittest.skipUnless(HAVE_DBUS, "requires dbus-next")
class ControlUnit(unittest.TestCase):
    def make(self, path):
        engine = Engine()
        engine.connect("AA:BB:CC:DD:EE:FF")
        generation = engine.announce("id:1", "ID:1", "optional")
        engine.sample("id:1", generation, 0, [1, 0, 0, 0])
        return Control(engine, path)

    def test_setting_is_persisted_before_state_publish(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "prefs.json"
            control = self.make(path)
            control.SetTrackerEnabled("id:1", True)
            self.assertTrue(json.loads(path.read_text())["optional"]["id:1"])
            self.assertEqual(json.loads(control.State)["active_id"], "id:1")

    def test_failed_write_rolls_back(self):
        with tempfile.TemporaryDirectory() as root:
            control = self.make(Path(root) / "prefs.json")
            with patch.object(type(control.engine.preferences), "save", side_effect=OSError("disk full")):
                with self.assertRaises(DBusError):
                    control.SetTrackerEnabled("id:1", True)
            self.assertIsNone(control.engine.select())
            self.assertIsNone(json.loads(control.State)["active_id"])

    def test_state_does_not_broadcast_high_rate_orientation(self):
        control = self.make(None)
        self.assertNotIn("orientation", json.loads(control.State))


@unittest.skipUnless(HAVE_DBUS, "requires dbus-next")
class PlaybackControl(unittest.IsolatedAsyncioTestCase):
    async def test_async_playback_callbacks_are_awaited_and_published(self):
        events = []
        async def play(media):
            events.append(("play", media))
        async def stop():
            events.append(("stop",))
        control = Control(Engine(), on_play=play, on_stop=stop,
                          extra_state=lambda: {"playing": len(events) == 1})
        # dbus-next stores the coroutine as the decorated method's wrapped
        # function; the public wrapper is only a D-Bus registration marker.
        await Control.Play.__wrapped__(control, "/tmp/actual media.mkv")
        self.assertTrue(json.loads(control.State)["runtime"]["playing"])
        await Control.Stop.__wrapped__(control)
        self.assertEqual(events, [("play", "/tmp/actual media.mkv"), ("stop",)])
        self.assertFalse(json.loads(control.State)["runtime"]["playing"])

    async def test_invalid_media_never_reaches_playback_callback(self):
        from unittest.mock import AsyncMock
        play = AsyncMock()
        control = Control(Engine(), on_play=play)
        for media in ("", "bad\x00name", "x" * 16385):
            with self.assertRaises(DBusError):
                await Control.Play.__wrapped__(control, media)
        play.assert_not_called()

    async def test_absent_or_failed_playback_reports_dbus_error(self):
        control = Control(Engine())
        with self.assertRaises(DBusError):
            await Control.Play.__wrapped__(control, "/tmp/media.mkv")
        def reject(media):
            from spatial.errors import PublicError
            raise PublicError("renderer unavailable")
        control.on_play = reject
        with self.assertRaises(DBusError) as error:
            await Control.Play.__wrapped__(control, "/tmp/media.mkv")
        self.assertIn("renderer unavailable", str(error.exception))


@unittest.skipUnless(HAVE_DBUS, "requires dbus-next")
class LiveAudioControl(unittest.IsolatedAsyncioTestCase):
    async def test_live_callbacks_preserve_exact_serial_and_publish_state(self):
        events = []
        async def start(serial):
            events.append(("start", serial))
        async def stop():
            events.append(("stop",))
        control = Control(Engine(), on_live_start=start, on_live_stop=stop,
                          extra_state=lambda: {"live": {"running": len(events) == 1}})
        await Control.StartLive.__wrapped__(control, "9007199254740993")
        self.assertTrue(json.loads(control.State)["runtime"]["live"]["running"])
        await Control.StopLive.__wrapped__(control)
        self.assertEqual(events, [("start", "9007199254740993"), ("stop",)])
        self.assertFalse(json.loads(control.State)["runtime"]["live"]["running"])

    async def test_invalid_or_missing_identity_never_starts_capture(self):
        from unittest.mock import AsyncMock
        start = AsyncMock()
        control = Control(Engine(), on_live_start=start)
        for serial in ("", "0", "-1", "12.5", "１２", "9;rm", "18446744073709551616", "9" * 21):
            with self.assertRaises(DBusError):
                await Control.StartLive.__wrapped__(control, serial)
        start.assert_not_called()

    async def test_unavailable_and_failed_live_provider_are_dbus_errors(self):
        control = Control(Engine())
        with self.assertRaises(DBusError):
            await Control.StartLive.__wrapped__(control, "123")
        with self.assertRaises(DBusError):
            await Control.StopLive.__wrapped__(control)
        async def reject(serial):
            from spatial.errors import PublicError
            raise PublicError("The selected application's audio stream disappeared")
        control.on_live_start = reject
        with self.assertRaises(DBusError) as error:
            await Control.StartLive.__wrapped__(control, "123")
        self.assertIn("disappeared", str(error.exception))


@unittest.skipUnless(HAVE_DBUS, "requires dbus-next")
class OwnedPlaybackControl(unittest.IsolatedAsyncioTestCase):
    async def test_mismatched_process_never_stops_current_playback(self):
        active = {"pid": 789}
        async def stop_if(pid):
            if pid != active["pid"]:
                return False
            active["pid"] = None
            return True
        control = Control(Engine(), on_stop_if_process=stop_if)
        self.assertFalse(await Control.StopIfProcess.__wrapped__(control, 123))
        self.assertEqual(active["pid"], 789)
        self.assertTrue(await Control.StopIfProcess.__wrapped__(control, 789))
        self.assertIsNone(active["pid"])

    async def test_idle_playback_request_returns_the_atomic_provider_decision(self):
        calls = []
        async def play_if_idle(media):
            if calls:
                return False
            calls.append(media)
            return True
        control = Control(Engine(), on_play_if_idle=play_if_idle)
        self.assertTrue(await Control.PlayIfIdle.__wrapped__(control, "/tmp/test-tone.wav"))
        self.assertFalse(await Control.PlayIfIdle.__wrapped__(control, "/tmp/other-tone.wav"))
        self.assertEqual(calls, ["/tmp/test-tone.wav"])
        self.assertFalse(await Control.PlayIfIdle.__wrapped__(Control(Engine()), "/tmp/test-tone.wav"))

    async def test_missing_ownership_callback_never_claims_a_stop(self):
        control = Control(Engine())
        self.assertFalse(await Control.StopIfProcess.__wrapped__(control, 123))
        self.assertFalse(await Control.StopIfProcess.__wrapped__(control, 0))

    async def test_unclassified_errors_do_not_expose_signed_urls(self):
        async def fail(media):
            raise ValueError("https://media.example/file?token=private-token")
        control = Control(Engine(), on_play=fail)
        with self.assertRaises(DBusError) as error:
            await Control.Play.__wrapped__(control, "/tmp/file.flac")
        self.assertNotIn("private-token", str(error.exception))
        self.assertNotIn("media.example", str(error.exception))


@unittest.skipUnless(HAVE_DBUS, "requires dbus-next")
class TestPlaybackOwnership(unittest.IsolatedAsyncioTestCase):
    async def test_token_return_preserves_integer_and_explicit_replace_policy(self):
        calls = []
        async def begin(media, replace):
            calls.append((media, replace))
            return 9007199254740993 if replace else 0
        control = Control(Engine(), on_begin_test_playback=begin)
        self.assertEqual(await Control.BeginTestPlayback.__wrapped__(control, "/tmp/tone.wav", False), 0)
        self.assertEqual(await Control.BeginTestPlayback.__wrapped__(control, "/tmp/tone.wav", True), 9007199254740993)
        self.assertEqual(calls, [("/tmp/tone.wav", False), ("/tmp/tone.wav", True)])

    async def test_request_cleanup_does_not_stop_a_newer_user_request(self):
        current = {"token": 42, "stopped": False}
        async def stop(token):
            if token != current["token"]:
                return False
            current["stopped"] = True
            return True
        control = Control(Engine(), on_stop_if_request=stop)
        self.assertFalse(await Control.StopIfRequest.__wrapped__(control, 41))
        self.assertFalse(current["stopped"])
        self.assertTrue(await Control.StopIfRequest.__wrapped__(control, 42))
        self.assertTrue(current["stopped"])
        self.assertFalse(await Control.StopIfRequest.__wrapped__(Control(Engine()), 42))

    async def test_invalid_or_unconfigured_test_playback_never_reports_acceptance(self):
        with self.assertRaises(DBusError):
            await Control.BeginTestPlayback.__wrapped__(Control(Engine()), "/tmp/tone.wav", False)
        control = Control(Engine(), on_begin_test_playback=lambda *_: True)
        with self.assertRaises(DBusError):
            await Control.BeginTestPlayback.__wrapped__(control, "/tmp/tone.wav", False)
        with self.assertRaises(DBusError):
            await Control.BeginTestPlayback.__wrapped__(control, "/tmp/tone.wav", 1)
        self.assertFalse(await Control.StopIfRequest.__wrapped__(control, 0))
