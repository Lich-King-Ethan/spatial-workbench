"""Opt-in application PCM → temporary Omniphony input → physical headphones.

Only the explicitly selected stream's routing metadata is changed. The global
default is never changed, and PCM input is never labelled as decoded Atmos.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import tempfile
import time
from types import SimpleNamespace
import uuid

from . import pipewire
from .audio_runtime import AudioError, RendererTelemetry, config_text, renderer_pose
from .core import Sink
from .pose import IDENTITY, Quaternion
from .processes import stop_child


def _props(obj):
    return (obj.get("info") or {}).get("props") or obj.get("props") or {}


def _nodes(objects):
    return {str(obj["id"]): _props(obj) for obj in objects if isinstance(obj, dict)
            and obj.get("type") == "PipeWire:Interface:Node" and "id" in obj}


def available_streams(objects):
    """Actual movable application outputs, identified by their current serial."""
    found = []
    for props in _nodes(objects).values():
        name = str(props.get("node.name", ""))
        if (props.get("media.class") != "Stream/Output/Audio"
                or str(props.get("node.dont-move", "false")).lower() == "true"
                or name.startswith(("omniphony", "spatial-live-", "spatial-reference-"))
                or props.get("media.role") in ("DSP", "Filter")
                or props.get("object.serial") is None):
            continue
        label = props.get("application.name") or props.get("media.name") or name
        if isinstance(label, str) and label:
            found.append({"serial": str(props["object.serial"]), "name": label})
    return sorted(found, key=lambda item: (item["name"].casefold(), item["serial"]))


def _stream(objects, serial):
    matches = [(node, props) for node, props in _nodes(objects).items()
               if str(props.get("object.serial")) == str(serial)]
    if len(matches) != 1:
        raise AudioError("The selected application stream is no longer available")
    return matches[0]


def _metadata(objects, subject, key="target.object"):
    for obj in objects:
        if (isinstance(obj, dict) and obj.get("type") == "PipeWire:Interface:Metadata"
                and _props(obj).get("metadata.name") == "default"):
            for entry in obj.get("metadata", []):
                if str(entry.get("subject")) == str(subject) and entry.get("key") == key:
                    return {"type": entry.get("type"), "value": entry.get("value")}
    return None


async def _command(*args, timeout=5):
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    process = await asyncio.create_subprocess_exec(
        *map(str, args), env=environment,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise AudioError(f"{Path(args[0]).name} failed; check the user PipeWire session")
    return stdout.decode("utf-8", "replace")


GUARD_KEY = "spatiald.live-target"


def _guard_available(objects):
    marker = _metadata(objects, "0", "spatiald.live-guard")
    return marker is not None and str(marker.get("value")) == "1"


async def _set_target(node, entry, key="target.object"):
    if not str(node).isdigit():
        raise AudioError("Invalid PipeWire stream identifier")
    if entry is None:
        await _command("pw-metadata", "--name", "default", "--delete", node, key)
    else:
        value = entry["value"]
        if not isinstance(value, str):
            value = json.dumps(value, separators=(",", ":"))
        args = ["pw-metadata", "--name", "default", node, key, value]
        if entry.get("type"):
            args.append(entry["type"])
        await _command(*args)


async def _muted(node):
    output = await _command("wpctl", "get-volume", str(node))
    if not re.fullmatch(r"\s*Volume:\s*[0-9]+(?:\.[0-9]+)?(?:\s+\[MUTED\])?\s*", output):
        raise AudioError("Could not read the selected application's mute control")
    return "[MUTED]" in output


class Route:
    """One stream metadata transaction. Never restore over a later user choice."""
    def __init__(self, stream_serial, node, before, target_serial):
        self.stream_serial = str(stream_serial)
        self.node = str(node)
        self.before = before
        self.target_serial = str(target_serial)
        self.applied = False
        self.guarded = False

    @property
    def guard_value(self):
        return f"{self.stream_serial}:{self.target_serial}"

    async def apply(self, objects):
        node, _ = _stream(objects, self.stream_serial)
        if node != self.node or _metadata(objects, node) != self.before:
            raise AudioError("Application routing changed during setup; select it again")
        if not _guard_available(objects):
            raise AudioError("Restart WirePlumber after installing the application routing guard")
        if _metadata(objects, node, GUARD_KEY) is not None:
            raise AudioError("This application already has a protected spatial audio route")
        self.guarded = True
        await _set_target(node, {"type": "Spa:String", "value": self.guard_value}, GUARD_KEY)
        # Mark first: if a command times out after writing, cleanup still checks
        # the actual metadata and can undo our write.
        self.applied = True
        await _set_target(node, {"type": "Spa:Id", "value": self.target_serial})

    async def restore(self, objects):
        if not self.applied and not self.guarded:
            return
        try:
            node, _ = _stream(objects, self.stream_serial)
        except AudioError:
            self.applied = False  # object destruction already removes its metadata
            self.guarded = False
            return
        current = _metadata(objects, node)
        if node == self.node and current is not None and str(current["value"]) == self.target_serial:
            await _set_target(node, self.before)
        # Remove protection only after the route was restored or a newer user
        # choice superseded it. Never remove another session's marker.
        guard = _metadata(objects, node, GUARD_KEY)
        if node == self.node and guard and guard.get("value") == self.guard_value:
            await _set_target(node, None, GUARD_KEY)
        # A user/application changed the destination: their newer choice wins.
        self.applied = False
        self.guarded = False


def live_config(bridge_path, input_node, fifo):
    # v0.5.2's actual working path is `render` + input_mode: live. The separate
    # `input-live` subcommand exists but explicitly returns "not implemented".
    return (config_text(bridge_path) +
            "  enable_vbap: true\n"
            "  input_mode: live\n"
            f"  input_pipe: {json.dumps(str(fifo))}\n"
            "  live_input:\n"
            "    backend: pipewire\n"
            f"    node: {json.dumps(input_node)}\n"
            "    description: Application spatial audio input\n"
            "    channels: 8\n"
            "    sample_rate: 48000\n"
            "    sample_format: f32\n"
            "    map: seven-one-fixed\n"
            "    lfe_mode: direct\n")


class LiveTelemetry(RendererTelemetry):
    def __init__(self, input_node):
        super().__init__()
        self.input_node = input_node
        self.input = {}

    def accept(self, address, args):
        if address == "/omniphony/state/input" and len(args) == 1:
            value = json.loads(args[0])
            if isinstance(value, dict):
                self.input.update(value)  # core and host send complementary fields
        else:
            super().accept(address, args)

    @property
    def ready(self):
        applied = self.input.get("applied") or {}
        binaural = self.renderer.get("binaural") or {}
        return (self.capabilities.get("producer") == "renderer"
                and self.capabilities.get("variant") == "standalone"
                and self.capabilities.get("host") == "cli"
                and isinstance(binaural, dict) and binaural.get("outputMode") == "binaural"
                and self.input.get("activeMode") == "pipewire"
                and isinstance(applied, dict) and applied.get("node") == self.input_node
                and applied.get("streamFormat") == "pipewire-f32"
                and applied.get("channels") == 8 and not applied.get("error")
                and (self.expected_config is None or (self.config_path == self.expected_config
                     and self.config_status == "loaded"))
                and time.monotonic() - self.last_seen < 4)


class LiveAudio:
    def __init__(self, *, binary="orender", bridge_path="/usr/lib/orender/libharletty_bridge.so",
                 runtime_dir=None, authorized_filter_inputs=None, pending_filter_inputs=None):
        self.binary = str(binary)
        self.bridge_path = Path(bridge_path).expanduser()
        self.runtime_dir = Path(runtime_dir) if runtime_dir else None
        self.process = None
        self._temporary = self._transport = self._monitor = self._route = None
        self._lock = asyncio.Lock()
        self._sink = None
        self._input_node = ""
        self._input_serial = ""
        self._stream_serial = self._stream_name = ""
        self._state, self._reason, self._error = "idle", "Not enabled", ""
        self._audit = {"state": "idle", "reason": "Not enabled"}
        self.telemetry = LiveTelemetry("")
        self._pose = IDENTITY
        self._launch_task = None
        self._authorized_filter_inputs = authorized_filter_inputs
        self._pending_filter_inputs = pending_filter_inputs
        self._before_muted = None
        self._failure_mute = None

    async def probe(self):
        binary = shutil.which(self.binary)
        if not binary or any(not shutil.which(tool) for tool in ("pw-metadata", "pw-dump", "wpctl")):
            raise AudioError("Install orender 0.5.2 and PipeWire command-line tools")
        if not self.bridge_path.is_file():
            raise AudioError("The installed Omniphony decoder bridge is required by its live runtime")
        version = await _command(binary, "--version")
        if not re.search(r"(?<![\w.])v?0\.5\.2(?:\+spatial-loopback1)?(?![\w.+-])", version):
            raise AudioError("Application PCM capture requires the verified orender 0.5.2 release")
        if "+spatial-loopback1" not in version:
            raise AudioError("Install the packaged orender with loopback control support (+spatial-loopback1)")
        help_text = await _command(binary, "render", "--help")
        if any(flag not in help_text for flag in ("--output-device", "--osc-rx-port", "--continuous")):
            raise AudioError("This orender build lacks the required live-render control options")
        return binary

    async def start(self, sink, stream_serial, objects):
        """Accept an explicit request; slow device readiness runs outside D-Bus."""
        await self.stop("replaced")
        if not isinstance(sink, Sink) or not sink.usable:
            raise AudioError("Connect the selected headphones before enabling application spatial audio")
        chosen = next((entry for entry in available_streams(objects)
                       if entry["serial"] == str(stream_serial)), None)
        if chosen is None:
            raise AudioError("Select an available, movable application playback stream")
        if not _guard_available(objects):
            raise AudioError("Restart WirePlumber after installing the application routing guard")
        self._sink = sink
        self._stream_serial, self._stream_name = chosen["serial"], chosen["name"]
        self._state, self._reason, self._error = "starting", "Waiting for live PCM renderer", ""

        async def launch():
            try:
                await self._start(sink, stream_serial, objects)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.process is None:
                    self._state = "idle"
                if not self._error:
                    self._error = str(exc) if isinstance(exc, AudioError) else "Could not start application spatial audio"
        self._launch_task = asyncio.create_task(launch())
        return self.status()

    async def _start(self, sink, stream_serial, objects):
        async with self._lock:
            if not isinstance(sink, Sink) or not sink.usable:
                raise AudioError("Connect the selected headphones before enabling application spatial audio")
            chosen = next((entry for entry in available_streams(objects)
                           if entry["serial"] == str(stream_serial)), None)
            if chosen is None:
                raise AudioError("Select an available, movable application playback stream")
            binary = await self.probe()
            self._sink = sink
            self._stream_serial, self._stream_name = chosen["serial"], chosen["name"]
            self._state, self._reason, self._error = "starting", "Waiting for live PCM renderer", ""
            self._input_node = "spatial-live-" + uuid.uuid4().hex
            self.telemetry = LiveTelemetry(self._input_node)
            try:
                parent = self.runtime_dir or os.environ.get("XDG_RUNTIME_DIR")
                self._temporary = tempfile.TemporaryDirectory(prefix="spatial-live-", dir=parent)
                directory = Path(self._temporary.name)
                config, fifo = directory / "renderer.yaml", directory / "unused-bitstream.fifo"
                os.mkfifo(fifo, 0o600)
                config.write_text(live_config(self.bridge_path, self._input_node, fifo))
                config.chmod(0o600)
                self.telemetry.expected_config = str(config)
                loop = asyncio.get_running_loop()
                self._transport, _ = await loop.create_datagram_endpoint(
                    lambda: self.telemetry, local_addr=("127.0.0.1", 0))
                monitor_port = self._transport.get_extra_info("sockname")[1]
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
                    reservation.bind(("127.0.0.1", 0))
                    control_port = reservation.getsockname()[1]
                self.telemetry.target = ("127.0.0.1", control_port)
                argv = [binary, "--config", str(config), "render", "--continuous", "--enable-vbap",
                        "--output-backend", "pipewire", "--output-device", sink.name,
                        "--latency-target-ms", "40", "--pw-quantum", "256",
                        "--osc", "--osc-host", "127.0.0.1", "--osc-port", str(monitor_port),
                        "--osc-rx-port", str(control_port), str(fifo)]
                environment = os.environ.copy()
                # No target.object here: environment properties affect BOTH the
                # input and output streams. The output is pinned by --output-device.
                environment["PIPEWIRE_PROPS"] = json.dumps({"priority.session": 0,
                    "node.dont-fallback": True, "node.dont-reconnect": True})
                environment["OMNIPHONY_INPUT_PIPE"] = str(fifo)
                environment["OMNIPHONY_CONFIG_DIR"] = str(directory)
                environment["OMNIPHONY_OSC_BIND"] = "127.0.0.1"
                self.process = await asyncio.create_subprocess_exec(
                    *argv, env=environment, stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True)
                deadline = loop.time() + 20
                while loop.time() < deadline:
                    if self.process.returncode is not None:
                        raise AudioError("The live renderer exited during startup")
                    self.telemetry.send("/omniphony/register")
                    current = await pipewire.capture()
                    inputs = self._owned_input(current)
                    if len(inputs) == 1 and self.telemetry.ready:
                        input_id, props = inputs[0]
                        self._input_serial = str(props["object.serial"])
                        node, _ = _stream(current, self._stream_serial)
                        self._route = Route(self._stream_serial, node, _metadata(current, node),
                                            self._input_serial)
                        self._before_muted = await _muted(node)
                        if self.audit(current)["state"] == "violation":
                            raise AudioError(self._audit["reason"])
                        await self._route.apply(await pipewire.capture())
                        break
                    await asyncio.sleep(.1)
                else:
                    raise AudioError("The renderer did not confirm binaural PCM input; check engine compatibility")
                deadline = loop.time() + 6
                while loop.time() < deadline:
                    checked = self.audit(await pipewire.capture())
                    if checked["state"] == "violation":
                        raise AudioError(checked["reason"])
                    if checked["state"] == "ready":
                        break
                    await asyncio.sleep(.1)
                else:
                    raise AudioError("Application audio did not establish its path to the headphones")
                self._state, self._reason = "playing", "PCM spatial audio"
                self.set_pose(self._pose)
                self._monitor = asyncio.create_task(self._watch())
            except BaseException as exc:
                await self._stop("setup failed")
                if isinstance(exc, asyncio.CancelledError):
                    raise
                self._error = str(exc) if isinstance(exc, AudioError) else "Could not start application spatial audio"
                raise AudioError(self._error) from None
            return self.status()

    def _owned_nodes(self, objects):
        if self.process is None:
            return {}
        pid = str(self.process.pid)
        clients = {str(obj["id"]) for obj in objects if isinstance(obj, dict)
                   and obj.get("type") == "PipeWire:Interface:Client"
                   and str(_props(obj).get("application.process.id")) == pid}
        return {node: props for node, props in _nodes(objects).items()
                if str(props.get("application.process.id")) == pid or str(props.get("client.id")) in clients}

    def _owned_input(self, objects):
        return [(node, props) for node, props in self._owned_nodes(objects).items()
                if props.get("node.name") == self._input_node
                and props.get("media.class") == "Audio/Sink" and props.get("object.serial") is not None]

    def audit(self, objects, *, allowed_filter_inputs=None):
        def result(state, reason):
            self._audit = {"state": state, "reason": reason}
            return dict(self._audit)
        if self.process is None or self._sink is None:
            return result("idle", "Not enabled")
        if self.process.returncode is not None:
            return result("violation", "The live renderer stopped")
        nodes = _nodes(objects)
        physical = {node for node, props in nodes.items()
                    if props.get("node.name") == self._sink.name
                    and str(props.get("object.serial")) == self._sink.serial
                    and props.get("media.class") == "Audio/Sink"}
        if len(physical) != 1:
            return result("violation", "The selected physical headphone output disappeared")
        owned = self._owned_nodes(objects)
        inputs = {node for node, props in owned.items() if props.get("node.name") == self._input_node
                  and props.get("media.class") == "Audio/Sink"}
        outputs = {node for node, props in owned.items() if props.get("media.class") == "Stream/Output/Audio"}
        if len(inputs) > 1:
            return result("violation", "Ambiguous live capture input")
        if self._input_serial and any(str(nodes[node].get("object.serial")) != self._input_serial for node in inputs):
            return result("violation", "The live capture input was replaced")
        try:
            selected, _ = _stream(objects, self._stream_serial)
        except AudioError:
            return result("violation", "The selected application stream ended")
        if self._route and self._route.applied:
            guard = _metadata(objects, selected, GUARD_KEY)
            target = _metadata(objects, selected)
            if not _guard_available(objects) or not guard or guard.get("value") != self._route.guard_value:
                return result("violation", "Application fallback protection disappeared")
            if not target or str(target.get("value")) != self._route.target_serial:
                return result("violation", "Application routing changed while spatial audio was active")
        if allowed_filter_inputs is None:
            allowed_filter_inputs = (self._authorized_filter_inputs(objects)
                                     if self._authorized_filter_inputs is not None else ())
        allowed = physical | set(map(str, allowed_filter_inputs))
        pending = set(map(str, self._pending_filter_inputs(objects))) if self._pending_filter_inputs else set()
        for node in outputs:
            props = owned[node]
            if (str(props.get("target.object")) not in (self._sink.name, self._sink.serial)
                    or str(props.get("node.dont-fallback")).lower() != "true"
                    or str(props.get("node.dont-move")).lower() != "true"):
                return result("violation", "Renderer output routing protections are missing")
        output_ports = {}
        for obj in objects:
            if isinstance(obj, dict) and obj.get("type") == "PipeWire:Interface:Port":
                props = _props(obj)
                if props.get("port.direction") == "out" and str(props.get("port.control", "false")).lower() != "true":
                    output_ports.setdefault(str(props.get("node.id")), set()).add(str(obj.get("id")))
        incoming, outgoing, selected_targets = False, set(), set()
        linked_ports = set()
        for obj in objects:
            if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Link":
                continue
            info = obj.get("info") or {}
            source, target = str(info.get("output-node-id")), str(info.get("input-node-id"))
            established = info.get("state") in ("active", "paused")
            if info.get("state") == "error" and (source == selected or source in outputs or target in inputs):
                return result("violation", "An application spatial audio link reported an error")
            if source == selected:
                selected_targets.add(target)
            if target in inputs:
                if source != selected:
                    return result("violation", "An unselected stream is entering the live capture input")
                incoming = incoming or established
                if established:
                    linked_ports.add(str(info.get("output-port-id")))
            if source in outputs:
                if target not in allowed | pending:
                    return result("violation", "Renderer output is linked outside the selected headphones")
                if established and target in allowed:
                    outgoing.add(source)
                    linked_ports.add(str(info.get("output-port-id")))
            if (self._state == "playing" and self._route and self._route.applied
                    and source == selected and target not in inputs):
                return result("violation", "Application routing changed while spatial audio was active")
        complete = (bool(output_ports.get(selected)) and all(output_ports.get(node) for node in outputs)
                    and all(output_ports.get(node, set()) <= linked_ports for node in outputs | {selected}))
        if (incoming and selected_targets <= inputs and outputs and complete
                and outgoing == outputs and self.telemetry.ready):
            return result("ready", "Selected application → binaural renderer → physical headphones")
        return result("waiting", "Waiting for the selected application's complete audio path")

    def pending_input_ids(self, objects):
        """Owned capture nodes safe for the selected source while links settle."""
        if not self._route or not self._route.applied or not self._input_serial:
            return set()
        try:
            node, _ = _stream(objects, self._stream_serial)
        except AudioError:
            return set()
        target = _metadata(objects, node)
        guard = _metadata(objects, node, GUARD_KEY)
        if (node != self._route.node or target is None or str(target.get("value")) != self._input_serial
                or not guard or guard.get("value") != self._route.guard_value
                or not _guard_available(objects)
                or self.audit(objects)["state"] not in ("waiting", "ready")):
            return set()
        return {node for node, props in self._owned_input(objects)
                if str(props.get("object.serial")) == self._input_serial}

    def verified_input_ids(self, objects):
        pending = self.pending_input_ids(objects)
        return pending if self._audit.get("state") == "ready" else set()

    async def _watch(self):
        missing_since = None
        try:
            while self.process is not None:
                self.telemetry.send("/omniphony/heartbeat" if self.telemetry.registered else "/omniphony/register")
                self.set_pose(self._pose)
                checked = self.audit(await pipewire.capture())
                if checked["state"] == "violation":
                    raise AudioError(checked["reason"])
                if checked["state"] != "ready":
                    missing_since = missing_since or time.monotonic()
                    if time.monotonic() - missing_since > 4:
                        raise AudioError("The application spatial audio path stopped responding")
                else:
                    missing_since = None
                await asyncio.sleep(.5)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = str(exc) if isinstance(exc, AudioError) else "Live audio graph observation failed"
            await self.stop("connection lost")

    def set_pose(self, pose):
        self._pose = Quaternion.parse(pose.values()) if pose is not None else IDENTITY
        if self.telemetry.ready:
            self.telemetry.send("/omniphony/control/head/quat", *map(float, renderer_pose(self._pose)))

    async def update_sink(self, sink):
        if (self.process is not None or self._state == "starting") and (sink is None or not sink.usable or self._sink is None
                or (sink.device, sink.name, sink.serial) !=
                   (self._sink.device, self._sink.name, self._sink.serial)):
            await self.stop("headphones disconnected")

    async def _stop(self, reason):
        current = asyncio.current_task()
        monitor, self._monitor = self._monitor, None
        if monitor is not None and monitor is not current:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        deliberate = reason in ("stopped", "replaced")
        if self._route is not None and self._route.applied and not deliberate:
            try:
                current_graph = await pipewire.capture()
                node, _ = _stream(current_graph, self._stream_serial)
                target = _metadata(current_graph, node)
                # Respect an explicit newer user destination. Only mute a
                # stream whose routing metadata still belongs to this session.
                if target and str(target["value"]) == self._route.target_serial:
                    await _command("wpctl", "set-mute", node, "1")
                    confirm_graph = await pipewire.capture()
                    confirmed, _ = _stream(confirm_graph, self._stream_serial)
                    if confirmed != node or not await _muted(node):
                        raise AudioError("Application mute was not confirmed")
                    self._failure_mute = {"serial": self._stream_serial,
                                          "before": self._before_muted}
                    self._error = ((self._error + ". ") if self._error else "") + (
                        "The selected application was muted; use Stop or KDE Sound to restore it")
            except AudioError as exc:
                # A destroyed source needs no restoration. A still-present
                # source which cannot be muted must not be moved to speakers.
                try:
                    _stream(await pipewire.capture(), self._stream_serial)
                except (AudioError, OSError):
                    pass
                else:
                    self._state, self._reason = "error", "Capture retained for safe recovery"
                    self._error = str(exc) + "; select the application's output or mute it in KDE Sound"
                    return
            except Exception:
                self._state, self._reason = "error", "Capture retained for safe recovery"
                self._error = "Could not verify application mute; capture was retained instead of restoring another output"
                return
        if self._route is not None:
            try:
                await self._route.restore(await pipewire.capture())
            except Exception:
                self._error = "Could not restore the application route; select its output in KDE Sound settings"
            self._route = None
        if deliberate and self._failure_mute is not None:
            try:
                node, _ = _stream(await pipewire.capture(), self._failure_mute["serial"])
                if self._failure_mute["before"] is False and await _muted(node):
                    await _command("wpctl", "set-mute", node, "0")
            except Exception:
                self._error = "The application remains muted; unmute it in KDE Sound settings"
            self._failure_mute = None
        if self.process is not None:
            await stop_child(self.process)
            self.process = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        self._state, self._reason = "idle", reason
        self._input_serial = ""
        self._audit = {"state": "idle", "reason": reason}

    async def stop(self, reason="stopped"):
        launch, self._launch_task = self._launch_task, None
        if launch is not None and launch is not asyncio.current_task():
            launch.cancel()
            await asyncio.gather(launch, return_exceptions=True)
        async with self._lock:
            await self._stop(reason)

    def cancel_start(self):
        """Immediate cancellation hook for synchronous desktop sink callbacks."""
        if self._launch_task is not None and not self._launch_task.done():
            self._launch_task.cancel()

    def status(self):
        running = self.process is not None and self.process.returncode is None
        return {"state": self._state, "running": running,
                "renderer_ready": bool(running and self.telemetry.ready and self._audit.get("state") == "ready"),
                "source_mode": "pcm", "stream_serial": self._stream_serial,
                "stream_name": self._stream_name, "input_node": self._input_node if running else "",
                "process_id": self.process.pid if running else None,
                "input_serial": self._input_serial if running else "",
                "route_applied": bool(self._route and self._route.applied),
                "error": self._error, "reason": self._reason, "routing": dict(self._audit)}


def audit_snapshot(objects, status, sink, *, allowed_filter_inputs=(), pending_filter_inputs=()):
    """Read-only fresh-graph audit for external diagnostics; never launches audio.

    Telemetry readiness comes from the running daemon. Every identity, link and
    routing protection is independently checked against this fresh graph.
    """
    empty = {"pending_inputs": set(), "verified_inputs": set()}
    if not isinstance(status, dict) or not status.get("running"):
        return {"state": "idle", "reason": "Not enabled", **empty}
    try:
        pid = int(status["process_id"])
        if pid <= 0 or not isinstance(sink, Sink) or not sink.usable:
            raise ValueError
        input_node, input_serial = status["input_node"], str(status["input_serial"])
        stream_serial = str(status["stream_serial"])
        if not isinstance(input_node, str) or not input_node.startswith("spatial-live-"):
            raise ValueError
        if not input_serial.isdigit() or not stream_serial.isdigit():
            raise ValueError
        node, _ = _stream(objects, stream_serial)
    except (KeyError, TypeError, ValueError, AudioError):
        return {"state": "violation", "reason": "Live session identity is missing or stale", **empty}
    live = LiveAudio(authorized_filter_inputs=lambda _: allowed_filter_inputs,
                     pending_filter_inputs=lambda _: pending_filter_inputs)
    live.process = SimpleNamespace(pid=pid, returncode=None)
    live._sink = sink
    live._input_node, live._input_serial = input_node, input_serial
    live._stream_serial = stream_serial
    live._state = status.get("state", "idle")
    live.telemetry = SimpleNamespace(ready=status.get("renderer_ready") is True)
    if status.get("route_applied"):
        live._route = Route(stream_serial, node, None, input_serial)
        live._route.applied = live._route.guarded = True
    result = live.audit(objects)
    result["pending_inputs"] = live.pending_input_ids(objects)
    result["verified_inputs"] = live.verified_input_ids(objects)
    return result
