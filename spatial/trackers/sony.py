"""Supervise upstream SonyTrackerLinux for the connected headphone's exact HID.

Its loopback packet is six native-endian doubles: position, yaw, pitch, roll.
The upstream default port is never used or intercepted.

Protocol/mathematics adapted from SonyTrackerLinux (MIT).
Copyright (c) 2026 K Ntanakas

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
import fcntl
import math
import os
import socket
import struct
import time

from ..pose import Quaternion
from ..processes import stop_child
from ..retry import Backoff
from .common import EngineSource, Reporter, pause
from .hid import inventory, sony_candidate


def supported_layout(descriptor):
    """Verify the report layout upstream hard-codes before starting its decoder.

    Android's marker alone doesn't guarantee report offsets or units. In
    particular, upstream has verified WH-1000XM5, not every WF firmware.
    """
    globals_ = {}
    stack = []
    fields = []
    offset = 0
    index = 0
    try:
        while index < len(descriptor):
            prefix = descriptor[index]
            index += 1
            if prefix == 0xFE:  # unsupported long item; don't guess its meaning
                return False
            size = (0, 1, 2, 4)[prefix & 3]
            if index + size > len(descriptor):
                return False
            raw = descriptor[index:index + size]
            index += size
            unsigned = int.from_bytes(raw, "little")
            signed = int.from_bytes(raw, "little", signed=True)
            kind, tag = (prefix >> 2) & 3, prefix >> 4
            if kind == 1:
                if tag == 10:
                    stack.append(dict(globals_))
                elif tag == 11:
                    globals_ = stack.pop()
                elif tag == 5:  # Unit Exponent uses a signed four-bit value
                    nibble = unsigned & 15
                    globals_[tag] = nibble - 16 if nibble >= 8 else nibble
                else:
                    globals_[tag] = signed if tag in (1, 2, 3, 4) else unsigned
            elif kind == 0 and tag == 8 and globals_.get(8) == 1:  # report 1 input
                bits, count = globals_.get(7, 0), globals_.get(9, 0)
                if not bits or not count or bits * count > 4096:
                    return False
                for _ in range(min(count, 3)):
                    if offset < 48:
                        fields.append((offset, bits, unsigned & 1,
                                       globals_.get(1), globals_.get(2),
                                       globals_.get(3), globals_.get(4), globals_.get(5)))
                    offset += bits
                offset += bits * max(0, count - 3)
    except (IndexError, ValueError):
        return False
    expected = [(bit, 16, 0, -32767, 32767, -314159264, 314159265, -8)
                for bit in (0, 16, 32)]
    return fields == expected and offset >= 104


def confirm_feature(path):
    """Same read-only AndroidHeadTracker feature check as upstream hidraw.h."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        report = bytearray(24)
        report[0] = 2
        # Linux asm-generic _IOC(READ|WRITE, 'H', 7, 24), HIDIOCGFEATURE(24).
        fcntl.ioctl(descriptor, (3 << 30) | (24 << 16) | (ord("H") << 8) | 7, report, True)
        return report[1:21] == b"#AndroidHeadTracker#"
    finally:
        os.close(descriptor)


def decode_pose(packet):
    if len(packet) != 48:
        raise ValueError("Sony pose must contain six doubles")
    values = struct.unpack("=6d", packet)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Sony pose contains nonfinite data")
    if any(abs(value) > 360 for value in values[3:]):
        raise ValueError("Sony pose contains out-of-range angles")
    yaw, pitch, roll = (math.radians(value) / 2 for value in values[3:])
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cr, sr = math.cos(roll), math.sin(roll)
    # Invert upstream quat_to_euler_deg (ZYX); then Xright,Yforward,Zup ->
    # canonical Xright,Yup,Zback. This is head-to-world, not its inverse.
    w = cr * cp * cy + sr * sp * sy
    x = sr * cp * cy - cr * sp * sy
    y = cr * sp * cy + sr * cp * sy
    z = cr * cp * sy - sr * sp * cy
    return Quaternion.parse((w, x, z, -y))


async def session(engine, stop, device, source, reporter, executable):
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    child = None
    key, epoch = engine.device, engine.epoch
    try:
        sock.bind(("127.0.0.1", 0))
        sock.setblocking(False)
        child = await asyncio.create_subprocess_exec(
            executable, "--device", device.path, "--port", str(sock.getsockname()[1]),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        reporter("waiting", "Waiting for valid Sony orientation reports")
        last_received = time.monotonic()
        peer = None
        while (not stop.is_set() and engine.connected and engine.device == key
               and engine.epoch == epoch and child.returncode is None):
            try:
                packet, sender = await asyncio.wait_for(loop.sock_recvfrom(sock, 256), 0.25)
            except TimeoutError:
                if time.monotonic() - last_received > 3:
                    reporter("waiting", "Sony sensor stopped sending; reconnecting helper")
                    break
                continue
            # A disconnect/reconnect can happen while awaiting the datagram,
            # including a reconnect to the same MAC. The old helper must never
            # announce its previous reference frame into that new session.
            if (stop.is_set() or not engine.connected or engine.device != key
                    or engine.epoch != epoch):
                break
            if sender[0] != "127.0.0.1" or (peer is not None and sender != peer):
                continue
            try:
                pose = decode_pose(packet)
            except ValueError:
                continue
            peer = sender
            last_received = time.monotonic()
            if source.sample(key, device.name or key, "earbud", pose):
                reporter("ready", "Android head-tracker reports received")
        if child.returncode is not None:
            raise RuntimeError(f"sony-tracker exited ({child.returncode})")
    finally:
        sock.close()
        if child is not None:
            await stop_child(child)
        source.remove(key)


async def run(engine, stop, on_change=None, status=None, *, executable="sony-tracker",
              sysfs="/sys/class/hidraw", devdir="/dev"):
    source = EngineSource(engine, on_change)
    reporter = Reporter("sony_tracker", status)
    backoff = Backoff()
    try:
        while not stop.is_set():
            if not engine.connected:
                reporter("waiting", "Headphones disconnected")
                await pause(stop, 0.5)
                continue
            try:
                selected_address, selected_epoch = engine.device, engine.epoch
                devices = [device for device in inventory(sysfs, devdir)
                           if sony_candidate(device, selected_address)]
                if not devices:
                    reporter("waiting", "Waiting for this headphone's Android head-tracker HID")
                else:
                    usable = []
                    unsupported = False
                    for device in devices:
                        if not supported_layout(device.descriptor()):
                            unsupported = True
                            continue
                        if await asyncio.to_thread(confirm_feature, device.path):
                            usable.append(device)
                    if (not engine.connected or engine.device != selected_address
                            or engine.epoch != selected_epoch):
                        # Feature I/O happens on a worker thread. Its result is
                        # valid only for the headphone session it probed.
                        continue
                    if len(usable) == 1:
                        started = time.monotonic()
                        await session(engine, stop, usable[0], source, reporter, executable)
                        if time.monotonic() - started > 10:
                            backoff.reset()
                    elif len(usable) > 1:
                        reporter("unavailable", "Multiple sensor interfaces match; refusing ambiguous input")
                    elif unsupported:
                        reporter("unavailable", "Sony sensor report layout differs from supported decoder")
                    else:
                        reporter("waiting", "HID present; Android head-tracker feature not available yet")
            except asyncio.CancelledError:
                raise
            except FileNotFoundError:
                reporter("unavailable", "Install sony-tracker, or wait for HID reconnect")
            except PermissionError:
                reporter("unavailable", "Sony HID permission denied; check uaccess and reconnect headphones")
            except Exception as exc:
                reporter("unavailable", f"Sony provider: {type(exc).__name__}: {exc}")
            await pause(stop, backoff.next_delay())
    finally:
        source.clear()
