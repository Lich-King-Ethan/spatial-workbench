#!/usr/bin/env python3
"""CI-only Sony UDP fixtures through the installed adapter and policy engine.

This simulates orientation reports, not a physical Bluetooth/HID device. The
production discovery path is unchanged. Position is outside the 3DOF contract.
Import SonyPoseFixtures and use its async context manager and .pose(name) API.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import time
from types import SimpleNamespace

from spatial.audio_runtime import renderer_pose
from spatial.core import Engine
from spatial.pose import IDENTITY, Quaternion
from spatial.preferences import Preferences
from spatial.trackers.common import EngineSource
from spatial.trackers.sony import session as sony_session


ADDRESS = "02:00:00:00:00:01"
UPSTREAM_COMMIT = "f5326577c4ae1949c6cbce3d8a4905107a86452d"
POSE_NAMES = (
    "pose_neutral", "pose_yaw_plus90", "pose_yaw_minus90", "pose_yaw_180",
    "pose_pitch_plus45", "pose_pitch_minus45", "pose_roll_plus45", "pose_roll_minus45",
    "pose_pitch_plus45_roll_plus90", "pose_pitch_minus45_roll_plus90",
    "pose_neutral_repeat", "pose_recentered", "pose_recentered_yaw_plus90",
)


def rotation(axis, degrees):
    half = math.radians(degrees) / 2
    vector = [0., 0., 0.]
    vector[axis] = math.sin(half)
    return Quaternion(math.cos(half), *vector)


PITCH_ROLL_REFERENCE = rotation(0, -45) * rotation(2, 90)

# Exact mathematical poses (no int16 quantization) checked by compiling the
# pinned upstream remap_vec3 -> rotvec_to_quat -> quat_to_euler_deg functions.
# Android reports reference-to-head; these Euler values include the upstream
# AXIS_MAP_DEFAULT. They must not be interpreted as unmodified physical axes.
FIXTURES = {
    "pose_neutral": ((0, 0, 0), IDENTITY),
    "pose_yaw_plus90": ((90, 0, 0), rotation(1, 90)),
    "pose_yaw_minus90": ((-90, 0, 0), rotation(1, -90)),
    "pose_yaw_180": ((180, 0, 0), rotation(1, 180)),
    "pose_pitch_plus45": ((0, -45, 0), rotation(0, 45)),
    "pose_pitch_minus45": ((0, 45, 0), rotation(0, -45)),
    "pose_roll_plus45": ((0, 0, -45), rotation(2, 45)),
    "pose_roll_minus45": ((0, 0, 45), rotation(2, -45)),
    "pose_pitch_plus45_roll_plus90": ((0, -45, -90), rotation(0, 45) * rotation(2, 90)),
    "pose_pitch_minus45_roll_plus90": ((0, 45, -90), PITCH_ROLL_REFERENCE),
    "pose_neutral_repeat": ((0, 0, 0), IDENTITY),
    # Source pose is reference * yaw(+90); Engine must apply reference^-1 on
    # the left to recover the relative yaw. Reversing the order fails this.
    "pose_recentered_yaw_plus90": ((-180, 45, 90),
                                    PITCH_ROLL_REFERENCE * rotation(1, 90)),
}


def same_rotation(left, right, tolerance=1e-9):
    return min(max(abs(a - sign*b) for a, b in zip(left.values(), right.values()))
               for sign in (-1, 1)) <= tolerance


class SonyPoseFixtures:
    """Run the real Sony subprocess/UDP adapter with an explicit CI producer."""
    def __init__(self):
        self.engine = Engine(Preferences(earbuds={ADDRESS: True}))
        self.engine.connect(ADDRESS)
        self.source = EngineSource(self.engine)
        self.stop = asyncio.Event()
        self.records = []
        self.status = []
        self._task = None
        self._private = None
        self._recentered = False

    async def __aenter__(self):
        self._private = tempfile.TemporaryDirectory(prefix="spatial-ci-sony-")
        directory = Path(self._private.name)
        self._control = directory / "fixture.json"
        self._write((0, 0, 0))
        helper = directory / "sony-fixture-helper"
        helper.write_text(f"#!{sys.executable}\nimport runpy\n"
                          f"runpy.run_path({str(Path(__file__).resolve())!r}, run_name='__main__')\n")
        helper.chmod(0o700)
        device = SimpleNamespace(path=str(self._control), name="CI synthetic Sony orientation")
        self._task = asyncio.create_task(sony_session(
            self.engine, self.stop, device, self.source,
            lambda state, detail: self.status.append({"state": state, "detail": detail}),
            str(helper)))
        try:
            await self._drive((0, 0, 0), IDENTITY)
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *_):
        self.stop.set()
        try:
            if self._task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(self._task), 3)
                except TimeoutError:
                    self._task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._task
        finally:
            if self._private is not None:
                self._private.cleanup()

    def _write(self, euler):
        staged = self._control.with_suffix(".new")
        staged.write_text(json.dumps({"euler_degrees": list(euler)}))
        staged.chmod(0o600)
        os.replace(staged, self._control)

    async def _drive(self, euler, expected_source):
        previous = self.source.sequence
        self._write(euler)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self._task.done():
                self._task.result()
                raise RuntimeError("Sony fixture adapter stopped before receiving the pose")
            tracker = self.engine.trackers.get(ADDRESS)
            if (self.source.sequence > previous and tracker is not None
                    and tracker.orientation is not None
                    and same_rotation(tracker.orientation, expected_source)):
                self.engine.select()
                return
            await asyncio.sleep(0.01)
        raise RuntimeError(f"Sony UDP fixture did not reach its expected orientation: {euler}")

    async def pose(self, name):
        if name not in POSE_NAMES:
            raise ValueError(f"Unknown CI orientation: {name}")
        if name == "pose_recentered":
            euler, source_pose = FIXTURES["pose_pitch_minus45_roll_plus90"]
            await self._drive(euler, source_pose)
            self.engine.recenter_active()
            self._recentered = True
            expected = IDENTITY
        elif name == "pose_recentered_yaw_plus90":
            if not self._recentered:
                await self.pose("pose_recentered")
            euler, source_pose = FIXTURES[name]
            expected = rotation(1, 90)
            await self._drive(euler, source_pose)
        else:
            if self._recentered:
                await self._drive((0, 0, 0), IDENTITY)
                self.engine.recenter_active()
                self._recentered = False
            euler, expected = FIXTURES[name]
            source_pose = expected
            await self._drive(euler, source_pose)
        state = self.engine.snapshot()
        if state["active_id"] != ADDRESS or not same_rotation(self.engine.pose, expected):
            raise RuntimeError(f"Engine selected an incorrect canonical pose for {name}")
        canonical = self.engine.pose
        self.records.append({
            "name": name, "canonical": list(canonical.values()),
            "expected_canonical": list(expected.values()),
            "renderer": list(renderer_pose(canonical)),
            "source": {
                "type": "CI synthetic Sony helper UDP; no physical HID/Bluetooth",
                "wire_euler_degrees": list(euler),
                "wire_packet_hex": struct.pack("=6d", 0, 0, 0, *euler).hex(),
                "source_canonical": list(source_pose.values()),
                "upstream_commit": UPSTREAM_COMMIT,
                "accepted_sequence": self.source.sequence,
                "engine_recenter": list(self.engine.trackers[ADDRESS].recenter.values()),
                "position_tested": False, "orientation_degrees_of_freedom": 3,
            },
        })
        return canonical


async def pose_sequence():
    async with SonyPoseFixtures() as fixtures:
        for name in POSE_NAMES:
            await fixtures.pose(name)
        return fixtures.records


def helper(control, port):
    """Only launched by the production sony.session subprocess supervisor."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        while True:
            value = json.loads(control.read_text())
            packet = struct.pack("=6d", 0, 0, 0, *value["euler_degrees"])
            sock.sendto(packet, ("127.0.0.1", port))
            time.sleep(0.02)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.device is not None and args.port is not None:
        helper(args.device, args.port)
        return 0
    report = {"status": "failed", "physical_hardware_validated": False}
    try:
        report["poses"] = asyncio.run(pose_sequence())
        report["status"] = "passed"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    encoded = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded)
    print(encoded, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
