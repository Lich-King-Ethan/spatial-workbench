"""MPRIS property/command logic without a bus; native bus tests are separate."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

try:
    from dbus_next import DBusError
    from spatial.mpris import Player
    HAVE_DBUS = True
except ImportError:
    HAVE_DBUS = False


@unittest.skipUnless(HAVE_DBUS, "requires dbus-next")
class MediaControls(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = {"running": True, "paused": False, "loaded": True,
                      "end_reason": None, "volume": 80, "position": 1.25,
                      "duration": 120, "seekable": True}
        self.runtime = SimpleNamespace(
            audio=SimpleNamespace(status=lambda: dict(self.state)),
            queue=["first", "second"], queue_index=0,
            track_path="/org/mpris/MediaPlayer2/Track/1",
            metadata={"title": "Track", "artist": "Artist"},
            resume=AsyncMock(), pause=AsyncMock(), seek=AsyncMock(),
            set_volume=AsyncMock(), provider_status=Mock())
        self.player = Player(self.runtime)
        self.player.emit_properties_changed = Mock()

    async def test_eof_is_stopped_despite_live_idle_player_process(self):
        self.state.update(end_reason="eof", loaded=False)
        self.assertEqual(self.player.PlaybackStatus, "Stopped")
        self.assertFalse(self.player.CanPause)
        await Player.PlayPause.__wrapped__(self.player)
        self.runtime.resume.assert_awaited_once()
        self.runtime.pause.assert_not_awaited()

    async def test_active_and_paused_playback_are_reported_distinctly(self):
        self.assertEqual(self.player.PlaybackStatus, "Playing")
        self.state["paused"] = True
        self.assertEqual(self.player.PlaybackStatus, "Paused")
        await Player.PlayPause.__wrapped__(self.player)
        self.runtime.pause.assert_awaited_once_with(False)

    async def test_volume_updates_publish_to_other_desktop_clients(self):
        self.player.publish()
        self.player.emit_properties_changed.reset_mock()
        self.state["volume"] = 35
        self.player.publish()
        self.player.emit_properties_changed.assert_called_once()
        changed = self.player.emit_properties_changed.call_args.args[0]
        self.assertEqual(changed["Volume"], 0.35)

    async def test_volume_setter_forwards_normalized_value_and_rejects_nonfinite(self):
        self.player.Volume = 0.35
        await asyncio.sleep(0)
        self.runtime.set_volume.assert_awaited_once_with(0.35)
        for value in (float("nan"), float("inf"), -0.1, 1.1):
            with self.subTest(value=value), self.assertRaises(DBusError):
                self.player.Volume = value

    async def test_stale_track_position_request_does_not_seek_current_track(self):
        await Player.SetPosition.__wrapped__(self.player, "/org/mpris/MediaPlayer2/Track/old", 10_000_000)
        self.runtime.seek.assert_not_awaited()
        await Player.SetPosition.__wrapped__(self.player, self.runtime.track_path, 10_000_000)
        self.runtime.seek.assert_awaited_once_with(10.0, True)

    async def test_metadata_uses_dbus_types_and_microsecond_units(self):
        metadata = self.player.Metadata
        self.assertEqual(metadata["mpris:trackid"].signature, "o")
        self.assertEqual(metadata["xesam:artist"].value, ["Artist"])
        self.assertEqual(metadata["mpris:length"].value, 120_000_000)
        self.assertEqual(self.player.Position, 1_250_000)
        self.assertNotIn("xesam:url", metadata)

    async def test_unexpected_provider_exception_is_not_broadcast_with_source_credentials(self):
        self.runtime.resume.side_effect = RuntimeError("https://source.example/audio?token=private-secret")
        with self.assertRaises(DBusError) as error:
            await Player.Play.__wrapped__(self.player)
        self.assertNotIn("private-secret", str(error.exception))
