"""Exercise production TIDAL orchestration and an explicitly private D-Bus.

The provider/player are injected: these checks prove neither service entitlement
nor decoder output. Actual Atmos proof remains owned by the decoder audit.
"""
import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from spatial.core import Sink
from spatial.runtime import PlaybackError, Runtime
from spatial.settings import Settings
from spatial.tidal import PreparedTrack, TidalError
from tests.test_runtime import ADDRESS, FakeAudio

try:
    from dbus_next import DBusError
    from dbus_next.aio import MessageBus
    from spatial.desktop import BUS, PATH, export
    HAVE_DBUS = True
except ImportError:
    HAVE_DBUS = False


class FakeProvider:
    def __init__(self):
        self.prepared = []
        self.session_status = Mock(return_value={"state": "signed_in", "authenticated": True})
        self.login_start = Mock(return_value={"login_id": "request-one", "state": "pending",
            "verification_url": "https://link.tidal.com/private-user-code", "user_code": "PRIVATE-CODE",
            "expires_in": 300, "interval": 5, "error": ""})
        self.login_status = Mock(return_value=self.login_start.return_value)
        self.login_poll = Mock(return_value={"login_id": "request-one", "state": "pending",
                                            "expires_in": 290, "interval": 5, "error": ""})
        self.login_cancel = Mock(return_value={"state": "cancelled", "login_id": "request-one"})
        self.logout = Mock()
        self.search_catalogue = Mock(return_value={"tracks": [], "albums": [], "artists": [],
                                                  "playlists": [], "has_more": False})
        self.collection = Mock(return_value={"item": {"kind": "album", "id": "45"},
                                            "tracks": [], "albums": [], "has_more": False})
        self.library = Mock(return_value={"kind": "tracks", "items": [], "has_more": False})
        self.track_ids = Mock(return_value=["123", "124"])
        self.prepare = Mock(side_effect=self._prepare)

    def _prepare(self, identifier, require_atmos=True, *, require_lossless=False, quality=None):
        item = PreparedTrack("https://cdn.example/audio?token=private-stream-token", {
            "id": identifier, "title": "A track", "source": "TIDAL",
            "atmos_manifest": require_atmos, "renderer_confirmed_atmos": False})
        self.prepared.append(item)
        return item


class RuntimeSetup:
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.audio = FakeAudio()
        self.runtime = Runtime(Settings(readiness_timeout=1), audio=self.audio,
                               preferences_path=Path(self.temp.name) / "preferences.json")
        self.provider = FakeProvider()
        self.runtime._tidal = self.provider
        self.tasks = []
        self.releases = []
        self.runtime.engine.connect(ADDRESS)
        sink = Sink(ADDRESS, "bluez_output.fixture", "101", "a2dp-sink", "suspended", "ldac")
        self.runtime.engine.observe_sink(self.runtime.engine.epoch, sink)

    async def asyncTearDown(self):
        for event in self.releases:
            event.set()
        self.runtime.stop_event.set()
        self.runtime.notify()
        tasks = [*self.tasks, *self.runtime._background]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.runtime._workers:
            await asyncio.gather(*self.runtime._workers, return_exceptions=True)
        self.runtime._cleanup_prepared()
        self.temp.cleanup()

    def task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    def blocked(self, result):
        entered, release = asyncio.Event(), threading.Event()
        self.releases.append(release)
        loop = asyncio.get_running_loop()

        def request(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(3):
                raise AssertionError("test did not release its provider worker")
            return result

        return request, entered, release

    async def finish_launches(self):
        await asyncio.wait_for(asyncio.gather(*list(self.runtime._background)), 1)


class TidalOrchestration(RuntimeSetup, unittest.IsolatedAsyncioTestCase):
    async def test_mobile_quality_ceiling_stays_with_queue_and_preserves_settings(self):
        for quality in ("max", "cd", "aac320", "aac96", "auto"):
            with self.subTest(quality=quality):
                self.provider.prepare.reset_mock()
                response = await self.runtime.tidal_request("play", {
                    "reference": "tidal:album:45", "quality": quality})
                await self.finish_launches()
                await self.runtime.next()
                expected = "max" if quality == "auto" else quality
                self.assertEqual(response["quality"], expected)
                calls = self.provider.prepare.call_args_list
                self.assertEqual([call.kwargs["quality"] for call in calls], [expected, expected])
                self.assertTrue(all(call.kwargs["require_atmos"] is False for call in calls))
                self.assertTrue(all(not call.kwargs.get("require_lossless") for call in calls))
                self.assertTrue(self.runtime.settings.tidal_require_atmos)
                self.assertFalse(self.runtime.preferences_path.exists())
        await self.runtime.tidal_request("play", {"reference": "123", "quality": "atmos"})
        await self.finish_launches()
        self.assertNotIn("quality", self.provider.prepare.call_args_list[-1].kwargs)
        self.assertTrue(self.provider.prepare.call_args_list[-1].kwargs["require_atmos"])

    async def test_playlist_folder_dispatch_is_scoped_and_paged(self):
        folder = "11111111-2222-3333-4444-555555555555"
        await self.runtime.tidal_request("library", {"kind": "playlists", "folder_id": folder,
                                                    "limit": 20, "offset": 40})
        self.provider.library.assert_called_once_with("playlists", folder_id=folder, limit=20, offset=40)
        self.provider.library.reset_mock()
        for arguments in ({"kind": "tracks", "folder_id": folder},
                          {"kind": "playlists", "folder_id": [folder]}):
            with self.subTest(arguments=arguments), self.assertRaises(PlaybackError):
                await self.runtime.tidal_request("library", arguments)
        self.provider.library.assert_not_called()

    async def test_explicit_max_overrides_cli_atmos_default_without_leaking_to_local_files(self):
        await self.runtime.play("tidal:track:123", quality="max")
        self.provider.prepare.assert_called_once_with("123", require_atmos=False, quality="max")
        self.assertTrue(self.runtime.settings.tidal_require_atmos)
        await self.runtime.play("/tmp/ordinary-test-media.wav")
        self.assertIsNone(self.runtime._queue_quality)

    async def test_login_recovery_is_local_and_never_replaces_public_account_state(self):
        before = self.runtime.state()["tidal"].copy()
        self.runtime._workers.update(object() for _ in range(5))
        try:
            result = await self.runtime.tidal_request("login_status", {})
        finally:
            self.runtime._workers.clear()
        self.assertEqual(result["user_code"], "PRIVATE-CODE")
        self.provider.login_status.assert_called_once_with()
        self.provider.login_start.assert_not_called()
        self.provider.login_poll.assert_not_called()
        self.assertEqual(self.runtime.state()["tidal"], before)
        with self.assertRaises(PlaybackError):
            await self.runtime.tidal_request("login_status", {"login_id": "not-supported"})

    async def test_dispatch_uses_bounded_provider_pages_and_explicit_quality(self):
        await self.runtime.tidal_request("status", {"refresh": True})
        self.provider.session_status.assert_called_once_with(refresh=True)
        await self.runtime.tidal_request("search", {"query": "music", "limit": 20, "offset": 20})
        self.provider.search_catalogue.assert_called_once_with("music", limit=20, offset=20)
        await self.runtime.tidal_request("collection", {"kind": "artist", "id": "45", "offset": 50})
        self.provider.collection.assert_called_once_with("artist", "45", limit=50, offset=50)
        await self.runtime.tidal_request("library", {"kind": "playlists"})
        self.provider.library.assert_called_once_with("playlists", limit=50, offset=0)
        for action, payload in (("search", {"query": "music", "limit": 51}),
                                ("status", {"refresh": "true"}),
                                ("logout", {"access_token": "private"}),
                                ("play", {"reference": "123", "quality": "automatic"})):
            with self.subTest(action=action), self.assertRaises(PlaybackError):
                await self.runtime.tidal_request(action, payload)

    async def test_numeric_selection_is_canonicalized_as_tidal_not_local_file(self):
        response = await self.runtime.tidal_request("play", {"reference": "123", "quality": "atmos"})
        self.assertEqual(response["state"], "loading")
        await self.finish_launches()
        self.provider.track_ids.assert_called_once_with("tidal:track:123")
        self.assertTrue(self.audio.starts[0][2]["require_spatial"])

    async def test_quality_is_scoped_to_whole_queue_and_does_not_change_preferences(self):
        await self.runtime.tidal_request("play", {"reference": "tidal:album:45", "quality": "lossless"})
        await self.finish_launches()
        await self.runtime.next()
        self.assertEqual([call.kwargs["require_atmos"] for call in self.provider.prepare.call_args_list], [False, False])
        self.assertTrue(all(call.kwargs.get("require_lossless") is True for call in self.provider.prepare.call_args_list))
        self.assertTrue(self.runtime.settings.tidal_require_atmos)
        self.assertFalse(self.audio.starts[0][2]["require_spatial"])
        self.assertFalse(self.audio.starts[1][2]["require_spatial"])
        await self.runtime.tidal_request("play", {"reference": "tidal:album:46", "quality": "atmos"})
        await self.finish_launches()
        await self.runtime.next()
        self.assertEqual([call.kwargs["require_atmos"] for call in self.provider.prepare.call_args_list], [False, False, True, True])
        self.assertFalse(self.provider.prepare.call_args_list[-1].kwargs.get("require_lossless", False))
        self.assertTrue(self.audio.starts[-1][2]["require_spatial"])
        self.assertFalse(self.runtime.metadata["renderer_confirmed_atmos"])
        self.assertEqual(self.runtime.state()["tidal"]["default_quality"], "atmos")

    async def test_returned_atmos_manifest_always_requires_decoder_even_in_lossless_mode(self):
        self.provider.prepare.side_effect = lambda identifier, **kwargs: self.provider._prepare(identifier, True)
        await self.runtime.tidal_request("play", {"reference": "tidal:track:123", "quality": "lossless"})
        await self.finish_launches()
        self.assertTrue(self.audio.starts[0][2]["require_spatial"])
        self.assertFalse(self.runtime.metadata["renderer_confirmed_atmos"])

    async def test_new_cli_selection_uses_configured_default_after_lossless_ui_queue(self):
        await self.runtime.tidal_request("play", {"reference": "tidal:album:45", "quality": "lossless"})
        await self.finish_launches()
        await self.runtime.play("tidal:track:333")
        self.assertEqual([call.kwargs["require_atmos"] for call in self.provider.prepare.call_args_list], [False, True])
        self.assertFalse(self.provider.prepare.call_args_list[-1].kwargs.get("require_lossless", False))
        self.assertTrue(self.audio.starts[-1][2]["require_spatial"])

    async def test_atmos_rejection_does_not_retry_as_stereo_or_launch_player(self):
        self.provider.prepare.side_effect = TidalError("TIDAL returned no Atmos stream; stereo was not substituted")
        await self.runtime.tidal_request("play", {"reference": "tidal:track:123", "quality": "atmos"})
        await self.finish_launches()
        self.provider.prepare.assert_called_once_with("123", require_atmos=True)
        self.assertEqual(self.audio.starts, [])
        self.assertIn("no Atmos", self.runtime.playback_error)

    async def test_logout_cancels_collection_expansion_before_first_queue_is_assigned(self):
        block, entered, release = self.blocked(["123"])
        self.provider.track_ids.side_effect = block
        await self.runtime.tidal_request("play", {"reference": "tidal:album:45", "quality": "atmos"})
        await asyncio.wait_for(entered.wait(), 1)
        self.assertTrue(self.runtime.loading)
        self.assertEqual(self.runtime.queue, [])
        await self.runtime.tidal_request("logout", {})
        release.set()
        await self.finish_launches()
        self.assertEqual(self.audio.starts, [])
        self.assertEqual(self.runtime.queue, [])
        self.assertEqual(self.runtime.metadata, {})
        self.assertFalse(self.runtime.loading)
        self.provider.login_cancel.assert_called_once()

    async def test_logout_clears_owned_queue_and_does_not_allow_resume(self):
        await self.runtime.tidal_request("play", {"reference": "tidal:album:45", "quality": "atmos"})
        await self.finish_launches()
        await self.runtime.tidal_request("logout", {})
        self.assertFalse(self.audio.status()["running"])
        self.assertEqual(self.runtime.queue, [])
        self.assertEqual(self.runtime.metadata, {})
        with self.assertRaises(PlaybackError):
            await self.runtime.resume()

    async def test_logout_cleans_a_prepared_stream_returned_by_late_worker(self):
        prepared = self.provider._prepare("123", True)
        prepared.cleanup = Mock()
        block, entered, release = self.blocked(prepared)
        self.provider.prepare.side_effect = block
        await self.runtime.tidal_request("play", {"reference": "tidal:track:123", "quality": "atmos"})
        await asyncio.wait_for(entered.wait(), 1)
        await self.runtime.tidal_request("logout", {})
        release.set()
        await self.finish_launches()
        prepared.cleanup.assert_called_once()
        self.assertEqual(self.audio.starts, [])
        self.assertEqual(self.runtime.queue, [])

    async def test_old_status_completion_cannot_replace_newer_logout(self):
        block, entered, release = self.blocked({"state": "signed_in", "authenticated": True})
        self.provider.session_status.side_effect = block
        old = self.task(self.runtime.tidal_request("status", {"refresh": True}))
        await asyncio.wait_for(entered.wait(), 1)
        await self.runtime.tidal_request("logout", {})
        release.set()
        await old
        self.assertEqual(self.runtime.state()["tidal"]["state"], "signed_out")
        self.assertFalse(self.runtime.state()["tidal"]["authenticated"])

    async def test_old_login_completion_cannot_replace_newer_cancel_or_publish_codes(self):
        block, entered, release = self.blocked(self.provider.login_start.return_value)
        self.provider.login_start.side_effect = block
        old = self.task(self.runtime.tidal_request("login_start", {}))
        await asyncio.wait_for(entered.wait(), 1)
        await self.runtime.tidal_request("login_cancel", {"login_id": "request-one"})
        release.set()
        await old
        state = self.runtime.state()
        self.assertEqual(state["tidal"]["state"], "cancelled")
        self.assertNotIn("PRIVATE-CODE", json.dumps(state))
        self.assertNotIn("private-user-code", json.dumps(state))

    async def test_rejected_stale_poll_does_not_hide_successful_inflight_logout(self):
        await self.runtime.tidal_request("status", {})
        block, entered, release = self.blocked(None)
        self.provider.logout.side_effect = block
        logout = self.task(self.runtime.tidal_request("logout", {}))
        await asyncio.wait_for(entered.wait(), 1)
        self.provider.login_poll.side_effect = TidalError("TIDAL sign-in request is no longer active")
        with self.assertRaises(TidalError):
            await self.runtime.tidal_request("login_poll", {"login_id": "expired-ui-request"})
        release.set()
        await logout
        self.assertEqual(self.runtime.state()["tidal"]["state"], "signed_out")
        self.assertFalse(self.runtime.state()["tidal"]["authenticated"])

    async def test_status_read_during_logout_cannot_supersede_its_terminal_state(self):
        block, entered, release = self.blocked(None)
        self.provider.logout.side_effect = block
        logout = self.task(self.runtime.tidal_request("logout", {}))
        await asyncio.wait_for(entered.wait(), 1)
        await self.runtime.tidal_request("status", {})
        release.set()
        await logout
        self.assertEqual(self.runtime.state()["tidal"]["state"], "signed_out")

    async def test_old_status_cannot_overwrite_a_successful_login_poll(self):
        block, entered, release = self.blocked({"state": "signed_out", "authenticated": False})
        self.provider.session_status.side_effect = block
        old = self.task(self.runtime.tidal_request("status", {}))
        await asyncio.wait_for(entered.wait(), 1)
        self.provider.login_poll.return_value = {"state": "signed_in", "login_id": "request-one"}
        await self.runtime.tidal_request("login_poll", {"login_id": "request-one"})
        release.set()
        await old
        self.assertEqual(self.runtime.state()["tidal"]["state"], "signed_in")
        self.assertTrue(self.runtime.state()["tidal"]["authenticated"])

    async def test_new_login_is_rejected_until_earlier_logout_finishes(self):
        block, entered, release = self.blocked(None)
        self.provider.logout.side_effect = block
        logout = self.task(self.runtime.tidal_request("logout", {}))
        await asyncio.wait_for(entered.wait(), 1)
        with self.assertRaisesRegex(PlaybackError, "sign-out is still finishing"):
            await self.runtime.tidal_request("login_start", {})
        self.provider.login_start.assert_not_called()
        release.set()
        await logout
        self.assertEqual((await self.runtime.tidal_request("login_start", {}))["state"], "pending")

    async def test_abandoned_logout_keeps_barrier_and_publishes_its_actual_completion(self):
        await self.runtime.tidal_request("status", {})
        block, entered, release = self.blocked(None)
        self.provider.logout.side_effect = block
        logout = self.task(self.runtime.tidal_request("logout", {}))
        await asyncio.wait_for(entered.wait(), 1)
        logout.cancel()
        await asyncio.gather(logout, return_exceptions=True)
        with self.assertRaisesRegex(PlaybackError, "sign-out is still finishing"):
            await self.runtime.tidal_request("login_start", {})
        self.provider.login_start.assert_not_called()
        await self.runtime.tidal_request("status", {})
        release.set()
        await asyncio.gather(*list(self.runtime._workers))
        self.assertEqual(self.runtime.state()["tidal"]["state"], "signed_out")
        self.assertFalse(self.runtime.state()["tidal"]["authenticated"])
        self.assertEqual((await self.runtime.tidal_request("login_start", {}))["state"], "pending")

    async def test_late_login_cancel_cannot_supersede_pending_account_logout(self):
        await self.runtime.tidal_request("status", {})
        block, entered, release = self.blocked(None)
        self.provider.logout.side_effect = block
        logout = self.task(self.runtime.tidal_request("logout", {}))
        await asyncio.wait_for(entered.wait(), 1)
        await self.runtime.tidal_request("login_cancel", {})
        release.set()
        await logout
        self.assertEqual(self.runtime.state()["tidal"]["state"], "signed_out")
        self.assertFalse(self.runtime.state()["tidal"]["authenticated"])

    async def test_slow_catalogue_network_work_does_not_block_event_loop(self):
        block, entered, release = self.blocked({"tracks": []})
        self.provider.search_catalogue.side_effect = block
        request = self.task(self.runtime.tidal_request("search", {"query": "music"}))
        await asyncio.wait_for(entered.wait(), 1)
        heartbeat = asyncio.Event()
        asyncio.get_running_loop().call_soon(heartbeat.set)
        await asyncio.wait_for(heartbeat.wait(), 0.2)
        self.assertFalse(request.done())
        release.set()
        await request

    async def test_worker_limit_survives_caller_cancel_and_reserves_logout_capacity(self):
        block, entered, release = self.blocked({"tracks": []})
        self.provider.search_catalogue.side_effect = block
        requests = [self.task(self.runtime.tidal_request("search", {"query": str(index)}))
                    for index in range(4)]
        await asyncio.wait_for(entered.wait(), 1)
        # All four task bodies enqueue before this callback reaches the loop.
        await asyncio.sleep(0)
        self.assertEqual(len(self.runtime._workers), 4)
        with self.assertRaisesRegex(PlaybackError, "busy|progress|requests"):
            await self.runtime.tidal_request("search", {"query": "one too many"})
        for request in requests:
            request.cancel()
        await asyncio.gather(*requests, return_exceptions=True)
        self.assertEqual(len(self.runtime._workers), 4)
        with self.assertRaises(PlaybackError):
            await self.runtime.tidal_request("status", {})
        # The four abandoned network workers retain capacity until they exit;
        # local cancellation and sign-out still have their dedicated path.
        await asyncio.wait_for(self.runtime.tidal_request("login_cancel", {}), 0.2)
        await asyncio.wait_for(self.runtime.tidal_request("logout", {}), 0.5)
        self.provider.logout.assert_called_once()
        release.set()
        await asyncio.gather(*list(self.runtime._workers))
        self.provider.search_catalogue.side_effect = None
        await self.runtime.tidal_request("search", {"query": "capacity returned"})

    async def test_control_worker_reservation_is_bounded_too(self):
        block, entered, release = self.blocked(None)
        self.provider.logout.side_effect = block
        requests = [self.task(self.runtime.tidal_request("logout", {})) for _ in range(5)]
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0)
        self.assertEqual(len(self.runtime._workers), 5)
        with self.assertRaises(PlaybackError):
            await self.runtime.tidal_request("logout", {})
        release.set()
        await asyncio.gather(*requests)
        self.assertEqual(self.runtime.state()["tidal"]["state"], "signed_out")


@unittest.skipUnless(HAVE_DBUS and os.environ.get("DBUS_SESSION_BUS_ADDRESS")
                     and os.environ.get("SPATIAL_TEST_DBUS") == "1", "needs explicitly isolated D-Bus session")
class TidalPrivateBus(RuntimeSetup, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.server, self.control = await export(self.runtime.engine, extra_state=self.runtime.state,
                                                 on_tidal_request=self.runtime.tidal_request)
        self.client = await MessageBus().connect()
        intro = await self.client.introspect(BUS, PATH)
        self.api = self.client.get_proxy_object(BUS, PATH, intro).get_interface(BUS)

    async def asyncTearDown(self):
        self.client.disconnect()
        self.server.disconnect()
        await asyncio.gather(self.client.wait_for_disconnect(), self.server.wait_for_disconnect())
        await super().asyncTearDown()

    async def request(self, action, arguments):
        return json.loads(await self.api.call_tidal_request(action, json.dumps(arguments)))

    async def test_device_auth_reply_crosses_real_bus_without_codes_in_state(self):
        result = await self.request("login_start", {})
        self.assertEqual(result["verification_url"], "https://link.tidal.com/private-user-code")
        recovered = await self.request("login_status", {})
        self.assertEqual(recovered, result)
        self.provider.login_status.assert_called_once_with()
        self.control.publish()
        public = await self.api.get_state()
        self.assertNotIn("PRIVATE-CODE", public)
        self.assertNotIn("private-user-code", public)
        self.assertNotIn("request-one", public)
        self.assertEqual(json.loads(public)["runtime"]["tidal"]["state"], "pending")
        await self.request("login_poll", {"login_id": "request-one"})
        self.provider.login_poll.assert_called_once_with("request-one")
        await self.request("login_cancel", {"login_id": "request-one"})
        self.provider.login_cancel.assert_called_once_with("request-one")

    async def test_catalogue_calls_use_real_bus_and_safe_service_errors(self):
        await self.request("search", {"query": "an artist"})
        self.provider.search_catalogue.assert_called_once_with("an artist", limit=20, offset=0)
        self.provider.search_catalogue.side_effect = RuntimeError("https://cdn.example/?token=private-token")
        with self.assertRaises(DBusError) as failure:
            await self.request("search", {"query": "music"})
        self.assertNotIn("private-token", str(failure.exception))
        self.assertIn("RuntimeError", str(failure.exception))

    async def test_malformed_or_oversized_requests_are_rejected_before_provider(self):
        for action, payload in (("search", "[]"), ("search", "{bad json"),
                                ("search", json.dumps({"query": "x" * 9000})),
                                ("delete_account", "{}"), ("status", '{"token":"private"}')):
            with self.subTest(action=action), self.assertRaises(DBusError):
                await self.api.call_tidal_request(action, payload)
        self.provider.search_catalogue.assert_not_called()
        self.provider.session_status.assert_not_called()

    async def test_bus_stays_responsive_while_catalogue_worker_waits(self):
        block, entered, release = self.blocked({"tracks": []})
        self.provider.search_catalogue.side_effect = block
        pending = self.task(self.request("search", {"query": "music"}))
        await asyncio.wait_for(entered.wait(), 1)
        self.assertIsInstance(await asyncio.wait_for(self.api.get_state(), 0.5), str)
        self.assertFalse(pending.done())
        release.set()
        await pending
