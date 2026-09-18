"""Live orchestration for independent device, tracking and playback providers."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import signal
import time
from urllib.parse import urlparse, unquote

from .core import Engine
from .errors import PublicError, public_message
from .preferences import Preferences
from .settings import Settings, config_directory

LOG = logging.getLogger(__name__)


class PlaybackError(PublicError):
    """A credential-free error from the orchestration layer."""



class Runtime:
    def __init__(self, settings: Settings, *, audio=None, preferences_path=None):
        self.settings = settings
        self.preferences_path = Path(preferences_path) if preferences_path else config_directory() / "preferences.json"
        self.engine = Engine(Preferences.load(self.preferences_path), settings.sample_timeout)
        if audio is None:
            from .audio_runtime import AudioRuntime
            audio = AudioRuntime(binary=settings.mpv_binary, bridge_path=settings.resolve_bridge(),
                                 library_path=settings.library_path,
                                 allow_pcm_route=settings.stereo_spatialization and settings.live_enabled)
        self.audio = audio
        self.stop_event = asyncio.Event()
        self.changed = asyncio.Event()
        self.desktop = None
        self.equalizer = None
        self.live = None
        self.diagnostics = None
        self.providers = {}
        self.queue = []
        self.queue_index = 0
        self.metadata = {}
        self.track_serial = 0
        self.track_path = "/org/mpris/MediaPlayer2/Track/none"
        self.playback_error = None
        self._lock = asyncio.Lock()
        self._play_lock = asyncio.Lock()
        self._request = 0
        self._prepared = None
        self._had_playback = False
        self._tidal = None
        self._launch_task = None
        self._launch_target = None
        self._background = set()
        self.loading = False
        self._ui_request = 0
        self._workers = set()
        self._live_automatic = False
        self._live_media_pid = None
        self._auto_paused_pid = None

    def notify(self):
        self.changed.set()
        # A tick may already be waiting on the player lock. Device callbacks must
        # cancel the launch immediately, without needing that same lock.
        if self._launch_task is not None:
            if self._launch_target != self._target():
                self._cancel_launch()
        if self.live is not None and not self._ready():
            self.live.cancel_start()

    def provider_status(self, module, state, detail=""):
        current = {"state": state, "detail": detail}
        if self.diagnostics is not None:
            self.diagnostics.request(module, state, detail)
        if self.providers.get(module) != current:
            self.providers[module] = current
            self.notify()

    def state(self):
        live = self.live.status() if self.live is not None else {"state": "unavailable"}
        if self.live is not None and self.desktop is not None:
            from .live_audio import available_streams
            live["available_streams"] = available_streams(self.desktop.pipewire_objects)
        else:
            live["available_streams"] = []
        return {"providers": dict(self.providers), "audio": self.audio.status(),
                "equalizer": self.equalizer.status() if self.equalizer is not None else {"state": "unavailable"},
                "live": live,
                "diagnostics": self.diagnostics.status() if self.diagnostics is not None else {"state": "disabled"},
                "playback_error": self.playback_error, "track": dict(self.metadata),
                "queue_length": len(self.queue), "queue_index": self.queue_index,
                "loading": self.loading}

    def _cancel_launch(self):
        if self._launch_task is not None and not self._launch_task.done():
            self._launch_task.cancel()

    async def request_play(self, reference):
        """Acknowledge a UI request promptly; readiness/decoder errors appear in State."""
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError("select a media path or TIDAL URL")
        self._ui_request += 1
        ui_request = self._ui_request
        self.loading = True
        self.playback_error = None
        self.notify()
        async def launch():
            if ui_request != self._ui_request:
                return
            if ui_request == self._ui_request:
                self.loading = True
                self.playback_error = None
            self.notify()
            try:
                await self.play(reference, _ui_token=ui_request)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Audio/Tidal providers deliberately return credential-free errors.
                if ui_request == self._ui_request:
                    self.playback_error = public_message(exc)
                    self.provider_status("playback", "unavailable", self.playback_error)
            finally:
                if ui_request == self._ui_request:
                    self.loading = False
                self.notify()
        task = asyncio.create_task(launch(), name="play-request")
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return ui_request

    async def play_if_idle(self, reference):
        if self.loading or self.audio.status().get("running"):
            return False
        await self.request_play(reference)
        return True

    async def begin_test_playback(self, reference, replace=False):
        if not replace and (self.loading or self.audio.status().get("running")):
            return 0
        return await self.request_play(reference)

    async def stop_if_request(self, request_id):
        if type(request_id) is not int or request_id <= 0 or request_id != self._ui_request:
            return False
        await self.stop_playback()
        return True

    def _ready(self):
        return self.engine.connected and self.engine.sink is not None and self.engine.sink.usable

    def _target(self):
        if not self._ready():
            return None
        sink = self.engine.sink
        return self.engine.epoch, sink.device, sink.name, sink.serial

    async def _wait_ready(self, request):
        deadline = asyncio.get_running_loop().time() + self.settings.readiness_timeout
        while not self._ready():
            if request != self._request or self.stop_event.is_set():
                raise PlaybackError("playback request was cancelled")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise PlaybackError("WF-1000XM5 output is not ready; connect the earbuds and retry")
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), min(remaining, 0.5))
            except TimeoutError:
                pass

    def _tidal_provider(self):
        if self._tidal is None:
            from .tidal import TidalProvider
            self._tidal = TidalProvider()
        return self._tidal

    async def _worker(self, callback, *args, cleanup=None, **kwargs):
        """Keep ownership of bounded network work even if its caller is cancelled."""
        task = asyncio.create_task(asyncio.to_thread(callback, *args, **kwargs))
        self._workers.add(task)
        task.add_done_callback(self._workers.discard)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            def dispose(result):
                if result.cancelled():
                    return
                try:
                    value = result.result()
                except Exception:
                    return
                if cleanup is not None:
                    cleanup(value)
            task.add_done_callback(dispose)
            raise

    @staticmethod
    def is_tidal(reference):
        parsed = urlparse(reference)
        return (parsed.scheme == "tidal" or
                parsed.scheme in ("http", "https") and
                (parsed.hostname or "").lower() in ("tidal.com", "www.tidal.com", "listen.tidal.com"))

    async def play(self, reference, *, _ui_token=None):
        if _ui_token is None:
            self._ui_request += 1
            self.loading = False
        elif _ui_token != self._ui_request:
            raise PlaybackError("playback request was cancelled")
        if not isinstance(reference, str) or not reference.strip():
            return await self.resume()
        self._request += 1
        self._cancel_launch()
        request = self._request
        self.provider_status("playback", "starting", "Preparing the selected source")
        self.notify()
        async with self._play_lock:
            if self.is_tidal(reference):
                provider = self._tidal_provider()
                ids = await self._worker(provider.track_ids, reference)
                if not ids:
                    raise ValueError("the TIDAL selection contains no tracks")
                queue = [f"tidal:track:{track_id}" for track_id in ids]
            else:
                queue = [reference]
            if request != self._request:
                raise PlaybackError("playback request was superseded")
            self.queue = queue
            self.queue_index = 0
            await self._start_current(request)

    async def _prepare_current(self):
        reference = self.queue[self.queue_index]
        parsed = urlparse(reference)
        if self.is_tidal(reference):
            provider = self._tidal_provider()
            track_id = reference.rsplit(":", 1)[-1]
            prepared = await self._worker(provider.prepare, track_id, require_atmos=self.settings.tidal_require_atmos,
                                          cleanup=lambda item: item.cleanup())
            return prepared.media, dict(prepared.metadata), prepared
        if parsed.scheme == "file":
            if parsed.netloc not in ("", "localhost"):
                raise ValueError("remote file URIs are not supported")
            media = unquote(parsed.path)
        else:
            media = reference
        if parsed.scheme in ("http", "https"):
            title = unquote(Path(parsed.path).name) or (parsed.hostname or "Stream")
        else:
            title = Path(media).name
        return media, {"title": title}, None

    def _cleanup_prepared(self):
        if self._prepared is not None:
            self._prepared.cleanup()
            self._prepared = None

    async def _start_current(self, request):
        await self._wait_ready(request)
        media, metadata, prepared = await self._prepare_current()
        if request != self._request or self.stop_event.is_set():
            if prepared is not None:
                prepared.cleanup()
            raise PlaybackError("playback request was cancelled")
        async with self._lock:
            if request != self._request or self.stop_event.is_set():
                if prepared is not None:
                    prepared.cleanup()
                raise PlaybackError("playback request was cancelled")
            if not self._ready():
                if prepared is not None:
                    prepared.cleanup()
                raise PlaybackError("headphone output disconnected while preparing playback")
            await self.audio.stop()
            self._cleanup_prepared()
            self._prepared = prepared
            try:
                target = self._target()
                self._launch_target = target
                self._launch_task = asyncio.create_task(self.audio.start(
                    media, self.engine.sink, require_spatial=bool(prepared is not None and
                         metadata.get("atmos_manifest", self.settings.tidal_require_atmos))), name="player-launch")
                try:
                    await self._launch_task
                except asyncio.CancelledError:
                    if request != self._request or target != self._target():
                        raise PlaybackError("playback request was cancelled") from None
                    raise
                finally:
                    self._launch_task = None
                    self._launch_target = None
                if (request != self._request or self.stop_event.is_set() or not self._ready()
                        or target != self._target()):
                    await self.audio.stop("playback request was cancelled")
                    raise PlaybackError("playback request was cancelled")
            except BaseException:
                self._cleanup_prepared()
                raise
            self.metadata = metadata
            self.track_serial += 1
            self.track_path = f"/org/mpris/MediaPlayer2/Track/{self.track_serial}"
            self.playback_error = None
            self._had_playback = True
            self.provider_status("playback", "ready", "Playback started")
        self.notify()

    async def stop_playback(self):
        self._ui_request += 1
        self.loading = False
        self._request += 1
        self._cancel_launch()
        self.notify()
        async with self._lock:
            await self.audio.stop()
            self._cleanup_prepared()
            self._had_playback = False
        self.notify()

    async def stop_if_process(self, expected_pid):
        async with self._lock:
            if self.loading or self.audio.status().get("process_id") != expected_pid:
                return False
            self._ui_request += 1
            self._request += 1
            self._cancel_launch()
            await self.audio.stop()
            self._cleanup_prepared()
            self._had_playback = False
            self.notify()
            return True

    async def pause(self, paused=True):
        async with self._lock:
            await self.audio.pause(paused)
        self.notify()

    async def resume(self):
        if self.audio.status().get("running") and self.audio.status().get("end_reason") != "eof":
            return await self.pause(False)
        if not self.queue:
            raise PlaybackError("no track loaded; use spatialctl play FILE or a TIDAL URL")
        self._ui_request += 1
        self._request += 1
        request = self._request
        async with self._play_lock:
            await self._start_current(request)

    async def next(self):
        if self.queue_index + 1 >= len(self.queue):
            return
        self._ui_request += 1
        self._request += 1
        request = self._request
        async with self._play_lock:
            self.queue_index += 1
            await self._start_current(request)

    async def previous(self):
        if self.queue_index <= 0:
            return
        self._ui_request += 1
        self._request += 1
        request = self._request
        async with self._play_lock:
            self.queue_index -= 1
            await self._start_current(request)

    async def seek(self, seconds, absolute=False):
        async with self._lock:
            await self.audio.seek(seconds, absolute=absolute)

    async def set_volume(self, volume):
        async with self._lock:
            await self.audio.set_volume(volume * 100)
        self.notify()

    async def _equalizer_loop(self):
        try:
            while not self.stop_event.is_set():
                await self.equalizer.update_sink(self.engine.sink if self._ready() else None)
                if self.desktop is not None:
                    self.equalizer.verified_input_ids(self.desktop.pipewire_objects)
                state = self.equalizer.status()
                self.provider_status("equalizer", "unavailable" if state.get("state") == "error"
                                     else state.get("state", "waiting"), state.get("reason", ""))
                try:
                    await asyncio.wait_for(self.stop_event.wait(), 0.25)
                except TimeoutError:
                    pass
        finally:
            await self.equalizer.stop()

    async def start_live(self, stream_serial):
        if self.live is None or not self.settings.live_enabled:
            raise PlaybackError("Live application audio is disabled in configuration")
        if not self._ready() or self.desktop is None:
            raise PlaybackError("Connect the WF-1000XM5 before starting application audio")
        media = self.audio.status()
        if (media.get("decoder") == "orender" and media.get("process_id") and
                self._player_stream(self.desktop.pipewire_objects, media["process_id"]) == str(stream_serial)):
            raise PlaybackError("This player is already rendering binaural object audio")
        self._live_automatic = False
        self._auto_paused_pid = self.audio.status().get("process_id")
        await self.live.start(self.engine.sink, stream_serial, self.desktop.pipewire_objects)
        self.notify()

    async def stop_live(self):
        self._live_automatic = False
        self._auto_paused_pid = self.audio.status().get("process_id")
        if self.live is not None:
            await self.live.stop()
        self.notify()

    @staticmethod
    def _player_stream(objects, pid):
        from .live_audio import available_streams
        clients = {str(obj.get("id")) for obj in objects
                   if obj.get("type") == "PipeWire:Interface:Client" and
                   str(((obj.get("info") or {}).get("props") or {}).get("application.process.id")) == str(pid)}
        eligible = {row["serial"] for row in available_streams(objects)}
        matches = []
        for obj in objects:
            props = (obj.get("info") or {}).get("props") or {}
            if (obj.get("type") == "PipeWire:Interface:Node"
                    and str(props.get("object.serial")) in eligible
                    and (str(props.get("application.process.id")) == str(pid)
                         or str(props.get("client.id")) in clients)):
                matches.append(str(props["object.serial"]))
        return matches[0] if len(matches) == 1 else None

    async def _native_audio_loop(self):
        from .retry import Backoff
        retry = Backoff()
        next_attempt = 0.0
        while not self.stop_event.is_set():
            state, live = self.audio.status(), self.live.status()
            pid = state.get("process_id")
            suitable = bool(state.get("running") and state.get("loaded") and
                            state.get("decoder") and state["decoder"] != "orender" and pid)
            if self._live_automatic and (not suitable or pid != self._live_media_pid):
                await self.live.stop("stopped")
                self._live_automatic = False
                self._live_media_pid = None
                next_attempt = 0.0
                retry.reset()
                live = self.live.status()
            busy = live.get("running") or live.get("state") == "starting"
            if (suitable and not busy and self._ready() and self.desktop is not None
                    and pid != self._auto_paused_pid and time.monotonic() >= next_attempt):
                stream = self._player_stream(self.desktop.pipewire_objects, pid)
                if stream:
                    self._live_automatic = True
                    self._live_media_pid = pid
                    try:
                        await self.live.start(self.engine.sink, stream, self.desktop.pipewire_objects)
                    except Exception as exc:
                        self.provider_status("stereo_audio", "unavailable", public_message(exc))
                    next_attempt = time.monotonic() + retry.next_delay()
            if self._live_automatic and live.get("renderer_ready"):
                retry.reset()
                self.provider_status("stereo_audio", "ready", "Binaural PCM rendering")
            elif not self._live_automatic:
                self.provider_status("stereo_audio", "waiting", "No automatic PCM capture is active")
            elif live.get("error"):
                self.provider_status("stereo_audio", "unavailable", live["error"])
            elif busy:
                self.provider_status("stereo_audio", "waiting", "Waiting for the binaural PCM path")
            try:
                await asyncio.wait_for(self.stop_event.wait(), 0.25)
            except TimeoutError:
                pass

    async def _live_loop(self):
        try:
            while not self.stop_event.is_set():
                await self.live.update_sink(self.engine.sink if self._ready() else None)
                self.live.set_pose(self.engine.pose if self.engine.active else None)
                state = self.live.status()
                self.provider_status("live_audio", "unavailable" if state.get("error") else
                                     "ready" if state.get("renderer_ready") else "waiting",
                                     state.get("error") or state.get("reason", ""))
                try:
                    await asyncio.wait_for(self.stop_event.wait(), 0.01)
                except TimeoutError:
                    pass
        finally:
            await self.live.stop("service stopped")

    async def _tick(self):
        previous_target = None
        audited_objects = None
        audited_pid = None
        while not self.stop_event.is_set():
            self.engine.advance(time.monotonic())
            self.engine.select()
            current_sink = self.engine.sink if self._ready() else None
            current_target = self._target()
            if self._launch_task is not None and self._launch_target != current_target:
                self._cancel_launch()
            if current_target != previous_target or (current_sink is None and self.audio.status().get("running")):
                async with self._lock:
                    await self.audio.update_sink(current_sink)
                previous_target = current_target
            state = self.audio.status()
            if (self.desktop is not None and state.get("running") and
                    (self.desktop.pipewire_objects is not audited_objects or
                     state.get("process_id") != audited_pid)):
                objects = self.desktop.pipewire_objects
                inputs = self.equalizer.verified_input_ids(objects) if self.equalizer is not None else set()
                pending = self.equalizer.pending_input_ids(objects) if self.equalizer is not None else set()
                if self.live is not None:
                    inputs |= self.live.verified_input_ids(objects)
                    pending |= self.live.pending_input_ids(objects)
                pending -= inputs
                routing = self.audio.verify_output(objects, allowed_filter_inputs=inputs,
                                                   pending_filter_inputs=pending)
                if routing["state"] == "violation":
                    self.playback_error = routing["reason"]
                    self.provider_status("playback", "unavailable", routing["reason"])
                    self._cancel_launch()
                    async with self._lock:
                        await self.audio.stop(routing["reason"])
                    state = self.audio.status()
                audited_objects = objects
                audited_pid = state.get("process_id")
            live_state = self.live.status() if self.live is not None else {}
            self.engine.capability(self.engine.epoch, "renderer", bool(
                state.get("renderer_ready") or live_state.get("renderer_ready")))
            self.engine.source_mode = (state.get("source_mode", "none") if state.get("renderer_ready")
                                      else "pcm" if live_state.get("renderer_ready") else state.get("source_mode", "none"))
            self.audio.set_pose(self.engine.pose if self.engine.active else None)
            if self._had_playback and (not state.get("running") or state.get("end_reason") == "eof"):
                self._had_playback = False
                self._cleanup_prepared()
                # Only a confirmed natural end advances the queue, never a crash/disconnect.
                if state.get("end_reason") == "eof" and self._ready() and self.queue_index + 1 < len(self.queue):
                    async def advance_queue(request=self._request):
                        if request != self._request or self.stop_event.is_set():
                            return
                        try:
                            await self.next()
                        except Exception as exc:
                            self.playback_error = public_message(exc)
                            self.provider_status("playback", "unavailable", self.playback_error)
                    task = asyncio.create_task(advance_queue(), name="queue-advance")
                    self._background.add(task)
                    task.add_done_callback(self._background.discard)
            try:
                await asyncio.wait_for(self.stop_event.wait(), 0.01)
            except TimeoutError:
                pass

    async def _optional(self, name, coroutine_factory):
        from .retry import Backoff
        retry = Backoff()
        while not self.stop_event.is_set():
            try:
                await coroutine_factory()
                if self.stop_event.is_set():
                    return
                self.provider_status(name, "waiting", "provider stopped; retrying")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Provider exception text can contain signed source URLs; keep status safe.
                self.provider_status(name, "unavailable", type(exc).__name__)
                LOG.warning("%s unavailable (%s)", name, type(exc).__name__)
            try:
                await asyncio.wait_for(self.stop_event.wait(), retry.next_delay())
            except TimeoutError:
                pass

    async def run(self):
        from .desktop import DesktopRuntime
        from .trackers import run as run_trackers
        from .mpris import serve
        from .equalizer import Equalizer
        from .live_audio import LiveAudio
        from .health import Diagnostics
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.stop_event.set)
        self.diagnostics = Diagnostics(enabled=self.settings.automatic_diagnostics)
        self.equalizer = Equalizer(self.settings.equalizer_profile, enabled=self.settings.equalizer_enabled,
                                   bluetooth_address=self.settings.bluetooth_address)
        if self.settings.live_enabled:
            self.live = LiveAudio(binary=self.settings.orender_binary, bridge_path=self.settings.resolve_bridge(),
                                  authorized_filter_inputs=self.equalizer.verified_input_ids,
                                  pending_filter_inputs=self.equalizer.pending_input_ids)
        self.desktop = DesktopRuntime(self.engine, self.settings.bluetooth_address,
                                      self.preferences_path, on_change=self.notify,
                                      extra_state=self.state, on_play=self.request_play, on_stop=self.stop_playback,
                                      on_live_start=self.start_live, on_live_stop=self.stop_live,
                                      on_play_if_idle=self.play_if_idle, on_stop_if_process=self.stop_if_process,
                                      on_begin_test_playback=self.begin_test_playback,
                                      on_stop_if_request=self.stop_if_request)
        tasks = [
            asyncio.create_task(self.desktop.run(self.stop_event), name="desktop"),
            asyncio.create_task(self._tick(), name="audio-policy"),
            asyncio.create_task(self._optional("trackers", lambda: run_trackers(
                self.engine, self.stop_event, on_change=self.notify, status=self.provider_status,
                sony_executable=self.settings.sony_binary, sony_enabled=self.settings.sony_enabled,
                optional_enabled=self.settings.slime_enabled)), name="trackers"),
            asyncio.create_task(self._optional("media_controls", lambda: serve(self, self.stop_event)), name="mpris"),
            asyncio.create_task(self._optional("equalizer", self._equalizer_loop), name="equalizer"),
        ]
        if self.live is not None:
            tasks.append(asyncio.create_task(self._optional("live_audio", self._live_loop), name="live-audio"))
            if self.settings.stereo_spatialization:
                tasks.append(asyncio.create_task(self._optional("stereo_audio", self._native_audio_loop), name="stereo-audio"))
        stopped = asyncio.create_task(self.stop_event.wait())
        try:
            await asyncio.wait([stopped, *tasks], return_when=asyncio.FIRST_COMPLETED)
            for task in tasks:
                if task.done() and not task.cancelled():
                    task.result()
        finally:
            self.stop_event.set()
            self._request += 1
            self.notify()
            background = list(self._background)
            for task in [*tasks, stopped, *background]:
                task.cancel()
            await asyncio.gather(*tasks, stopped, *background, return_exceptions=True)
            await self.diagnostics.close()
            await self.audio.stop()
            self._cleanup_prepared()
            if self._workers:
                await asyncio.gather(*list(self._workers), return_exceptions=True)
            if self._tidal is not None:
                self._tidal.close()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
