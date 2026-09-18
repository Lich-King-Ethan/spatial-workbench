#!/usr/bin/env python3
"""Exercise installed routing/EQ against a private, real PipeWire session.

Run as an ordinary user under dbus-run-session. The endpoints and PCM signal are
synthetic: this is a software integration gate, never a Bluetooth/hearing test.
Installed production modules and genuine native binaries are exercised directly.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import random
import shutil
import struct
import sys
import tempfile
import time
import uuid


class Failure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Failure(message)


def props(obj):
    return (obj.get("info") or {}).get("props") or obj.get("props") or {}


def nodes(objects):
    return {str(obj["id"]): obj for obj in objects
            if obj.get("type") == "PipeWire:Interface:Node"}


def named(objects, name):
    found = [obj for obj in nodes(objects).values() if props(obj).get("node.name") == name]
    return found[0] if len(found) == 1 else None


def destinations(objects, source):
    return {str(obj["info"]["input-node-id"]) for obj in objects
            if obj.get("type") == "PipeWire:Interface:Link"
            and str(obj["info"].get("output-node-id")) == str(source)
            and obj["info"].get("state") in ("active", "paused")}


def stereo_linked(objects, source, target):
    """Require both actual output ports, not merely one node-to-node edge."""
    links = [obj["info"] for obj in objects
             if obj.get("type") == "PipeWire:Interface:Link"
             and str(obj["info"].get("output-node-id")) == str(source)
             and str(obj["info"].get("input-node-id")) == str(target)
             and obj["info"].get("state") in ("active", "paused")]
    return (len({link["output-port-id"] for link in links}) == 2
            and len({link["input-port-id"] for link in links}) == 2
            and destinations(objects, source) == {str(target)})


class JSONSequence:
    """pw-dump --monitor emits consecutive JSON arrays, not JSON Lines."""
    def __init__(self):
        self.buffer = ""
        self.decoder = json.JSONDecoder()

    def feed(self, chunk):
        self.buffer += chunk
        result = []
        while self.buffer.strip():
            self.buffer = self.buffer.lstrip()
            try:
                value, end = self.decoder.raw_decode(self.buffer)
            except json.JSONDecodeError:
                break
            require(isinstance(value, list), "pw-dump monitor emitted a non-array")
            result.append(value)
            self.buffer = self.buffer[end:]
        require(len(self.buffer) < 32 * 1024 * 1024, "Unparseable pw-dump monitor output")
        return result


class GraphWatch:
    """Keep every observed link creation, including links removed before polling."""
    def __init__(self):
        self.objects = {}
        self.batches = 0
        self.removals = 0
        self.links = []
        self.link_endpoints = {}
        self.protected = None
        self.violations = []

    def arm(self, source_serial, allowed_serial):
        self.protected = (str(source_serial), str(allowed_serial))
        self._check_links(list(self.objects.values()))

    def _check_links(self, changed):
        by_id = nodes(self.objects.values())
        for obj in changed:
            if obj.get("type") != "PipeWire:Interface:Link":
                continue
            info = obj.get("info") or {}
            previous = self.link_endpoints.get(str(obj["id"]), {})
            source = props(by_id.get(str(info.get("output-node-id")), {}))
            target = props(by_id.get(str(info.get("input-node-id")), {}))
            # Registry removal can precede the final state change of an existing
            # link. Retain that link's established endpoint identity, never an
            # ID-wide tombstone that could authorize a newly created link.
            if not source and previous.get("source_id") == info.get("output-node-id"):
                source = previous.get("source_props", {})
            if not target and previous.get("target_id") == info.get("input-node-id"):
                target = previous.get("target_props", {})
            self.link_endpoints[str(obj["id"])] = {
                "source_id": info.get("output-node-id"), "source_props": source,
                "target_id": info.get("input-node-id"), "target_props": target}
            observation = {"at": time.monotonic(), "id": obj["id"],
                           "source": str(source.get("object.serial", "")),
                           "target": str(target.get("object.serial", "")),
                           "source_name": source.get("node.name"),
                           "target_name": target.get("node.name"),
                           "state": info.get("state")}
            self.links.append(observation)
            if (self.protected and observation["source"] == self.protected[0]
                    and observation["target"] != self.protected[1]):
                # Reject the link regardless of its state. A later failed or
                # destroyed link must not erase the evidence of a bad attempt.
                self.violations.append(observation)

    def feed(self, batch):
        self.batches += 1
        changed = []
        for obj in batch:
            require(isinstance(obj, dict) and "id" in obj, "Malformed graph event")
            key = str(obj["id"])
            if (("info" in obj and obj["info"] is None)
                    or ("props" in obj and obj["props"] is None)):
                self.objects.pop(key, None)
                self.link_endpoints.pop(key, None)
                self.removals += 1
            else:
                self.objects[key] = obj
                changed.append(obj)
        self._check_links(changed)


def load_helper(filename):
    path = Path(__file__).resolve().with_name(filename)
    require(path.is_file(), f"Required integration helper is missing: {path}")
    module_name = "spatial_ci_" + path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class PCMRecorder:
    """Observe a synthetic sink's real post-processing monitor ports."""
    def __init__(self, gate, sink):
        self.gate, self.sink = gate, sink
        self.data = bytearray()
        self.process = self.task = None

    async def start(self):
        log = (self.gate.report_dir / "earbud-recorder.log").open("wb")
        self.gate.files.append(log)
        self.process = await asyncio.create_subprocess_exec("pw-cat", "--record", "--raw",
            "--rate", "48000", "--channels", "2", "--channel-map", "FL,FR", "--format", "f32",
            "--target", self.sink.serial, "--properties", json.dumps({
                "node.name": "spatial-ci-earbud-recorder", "stream.capture.sink": True}),
            "-", stdout=asyncio.subprocess.PIPE, stderr=log)
        self.gate.processes.append(("earbud-recorder", self.process))

        async def consume():
            while chunk := await self.process.stdout.read(65536):
                self.data.extend(chunk)

        self.task = asyncio.create_task(consume())
        self.gate.feeds.append(self.task)
        await self.gate.until("earbud-recorder-ready", lambda objects:
            (capture := named(objects, "spatial-ci-earbud-recorder")) is not None
            and (endpoint := named(objects, self.sink.name)) is not None
            and str(props(endpoint).get("object.serial")) == self.sink.serial
            and stereo_linked(objects, endpoint["id"], capture["id"]))

    async def window(self, name, seconds=1):
        require(self.process.returncode is None, "Synthetic earbud recorder exited")
        start = (len(self.data) + 7) // 8 * 8
        await self.gate.observe(seconds)
        end = len(self.data) // 8 * 8
        pcm = self.data[start:end]
        require(len(pcm) >= int(seconds * 48000 * 8 * 0.8),
                f"Too little actual sink audio for {name}: {len(pcm) // 8} frames")
        path = self.gate.report_dir / "earbud-audio" / (name + ".f32le")
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(pcm)
        return path


class Gate:
    def __init__(self, report_dir, *, routing_only=False,
                 require_renderer_audio=False, require_media_audio=False,
                 bridge=Path("/usr/lib/orender/libharletty_bridge.so")):
        self.report_dir = report_dir.resolve()
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.processes = []
        self.files = []
        self.feeds = []
        self.monitor_task = None
        self.monitor = None
        self.watch = GraphWatch()
        self.last_objects = []
        self.equalizer = None
        self.live = None
        self.require_renderer_audio = require_renderer_audio
        self.require_media_audio = require_media_audio
        self.routing_only = routing_only
        self.bridge = bridge
        self.report = {"status": "running", "hardware_validated": False,
                       "scope": "routing-and-eq-only" if routing_only else
                                "full-media-and-pcm-audio" if require_media_audio else
                                "full-pcm-audio" if require_renderer_audio else
                                "routing-eq-and-renderer-capabilities",
                       "endpoint_type": "synthetic headless stereo sinks",
                       "checks": [], "renderer_audio": {
                           "tested": False,
                           "reason": "No genuine Harletty decoder bridge supplied; CLI/FFI only"}}
        if routing_only:
            self.report["renderer_audio"]["reason"] = "Explicit early routing gate; full renderer gate runs separately"

    def passed(self, name, **detail):
        self.report["checks"].append({"name": name, "status": "passed", **detail})
        print(f"PASS {name}", flush=True)

    async def command(self, *args, timeout=10):
        proc = await asyncio.create_subprocess_exec(*map(str, args),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        require(proc.returncode == 0,
                f"{args[0]} exited {proc.returncode}: {err.decode(errors='replace')[-2000:]}")
        return out.decode(errors="replace")

    async def spawn(self, label, *args, stdin=None):
        log = (self.report_dir / (label + ".log")).open("wb")
        self.files.append(log)
        proc = await asyncio.create_subprocess_exec(*map(str, args),
            stdin=stdin or asyncio.subprocess.DEVNULL, stdout=log, stderr=log)
        self.processes.append((label, proc))
        return proc

    def health(self):
        for label, proc in self.services:
            require(proc.returncode is None, f"{label} exited: inspect {label}.log")
        if self.monitor_task is not None and self.monitor_task.done():
            self.monitor_task.result()
            raise Failure("PipeWire event monitor stopped early")
        require(not self.watch.violations,
                f"Protected source acquired forbidden links: {self.watch.violations}")

    async def snapshot(self):
        self.health()
        result = json.loads(await self.command("pw-dump", "--no-colors"))
        require(isinstance(result, list), "pw-dump returned a non-array")
        self.last_objects = result
        return result

    async def until(self, description, predicate, timeout=15, child=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            require(child is None or child.returncode is None,
                    f"Owned process exited {child.returncode if child else ''} during {description}")
            objects = await self.snapshot()
            if predicate(objects):
                (self.report_dir / (description + ".json")).write_text(json.dumps(objects, indent=2))
                return objects
            await asyncio.sleep(0.08)
        self.report["last_wait"] = description
        (self.report_dir / (description + "-timeout.json")).write_text(json.dumps(objects, indent=2))
        raise Failure(f"Timed out: {description}")

    async def observe(self, seconds=2):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.health()
            await asyncio.sleep(0.05)

    async def monitor_graph(self):
        decoder = JSONSequence()
        # UTF-8 node descriptions can straddle read boundaries.
        import codecs
        utf8 = codecs.getincrementaldecoder("utf-8")()
        raw = (self.report_dir / "pw-dump-monitor.jsonseq").open("wb")
        self.files.append(raw)
        while chunk := await self.monitor.stdout.read(65536):
            raw.write(chunk)
            raw.flush()
            for batch in decoder.feed(utf8.decode(chunk)):
                self.watch.feed(batch)
        require(not decoder.buffer.strip(), "Truncated pw-dump monitor JSON")

    async def sink(self, name, priority):
        await self.command("pw-cli", "create-node", "adapter", json.dumps({
            "factory.name": "support.null-audio-sink", "node.name": name,
            "node.description": "CI synthetic endpoint " + name,
            "media.class": "Audio/Sink", "object.linger": True,
            "audio.position": ["FL", "FR"], "audio.channels": 2,
            "priority.session": priority, "node.virtual": True}))
        objects = await self.until("created-" + name, lambda value: named(value, name) is not None)
        return named(objects, name)

    async def playback(self, name, target, *, expected_target=None):
        proc = await self.spawn(name, "pw-cat", "--playback", "--raw", "--rate", "48000",
            "--channels", "2", "--channel-map", "FL,FR", "--format", "f32",
            "--target", str(props(target)["object.serial"]), "--properties",
            json.dumps({"node.name": name, "application.name": "Spatial CI " + name}),
            "-", stdin=asyncio.subprocess.PIPE)
        # Continuous real low-level PCM, rather than a silent/unnegotiated node.
        block = b"".join(struct.pack("<ff", value, value)
                         for value in (0.03 * math.sin(2 * math.pi * 440 * i / 48000)
                                       for i in range(4800)))

        async def feed():
            try:
                while proc.returncode is None:
                    proc.stdin.write(block)
                    await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                return

        self.feeds.append(asyncio.create_task(feed()))
        destination = expected_target or target
        objects = await self.until("playing-" + name, lambda value:
            (obj := named(value, name)) is not None
            and stereo_linked(value, obj["id"], destination["id"]))
        return proc, named(objects, name)

    async def loopback(self, name, target, *, smart=False):
        capture_props = {"node.name": name, "node.description": "CI synthetic capture",
                         "media.class": "Audio/Sink", "priority.session": -1000000}
        if smart:
            capture_props.update({"filter.smart": True, "filter.smart.targetable": False,
                "filter.smart.name": name, "filter.smart.target": {
                    "node.name": props(target)["node.name"],
                    "object.serial": str(props(target)["object.serial"])}})
        # pw-loopback 1.6.x inserts its channel-map verbatim into SPA module
        # arguments; unlike pw-cat, it needs the brackets. Bare FL,FR consumes
        # subsequent keys as values and discards capture/playback properties.
        proc = await self.spawn(name + "-" + uuid.uuid4().hex[:8], "pw-loopback",
            "--remote", os.environ["PIPEWIRE_REMOTE"],
            "--channels", "2", "--channel-map", "[ FL, FR ]", "--capture-props",
            json.dumps(capture_props),
            "--playback-props", json.dumps({"node.name": name + ".output",
                "target.object": str(props(target)["object.serial"]),
                "node.passive": smart,
                "node.dont-fallback": True, "node.dont-reconnect": True}))
        objects = await self.until("created-" + name,
                                   lambda value: named(value, name) is not None, child=proc)
        return proc, named(objects, name)

    async def renderer(self):
        from spatial.audio_runtime import library_supports_loopback
        version = (await self.command("orender", "--version")).strip()
        require(re.search(r"(?<![\w.])v?0\.5\.2\+spatial-loopback1(?![\w.+-])", version),
                "Installed renderer lacks the exact 0.5.2+spatial-loopback1 marker")
        options = await self.command("orender", "render", "--help")
        require(all(flag in options for flag in
                    ("--output-device", "--osc-rx-port", "--continuous")),
                "Installed renderer CLI contract is incomplete")
        library = Path("/usr/lib/liborender.so.0")
        require(library.is_file(), "Installed renderer shared library is missing")
        require(await library_supports_loopback(library), "Actual renderer FFI marker failed")
        self.passed("installed-renderer-cli-and-ffi", version=version,
                    library=str(library.resolve()),
                    sha256=hashlib.sha256(library.read_bytes()).hexdigest())

    async def spatial_chain(self, sink, original_sink):
        """Actual Sony UDP → canonical pose → OSC/DSP → EQ → recorded PCM."""
        from spatial.equalizer import Equalizer
        from spatial.audio_runtime import renderer_pose
        from spatial.live_audio import LiveAudio, GUARD_KEY, _metadata
        require(self.bridge.is_file(), f"Genuine decoder bridge is required: {self.bridge}")
        metrics = load_helper("spatial-metrics.py")
        poses = load_helper("pose-fixtures.py")
        self.equalizer = Equalizer(bluetooth_address=sink.device, enabled=True)
        await self.equalizer.update_sink(sink)
        require(self.equalizer.process is not None,
                f"End-to-end EQ failed to start: {self.equalizer.status()}")

        source_name = "spatial-ci-surround-source"
        source_process = await self.spawn(source_name, "pw-cat", "--playback", "--raw",
            "--rate", "48000", "--channels", "8", "--channel-map", "FL,FR,FC,LFE,SL,SR,RL,RR",
            "--format", "f32", "--target", str(props(original_sink)["object.serial"]),
            "--properties", json.dumps({"node.name": source_name,
                "application.name": "Spatial CI positioned broadband PCM"}),
            "-", stdin=asyncio.subprocess.PIPE)
        generator = random.Random(4844)
        noise = [generator.uniform(-0.03, 0.03) for _ in range(4800)]
        blocks = {channel: b"".join(struct.pack("<8f", *(
                    value if index == channel else 0.0 for index in range(8)))
                    for value in noise) for channel in (0, 1, 2, 6, 7)}
        signal = {"channel": 2}

        async def feed():
            try:
                while source_process.returncode is None:
                    source_process.stdin.write(blocks[signal["channel"]])
                    await source_process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                return

        self.feeds.append(asyncio.create_task(feed()))
        objects = await self.until("surround-source-ready", lambda value:
            (source := named(value, source_name)) is not None
            and destinations(value, source["id"]) == {str(original_sink["id"])})
        source = named(objects, source_name)
        before = _metadata(objects, source["id"])
        self.live = LiveAudio(bridge_path=self.bridge,
            authorized_filter_inputs=self.equalizer.verified_input_ids,
            pending_filter_inputs=self.equalizer.pending_input_ids)
        await self.live.start(sink, str(props(source)["object.serial"]), objects)

        def full_graph(value):
            state = self.live.status()
            require(not state["error"], f"Genuine live renderer failed: {state}")
            if state["state"] != "playing":
                return False
            require(source_process.returncode is None, "Positioned PCM source exited")
            audit = self.live.audit(value)
            require(audit["state"] != "violation", f"Unsafe full-chain graph: {audit}")
            eq_inputs = self.equalizer.verified_input_ids(value)
            eq_state = self.equalizer.status()
            require(eq_state["link_group"] is not None and eq_state["state"] != "error",
                    f"Full-chain EQ failed: {eq_state}")
            eq_output = named(value, eq_state["link_group"] + ".output")
            owned = self.live._owned_nodes(value)
            rendered = [node for node, properties in owned.items()
                        if properties.get("media.class") == "Stream/Output/Audio"]
            return (audit["state"] == "ready" and len(eq_inputs) == 1
                    and len(rendered) == 1 and eq_output is not None
                    and stereo_linked(value, rendered[0], next(iter(eq_inputs)))
                    and stereo_linked(value, eq_output["id"], named(value, sink.name)["id"]))

        await self.until("full-spatial-renderer-eq-earbud-chain", full_graph, timeout=35)
        await self.until("monitor-observed-real-renderer-input", lambda _: (
            actual := named(list(self.watch.objects.values()), self.live.status()["input_node"]))
            is not None and destinations(list(self.watch.objects.values()), source["id"]) == {str(actual["id"])})
        self.watch.arm(props(source)["object.serial"], self.live.status()["input_serial"])
        recorder = PCMRecorder(self, sink)
        await recorder.start()
        windows = {}

        async def apply_pose(pose):
            self.live.set_pose(pose)
            expected = renderer_pose(pose)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                applied = self.live.telemetry.renderer.get("binaural", {}).get("headPose", {})
                if all(key in applied for key in ("w", "x", "y", "z")):
                    values = [applied[key] for key in ("w", "x", "y", "z")]
                    if min(max(abs(a - sign * b) for a, b in zip(values, expected))
                           for sign in (-1, 1)) < 1e-5:
                        return values
                # Registration asks the real renderer for a fresh state snapshot.
                self.live.telemetry.send("/omniphony/register")
                await self.observe(0.1)
            raise Failure("Real standalone renderer did not acknowledge the production head pose")

        async with poses.SonyPoseFixtures() as tracker:
            neutral = await tracker.pose("pose_neutral")
            tracker.records[-1]["renderer_acknowledged"] = await apply_pose(neutral)
            for name, channel in (("position_fl", 0), ("position_fr", 1), ("position_fc", 2),
                                  ("position_rl", 6), ("position_rr", 7)):
                signal["channel"] = channel
                await self.observe(1)
                require(full_graph(await self.snapshot()), f"Full chain not verified for {name}")
                windows[name] = await recorder.window(name)
            signal["channel"] = 2
            for name in ("pose_neutral", "pose_yaw_plus90", "pose_yaw_minus90", "pose_yaw_180",
                         "pose_pitch_plus45", "pose_pitch_minus45", "pose_roll_plus45",
                         "pose_roll_minus45", "pose_pitch_plus45_roll_plus90",
                         "pose_pitch_minus45_roll_plus90", "pose_neutral_repeat",
                         "pose_recentered", "pose_recentered_yaw_plus90"):
                pose = await tracker.pose(name)
                tracker.records[-1]["renderer_acknowledged"] = await apply_pose(pose)
                await self.observe(1)
                require(full_graph(await self.snapshot()), f"Full chain not verified for {name}")
                windows[name] = await recorder.window(name)
            pose_records = tracker.records
        (self.report_dir / "sony-pose-evidence.json").write_text(json.dumps(pose_records, indent=2) + "\n")
        try:
            result = metrics.analyze_windows(windows, sample_rate=48000)
        except Exception as exc:
            if hasattr(exc, "report"):
                (self.report_dir / "spatial-acoustics.json").write_text(
                    json.dumps(exc.report, indent=2) + "\n")
            raise
        (self.report_dir / "spatial-acoustics.json").write_text(json.dumps(result, indent=2) + "\n")
        self.report["renderer_audio"] = {"tested": True, "status": "passed",
            "bridge": str(self.bridge.resolve()),
            "bridge_sha256": hashlib.sha256(self.bridge.read_bytes()).hexdigest(),
            "input": "seeded broadband float32 PCM, 48 kHz, fixed 7.1 channels",
            "output": "real post-EQ synthetic sink monitor, stereo float32 at 48 kHz",
            "windows": {name: str(path.relative_to(self.report_dir)) for name, path in windows.items()},
            "pose_transport": "actual Sony helper UDP → production adapter/Engine → OSC",
            "acoustic_metrics": "spatial-acoustics.json", "hardware_validated": False}
        self.passed("real-pcm-position-headpose-recenter-through-renderer-eq-earbud-chain",
                    windows=len(windows))
        self.watch.protected = None
        await self.live.stop()
        objects = await self.until("full-chain-stop-restored-source", lambda value:
            destinations(value, source["id"]) == {str(original_sink["id"])})
        require(_metadata(objects, source["id"]) == before, "Full chain stop changed prior routing metadata")
        require(_metadata(objects, source["id"], GUARD_KEY) is None, "Full chain stop retained route guard")
        source_process.terminate()
        await source_process.wait()
        if self.require_media_audio:
            media = load_helper("media-spatial-smoke.py")
            self.report["media_audio"] = await media.run(self, sink, self.bridge, recorder)
            self.passed("actual-mpv-object-decoder-renderer-eq-earbud-chain")
        await self.equalizer.stop()
        recorder.process.terminate()
        await recorder.process.wait()
        self.passed("full-chain-stop-restores-original-source-route")

    async def run(self, private):
        require(os.geteuid() != 0, "Run this gate as an unprivileged build user")
        require(os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "Run under dbus-run-session")
        required_tools = ("pipewire", "wireplumber", "pw-dump", "pw-cli", "pw-cat",
                          "pw-loopback", "pw-metadata", "wpctl")
        if not self.routing_only:
            required_tools += ("orender",)
        for executable in required_tools:
            require(shutil.which(executable), f"Missing installed executable: {executable}")
        import spatial
        from spatial import pipewire
        from spatial.core import Sink
        from spatial.equalizer import Equalizer
        from spatial.live_audio import Route, GUARD_KEY, _guard_available, _metadata, _set_target
        source = Path(__file__).resolve().parents[2]
        imported = Path(spatial.__file__).resolve()
        require(not imported.is_relative_to(source), "Gate imported source tree instead of installed package")
        self.report["installed_module"] = str(imported)
        for relative in ("scripts/spatial-live-guard.lua", "wireplumber.conf.d/90-spatial-live-guard.conf"):
            installed = Path("/usr/share/wireplumber") / relative
            require(installed.is_file(), f"Missing installed policy: {installed}")
            require(installed.read_bytes() == (source / "wireplumber" / relative).read_bytes(),
                    f"Installed policy differs from packaged source: {installed}")
        self.passed("installed-policy-assets-match-source")
        self.report["versions"] = {tool: (await self.command(tool, "--version")).strip()
                                    for tool in ("pipewire", "wireplumber", "pw-cat")}
        if not self.routing_only:
            await self.renderer()
        for name in ("PIPEWIRE_CONFIG_DIR", "PIPEWIRE_CONFIG_PREFIX", "PIPEWIRE_CONFIG_NAME",
                     "PIPEWIRE_PROPS", "WIREPLUMBER_CONFIG_DIR", "WIREPLUMBER_DATA_DIR"):
            os.environ.pop(name, None)
        for key, leaf in (("XDG_RUNTIME_DIR", "runtime"), ("XDG_CONFIG_HOME", "config"),
                          ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache"),
                          ("XDG_DATA_HOME", "data")):
            value = private / leaf
            value.mkdir(mode=0o700)
            os.environ[key] = str(value)
        os.environ["PIPEWIRE_RUNTIME_DIR"] = os.environ["XDG_RUNTIME_DIR"]
        os.environ["PIPEWIRE_REMOTE"] = "spatial-ci-" + uuid.uuid4().hex[:12]
        os.environ["LC_ALL"] = "C"
        config = private / "config"
        pwconf = config / "pipewire" / "pipewire.conf.d"
        wpconf = config / "wireplumber" / "wireplumber.conf.d"
        pwconf.mkdir(parents=True)
        wpconf.mkdir(parents=True)
        (pwconf / "99-ci-private.conf").write_text(json.dumps({"context.properties": {
            "core.name": os.environ["PIPEWIRE_REMOTE"], "default.clock.rate": 48000}}))
        (wpconf / "99-ci-no-hardware.conf").write_text("""wireplumber.profiles = {
  main = {
    hardware.audio = disabled
    hardware.bluetooth = disabled
    hardware.video-capture = disabled
    monitor.alsa = disabled
    monitor.alsa-midi = disabled
    monitor.bluez = disabled
    monitor.bluez-midi = disabled
    monitor.v4l2 = disabled
    monitor.libcamera = disabled
  }
}
""")
        self.services = []
        pw = await self.spawn("pipewire", "pipewire")
        self.services.append(("pipewire", pw))
        deadline = time.monotonic() + 12
        while not (private / "runtime" / os.environ["PIPEWIRE_REMOTE"]).exists():
            self.health()
            require(time.monotonic() < deadline, "Private PipeWire socket did not appear")
            await asyncio.sleep(0.05)
        wp = await self.spawn("wireplumber", "wireplumber")
        self.services.append(("wireplumber", wp))
        await self.until("guard-ready", _guard_available)
        self.passed("real-private-pipewire-wireplumber-guard")
        monitor_log = (self.report_dir / "pw-dump-monitor.stderr.log").open("wb")
        self.files.append(monitor_log)
        self.monitor = await asyncio.create_subprocess_exec("pw-dump", "--monitor", "--no-colors",
            stdout=asyncio.subprocess.PIPE, stderr=monitor_log)
        self.processes.append(("pw-dump-monitor", self.monitor))
        self.monitor_task = asyncio.create_task(self.monitor_graph())
        await self.until("monitor-ready", lambda _: self.watch.batches > 0)
        speakers = await self.sink("spatial-ci-speakers", 1000)
        headphones = await self.sink("bluez_output.02_00_00_00_00_01.ci-synthetic", 900)
        alternate = await self.sink("spatial-ci-user-output", 800)
        selected_proc, selected = await self.playback("spatial-ci-selected", speakers)
        unrelated_proc, unrelated = await self.playback("spatial-ci-unrelated", speakers)
        capture_proc, capture = await self.loopback("spatial-ci-capture", headphones)
        # Stock WirePlumber treats ANY node.link-group as a filter. Its
        # get_filter_from_target() deliberately bypasses a non-smart loopback
        # target, so exercise the positive control on a plain endpoint instead.
        # This also matches the real renderer input / physical EQ target shape.
        smart_proc, smart_input = await self.loopback("spatial-ci-target-filter", alternate, smart=True)
        smart_probe, _ = await self.playback("spatial-ci-filter-probe", alternate,
                                              expected_target=smart_input)
        smart_probe.terminate()
        await smart_probe.wait()
        objects = await self.snapshot()
        defaults = {key: _metadata(objects, "0", key) for key in
                    ("default.audio.sink", "default.configured.audio.sink")}
        original = _metadata(objects, selected["id"])

        def route():
            return Route(props(selected)["object.serial"], selected["id"], original,
                         props(capture)["object.serial"])

        def original_links(value):
            return (stereo_linked(value, selected["id"], speakers["id"])
                    and stereo_linked(value, unrelated["id"], speakers["id"]))

        guarded_filter_route = Route(props(selected)["object.serial"], selected["id"],
                                     original, props(alternate)["object.serial"])
        await guarded_filter_route.apply(objects)
        await self.until("guard-bypasses-target-associated-smart-filter", lambda value:
            stereo_linked(value, selected["id"], alternate["id"])
            and stereo_linked(value, unrelated["id"], speakers["id"]))
        await guarded_filter_route.restore(await self.snapshot())
        objects = await self.until("smart-filter-guard-restored-original", original_links)
        require(_metadata(objects, selected["id"]) == original, "Smart-filter guard changed original route")
        require(_metadata(objects, selected["id"], GUARD_KEY) is None, "Smart-filter guard not removed")
        self.passed("guard-retains-exact-target-through-stock-smart-filter-policy")
        smart_proc.terminate()
        await smart_proc.wait()
        objects = await self.until("smart-filter-positive-control-cleaned-up", lambda value:
                                  named(value, "spatial-ci-target-filter") is None)

        transaction = route()
        await transaction.apply(objects)
        await self.until("selected-only-routed", lambda value:
            stereo_linked(value, selected["id"], capture["id"])
            and stereo_linked(value, unrelated["id"], speakers["id"]))
        await transaction.restore(await self.snapshot())
        objects = await self.until("original-route-restored", original_links)
        require(_metadata(objects, selected["id"]) == original, "Original metadata not restored")
        require(_metadata(objects, selected["id"], GUARD_KEY) is None, "Guard not removed after restore")
        self.passed("production-route-apply-and-restore-selected-only")

        transaction = route()
        await transaction.apply(objects)
        await self.until("before-user-choice", lambda value: stereo_linked(value, selected["id"], capture["id"]))
        explicit = {"type": "Spa:Id", "value": str(props(alternate)["object.serial"])}
        await _set_target(str(selected["id"]), explicit)
        objects = await self.until("explicit-user-route", lambda value: stereo_linked(value, selected["id"], alternate["id"]))
        await transaction.restore(objects)
        objects = await self.snapshot()
        actual_choice = _metadata(objects, selected["id"])
        require(actual_choice is not None and actual_choice["type"] == explicit["type"]
                and str(actual_choice["value"]) == explicit["value"],
                "Restore overwrote explicit user route")
        require(_metadata(objects, selected["id"], GUARD_KEY) is None, "Superseded guard retained")
        self.passed("explicit-user-route-survives-production-restore")
        await _set_target(str(selected["id"]), original)
        objects = await self.until("reset-original-route", original_links)

        transaction = route()
        await transaction.apply(objects)
        await self.until("before-capture-sigkill", lambda value:
            stereo_linked(value, selected["id"], capture["id"]))
        # Wait until the continuously running monitor has observed the route.
        await self.until("monitor-observed-route", lambda _: stereo_linked(
            list(self.watch.objects.values()), selected["id"], capture["id"]))
        self.watch.arm(props(selected)["object.serial"], props(capture)["object.serial"])
        capture_proc.kill()
        await capture_proc.wait()
        await self.until("capture-destroyed", lambda value: named(value, "spatial-ci-capture") is None)
        await self.observe()
        replacement_proc, replacement = await self.loopback("spatial-ci-capture", headphones)
        require(props(replacement)["object.serial"] != props(capture)["object.serial"],
                "Replacement unexpectedly retained old object serial")
        await self.observe()
        objects = await self.snapshot()
        require(not destinations(objects, selected["id"]), "Protected stream fell back after capture death")
        require(stereo_linked(objects, unrelated["id"], speakers["id"]), "Unrelated stream moved")
        require(selected_proc.returncode is None and unrelated_proc.returncode is None,
                "Application process died during crash check")
        require(self.watch.removals > 0, "Monitor did not observe object removals")
        self.health()
        self.watch.protected = None
        await transaction.restore(objects)
        objects = await self.until("post-crash-restore", original_links)
        require(_metadata(objects, selected["id"]) == original, "Crash restore changed original metadata")
        require(_metadata(objects, selected["id"], GUARD_KEY) is None, "Crash restore retained guard")
        self.passed("sigkill-no-fallback-and-same-name-new-serial-rejected",
                    observed_link_events=len(self.watch.links),
                    old_serial=str(props(capture)["object.serial"]),
                    replacement_serial=str(props(replacement)["object.serial"]))
        replacement_proc.terminate()
        await replacement_proc.wait()
        await self.until("replacement-capture-cleaned-up", lambda value:
                         named(value, "spatial-ci-capture") is None)

        # This descriptor intentionally represents a synthetic endpoint with a
        # Bluetooth-shaped name so the real production EQ configuration is used.
        sink = Sink("02:00:00:00:00:01", props(headphones)["node.name"],
                    str(props(headphones)["object.serial"]), "a2dp-sink", "idle")
        self.equalizer = Equalizer(bluetooth_address=sink.device, enabled=True)
        await self.equalizer.update_sink(sink)
        require(self.equalizer.process is not None,
                f"Production EQ failed to start: {self.equalizer.status()}")
        # A fresh stream must be transparently inserted into the smart filter.
        eq_source = await self.spawn("spatial-ci-eq-source", "pw-cat", "--playback", "--raw",
            "--rate", "48000", "--channels", "2", "--channel-map", "FL,FR", "--format", "f32",
            "--target", sink.serial, "--properties", '{ "node.name": "spatial-ci-eq-source" }',
            "/dev/zero")

        def eq_ready(value):
            self.equalizer.verified_input_ids(value)
            require(self.equalizer.process is not None and self.equalizer.process.returncode is None,
                    f"EQ process failed: {self.equalizer.status()}")
            inputs = self.equalizer.verified_input_ids(value)
            output = named(value, self.equalizer.status()["link_group"] + ".output")
            source_node = named(value, "spatial-ci-eq-source")
            return (len(inputs) == 1 and output is not None and source_node is not None
                    and stereo_linked(value, source_node["id"], next(iter(inputs)))
                    and stereo_linked(value, output["id"], headphones["id"]))

        await self.until("production-smart-eq-linked", eq_ready)
        self.passed("production-eq-smart-filter-real-swh-limiter", status=self.equalizer.status())
        await self.equalizer.stop()
        eq_source.terminate()
        await eq_source.wait()
        if self.require_renderer_audio:
            await self.spatial_chain(sink, speakers)
        objects = await self.snapshot()
        require(all(_metadata(objects, "0", key) == value for key, value in defaults.items()),
                "Scoped routing or EQ changed the global default")
        require(original_links(objects), "Final application routes differ from initial routes")
        self.health()
        self.passed("services-alive-original-routes-and-global-default-preserved")
        self.report["status"] = "passed"

    async def cleanup(self):
        if self.live is not None:
            self.report["live_status_at_cleanup"] = self.live.status()
            self.watch.protected = None
            await self.live.stop()
        if self.equalizer is not None:
            self.report["equalizer_status_at_cleanup"] = self.equalizer.status()
            (self.report_dir / "equalizer-backend.log").write_text(self.equalizer._stderr)
            await self.equalizer.stop()
        for task in self.feeds:
            task.cancel()
        await asyncio.gather(*self.feeds, return_exceptions=True)
        if self.monitor_task is not None:
            self.monitor_task.cancel()
            await asyncio.gather(self.monitor_task, return_exceptions=True)
        for _, proc in reversed(self.processes):
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 3)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
        for handle in self.files:
            handle.close()
        (self.report_dir / "observed-links.json").write_text(json.dumps(self.watch.links, indent=2))
        self.report["monitor"] = {"batches": self.watch.batches, "removals": self.watch.removals,
                                  "violations": self.watch.violations}
        (self.report_dir / "report.json").write_text(json.dumps(self.report, indent=2) + "\n")

    def print_failure_diagnostics(self):
        """Keep the next real failure actionable directly from the job log."""
        state = {"processes": [{"name": label, "pid": proc.pid, "exit_code": proc.returncode}
                               for label, proc in self.processes],
                 "versions": self.report.get("versions", {}),
                 "pipewire_remote": os.environ.get("PIPEWIRE_REMOTE"),
                 "nodes": [], "links": [], "metadata": []}
        fields = ("object.serial", "node.name", "media.class", "application.process.id",
                  "target.object", "node.link-group", "node.dont-fallback", "filter.smart.target")
        for obj in self.last_objects:
            kind, info = obj.get("type"), obj.get("info") or {}
            if kind == "PipeWire:Interface:Node" and len(state["nodes"]) < 80:
                state["nodes"].append({"id": obj["id"], "state": info.get("state"),
                    **{key: str(props(obj)[key])[:256] for key in fields if key in props(obj)}})
            elif kind == "PipeWire:Interface:Link" and len(state["links"]) < 160:
                state["links"].append({"id": obj["id"], **{key: info.get(key) for key in
                    ("output-node-id", "input-node-id", "output-port-id", "input-port-id", "state", "error")}})
            elif kind == "PipeWire:Interface:Metadata":
                for entry in obj.get("metadata", []):
                    if (len(state["metadata"]) < 80 and entry.get("key") in
                            ("target.object", "spatiald.live-target", "spatiald.live-guard",
                             "default.audio.sink", "default.configured.audio.sink")):
                        state["metadata"].append(entry)
        if self.equalizer is not None:
            state["equalizer"] = self.equalizer.status()
            (self.report_dir / "equalizer-backend.log").write_text(self.equalizer._stderr)
        if self.live is not None:
            state["live_audio"] = self.live.status()
        (self.report_dir / "failure-diagnostics.json").write_text(json.dumps(state, indent=2) + "\n")
        print("Audio failure: process exits and latest graph", file=sys.stderr, flush=True)
        print(json.dumps(state, indent=2), file=sys.stderr, flush=True)
        for handle in self.files:
            if not handle.closed:
                handle.flush()
        # This gate creates these logs itself. Do not enumerate environment,
        # external logs, or the binary PCM evidence in the console diagnostics.
        for path in sorted(self.report_dir.glob("*.log"))[:24]:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                handle.seek(max(0, handle.tell() - 4096))
                tail = handle.read().decode("utf-8", errors="replace")
            tail = re.sub(r"\x1b\[[0-9;]*m", "", tail)
            tail = "".join(char for char in tail if char in "\n\t" or ord(char) >= 32)
            print(f"Audio failure log tail: {path.name}", file=sys.stderr, flush=True)
            for line in tail.splitlines()[-25:]:
                print("> " + line, file=sys.stderr, flush=True)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--routing-only", action="store_true",
                        help="Early real routing/EQ gate; renderer capability/audio gates run separately")
    parser.add_argument("--require-renderer-audio", action="store_true",
                        help="Require genuine bridge, real PCM renderer, head poses, EQ and recorded output")
    parser.add_argument("--require-media-audio", action="store_true",
                        help="Also require genuine mpv-Omniphony object decode through the same EQ/output")
    parser.add_argument("--bridge", type=Path, default=Path("/usr/lib/orender/libharletty_bridge.so"))
    args = parser.parse_args()
    if args.routing_only and (args.require_renderer_audio or args.require_media_audio):
        parser.error("--routing-only cannot be combined with required renderer/media audio")
    gate = Gate(args.report_dir, routing_only=args.routing_only,
                require_renderer_audio=args.require_renderer_audio or args.require_media_audio,
                require_media_audio=args.require_media_audio, bridge=args.bridge)
    with tempfile.TemporaryDirectory(prefix="spatial-ci-") as directory:
        try:
            await gate.run(Path(directory))
        except Exception as exc:
            gate.report["status"] = "failed"
            gate.report["error"] = f"{type(exc).__name__}: {exc}"
            print(gate.report["error"], file=sys.stderr, flush=True)
            try:
                gate.print_failure_diagnostics()
            except Exception as diagnostic_error:
                print(f"Could not collect failure diagnostics: {diagnostic_error}", file=sys.stderr, flush=True)
            return 1
        finally:
            await gate.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
