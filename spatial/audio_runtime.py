"""Own one mpv-omniphony process, its IPC connection, and renderer telemetry.

This adapter never changes the desktop default or creates an audio device.
Native decoder playback and verified binaural object rendering are separate
states. Credentials in a media URL are never included in status or exceptions.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from .core import Sink
from .errors import PublicError
from .osc import decode, encode
from .pose import IDENTITY, Quaternion


class AudioError(PublicError):
    """An actionable playback error safe to display in the companion."""


async def library_supports_loopback(path):
    """Inspect an exact library in a child process, never in the daemon.

    Upstream 0.5.2 accepts an OSC bind argument but its shared engine ignores
    it. The additive downstream marker verifies environment bind support before
    its listener is allowed to start.
    """
    code = ("import ctypes,sys; lib=ctypes.CDLL(sys.argv[1]); "
            "cap=lib.orender_spatial_loopback_supported; cap.argtypes=[]; "
            "cap.restype=ctypes.c_uint32; print(cap())")
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code, str(path), stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        output, _ = await asyncio.wait_for(process.communicate(), 5)
        return process.returncode == 0 and output.strip() == b"1"
    except (OSError, asyncio.TimeoutError):
        return False
    finally:
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()


def _reject_json_constant(value):
    raise ValueError("Non-finite JSON values are not valid renderer state")


def _json_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        _reject_json_constant(value)
    return parsed


def media_source(value):
    """Accept local regular files or explicit HTTP(S) media, never mpv pseudo URLs."""
    if not isinstance(value, (str, os.PathLike)):
        raise AudioError("Select a media file or an HTTP(S) media URL")
    value = os.fspath(value)
    if not value or "\0" in value or any(ord(c) < 32 for c in value):
        raise AudioError("The media path or URL is invalid")
    parsed = urlsplit(value)
    if parsed.scheme:
        if parsed.scheme not in ("https", "http") or not parsed.hostname:
            raise AudioError("Only HTTP(S) media URLs and local files are supported")
        return value
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise AudioError("The selected media file does not exist") from None
    if not path.is_file():
        raise AudioError("Select a regular media file")
    return str(path)


def renderer_pose(pose):
    """Head→world (right/up/back) to world→head ADM (right/front/up)."""
    q = Quaternion.parse(pose.values())
    return (q.w, -q.x, q.z, -q.y)


def config_text(bridge_path):
    # JSON quoted scalars are valid YAML; paths cannot inject YAML directives.
    return ("render:\n"
            f"  bridge_path: {json.dumps(str(bridge_path))}\n"
            "  channel_render_mode: spatial\n"
            "  auto_gain: true\n"
            "  auto_gain_ceiling_db: -1.0\n"
            "  use_loudness: false\n"
            "  binaural:\n"
            "    output_mode: binaural\n"
            "    hrir_source: saf\n"
            "    head_tracking:\n"
            "      osc_address: ''\n"
            "    reflections:\n"
            "      enabled: false\n"
            "    reverb:\n"
            "      enabled: false\n")


def playback_argv(binary, config, ipc, sink, osc_port, monitor_port, library_path=None):
    if not isinstance(sink, Sink) or not sink.usable:
        raise AudioError("Waiting for the selected headphones' A2DP output")
    args = [binary, "--no-config", "--load-scripts=no", "--ytdl=no",
            "--terminal=no", "--idle=yes", "--pause=yes", "--keep-open=no",
            "--title=Spatial audio", "--force-media-title=Spatial audio",
            "--audio-client-name=Spatial audio", "--volume-max=100",
            "--ao=pipewire", "--ad=orender", "--audio-channels=stereo",
            "--audio-buffer=0.05", "--audio-device=pipewire/" + sink.name,
            "--input-ipc-server=" + str(ipc), "--ad-orender-config=" + str(config),
            "--ad-orender-osc", "--ad-orender-osc-bind=127.0.0.1",
            "--ad-orender-osc-rx-port=" + str(osc_port),
            "--ad-orender-osc-port=" + str(monitor_port),
            "--ad-orender-osc-monitor-target=127.0.0.1"]
    if library_path:
        args.append("--ad-orender-library=" + str(library_path))
    return args


class RendererTelemetry(asyncio.DatagramProtocol):
    """Receiver bound to a private loopback port selected for the owned player."""
    def __init__(self):
        self.transport = None
        self.target = None
        self.capabilities = {}
        self.renderer = {}
        self.expected_config = None
        self.config_path = None
        self.config_status = None
        self.last_seen = 0.0
        self.clipping = None
        self.master_gain = None
        self.object_count = None
        self.registered = False
        self.error = ""

    def connection_made(self, transport):
        self.transport = transport

    def send(self, address, *values):
        if self.transport is not None and self.target is not None:
            self.transport.sendto(encode(address, *values), self.target)

    def datagram_received(self, data, addr):
        # Omniphony transmits from a separate ephemeral socket, not its RX port.
        if addr[0] != "127.0.0.1":
            return
        try:
            messages = decode(data)
        except ValueError:
            return
        for address, args in messages:
            try:
                self.accept(address, args)
            except (ValueError, TypeError, KeyError, IndexError, RecursionError):
                continue

    def accept(self, address, args):
        prefix = "/omniphony/"
        if not address.startswith(prefix):
            return
        key = address[len(prefix):]
        if key in ("state/capabilities", "state/renderer") and len(args) == 1:
            value = json.loads(args[0], parse_constant=_reject_json_constant, parse_float=_json_float)
            if not isinstance(value, dict):
                return
            if key == "state/capabilities":
                self.capabilities = value
                self.registered = True
                self.send(prefix + "control/metering", 1)
            else:
                if not isinstance(value.get("binaural", {}), dict):
                    return
                self.renderer = value
            self.last_seen = time.monotonic()
        elif key == "heartbeat/ack":
            self.last_seen = time.monotonic()
        elif key == "heartbeat/unknown":
            self.registered = False
            self.capabilities = {}
            self.renderer = {}
            self.config_path = self.config_status = None
        elif key == "state/shutdown":
            self.capabilities = {}
            self.last_seen = 0
        elif key == "state/clip" and args:
            self.clipping = args[0]
        elif key == "state/realtime/master_gain" and args:
            self.master_gain = args[0]
        elif key == "spatial/frame" and len(args) >= 3 and type(args[2]) is int:
            self.object_count = max(0, args[2])
        elif key == "state/render/config_path" and args and isinstance(args[0], str):
            self.config_path = args[0]
        elif key == "state/render/config_status" and args and isinstance(args[0], str):
            self.config_status = args[0]
        elif key == "state/render/bridge_error" and args and args[0]:
            self.error = "The decoder bridge reported an error; check the installed engine/bridge pair"

    @property
    def ready(self):
        binaural = self.renderer.get("binaural")
        return (self.capabilities.get("producer") == "renderer"
                and self.capabilities.get("variant") == "embedded"
                and self.capabilities.get("host") == "mpv"
                and isinstance(binaural, dict) and binaural.get("outputMode") == "binaural"
                and (self.expected_config is None or (self.config_path == self.expected_config
                     and self.config_status == "loaded"))
                and time.monotonic() - self.last_seen < 4)


class AudioRuntime:
    def __init__(self, *, binary="mpv", bridge_path="/usr/lib/orender/libharletty_bridge.so",
                 runtime_dir=None, library_path=None, allow_pcm_route=False):
        self.binary = os.fspath(binary)
        self.bridge_path = Path(bridge_path).expanduser() if bridge_path else None
        self.library_path = Path(library_path or "/usr/lib/liborender.so.0").expanduser()
        self.runtime_dir = Path(runtime_dir).expanduser() if runtime_dir else None
        self.allow_pcm_route = bool(allow_pcm_route)
        self.process = None
        self.reader = self.writer = None
        self.telemetry = RendererTelemetry()
        self._transport = None
        self._temporary = None
        self._tasks = []
        self._pending = {}
        self._sequence = 0
        self._stop_lock = asyncio.Lock()
        self._properties = {}
        self._file_loaded = False
        self._end_reason = ""
        self._sink = None
        self._error = ""
        self._reason = "idle"
        self._pose = None
        self._last_sent = None
        self._offset = IDENTITY
        self._routing = {"state": "idle", "reason": "No owned playback stream"}

    def _bridge(self):
        if self.bridge_path:
            return self.bridge_path
        preferred = Path("/usr/lib/orender/libharletty_bridge.so")
        if preferred.is_file():
            return preferred
        candidates = sorted(Path("/usr/lib/orender").glob("*_bridge.so"))
        return candidates[0] if len(candidates) == 1 else None

    async def probe(self):
        """Check executable features without loading a plugin into this daemon."""
        binary = shutil.which(self.binary)
        if not binary:
            return {"available": False, "reason": "Install mpv-omniphony (stock mpv has no orender decoder)"}

        async def inspect(option):
            process = await asyncio.create_subprocess_exec(
                binary, "--no-config", option, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            try:
                output, _ = await asyncio.wait_for(process.communicate(), 6)
            except BaseException:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise
            return process.returncode, output.decode("utf-8", "replace")

        try:
            returncode, options = await inspect("--list-options")
        except (OSError, asyncio.TimeoutError):
            return {"available": False, "reason": "The player capability check failed"}
        if returncode != 0:
            return {"available": False,
                    "reason": f"The player capability check failed (--list-options exited {returncode})"}
        # mpv-omniphony 0.5.2 selects orender only inside reinit_decoder when
        # explicitly requested. Its public --ad=help list contains lavc's
        # decoders and does not advertise this working opt-in decoder. The
        # ad-orender option group is compiled only with HAVE_ORENDER; require
        # its exact controls here, then verify the actual decoder/ABI/objects
        # through the playback handshake below.
        required = ("ad-orender-config", "ad-orender-osc-rx-port", "ad-orender-osc-bind",
                    "ad-orender-osc-port", "ad-orender-osc-monitor-target", "input-ipc-server",
                    "ad-orender-library")
        advertised = set(re.findall(r"^\s*--([a-zA-Z0-9][a-zA-Z0-9-]*)(?=\s|$)", options, re.MULTILINE))
        missing = [key for key in required if key not in advertised]
        if missing:
            return {"available": False,
                    "reason": "This mpv build lacks required Omniphony decoder/control options: "
                              + ", ".join("--" + key for key in missing)}
        bridge = self._bridge()
        if bridge is None or not bridge.is_file():
            return {"available": False, "reason": "Install the decoder bridge or configure its shared-library path"}
        if not self.library_path.is_file():
            return {"available": False, "reason": "Install orender-spatial: the configured engine library does not exist"}
        if not await library_supports_loopback(self.library_path):
            return {"available": False, "reason": "Install orender-spatial: this engine library cannot guarantee a localhost-only OSC listener"}
        return {"available": True, "binary": binary,
                "reason": "Player features found; engine ABI and binaural output are checked during playback"}

    async def start(self, media, sink, *, require_spatial=False):
        """Start a real player and load media over private IPC, keeping URLs out of argv."""
        media = media_source(media)
        if not isinstance(sink, Sink) or not sink.usable:
            raise AudioError("Waiting for the selected headphones' A2DP output")
        probe = await self.probe()
        if not probe["available"]:
            self._error = probe["reason"]
            raise AudioError(self._error)
        await self.stop()
        self._error, self._reason = "", "starting"
        self._sink = sink
        self._properties = {}
        self._file_loaded = False
        self._end_reason = ""
        self.telemetry = RendererTelemetry()
        self._last_sent = None
        self._offset = IDENTITY
        self._routing = {"state": "waiting", "reason": "Waiting for the player's PipeWire links"}
        root = self.runtime_dir
        if root is None and os.environ.get("XDG_RUNTIME_DIR"):
            root = Path(os.environ["XDG_RUNTIME_DIR"])
        try:
            if root is not None:
                root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._temporary = tempfile.TemporaryDirectory(prefix="spatial-audio-", dir=root)
            private = Path(self._temporary.name)
            config, ipc = private / "renderer.yaml", private / "mpv.sock"
            self.telemetry.expected_config = str(config)
            if len(os.fsencode(ipc)) > 100:
                raise AudioError("The runtime directory path is too long for the player's IPC socket")
            config.write_text(config_text(self._bridge()), encoding="utf-8")
            config.chmod(0o600)
        except (OSError, AudioError) as exc:
            await self.stop("configuration error")
            self._error = str(exc) if isinstance(exc, AudioError) else "Could not create the player's private runtime files"
            raise AudioError(self._error) from None
        try:
            loop = asyncio.get_running_loop()
            self._transport, _ = await loop.create_datagram_endpoint(
                lambda: self.telemetry, local_addr=("127.0.0.1", 0))
            monitor_port = self._transport.get_extra_info("sockname")[1]
            # Reserve a free target for this launch. mpv owns the socket after
            # exec; the handshake below refuses any non-mpv renderer there.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
                reservation.bind(("127.0.0.1", 0))
                control_port = reservation.getsockname()[1]
            self.telemetry.target = ("127.0.0.1", control_port)
            args = playback_argv(probe["binary"], config, ipc, sink, control_port,
                                 monitor_port, self.library_path)
            env = os.environ.copy()
            env["OMNIPHONY_OSC_BIND"] = "127.0.0.1"
            env["PIPEWIRE_PROPS"] = json.dumps({"node.dont-fallback": True,
                "node.dont-reconnect": not self.allow_pcm_route,
                "node.dont-move": not self.allow_pcm_route,
                "target.object": sink.serial,
                "application.name": "Spatial audio"})
            self.process = await asyncio.create_subprocess_exec(
                *args, env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            deadline = loop.time() + 8
            while loop.time() < deadline:
                if self.process.returncode is not None:
                    raise AudioError("The player exited before its control connection was ready")
                try:
                    self.reader, self.writer = await asyncio.open_unix_connection(str(ipc), limit=1024 * 1024)
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    await asyncio.sleep(0.05)
            else:
                raise AudioError("The player did not establish its control connection")
            self._tasks.append(asyncio.create_task(self._read_ipc()))
            for index, name in enumerate(("track-list", "pause", "time-pos", "duration",
                    "volume", "audio-params", "audio-out-params"), 1):
                await self.command("observe_property", index, name)
            await self.command("loadfile", media, "replace")
            self._tasks.append(asyncio.create_task(self._heartbeat()))
            if require_spatial:
                await self._require_spatial()
            await self.command("set_property", "pause", False)
            self._reason = "playing"
        except BaseException as exc:
            await self.stop("start failed")
            if isinstance(exc, asyncio.CancelledError):
                raise
            self._error = str(exc) if isinstance(exc, AudioError) else "Could not start the audio player"
            raise AudioError(self._error) from None
        return self.status()

    async def _require_spatial(self, timeout=20):
        """Keep expected object content paused until decoded facts confirm it."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            state = self.status()
            if state["renderer_ready"] and state["source_mode"] == "spatial":
                return
            if self.process is None or self.process.returncode is not None or self._end_reason == "error":
                break
            await asyncio.sleep(0.1)
        raise AudioError("Spatial playback could not be verified: a compatible decoder bridge, binaural engine, and decoded audio objects are required")

    async def command(self, *args):
        if self.writer is None or self.writer.is_closing():
            raise AudioError("The player control connection is unavailable")
        self._sequence += 1
        request_id = self._sequence
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            self.writer.write((json.dumps({"command": args, "request_id": request_id}) + "\n").encode())
            await self.writer.drain()
            response = await asyncio.wait_for(future, 3)
            if response.get("error") != "success":
                raise AudioError("The player could not apply the requested operation")
            return response.get("data")
        except (OSError, asyncio.TimeoutError):
            raise AudioError("The player control connection stopped responding") from None
        finally:
            self._pending.pop(request_id, None)

    async def _read_ipc(self):
        try:
            while line := await self.reader.readline():
                try:
                    data = json.loads(line, parse_constant=_reject_json_constant, parse_float=_json_float)
                except (ValueError, UnicodeDecodeError, RecursionError):
                    continue
                if not isinstance(data, dict):
                    continue
                future = self._pending.get(data.get("request_id"))
                if future is not None and not future.done():
                    future.set_result(data)
                if data.get("event") == "property-change":
                    self._properties[data.get("name")] = data.get("data")
                elif data.get("event") == "file-loaded":
                    self._file_loaded = True
                    self._end_reason = ""
                elif data.get("event") == "end-file":
                    self._file_loaded = False
                    self._end_reason = data.get("reason", "unknown")
                    self._reason = "finished"
                    if data.get("reason") == "error":
                        self._error = "Playback failed; verify the media, decoder bridge, and headphone connection"
                    self.telemetry.object_count = None
                    self.telemetry.capabilities = {}
                    self.telemetry.renderer = {}
        except (OSError, ValueError):
            self._error = "The player control connection was lost"
        finally:
            for pending in self._pending.values():
                if not pending.done():
                    pending.set_exception(AudioError("The player control connection was lost"))

    async def _heartbeat(self):
        try:
            while self.process is not None and self.process.returncode is None:
                telemetry = self.telemetry
                action = "heartbeat" if telemetry.registered else "register"
                telemetry.send("/omniphony/" + action)
                # UDP pose updates are repeated so one lost datagram cannot
                # leave an old rotation in place after tracking is switched off.
                if telemetry.ready:
                    self._send_pose(force=True)
                # Older mpv versions do not notify track-list when the decoder
                # changes its profile after decode; poll this cheap property.
                with contextlib.suppress(AudioError):
                    self._properties["track-list"] = await self.command("get_property", "track-list")
                await asyncio.sleep(1)
            if self.process is not None and self.process.returncode not in (None, 0):
                self._error = "The audio player exited unexpectedly"
        except asyncio.CancelledError:
            raise
        except OSError:
            self._error = "The renderer control connection was lost"

    def set_pose(self, pose):
        self._pose = Quaternion.parse(pose.values()) if pose is not None else None
        if self.telemetry.ready:
            self._send_pose()

    def _send_pose(self, *, force=False):
        pose = self._offset * self._pose if self._pose else IDENTITY
        values = tuple(map(float, renderer_pose(pose)))
        if force or values != self._last_sent:
            self.telemetry.send("/omniphony/control/head/quat", *values)
            self._last_sent = values

    def recenter(self):
        """Standalone API; daemon callers should instead recenter Engine then set_pose."""
        self._offset = self._pose.inverse() if self._pose else IDENTITY
        if self.telemetry.ready:
            self._send_pose()

    async def pause(self, paused=True):
        if type(paused) is not bool:
            raise ValueError("pause state must be boolean")
        await self.command("set_property", "pause", paused)

    async def seek(self, seconds, *, absolute=False):
        if type(seconds) not in (int, float) or not math.isfinite(seconds):
            raise ValueError("seek position must be finite seconds")
        await self.command("seek", seconds, "absolute" if absolute else "relative")

    async def set_volume(self, percent):
        if type(percent) not in (int, float) or not math.isfinite(percent) or not 0 <= percent <= 100:
            raise ValueError("volume must be between 0 and 100 percent")
        await self.command("set_property", "volume", percent)

    async def update_sink(self, sink):
        if self.process is None:
            return
        # Numeric IDs and even node names may be reused after reconnection;
        # a different serial marks a different physical-output session.
        if (sink is None or not sink.usable or self._sink is None
                or (sink.device, sink.name, sink.serial) !=
                   (self._sink.device, self._sink.name, self._sink.serial)):
            await self.stop("headphones disconnected")

    def verify_output(self, objects, *, allowed_filter_inputs=(), pending_filter_inputs=()):
        """Audit the owned stream against a real ``pw-dump`` snapshot.

        The desktop provider may call this whenever its graph changes. A
        ``violation`` requires stopping playback; ``waiting`` means no stream or
        links have appeared yet. ``allowed_filter_inputs`` may only contain
        node IDs from a filter module that independently verified its owned
        output's complete link chain to this exact physical sink session.
        ``pending_filter_inputs`` contains independently validated owned inputs
        still awaiting their output links. Their supervisor must enforce a
        bounded connection timeout; they are reported as waiting, never verified.
        Nothing is linked, moved, or created here.
        """
        if self.process is None or self._sink is None:
            self._routing = {"state": "idle", "reason": "No owned playback stream"}
            return dict(self._routing)
        pid = str(self.process.pid)
        nodes, clients = {}, set()
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            props = (obj.get("info") or {}).get("props") or {}
            if obj.get("type") == "PipeWire:Interface:Client" and str(props.get("application.process.id")) == pid:
                clients.add(str(obj.get("id")))
            if obj.get("type") == "PipeWire:Interface:Node":
                nodes[str(obj.get("id"))] = props
        output_ids = {node for node, props in nodes.items()
                      if props.get("media.class") == "Stream/Output/Audio"
                      and (str(props.get("application.process.id")) == pid
                           or str(props.get("client.id")) in clients)}
        if not output_ids:
            self._routing = {"state": "waiting", "reason": "Waiting for the player's PipeWire stream"}
            return dict(self._routing)
        target_ids = {node for node, props in nodes.items()
                      if props.get("node.name") == self._sink.name
                      and str(props.get("object.serial")) == self._sink.serial}
        target_ids.update(str(node) for node in allowed_filter_inputs)
        for node in output_ids:
            props = nodes[node]
            if (str(props.get("target.object")) not in (self._sink.name, self._sink.serial)
                    or str(props.get("node.dont-fallback")).lower() != "true"
                    or str(props.get("node.dont-reconnect")).lower() != str(not self.allow_pcm_route).lower()):
                self._routing = {"state": "violation", "reason": "The player's PipeWire routing protections are missing"}
                return dict(self._routing)
        linked = False
        pending = False
        pending_ids = {str(node) for node in pending_filter_inputs}
        for obj in objects:
            if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Link":
                continue
            info = obj.get("info") or {}
            if str(info.get("output-node-id")) in output_ids:
                destination = str(info.get("input-node-id"))
                if destination in pending_ids:
                    pending = True
                    continue
                if destination not in target_ids:
                    self._routing = {"state": "violation", "reason": "The playback stream is linked to another output"}
                    return dict(self._routing)
                linked = True
        if pending:
            self._routing = {"state": "waiting", "reason": "Waiting for the equalizer's physical output link"}
            return dict(self._routing)
        self._routing = ({"state": "verified", "reason": "The player is linked to the selected physical headphones"}
                         if linked else {"state": "waiting", "reason": "Waiting for the player's PipeWire links"})
        return dict(self._routing)

    async def stop(self, reason="stopped"):
        async with self._stop_lock:
            tasks, self._tasks = self._tasks, []
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if self.writer is not None:
                self.writer.close()
                with contextlib.suppress(OSError):
                    await self.writer.wait_closed()
            self.writer = self.reader = None
            if self.process is not None and self.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 3)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        self.process.kill()
                    await self.process.wait()
            self.process = None
            if self._transport is not None:
                self._transport.close()
            self._transport = None
            self.telemetry.transport = None
            self.telemetry.capabilities = {}
            self.telemetry.renderer = {}
            self._file_loaded = False
            self._end_reason = reason
            self._sink = None
            self._reason = reason
            self._routing = {"state": "idle", "reason": "No owned playback stream"}
            if self._temporary is not None:
                self._temporary.cleanup()
            self._temporary = None

    def status(self):
        running = self.process is not None and self.process.returncode is None
        tracks = self._properties.get("track-list") or []
        if not isinstance(tracks, list):
            tracks = []
        selected = next((t for t in tracks if isinstance(t, dict)
                         and t.get("type") == "audio" and t.get("selected")), {})
        decoder = selected.get("decoder") or ""
        ready = running and self._file_loaded and decoder == "orender" and self.telemetry.ready
        profile = str(selected.get("codec-profile") or "")
        # This suffix is written by ad_orender from the *decoded* object count,
        # not from a container's Atmos hint or the capability's spatial flag.
        object_match = re.search(r"(?:\+|\s)([1-9][0-9]*) objects\b", str(profile))
        objects = int(object_match[1]) if object_match else 0
        content = "ordinary audio"
        if ready and objects:
            content = "Dolby Atmos" if "Atmos" in profile else "DTS:X" if "DTS:X" in profile else "object audio"
        elif ready:
            content = "binaural channel audio"
        mode = "spatial" if ready and objects else "stereo" if running and self._file_loaded else "none"
        binaural = self.telemetry.renderer.get("binaural") or {}
        error = self._error or self.telemetry.error
        if running and self._file_loaded and decoder != "orender" and selected.get("codec") in ("truehd", "eac3", "ac3", "dts"):
            error = error or "Omniphony did not decode this track; ordinary playback is active"
        return {"running": running, "renderer_ready": bool(ready), "source_mode": mode,
                "pcm_route_allowed": self.allow_pcm_route,
                "content_format": content if mode != "none" else "none",
                "decoder": decoder if running else None,
                "codec": selected.get("codec") if running else None,
                "process_id": getattr(self.process, "pid", None) if running else None,
                "audio_parameters": self._properties.get("audio-params") if running else None,
                "output": self._sink.name if self._sink else None,
                "error": error, "state": self._reason,
                "end_reason": self._end_reason, "loaded": self._file_loaded,
                "paused": bool(self._properties.get("pause", False)),
                "position": self._properties.get("time-pos"),
                "duration": self._properties.get("duration"),
                "volume": self._properties.get("volume", 100),
                "routing": dict(self._routing),
                "capabilities": dict(self.telemetry.capabilities),
                "hrir_effective": binaural.get("hrirEffective"),
                "hrir_error": binaural.get("hrirError"),
                "clipping": self.telemetry.clipping if ready else None,
                "master_gain": self.telemetry.master_gain if ready else None,
                "object_count": objects if ready else 0,
                "tracking": bool(ready and self._pose is not None)}
