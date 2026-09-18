"""Exercise production orchestration with an injected player; no hardware claims.

The injected player records requested operations. It does not simulate a decoder,
PipeWire, D-Bus, or a physical device, and these tests do not validate those layers.
"""
import asyncio
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from spatial.core import Sink
from spatial.runtime import Runtime
from spatial.settings import Settings


ADDRESS = "AA:BB:CC:DD:EE:FF"


class FakeAudio:
    def __init__(self):
        self.data = {"running": False, "renderer_ready": False, "source_mode": "none",
                     "end_reason": None, "paused": False, "loaded": False}
        self.starts = []
        self.stops = []
        self.sinks = []
        self.poses = []
        self.sink = None
        self.started = asyncio.Queue()
        self.pauses = []

    def status(self):
        return dict(self.data)

    async def start(self, media, sink, **options):
        self.sink = sink
        self.starts.append((media, sink, options))
        self.data.update(running=True, loaded=True, renderer_ready=True,
                         source_mode="spatial", end_reason=None, paused=False)
        self.started.put_nowait(media)

    async def stop(self, reason="stopped"):
        self.stops.append(reason)
        self.data.update(running=False, loaded=False, renderer_ready=False,
                         source_mode="none", end_reason=reason)

    async def update_sink(self, sink):
        self.sinks.append(sink)
        identity = lambda item: (item.device, item.name, item.serial) if item else None
        if self.data["running"] and (sink is None or not sink.usable
                                     or identity(sink) != identity(self.sink)):
            await self.stop("headphones disconnected")

    def set_pose(self, pose):
        self.poses.append(pose)

    async def pause(self, paused=True):
        self.pauses.append(paused)
        self.data["paused"] = paused

    async def seek(self, seconds, *, absolute=False):
        self.data["position"] = seconds

    async def set_volume(self, percent):
        self.data["volume"] = percent


class FakeLive:
    """Record capture requests without providing any renderer or graph."""
    def __init__(self):
        self.data = {"state": "idle", "running": False, "renderer_ready": False}
        self.starts = []
        self.stops = []
        self.started = asyncio.Event()

    def status(self):
        return dict(self.data)

    async def start(self, sink, stream, objects):
        self.starts.append((sink, stream))
        self.data.update(state="starting")
        self.started.set()

    async def stop(self, reason="stopped"):
        self.stops.append(reason)
        self.data.update(state="idle", running=False, renderer_ready=False)

    def cancel_start(self):
        pass


class Orchestration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.audio = FakeAudio()
        self.runtime = Runtime(Settings(readiness_timeout=1), audio=self.audio,
                               preferences_path=Path(self.temp.name) / "preferences.json")
        self.media = Path(self.temp.name) / "track one.mkv"
        self.media.write_bytes(b"test path only, not decoded media")
        self.other_media = Path(self.temp.name) / "track two.mkv"
        self.other_media.write_bytes(b"test path only, not decoded media")
        self.tasks = []

    async def asyncTearDown(self):
        self.runtime.stop_event.set()
        self.runtime.notify()
        tasks = self.tasks + list(self.runtime._background)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    def connect(self, serial="101"):
        self.runtime.engine.connect(ADDRESS)
        sink = Sink(ADDRESS, "bluez_output.verified", serial, "a2dp-sink", "suspended", "ldac")
        self.runtime.engine.observe_sink(self.runtime.engine.epoch, sink)
        self.runtime.notify()
        return sink

    async def turns(self, count=4):
        for _ in range(count):
            await asyncio.sleep(0)

    async def test_late_usable_sink_unblocks_playback_without_budslink_or_tracking(self):
        play = self.task(self.runtime.play(str(self.media)))
        await self.turns()
        self.assertFalse(play.done())
        self.assertEqual(self.audio.starts, [])
        sink = self.connect()
        await asyncio.wait_for(play, 1)
        self.assertEqual(self.audio.starts[0][:2], (str(self.media), sink))
        self.assertFalse(self.runtime.engine.controls_ready)
        self.assertEqual(self.runtime.engine.trackers, {})

    async def test_wrong_physical_output_does_not_release_waiting_playback(self):
        self.runtime.engine.connect(ADDRESS)
        wrong = Sink("11:22:33:44:55:66", "some_other_output", "9", "a2dp", "running")
        self.assertFalse(self.runtime.engine.observe_sink(self.runtime.engine.epoch, wrong))
        play = self.task(self.runtime.play(str(self.media)))
        await self.turns()
        self.assertEqual(self.audio.starts, [])
        await self.runtime.stop_playback()
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            await asyncio.wait_for(play, 1)

    async def test_stop_cancels_playback_waiting_for_headphones(self):
        play = self.task(self.runtime.play(str(self.media)))
        await self.turns()
        await self.runtime.stop_playback()
        self.connect()
        with self.assertRaisesRegex(RuntimeError, "cancelled|superseded"):
            await asyncio.wait_for(play, 1)
        self.assertEqual(self.audio.starts, [])
        self.assertFalse(self.audio.status()["running"])

    async def test_disconnect_during_source_preparation_cleans_source_without_launch(self):
        self.connect()
        preparing = asyncio.Event()
        release = asyncio.Event()
        cleaned = []
        class Prepared:
            def cleanup(self):
                cleaned.append(True)
        async def prepare():
            preparing.set()
            await release.wait()
            return str(self.media), {"title": "Private source"}, Prepared()
        with patch.object(self.runtime, "_prepare_current", prepare):
            play = self.task(self.runtime.play(str(self.media)))
            await asyncio.wait_for(preparing.wait(), 1)
            self.runtime.engine.disconnect()
            release.set()
            with self.assertRaisesRegex(RuntimeError, "disconnected"):
                await asyncio.wait_for(play, 1)
        self.assertEqual(self.audio.starts, [])
        self.assertEqual(cleaned, [True])

    async def test_stop_supersedes_play_queued_for_player_lock(self):
        self.connect()
        await self.runtime._lock.acquire()
        try:
            play = self.task(self.runtime.play(str(self.media)))
            await self.turns()
            stop = self.task(self.runtime.stop_playback())
            await self.turns()
            self.assertEqual(self.audio.starts, [])
        finally:
            self.runtime._lock.release()
        with self.assertRaisesRegex(RuntimeError, "cancelled|superseded"):
            await asyncio.wait_for(play, 1)
        await asyncio.wait_for(stop, 1)
        self.assertEqual(self.audio.starts, [])

    def delayed_launch(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        release = asyncio.Event()
        original_start = self.audio.start
        async def start(media, sink, **options):
            await original_start(media, sink, **options)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                # The real audio adapter reaps its child on launch cancellation.
                # This stand-in checks orchestration sends that cancellation.
                await self.audio.stop("launch cancelled")
                cancelled.set()
                raise
        return start, entered, cancelled, release

    async def test_stop_during_player_launch_cancels_owned_start_and_leaves_no_playback(self):
        self.connect()
        start, entered, cancelled, _ = self.delayed_launch()
        with patch.object(self.audio, "start", start):
            play = self.task(self.runtime.play(str(self.media)))
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.wait_for(self.runtime.stop_playback(), 1)
            await asyncio.wait_for(cancelled.wait(), 1)
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                await play
        self.assertFalse(self.audio.status()["running"])
        self.assertFalse(self.runtime._had_playback)

    async def test_disconnect_during_launch_cancels_even_when_tick_is_waiting_on_lock(self):
        self.connect()
        start, entered, cancelled, _ = self.delayed_launch()
        with patch.object(self.audio, "start", start):
            play = self.task(self.runtime.play(str(self.media)))
            await asyncio.wait_for(entered.wait(), 1)
            self.task(self.runtime._tick())
            await self.turns()
            self.runtime.engine.disconnect()
            self.runtime.notify()
            await asyncio.wait_for(cancelled.wait(), 0.5)
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                await asyncio.wait_for(play, 1)
        self.assertFalse(self.audio.status()["running"])

    async def test_expected_idle_to_running_sink_transition_does_not_cancel_launch(self):
        sink = self.connect()
        start, entered, cancelled, release = self.delayed_launch()
        with patch.object(self.audio, "start", start):
            play = self.task(self.runtime.play(str(self.media)))
            await asyncio.wait_for(entered.wait(), 1)
            running = Sink(sink.device, sink.name, sink.serial, sink.profile, "running", sink.codec)
            self.runtime.engine.observe_sink(self.runtime.engine.epoch, running)
            self.runtime.notify()
            release.set()
            await asyncio.wait_for(play, 1)
        self.assertFalse(cancelled.is_set())
        self.assertTrue(self.audio.status()["running"])

    async def test_new_sink_serial_stops_existing_output_session(self):
        self.connect()
        await self.runtime.play(str(self.media))
        self.task(self.runtime._tick())
        await self.turns()
        changed = self.connect(serial="202")
        for _ in range(100):
            if not self.audio.status()["running"]:
                break
            await asyncio.sleep(0.005)
        self.assertFalse(self.audio.status()["running"])
        self.assertIn(changed, self.audio.sinks)
        self.assertEqual(len(self.audio.starts), 1)

    async def test_eof_advances_queue_even_while_idle_mpv_process_is_running(self):
        self.connect()
        await self.runtime.play(str(self.media))
        self.runtime.queue.append(str(self.other_media))
        await self.audio.started.get()
        self.audio.data.update(end_reason="eof", loaded=False)
        self.task(self.runtime._tick())
        next_media = await asyncio.wait_for(self.audio.started.get(), 1)
        self.assertEqual(next_media, str(self.other_media))
        self.assertEqual(self.runtime.queue_index, 1)
        self.assertEqual(len(self.audio.starts), 2)

    async def test_crash_does_not_advance_queue_or_restart_playback(self):
        self.connect()
        await self.runtime.play(str(self.media))
        self.runtime.queue.append(str(self.other_media))
        self.audio.data.update(running=False, loaded=False, end_reason="error")
        self.task(self.runtime._tick())
        await self.turns()
        self.assertEqual(self.runtime.queue_index, 0)
        self.assertEqual(len(self.audio.starts), 1)
        self.assertFalse(self.audio.status()["running"])

    async def test_slow_queue_preparation_does_not_block_audio_policy_ticks(self):
        self.connect()
        await self.runtime.play(str(self.media))
        self.runtime.queue.append(str(self.other_media))
        self.audio.data.update(end_reason="eof", loaded=False)
        preparing = asyncio.Event()
        release = asyncio.Event()
        async def prepare():
            preparing.set()
            await release.wait()
            return str(self.other_media), {"title": "Next track"}, None
        with patch.object(self.runtime, "_prepare_current", prepare):
            self.task(self.runtime._tick())
            await asyncio.wait_for(preparing.wait(), 1)
            observed = len(self.audio.poses)
            deadline = asyncio.get_running_loop().time() + .3
            while len(self.audio.poses) <= observed + 1 and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(.005)
            self.assertGreater(len(self.audio.poses), observed + 1,
                               "Network preparation must not stop ongoing pose/sink policy")
            await self.runtime.stop_playback()
            release.set()
            await self.turns(20)
        self.assertEqual(len(self.audio.starts), 1)

    async def test_stop_supersedes_eof_advance_before_its_background_task_runs(self):
        self.connect()
        await self.runtime.play(str(self.media))
        self.runtime.queue.append(str(self.other_media))
        self.audio.data.update(end_reason="eof", loaded=False)
        tick_observed = asyncio.Event()
        original_pose = self.audio.set_pose
        def pose(value):
            original_pose(value)
            # Wake this already-waiting test before the newly queued EOF task.
            tick_observed.set()
        with patch.object(self.audio, "set_pose", pose):
            self.task(self.runtime._tick())
            async with asyncio.timeout(1):
                await tick_observed.wait()
            await self.runtime.stop_playback()
            await self.turns(20)
        self.assertEqual(len(self.audio.starts), 1)
        self.assertFalse(self.audio.status()["running"])

    async def test_live_only_renderer_readiness_reports_pcm_and_retracts_on_loss(self):
        self.connect()
        live_state = {"running": True, "renderer_ready": True, "source_mode": "pcm"}
        self.runtime.live = SimpleNamespace(status=lambda: dict(live_state))
        self.task(self.runtime._tick())
        await self.turns()
        self.assertFalse(self.audio.status()["running"])
        self.assertTrue(self.runtime.engine.snapshot()["renderer_ready"])
        self.assertEqual(self.runtime.engine.snapshot()["render_mode"], "pcm")
        live_state.update(running=False, renderer_ready=False)
        deadline = asyncio.get_running_loop().time() + .3
        while self.runtime.engine.renderer_ready and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(.005)
        self.assertFalse(self.runtime.engine.snapshot()["renderer_ready"])
        self.assertEqual(self.runtime.engine.snapshot()["render_mode"], "none")

    async def test_resume_after_eof_reloads_current_track(self):
        self.connect()
        await self.runtime.play(str(self.media))
        self.audio.data.update(end_reason="eof", loaded=False)
        await self.runtime.resume()
        self.assertEqual(len(self.audio.starts), 2)
        self.assertEqual(self.audio.starts[-1][0], str(self.media))
        self.assertEqual(self.audio.pauses, [])

    async def test_failing_optional_provider_does_not_stop_audio_or_bluetooth(self):
        self.connect()
        await self.runtime.play(str(self.media))
        async def fail():
            raise OSError("provider is unavailable")
        with self.assertLogs("spatial.runtime", level="WARNING"):
            provider = self.task(self.runtime._optional("test_provider", fail))
            await self.turns()
        self.assertFalse(provider.done())
        self.assertTrue(self.runtime.engine.connected)
        self.assertTrue(self.audio.status()["running"])
        self.assertEqual(self.runtime.providers["test_provider"]["state"], "unavailable")

    async def test_latest_background_request_keeps_loading_state_after_previous_is_superseded(self):
        await self.runtime.request_play(str(self.media))
        await self.turns()
        await self.runtime.request_play(str(self.other_media))
        await self.turns(30)
        self.assertTrue(self.runtime.loading)
        self.assertIsNone(self.runtime.playback_error)
        self.connect()
        await asyncio.wait_for(self.audio.started.get(), 1)
        await self.turns()
        self.assertFalse(self.runtime.loading)
        self.assertEqual(self.audio.starts[0][0], str(self.other_media))

    async def test_unexpected_background_error_does_not_expose_provider_secrets(self):
        async def reject(_, **kwargs):
            raise RuntimeError("https://media.example/?token=do-not-publish")
        with patch.object(self.runtime, "play", reject):
            await self.runtime.request_play(str(self.media))
            await self.turns()
        self.assertTrue(self.runtime.playback_error)
        self.assertNotIn("do-not-publish", str(self.runtime.state()))

    async def test_owned_test_cancelled_before_background_launch_never_starts(self):
        self.connect()
        token = await self.runtime.begin_test_playback(str(self.media))
        self.assertGreater(token, 0)
        self.assertTrue(await self.runtime.stop_if_request(token))
        await self.turns(20)
        self.assertEqual(self.audio.starts, [])
        self.assertFalse(self.runtime.loading)

    async def test_test_request_cannot_replace_pending_user_request_without_replace(self):
        await self.runtime.request_play(str(self.media))
        token = await self.runtime.begin_test_playback(str(self.other_media))
        self.assertEqual(token, 0)
        self.connect()
        await asyncio.wait_for(self.audio.started.get(), 1)
        self.assertEqual(self.audio.starts[0][0], str(self.media))

    async def test_old_test_token_cannot_stop_new_direct_media_request(self):
        self.connect()
        token = await self.runtime.begin_test_playback(str(self.media))
        await asyncio.wait_for(self.audio.started.get(), 1)
        await self.turns()
        # MPRIS OpenUri calls play directly, rather than request_play.
        await self.runtime.play(str(self.other_media))
        self.assertFalse(await self.runtime.stop_if_request(token))
        self.assertTrue(self.audio.status()["running"])
        self.assertEqual(self.audio.starts[-1][0], str(self.other_media))

    def native_setup(self):
        self.connect()
        self.audio.data.update(running=True, loaded=True, decoder="flac", process_id=42)
        objects = [
            {"id": 7, "type": "PipeWire:Interface:Client", "info": {"props": {
                "application.process.id": 42}}},
            {"id": 8, "type": "PipeWire:Interface:Node", "info": {"props": {
                "media.class": "Stream/Output/Audio", "object.serial": 108,
                "node.name": "mpv", "application.name": "Spatial audio", "client.id": 7}}},
            {"id": 9, "type": "PipeWire:Interface:Node", "info": {"props": {
                "media.class": "Stream/Output/Audio", "object.serial": 109,
                "node.name": "another-player", "application.name": "Other application",
                "application.process.id": 99}}},
        ]
        self.runtime.desktop = SimpleNamespace(pipewire_objects=objects)
        self.runtime.live = FakeLive()
        return objects

    async def test_native_capture_selects_only_unique_owned_player_client(self):
        objects = self.native_setup()
        self.assertEqual(self.runtime._player_stream(objects, 42), "108")
        duplicate = {"id": 10, "type": "PipeWire:Interface:Node", "info": {"props": {
            "media.class": "Stream/Output/Audio", "object.serial": 110,
            "node.name": "second-owned-stream", "application.process.id": 42}}}
        self.assertIsNone(self.runtime._player_stream(objects + [duplicate], 42))
        self.task(self.runtime._native_audio_loop())
        await asyncio.wait_for(self.runtime.live.started.wait(), .5)
        self.assertEqual([stream for _, stream in self.runtime.live.starts], ["108"])
        self.assertTrue(self.runtime._live_automatic)

    async def test_native_capture_never_renders_orender_output_twice(self):
        self.native_setup()
        self.audio.data["decoder"] = "orender"
        self.task(self.runtime._native_audio_loop())
        await self.turns()
        self.assertEqual(self.runtime.live.starts, [])
        with self.assertRaisesRegex(RuntimeError, "already rendering"):
            await self.runtime.start_live("108")
        self.assertEqual(self.runtime.live.starts, [])

    async def test_native_capture_preserves_explicit_other_application_selection(self):
        self.native_setup()
        await self.runtime.start_live("109")
        self.runtime.live.data.update(state="playing", running=True, renderer_ready=True)
        self.task(self.runtime._native_audio_loop())
        await self.turns()
        self.assertEqual([stream for _, stream in self.runtime.live.starts], ["109"])
        self.assertFalse(self.runtime._live_automatic)
        self.assertEqual(self.runtime.live.stops, [])

    async def test_stopping_native_capture_inhibits_same_player_but_allows_new_session(self):
        objects = self.native_setup()
        await self.runtime.stop_live()
        self.task(self.runtime._native_audio_loop())
        await self.turns()
        self.assertEqual(self.runtime.live.starts, [])
        self.audio.data["process_id"] = 43
        objects[0]["info"]["props"]["application.process.id"] = 43
        await asyncio.wait_for(self.runtime.live.started.wait(), .6)
        self.assertEqual([stream for _, stream in self.runtime.live.starts], ["108"])

    async def test_cancelled_tidal_prepare_disposes_late_worker_result(self):
        self.runtime.queue = ["tidal:track:123"]
        entered = threading.Event()
        release = threading.Event()
        disposed = asyncio.Event()
        cleanup_calls = []
        loop = asyncio.get_running_loop()
        class Prepared:
            media = "https://media.example/?token=private"
            metadata = {"title": "Track"}
            def cleanup(self):
                cleanup_calls.append(True)
                loop.call_soon_threadsafe(disposed.set)
        def prepare(*args, **kwargs):
            entered.set()
            release.wait(2)
            return Prepared()
        self.runtime._tidal = SimpleNamespace(prepare=prepare)
        job = self.task(self.runtime._prepare_current())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            job.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await job
            release.set()
            await asyncio.wait_for(disposed.wait(), 1)
            self.assertEqual(cleanup_calls, [True])
            self.assertIsNone(self.runtime._prepared)
        finally:
            release.set()
