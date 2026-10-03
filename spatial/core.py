"""Deterministic policy. Providers supply observations, never UI-derived hardware facts."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .pose import IDENTITY, Quaternion
from .preferences import Preferences, identity


@dataclass(frozen=True)
class Sink:
    device: str
    name: str
    serial: str
    profile: str
    state: str
    codec: str | None = None

    @property
    def usable(self):
        # Suspended/idle is normal until playback starts; requiring running deadlocks.
        return (self.profile.startswith("a2dp") and
                self.state in ("running", "idle", "suspended") and
                bool(self.name) and bool(self.serial))


@dataclass
class Tracker:
    key: str
    reported_name: str
    kind: str
    generation: int
    sequence: int = -1
    last_sample: float | None = None
    orientation: Quaternion | None = None
    recenter: Quaternion = IDENTITY
    timing: dict | None = None

    def fresh(self, now, ttl):
        return (self.orientation is not None and self.last_sample is not None
                and 0 <= now - self.last_sample <= ttl)


class Engine:
    def __init__(self, preferences=None, sample_timeout=0.5):
        if not isfinite(sample_timeout) or sample_timeout <= 0:
            raise ValueError("sample_timeout must be positive")
        self.preferences = preferences if preferences is not None else Preferences()
        self.sample_timeout = sample_timeout
        self.now = 0.0
        self.epoch = 0
        self.connected = False
        self.device = ""
        self.companion_path = ""
        self.sink: Sink | None = None
        self.controls_ready = False
        self.renderer_ready = False
        self.source_mode = "none"
        self.trackers: dict[str, Tracker] = {}
        self.generations: dict[str, int] = {}
        self.active: str | None = None
        self.pose = IDENTITY
        self.alignment = IDENTITY
        self.reference_generation = 0

    def advance(self, now):
        if type(now) not in (int, float) or not isfinite(now) or now < self.now:
            raise ValueError("time must be finite and monotonic")
        self.now = now

    def connect(self, device, companion_path=""):
        identity(device)
        if self.connected and device == self.device:
            self.companion_path = companion_path
            return self.epoch
        self.disconnect()
        self.connected = True
        self.device = device
        self.companion_path = companion_path
        return self.epoch

    def disconnect(self):
        self.epoch += 1
        self.connected = False
        self.sink = None
        self.controls_ready = False
        self.renderer_ready = False
        self.source_mode = "none"
        self.active = None
        self.pose = IDENTITY
        # Independent optional devices survive the headphone session.
        for key in [k for k, t in self.trackers.items() if t.kind == "earbud"]:
            del self.trackers[key]

    def observe_sink(self, epoch, sink):
        if epoch != self.epoch or not self.connected:
            return False
        if sink is not None and sink.device != self.device:
            return False
        self.sink = sink
        return True

    def capability(self, epoch, name, ready):
        if name not in ("controls", "renderer") or type(ready) is not bool:
            raise ValueError("unknown capability or non-boolean readiness")
        if epoch != self.epoch or not self.connected:
            return False
        setattr(self, name + "_ready", ready)
        return True

    def announce(self, key, reported_name, kind):
        identity(key)
        identity(reported_name)
        if kind not in ("earbud", "optional"):
            raise ValueError("invalid tracker kind")
        if kind == "earbud" and (not self.connected or key != self.device):
            raise ValueError("earbud tracker must match the connected device")
        generation = self.generations.get(key, 0) + 1
        self.generations[key] = generation
        # New provider session invalidates all prior packets and calibration.
        self.trackers[key] = Tracker(key, reported_name, kind, generation)
        if self.active == key:
            self.active = None
        return generation

    def sample(self, key, generation, sequence, orientation, *, timing=None):
        tracker = self.trackers.get(key)
        if (tracker is None or generation != tracker.generation or
                type(sequence) is not int or sequence < 0 or sequence <= tracker.sequence):
            return False
        try:
            pose = Quaternion.parse(orientation)
        except (ValueError, TypeError, OverflowError):
            return False
        observed = None
        if timing is not None:
            # Native helpers share CLOCK_MONOTONIC with this process. This is
            # host receipt timing, never an inferred device acquisition clock.
            if not isinstance(timing, dict) or timing.get("clock") != "host-monotonic":
                return False
            captured, received, packet_sequence = (timing.get(name) for name in
                                                  ("captured_ns", "received_ns", "sequence"))
            if (any(type(value) is not int or not 0 < value <= 2**63 - 1 for value in
                    (captured, received))
                    or type(packet_sequence) is not int or not 0 < packet_sequence <= 2**64 - 1
                    or captured > received
                    or received / 1e9 > self.now
                    or not 0 <= self.now - captured / 1e9 <= self.sample_timeout):
                return False
            previous = tracker.timing
            if previous is not None and (captured <= previous["captured_ns"]
                                         or packet_sequence <= previous["sequence"]):
                return False
            velocity = timing.get("angular_velocity")
            if velocity is not None and (not isinstance(velocity, (tuple, list))
                    or len(velocity) != 3 or any(type(value) not in (int, float)
                    or abs(value) > 64 or not isfinite(value) for value in velocity)):
                return False
            counter = timing.get("discontinuity")
            raw = timing.get("raw_report_hex")
            if (counter is not None and (type(counter) is not int or not 0 <= counter <= 255)
                    or raw is not None and (not isinstance(raw, str) or len(raw) != 28
                        or any(char not in "0123456789abcdef" for char in raw))):
                return False
            observed = {"captured_ns": captured, "received_ns": received,
                        "sequence": packet_sequence, "clock": "host-monotonic",
                        "angular_velocity": list(velocity) if velocity is not None else None,
                        "discontinuity": counter, "raw_report_hex": raw}
        if (observed is not None and tracker.timing is not None
                and observed["discontinuity"] != tracker.timing["discontinuity"]
                and self.active == key):
            # The sensor changed its arbitrary reference frame. Preserve the
            # audible orientation at that boundary instead of treating it as a
            # physical head turn; subsequent motion uses the new sensor frame.
            self.alignment = self.pose * (tracker.recenter * pose).inverse()
            self.reference_generation += 1
        tracker.sequence = sequence
        tracker.orientation = pose
        tracker.timing = observed
        tracker.last_sample = observed["captured_ns"] / 1e9 if observed else self.now
        return True

    def remove(self, key, generation):
        tracker = self.trackers.get(key)
        if tracker is not None and tracker.generation == generation:
            del self.trackers[key]
            return True
        return False

    def set_enabled(self, key, enabled):
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        tracker = self.trackers.get(key)
        if tracker is None or not tracker.fresh(self.now, self.sample_timeout):
            raise ValueError("tracker is no longer available")
        if tracker.kind == "earbud":
            self.preferences.earbuds[key] = enabled
        else:
            # User explicitly chooses which body tracker can drive audio.
            # Preserve OFF entries per identity, never infer a head role from its name.
            if enabled:
                for other in self.preferences.optional:
                    self.preferences.optional[other] = False
            self.preferences.optional[key] = enabled

    def allowed(self, tracker):
        values = (self.preferences.earbuds if tracker.kind == "earbud"
                  else self.preferences.optional)
        return values.get(tracker.key, False)

    def select(self):
        eligible = [t for t in self.trackers.values()
                    if t.fresh(self.now, self.sample_timeout) and self.allowed(t)]
        optional = sorted((t.key for t in eligible if t.kind == "optional"))
        earbuds = sorted(t.key for t in eligible if t.kind == "earbud"
                         and self.connected and t.key == self.device)
        chosen = optional[0] if optional else (earbuds[0] if earbuds else None)
        # Headphone session gates the audio consumer, not discovery or other consumers.
        if not self.connected:
            chosen = None
        if chosen != self.active:
            if chosen is not None:
                tracker = self.trackers[chosen]
                raw = tracker.recenter * tracker.orientation
                # Align reference frames at handoff; subsequent relative motion passes through.
                self.alignment = self.pose * raw.inverse()
            else:
                self.pose = IDENTITY
                self.alignment = IDENTITY
            self.active = chosen
        if chosen is not None:
            tracker = self.trackers[chosen]
            self.pose = self.alignment * tracker.recenter * tracker.orientation
        return chosen

    def recenter_active(self):
        key = self.select()
        if key is None:
            raise ValueError("no active tracker")
        self.trackers[key].recenter = self.trackers[key].orientation.inverse()
        self.alignment = IDENTITY
        self.pose = IDENTITY
        self.reference_generation += 1

    def snapshot(self):
        self.select()
        audio_ready = self.connected and self.sink is not None and self.sink.usable
        mode = (self.source_mode if audio_ready and self.renderer_ready else "none")
        fresh = [t for t in self.trackers.values()
                 if t.fresh(self.now, self.sample_timeout)]
        def row(t):
            return {"id": t.key, "name": t.reported_name, "enabled": self.allowed(t)}
        return {
            "schema": 1, "epoch": self.epoch,
            "device": self.device, "companion_path": self.companion_path,
            "connected": self.connected, "audio_ready": bool(audio_ready),
            "audio_state": "ready" if audio_ready else
                           ("waiting" if self.connected else "disconnected"),
            "codec": self.sink.codec if audio_ready else None,
            "target": self.sink.name if audio_ready else None,
            "controls_ready": self.controls_ready,
            "renderer_ready": self.renderer_ready,
            "render_mode": mode,
            "earbud": next((row(t) for t in fresh if t.kind == "earbud"), None),
            "optional_trackers": [row(t) for t in sorted(fresh, key=lambda t: t.key)
                                  if t.kind == "optional"],
            "active_id": self.active,
            "active_name": self.trackers[self.active].reported_name if self.active else "",
            "orientation": list(self.pose.values()),
        }

    def desired_output(self):
        state = self.snapshot()
        if not state["audio_ready"] or not self.renderer_ready or self.source_mode == "none":
            return None
        return {"target.object": self.sink.name,
                "node.dont-fallback": True, "node.dont-reconnect": True,
                "mode": self.source_mode, "tracking": self.active}
