"""Device-specific PipeWire PEQ, supervised independently of the player.

WirePlumber smart filters keep the physical headphones as the application target.
This module never selects a global default or writes system PipeWire config.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .core import Sink
from .errors import PublicError
from .pipewire import address
from .retry import Backoff


class EqualizerError(PublicError):
    pass


def reference_profile() -> Path:
    return Path(__file__).with_name("data") / "wf1000xm5-dhrme-5band.txt"


@dataclass(frozen=True)
class EqProfile:
    text: str
    preamp_db: float
    bands: int
    sha256: str


def read_profile(path) -> EqProfile:
    """Validate and normalize the AutoEQ subset supported by PipeWire param_eq."""
    with Path(path).expanduser().open(encoding="utf-8-sig") as handle:
        text = handle.read(65537)
    if len(text) > 65536:
        raise EqualizerError("EQ profile exceeds 64 KiB")
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
    if not lines or not (match := re.fullmatch(rf"Preamp:\s*({number})\s*dB", lines[0])):
        raise EqualizerError("EQ profile must start with a Preamp value in dB")
    preamp = float(match[1])
    if not math.isfinite(preamp) or not -60 <= preamp <= 0:
        raise EqualizerError("EQ preamp must be between -60 and 0 dB")
    fmt = lambda value: f"{value:.3f}".rstrip("0").rstrip(".") if value else "0"
    normalized = [f"Preamp: {fmt(preamp)} dB"]
    bands = 0
    for line in lines[1:]:
        match = re.fullmatch(rf"Filter\s+\d+:\s+(ON|OFF)\s+(PK|LSC|HSC)\s+Fc\s+({number})\s+Hz\s+Gain\s+({number})\s+dB\s+Q\s+({number})", line)
        if not match:
            raise EqualizerError("EQ profile contains an unsupported filter; use AutoEQ PK, LSC, or HSC filters")
        on, kind, frequency, gain, q = match.groups()
        frequency, gain, q = map(float, (frequency, gain, q))
        if not (10 <= frequency <= 20000 and -24 <= gain <= 24 and 0.1 <= q <= 20):
            raise EqualizerError("EQ filter parameters are outside supported frequency, gain, or Q limits")
        if on == "OFF":
            continue
        bands += 1
        normalized.append(f"Filter {bands}: ON {kind} Fc {fmt(frequency)} Hz Gain {fmt(gain)} dB Q {fmt(q)}")
    if not 1 <= bands <= 32:
        raise EqualizerError("EQ profile must have between 1 and 32 active filters")
    normalized = "\n".join(normalized) + "\n"
    return EqProfile(normalized, preamp, bands, hashlib.sha256(normalized.encode()).hexdigest())


def filter_config(profile_path: Path, sink: Sink, group: str, limiter_plugin: str,
                  *, ceiling_db=-1.0) -> dict:
    if (not sink.usable or not sink.name.startswith("bluez_output.")
            or not re.fullmatch(r"[0-9]+", sink.serial)):
        raise EqualizerError("EQ requires a discovered physical Bluetooth A2DP output")
    address(sink.device)
    if not re.fullmatch(r"spatial-eq-[a-f0-9]{32}", group):
        raise EqualizerError("Invalid EQ session identity")
    if not math.isfinite(ceiling_db) or not -20 <= ceiling_db <= -0.1:
        raise EqualizerError("EQ limiter ceiling must be between -20 and -0.1 dBFS")
    playback = {
        "node.name": group + ".output", "node.passive": True,
        "node.dont-fallback": True, "node.dont-reconnect": True,
        "target.object": sink.serial, "stream.dont-remix": True,
        "media.class": "Stream/Output/Audio",
    }
    capture = {
        "node.name": group + ".input", "media.class": "Audio/Sink",
        "device.class": "filter", "priority.session": -1000000,
        "filter.smart": True, "filter.smart.name": group,
        "filter.smart.targetable": False,
        "filter.smart.target": {"node.name": sink.name, "object.serial": sink.serial},
    }
    graph = {
        "nodes": [
            {"type": "builtin", "name": "eq", "label": "param_eq",
             "config": {"filename": str(profile_path)}},
            {"type": "ladspa", "name": "limit", "plugin": limiter_plugin,
             "label": "fastLookaheadLimiter", "control": {
                 "Input gain (dB)": 0.0, "Limit (dB)": ceiling_db,
                 "Release time (s)": 0.05}},
        ],
        "links": [{"output": "eq:Out 1", "input": "limit:Input 1"},
                  {"output": "eq:Out 2", "input": "limit:Input 2"}],
        "inputs": ["eq:In 1", "eq:In 2"],
        "outputs": ["limit:Output 1", "limit:Output 2"],
    }
    return {
        "context.properties": {"application.name": "Spatial Equalizer", "log.level": 1},
        "context.spa-libs": {"audio.convert.*": "audioconvert/libspa-audioconvert",
                             "support.*": "support/libspa-support"},
        "context.modules": [
            {"name": "libpipewire-module-rt", "flags": ["ifexists", "nofail"]},
            {"name": "libpipewire-module-protocol-native"},
            {"name": "libpipewire-module-client-node"},
            {"name": "libpipewire-module-adapter"},
            {"name": "libpipewire-module-filter-chain", "args": {
                "node.description": "WF-1000XM5 Equalizer", "media.name": "WF-1000XM5 Equalizer",
                "node.link-group": group, "node.virtual": True,
                "audio.channels": 2, "audio.position": ["FL", "FR"],
                "filter.graph": graph, "capture.props": capture, "playback.props": playback}},
        ],
    }


def find_limiter() -> str:
    dirs = [Path(entry) for entry in os.environ.get("LADSPA_PATH", "").split(":") if entry]
    dirs += [Path("/usr/lib/ladspa"), Path("/usr/lib64/ladspa"), Path("/usr/local/lib/ladspa")]
    for root in dirs:
        candidate = root / "fast_lookahead_limiter_1913.so"
        if candidate.is_file():
            return str(candidate.resolve())
    raise EqualizerError("EQ limiter is missing; install the official swh-plugins package")


def audit_graph(objects, *, pid, group, sink: Sink) -> dict:
    """Only a complete, owned, verified EQ chain can be an approved intermediary."""
    nodes, clients, links = {}, set(), []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        kind = obj.get("type")
        if kind == "PipeWire:Interface:Client" and str(props.get("application.process.id")) == str(pid):
            clients.add(str(obj.get("id")))
        elif kind == "PipeWire:Interface:Node":
            nodes[str(obj.get("id"))] = props
        elif kind == "PipeWire:Interface:Link":
            links.append((str(info.get("output-node-id")), str(info.get("input-node-id"))))
    def owned(props):
        return (str(props.get("application.process.id")) == str(pid)
                or str(props.get("client.id")) in clients)
    inputs = {node for node, props in nodes.items() if owned(props)
              and props.get("node.name") == group + ".input"}
    outputs = {node for node, props in nodes.items() if owned(props)
               and props.get("node.name") == group + ".output"}
    waiting = {"state": "waiting", "reason": "Waiting for the EQ graph", "input_node_ids": [],
               "pending_input_node_ids": []}
    violation = lambda reason: {"state": "violation", "reason": reason, "input_node_ids": [],
                                "pending_input_node_ids": []}
    if not inputs:
        return waiting
    if len(inputs) != 1 or len(outputs) > 1:
        return violation("EQ graph contains duplicate session nodes")
    input_props = nodes[next(iter(inputs))]
    if (input_props.get("media.class") != "Audio/Sink"
            or input_props.get("node.link-group") != group
            or str(input_props.get("filter.smart")).lower() != "true"):
        return violation("EQ routing protections are missing")
    target = input_props.get("filter.smart.target")
    if isinstance(target, str):
        try:
            target = json.loads(target)
        except ValueError:
            return violation("EQ smart target could not be verified")
    if (not isinstance(target, dict) or target.get("node.name") != sink.name
            or str(target.get("object.serial")) != sink.serial):
        return violation("EQ smart target does not match the current headphones")
    target_ids = {node for node, props in nodes.items() if props.get("node.name") == sink.name
                  and str(props.get("object.serial")) == sink.serial
                  and props.get("media.class") == "Audio/Sink"}
    if not target_ids:
        return waiting
    waiting["pending_input_node_ids"] = sorted(inputs)
    if not outputs:
        return waiting
    output_props = nodes[next(iter(outputs))]
    if (output_props.get("media.class") != "Stream/Output/Audio"
            or output_props.get("node.link-group") != group
            or str(output_props.get("target.object")) != sink.serial
            or str(output_props.get("node.dont-fallback")).lower() != "true"
            or str(output_props.get("node.dont-reconnect")).lower() != "true"):
        return violation("EQ routing protections are missing")
    connected = False
    for source, destination in links:
        if source in outputs:
            if destination not in target_ids:
                return violation("EQ output is linked to another device")
            connected = True
    if not connected:
        return waiting
    return {"state": "verified", "reason": "EQ is linked to the selected physical headphones",
            "input_node_ids": sorted(inputs), "pending_input_node_ids": []}


class Equalizer:
    def __init__(self, profile_path=None, *, bluetooth_address=None, enabled=False,
                 executable="pipewire", limiter_plugin=None, ceiling_db=-1.0):
        self.profile_path = Path(profile_path) if profile_path else reference_profile()
        self.bluetooth_address = address(bluetooth_address) if bluetooth_address else None
        self.enabled = bool(enabled)
        self.executable = executable
        self.limiter_plugin = limiter_plugin
        self.ceiling_db = ceiling_db
        self.process = None
        self._sink = None
        self._directory = None
        self._group = ""
        self._profile = None
        self._lock = asyncio.Lock()
        self._backoff = Backoff()
        self._next_attempt = 0.0
        self._fault = False
        self._pending_since = None
        self._stderr = ""
        self._log_task = None
        self._state = "disabled" if not enabled else "waiting"
        self._reason = "EQ is off" if not enabled else "Waiting for the headphones"

    async def _dependencies(self):
        if not shutil.which(self.executable):
            raise EqualizerError("PipeWire is missing")
        if not shutil.which("wireplumber"):
            raise EqualizerError("WirePlumber 0.5 or newer is required for device-specific EQ")
        proc = await asyncio.create_subprocess_exec("wireplumber", "--version",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), 3)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        match = re.search(rb"(?:^|\s)(\d+)\.(\d+)(?:\.\d+)?", stdout)
        if proc.returncode or not match or tuple(map(int, match.groups())) < (0, 5):
            raise EqualizerError("WirePlumber 0.5 or newer is required for device-specific EQ")
        return self.limiter_plugin or find_limiter()

    async def update_sink(self, sink):
        async with self._lock:
            if not self.enabled or sink is None or not sink.usable:
                await self._stop()
                self._sink = None
                self._state = "disabled" if not self.enabled else "waiting"
                self._reason = "EQ is off" if not self.enabled else "Waiting for the headphones"
                return
            if not sink.name.startswith("bluez_output."):
                await self._stop()
                self._state, self._reason = "error", "EQ requires a physical Bluetooth output"
                return
            if self.bluetooth_address is None:
                self.bluetooth_address = address(sink.device)
            if sink.device.upper() != self.bluetooth_address:
                await self._stop()
                self._state, self._reason = "error", "EQ output does not match the selected headphones"
                return
            key = lambda item: (item.device, item.name, item.serial) if item else None
            if key(sink) != key(self._sink):
                await self._stop()
                self._sink = sink
                self._next_attempt = 0
                self._backoff.reset()
            if self.process is not None and (self.process.returncode is not None or self._fault):
                await self._stop()
                self._next_attempt = time.monotonic() + self._backoff.next_delay()
                self._state, self._reason = "error", "EQ stopped; waiting to retry"
            if self.process is not None:
                return
            if time.monotonic() < self._next_attempt:
                return
            try:
                limiter = await self._dependencies()
                self._profile = read_profile(self.profile_path)
                self._directory = tempfile.TemporaryDirectory(prefix="spatial-eq-")
                root = Path(self._directory.name)
                profile = root / "profile.txt"
                profile.write_text(self._profile.text)
                self._group = "spatial-eq-" + uuid.uuid4().hex
                config = root / "filter.conf"
                config.write_text(json.dumps(filter_config(profile, sink, self._group, limiter,
                                                          ceiling_db=self.ceiling_db), indent=2))
                env = dict(os.environ)
                # Isolate from inherited application target overrides and user's
                # unrelated filter-chain fragments; connect to the same PW server.
                env.pop("PIPEWIRE_PROPS", None)
                env.pop("PIPEWIRE_CONFIG_NAME", None)
                env["PIPEWIRE_CONFIG_DIR"] = str(root)
                env["PIPEWIRE_CONFIG_PREFIX"] = ""
                self._stderr = ""
                self.process = await asyncio.create_subprocess_exec(
                    self.executable, "-c", str(config), env=env,
                    stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE)
                if getattr(self.process, "stderr", None) is not None:
                    self._log_task = asyncio.create_task(self._read_errors(self.process))
                self._fault = False
                self._state, self._reason = "starting", "Waiting for the EQ graph"
            except asyncio.CancelledError:
                await self._stop()
                raise
            except (OSError, ValueError, EqualizerError, TimeoutError) as exc:
                await self._stop()
                self._state, self._reason = "error", str(exc)
                self._next_attempt = time.monotonic() + self._backoff.next_delay()

    async def _read_errors(self, proc):
        while block := await proc.stderr.read(1024):
            text = block.decode("utf-8", errors="replace")
            text = re.sub(r"\x1b\[[0-9;]*m", "", text)
            text = "".join(c for c in text if c in "\n\t" or ord(c) >= 32)
            self._stderr = (self._stderr + text)[-4096:]

    def verified_input_ids(self, objects) -> set[str]:
        if self.process is None or self.process.returncode is not None or self._sink is None or self._fault:
            if self.process is not None and self.process.returncode is not None:
                self._state, self._reason = "error", "EQ stopped; waiting to retry"
            return set()
        audit = audit_graph(objects, pid=self.process.pid, group=self._group, sink=self._sink)
        if audit["state"] == "violation":
            self._state, self._reason = "error", audit["reason"]
            self._fault = True
            with contextlib.suppress(ProcessLookupError):
                self.process.terminate()
            return set()
        pending = set(audit["pending_input_node_ids"])
        in_use = any(str((obj.get("info") or {}).get("input-node-id")) in pending
                     for obj in objects if isinstance(obj, dict)
                     and obj.get("type") == "PipeWire:Interface:Link")
        if pending and in_use:
            if self._pending_since is None:
                self._pending_since = time.monotonic()
            elif time.monotonic() - self._pending_since > 5:
                self._state, self._reason = "error", "EQ output did not connect within five seconds"
                self._fault = True
                with contextlib.suppress(ProcessLookupError):
                    self.process.terminate()
                return set()
        else:
            self._pending_since = None
        self._state = "active" if audit["state"] == "verified" else "starting"
        self._reason = audit["reason"]
        if audit["state"] == "verified":
            self._backoff.reset()
        return set(audit["input_node_ids"])

    def pending_input_ids(self, objects) -> set[str]:
        """Owned inputs awaiting output links; callers must treat these as waiting."""
        # Apply the same violation and in-use timeout handling even when a caller
        # asks only for pending inputs.
        self.verified_input_ids(objects)
        if self.process is None or self.process.returncode is not None or self._sink is None or self._fault:
            return set()
        result = audit_graph(objects, pid=self.process.pid, group=self._group, sink=self._sink)
        return set(result["pending_input_node_ids"])

    async def _stop(self):
        proc, self.process = self.process, None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
        task, self._log_task = self._log_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._directory is not None:
            self._directory.cleanup()
            self._directory = None
        self._group = ""
        self._fault = False
        self._pending_since = None

    async def stop(self):
        async with self._lock:
            await self._stop()
            self._sink = None
            self._state, self._reason = "stopped", "EQ stopped"

    def status(self):
        return {"enabled": self.enabled, "state": self._state, "reason": self._reason,
                "profile": self.profile_path.name,
                "preamp_db": self._profile.preamp_db if self._profile else None,
                "bands": self._profile.bands if self._profile else None,
                "profile_sha256": self._profile.sha256 if self._profile else None,
                "limiter_ceiling_dbfs": self.ceiling_db,
                "target": self._sink.name if self._sink else None,
                "process_id": self.process.pid if self.process is not None else None,
                "link_group": self._group or None,
                "backend_error": self._stderr.strip()[-1000:] if self._state == "error" else None,
                "hardware_validated": False}
