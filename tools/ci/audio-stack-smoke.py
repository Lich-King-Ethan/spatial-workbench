#!/usr/bin/env python3
"""Exercise installed routing/EQ against a private, real PipeWire session.

Run as an ordinary user under dbus-run-session. The endpoints and PCM signal are
synthetic: this is a software integration gate, never a Bluetooth/hearing test.
No source-tree import override, mock session manager, or decoder bridge is used.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import re
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


class Gate:
    def __init__(self, report_dir):
        self.report_dir = report_dir.resolve()
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.processes = []
        self.files = []
        self.feeds = []
        self.monitor_task = None
        self.monitor = None
        self.watch = GraphWatch()
        self.equalizer = None
        self.report = {"status": "running", "hardware_validated": False,
                       "endpoint_type": "synthetic headless stereo sinks",
                       "checks": [], "renderer_audio": {
                           "tested": False,
                           "reason": "No genuine Harletty decoder bridge supplied; CLI/FFI only"}}

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
        return result

    async def until(self, description, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            objects = await self.snapshot()
            if predicate(objects):
                (self.report_dir / (description + ".json")).write_text(json.dumps(objects, indent=2))
                return objects
            await asyncio.sleep(0.08)
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
        proc = await self.spawn(name + "-" + uuid.uuid4().hex[:8], "pw-loopback",
            "--channels", "2", "--channel-map", "FL,FR", "--capture-props",
            json.dumps(capture_props),
            "--playback-props", json.dumps({"node.name": name + ".output",
                "target.object": str(props(target)["object.serial"]),
                "node.passive": smart,
                "node.dont-fallback": True, "node.dont-reconnect": True}))
        objects = await self.until("created-" + name, lambda value: named(value, name) is not None)
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

    async def run(self, private):
        require(os.geteuid() != 0, "Run this gate as an unprivileged build user")
        require(os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "Run under dbus-run-session")
        for executable in ("pipewire", "wireplumber", "pw-dump", "pw-cli", "pw-cat",
                           "pw-loopback", "pw-metadata", "wpctl", "orender"):
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
        smart_proc, smart_input = await self.loopback("spatial-ci-target-filter", capture, smart=True)
        smart_probe, _ = await self.playback("spatial-ci-filter-probe", capture,
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
        self.passed("guard-retains-exact-target-through-stock-smart-filter-policy")
        smart_proc.terminate()
        await smart_proc.wait()

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
        objects = await self.snapshot()
        require(all(_metadata(objects, "0", key) == value for key, value in defaults.items()),
                "Scoped routing or EQ changed the global default")
        require(original_links(objects), "Final application routes differ from initial routes")
        self.health()
        self.passed("services-alive-original-routes-and-global-default-preserved")
        self.report["status"] = "passed"

    async def cleanup(self):
        if self.equalizer is not None:
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


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", required=True, type=Path)
    args = parser.parse_args()
    gate = Gate(args.report_dir)
    with tempfile.TemporaryDirectory(prefix="spatial-ci-") as directory:
        try:
            await gate.run(Path(directory))
        except Exception as exc:
            gate.report["status"] = "failed"
            gate.report["error"] = f"{type(exc).__name__}: {exc}"
            print(gate.report["error"], file=sys.stderr, flush=True)
            return 1
        finally:
            await gate.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
