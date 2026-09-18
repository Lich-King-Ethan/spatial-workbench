"""Passive SlimeVR nRF HID reader, compatible with SlimeVR running concurrently.

This opens hidraw O_RDONLY and never sends control/output/feature requests.
Linux allocates a separate report queue for every hidraw reader. 0xff frames map
receiver slots to actual 48-bit tracker addresses; only rotation frames refresh
availability. Registration/status/battery packets cannot create phantom trackers.

Protocol decoding adapted from SlimeVR's MIT-licensed HIDCommon.kt and
DesktopHIDManager.kt. Copyright (c) 2020 Eiren Rain and SlimeVR Contributors.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import os
import struct
import time

from ..pose import Quaternion
from .common import EngineSource, Reporter, pause
from .hid import inventory, slime_candidate

# Exactly SlimeVR HIDCommon.kt's AXES_OFFSET. Firmware sends Z-up world axes;
# SlimeVR's server and our canonical pose use Xright,Yup,Zback.
AXES_OFFSET = Quaternion(math.sqrt(0.5), -math.sqrt(0.5), 0, 0)


@dataclass(frozen=True)
class Observation:
    action: str
    hardware_id: str
    pose: Quaternion | None = None

    @property
    def key(self):
        return "slime:" + self.hardware_id


def decode_rotation(packet):
    """Decode verified 16-byte nRF tracker frames (Q15 or quantized exp-map)."""
    if len(packet) != 16:
        raise ValueError("Slime tracker frame must contain 16 bytes")
    if packet[0] in (1, 4):
        x, y, z, w = struct.unpack_from("<4h", packet, 2)
        values = (w / 32768, x / 32768, y / 32768, z / 32768)
        # Q15 is unit-length to within its quantization error. Do not normalize
        # random, corrupt or uninitialized bytes into an apparently valid pose.
        if not 0.95 <= math.hypot(*values) <= 1.05:
            raise ValueError("Slime quaternion is not unit length")
        rotation = Quaternion.parse(values)
    elif packet[0] in (2, 7):
        packed = struct.unpack_from("<I", packet, 5)[0]
        vector = ((packed & 1023) / 1024 * 2 - 1,
                  ((packed >> 10) & 2047) / 2048 * 2 - 1,
                  ((packed >> 21) & 2047) / 2048 * 2 - 1)
        squared = sum(value * value for value in vector)
        if squared > 1.01:
            raise ValueError("Slime exponential-map quaternion exceeds unit ball")
        inverse = 1 / math.sqrt(squared + 1e-6)
        angle = math.pi / 2 * squared * inverse
        scale = math.sin(angle) * inverse
        rotation = Quaternion.parse((math.cos(angle), *(value * scale for value in vector)))
    else:
        raise ValueError("Frame contains no orientation")
    return AXES_OFFSET * rotation


class Decoder:
    """One receiver session; a slot number is never a persistent identity."""
    def __init__(self):
        self.identities = {}
        self.unknown_rotation = False
        self.invalid_packets = 0

    def feed(self, report):
        if not report or len(report) > 4096 or len(report) % 16:
            self.invalid_packets += 1
            return []
        events = []
        for offset in range(0, len(report), 16):
            packet = report[offset:offset + 16]
            packet_type, slot = packet[:2]
            if packet_type == 255:
                # SlimeVR DesktopHIDManager: LE uint64 masked to 48 bits, %012X.
                raw = int.from_bytes(packet[2:8], "little")
                if raw in (0, 0xFFFFFFFFFFFF):
                    # Unpaired or empty receiver slot; never invent an identity.
                    old = self.identities.pop(slot, None)
                    if old is not None:
                        events.append(Observation("remove", old))
                    continue
                hardware_id = f"{raw:012X}"
                old = self.identities.get(slot)
                if old is not None and old != hardware_id:
                    events.append(Observation("remove", old))
                self.identities[slot] = hardware_id
                continue
            hardware_id = self.identities.get(slot)
            if hardware_id is None:
                if packet_type in (1, 2, 4, 7):
                    self.unknown_rotation = True
                continue
            if packet_type == 3:
                # Server TrackerStatus IDs, not SolarXR's +1 representation.
                if packet[2] not in (1, 2):  # OK or BUSY
                    events.append(Observation("remove", hardware_id))
                continue
            if packet_type not in (1, 2, 4, 7):
                continue
            try:
                pose = decode_rotation(packet)
            except ValueError:
                self.invalid_packets += 1
                continue
            events.append(Observation("sample", hardware_id, pose))
        return events


async def session(engine, stop, device, on_change, reporter):
    source = EngineSource(engine, on_change)
    decoder = Decoder()
    descriptor = os.open(device.path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    loop = asyncio.get_running_loop()
    readable = asyncio.Event()
    registered = False
    last_pose = None
    try:
        loop.add_reader(descriptor, readable.set)
        registered = True
        reporter("waiting", "Receiver connected; waiting for tracker identity and live orientation")
        while not stop.is_set():
            try:
                await asyncio.wait_for(readable.wait(), 0.25)
            except TimeoutError:
                if last_pose is not None and time.monotonic() - last_pose > engine.sample_timeout:
                    reporter("waiting", "Receiver connected; no fresh tracker orientation")
                continue
            readable.clear()
            for _ in range(64):
                try:
                    report = os.read(descriptor, 4096)
                except BlockingIOError:
                    break
                if not report:
                    raise ConnectionError("Receiver disconnected")
                for event in decoder.feed(report):
                    if event.action == "remove":
                        source.remove(event.key)
                    elif source.sample(event.key, event.hardware_id, "optional", event.pose):
                        last_pose = time.monotonic()
                        reporter("ready", "Receiving physical tracker orientation")
                if last_pose is None and decoder.unknown_rotation:
                    reporter("waiting", "Registration not seen; waiting for receiver's repeated identity packets")
                elif last_pose is not None and time.monotonic() - last_pose > engine.sample_timeout:
                    reporter("waiting", "Receiver connected; no fresh tracker orientation")
            await asyncio.sleep(0)  # share the loop with Bluetooth, UI, and renderer
    finally:
        if registered:
            loop.remove_reader(descriptor)
        os.close(descriptor)
        source.clear()


async def device_worker(engine, stop, device, on_change, reporter):
    from ..retry import Backoff
    backoff = Backoff()
    while not stop.is_set():
        started = time.monotonic()
        try:
            await session(engine, stop, device, on_change, reporter)
        except asyncio.CancelledError:
            raise
        except PermissionError:
            reporter("unavailable", "Slime HID permission denied; check uaccess and reconnect receiver")
        except Exception as exc:
            reporter("waiting", f"Receiver reconnect: {type(exc).__name__}: {exc}")
        if time.monotonic() - started > 10:
            backoff.reset()
        await pause(stop, backoff.next_delay())


async def run(engine, stop, on_change=None, status=None, *,
              sysfs="/sys/class/hidraw", devdir="/dev"):
    reporter = Reporter("optional_tracker", status)
    workers = {}
    try:
        while not stop.is_set():
            candidates = {device.path: device for device in inventory(sysfs, devdir)
                          if slime_candidate(device)}
            for path, (device, task) in list(workers.items()):
                if candidates.get(path) != device or task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    del workers[path]
            for path, device in candidates.items():
                if path not in workers:
                    task = asyncio.create_task(device_worker(engine, stop, device, on_change, reporter))
                    workers[path] = (device, task)
            if not workers:
                reporter("waiting", "No supported SlimeVR nRF receiver detected")
            await pause(stop, 1)
    finally:
        tasks = [task for _, task in workers.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
