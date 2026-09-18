"""Run inside dbus-run-session; skipped if desktop dependency/session is absent."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spatial.core import Engine

try:
    from dbus_next.aio import MessageBus
    from spatial.desktop import BUS, PATH, export
    HAVE_DBUS = True
except ImportError:
    HAVE_DBUS = False


@unittest.skipUnless(HAVE_DBUS and os.environ.get("DBUS_SESSION_BUS_ADDRESS")
                     and os.environ.get("SPATIAL_TEST_DBUS") == "1", "needs explicitly isolated D-Bus session")
class Desktop(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine()
        self.engine.connect("AA:BB:CC:DD:EE:FF")
        generation = self.engine.announce("tracker:1", "Receiver-Reported-ID", "optional")
        self.engine.sample("tracker:1", generation, 1, [1, 0, 0, 0])
        self.path = Path(self.temp.name) / "preferences.json"
        self.playback_events = []
        async def play(media):
            self.playback_events.append(("play", media))
        async def stop():
            self.playback_events.append(("stop",))
        self.live_events = []
        async def live_start(serial):
            self.live_events.append(("start", serial))
        async def live_stop():
            self.live_events.append(("stop",))
        self.server, self.control = await export(self.engine, self.path, on_play=play, on_stop=stop,
                                                on_live_start=live_start, on_live_stop=live_stop)
        self.client = await MessageBus().connect()
        intro = await self.client.introspect(BUS, PATH)
        proxy = self.client.get_proxy_object(BUS, PATH, intro)
        self.api = proxy.get_interface(BUS)

    async def asyncTearDown(self):
        self.client.disconnect()
        self.server.disconnect()
        await asyncio.gather(self.client.wait_for_disconnect(), self.server.wait_for_disconnect())
        self.temp.cleanup()

    async def test_real_bus_toggle_and_persistence(self):
        await self.api.call_set_tracker_enabled("tracker:1", True)
        state = json.loads(await self.api.get_state())
        self.assertEqual(state["active_id"], "tracker:1")
        self.assertEqual(state["active_name"], "Receiver-Reported-ID")
        self.assertTrue(json.loads(self.path.read_text())["optional"]["tracker:1"])
        await self.api.call_recenter()

    async def test_failure_to_save_rolls_back(self):
        from dbus_next import DBusError
        with patch.object(type(self.engine.preferences), "save", side_effect=OSError("disk full")):
            with self.assertRaises(DBusError):
                await self.api.call_set_tracker_enabled("tracker:1", True)
        self.assertIsNone(self.engine.snapshot()["active_id"])
        self.assertFalse(self.path.exists())

    async def test_duplicate_service_is_rejected(self):
        with self.assertRaises(RuntimeError):
            await export(Engine())

    async def test_state_signal_removes_stale_row(self):
        changed = asyncio.Event()
        proxy = self.client.get_proxy_object(BUS, PATH, await self.client.introspect(BUS, PATH))
        properties = proxy.get_interface("org.freedesktop.DBus.Properties")
        properties.on_properties_changed(lambda iface, props, invalid: changed.set())
        self.engine.advance(1)
        self.control.publish()
        await asyncio.wait_for(changed.wait(), 1)
        self.assertEqual(json.loads(await self.api.get_state())["optional_trackers"], [])

    async def test_playback_commands_cross_real_bus(self):
        await self.api.call_play("/tmp/local media.mkv")
        await self.api.call_stop()
        self.assertEqual(self.playback_events, [("play", "/tmp/local media.mkv"), ("stop",)])

    async def test_invalid_playback_request_is_rejected(self):
        from dbus_next import DBusError
        with self.assertRaises(DBusError):
            await self.api.call_play("")
        self.assertEqual(self.playback_events, [])

    async def test_live_audio_commands_cross_real_bus(self):
        await self.api.call_start_live("9007199254740993")
        await self.api.call_stop_live()
        self.assertEqual(self.live_events, [("start", "9007199254740993"), ("stop",)])
