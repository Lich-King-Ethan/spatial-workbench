"""Supervise packaged SonyTrackerLinux for the connected headphone's exact HID.

Its private SPT1 packets preserve raw reports and host-monotonic HID delivery
time. The sensor reference survives until Engine recenters in canonical
coordinates. The upstream legacy UDP port is never intercepted.

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

from dbus_next import BusType
from dbus_next.aio import MessageBus

from ..discovery import BLUEZ, BLUEZ_PATH, DEVICE, PROPERTIES, call, unwrap
from ..pose import Quaternion
from ..processes import stop_child
from ..retry import Backoff
from .common import EngineSource, Reporter, pause
from .hid import address, inventory, sony_candidate


HID_PROFILE = "00001124-0000-1000-8000-00805f9b34fb"


async def connect_missing_hid(engine, selected_bluetooth):
    """Activate only the selected session's advertised HID; discovery stays passive."""
    selected = selected_bluetooth()
    epoch = engine.epoch
    if (selected is None or not engine.connected or not selected.connected
            or address(selected.address) != address(engine.device)
            or not BLUEZ_PATH.fullmatch(selected.path)):
        return False
    # Require the BlueZ path itself to encode the same address as the selection.
    if selected.path.rsplit("/dev_", 1)[-1].replace("_", ":").upper() != address(engine.device):
        return False

    def current():
        latest = selected_bluetooth()
        return (engine.connected and engine.epoch == epoch
                and address(engine.device) == address(selected.address)
                and latest is not None and latest.connected
                and latest.path == selected.path and latest.address == selected.address)

    bus = MessageBus(bus_type=BusType.SYSTEM)
    try:
        await asyncio.wait_for(bus.connect(), 5)
        if not current():
            return False
        properties = unwrap((await call(bus, BLUEZ, selected.path, PROPERTIES,
                                        "GetAll", "s", [DEVICE]))[0])
        if (not current() or address(properties.get("Address")) != address(selected.address)
                or not all(properties.get(key) is True
                           for key in ("Connected", "Paired", "ServicesResolved"))
                or HID_PROFILE not in properties.get("UUIDs", [])):
            return False
        await call(bus, BLUEZ, selected.path, DEVICE, "ConnectProfile", "s", [HID_PROFILE])
        return current()
    finally:
        bus.disconnect()


def _input_fields(descriptor):
    """Parse report-1 fields with HID global/local state and bit offsets."""
    globals_ = {}
    usages = []
    stack = []
    fields = []
    offset = 0
    index = 0
    try:
        while index < len(descriptor):
            prefix = descriptor[index]
            index += 1
            if prefix == 0xFE:  # unsupported long item; don't guess its meaning
                return None
            size = (0, 1, 2, 4)[prefix & 3]
            if index + size > len(descriptor):
                return None
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
                    signed_value = tag in (1, 3) or (tag in (2, 4) and globals_.get(tag - 1, 0) < 0)
                    globals_[tag] = signed if signed_value else unsigned
            elif kind == 2 and tag == 0:
                usages.append((unsigned >> 16, unsigned & 65535) if size == 4
                              else (globals_.get(0), unsigned))
            elif kind == 0:
                if tag == 8 and globals_.get(8) == 1:  # report 1 input
                    bits, count = globals_.get(7, 0), globals_.get(9, 0)
                    if not bits or not count or bits * count > 4096:
                        return None
                    for field in range(count):
                        usage = usages[min(field, len(usages) - 1)] if usages else (None, None)
                        fields.append((offset, bits, unsigned,
                                       globals_.get(1), globals_.get(2),
                                       globals_.get(3), globals_.get(4), globals_.get(5), *usage))
                        offset += bits
                usages.clear()  # HID local state expires after every Main item.
    except (IndexError, ValueError):
        return None
    return fields, offset


def supported_layout(descriptor):
    """Require the verified orientation scale and offsets; don't guess units."""
    parsed = _input_fields(descriptor)
    if not parsed:
        return False
    fields, offset = parsed
    expected = [(bit, 16, 2, -32767, 32767, -314159264, 314159265, -8)
                for bit in (0, 16, 32)]
    return [field[:8] for field in fields[:3]] == expected and offset >= 104


def supported_angular_velocity(descriptor):
    """Custom Value 2 defines rad/s, even if the HID Unit was inherited.

    Android's head-tracker protocol specifies both the usage's body frame and
    units. Verify that usage, offsets and scale instead of trusting offsets
    alone or an inherited Unit left over from the report-interval feature.
    """
    parsed = _input_fields(descriptor)
    if not parsed or not supported_layout(descriptor):
        return False
    fields, _ = parsed
    expected = [(bit, 16, 2, -32767, 32767, -32, 32, 0, 0x20, 0x545)
                for bit in (48, 64, 80)]
    return fields[3:6] == expected


class TimestampedDecoder:
    """One helper session; reject old packets before they can refresh a pose."""
    MAX_AGE_NS = 500_000_000

    def __init__(self, *, angular_velocity_verified=False):
        self.angular_velocity_verified = angular_velocity_verified
        self.previous_capture = 0
        self.previous_sequence = 0
        self.previous_received = 0

    def feed(self, packet, received_ns):
        if len(packet) != 34 or packet[:4] != b"SPT1":
            raise ValueError("Sony timestamped packet must be exactly 34 SPT1 bytes")
        captured_ns, sequence = struct.unpack_from("<QQ", packet, 4)
        if (type(received_ns) is not int or received_ns <= 0
                or not 0 < captured_ns <= received_ns < 2**63
                or received_ns - captured_ns > self.MAX_AGE_NS
                or captured_ns <= self.previous_capture
                or not self.previous_sequence < sequence < 2**63
                or received_ns < self.previous_received):
            raise ValueError("Sony timestamp or sequence is stale, future, or reordered")
        report = packet[20:]
        if report[0] != 1:
            raise ValueError("Sony timestamped packet has the wrong report ID")
        raw = struct.unpack_from("<6h", report, 1)
        if min(raw[:3]) < -32767:
            raise ValueError("Sony orientation exceeds the descriptor's logical range")
        vector = [(-314159264 + (value + 32767) / 65534 * 628318529) * 1e-8
                  for value in raw[:3]]
        angle = math.hypot(*vector)
        # One half-step per axis permits only the int16 quantization boundary.
        if angle > math.pi + math.sqrt(3) * math.pi / 65534:
            raise ValueError("Sony orientation exceeds Android's rotation-vector range")
        factor = math.sin(angle / 2) / angle if angle else .5
        rx, ry, rz = (value * factor for value in vector)
        pose = Quaternion.parse((math.cos(angle / 2), rx, rz, -ry))
        velocity = None
        if self.angular_velocity_verified:
            if min(raw[3:]) < -32767:
                raise ValueError("Sony angular velocity exceeds the descriptor's logical range")
            vx, vy, vz = (value * 32 / 32767 for value in raw[3:])
            velocity = (vx, vz, -vy)  # Body axes: right/forward/up -> right/up/back.
        timing = {"captured_ns": captured_ns, "received_ns": received_ns,
                  "sequence": sequence, "clock": "host-monotonic",
                  "angular_velocity": velocity, "discontinuity": report[13],
                  "raw_report_hex": report.hex()}
        self.previous_capture, self.previous_sequence = captured_ns, sequence
        self.previous_received = received_ns
        return pose, timing


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
    """Decode the helper's explicit absolute-mode Euler packet as head-to-world."""
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
    # Invert upstream quat_to_euler_deg (ZYX). Absolute mode retains the
    # Android active head orientation, remapped by the helper to (-ry, rx, -rz).
    w = cr * cp * cy + sr * sp * sy
    x = sr * cp * cy - cr * sp * sy
    y = cr * sp * cy + sr * cp * sy
    z = cr * cp * sy - sr * sp * cy
    # Undo the helper's improper map, then change Android Xright,Yforward,Zup
    # to canonical Xright,Yup,Zback. Android describes an ACTIVE frame rotation:
    # it maps head coordinates to world, not world vectors to head. Inverting
    # it here makes rendered sound follow the physical turn. The helper must
    # not remove its own reference: that improper map reverses product order,
    # so conjugating already-recentered UDP cannot recover a general head pose.
    return Quaternion.parse((w, y, -z, x))


async def session(engine, stop, device, source, reporter, executable):
    loop = asyncio.get_running_loop()
    key, epoch = engine.device, engine.epoch
    descriptor = device.descriptor() if hasattr(device, "descriptor") else b""
    decoder = TimestampedDecoder(angular_velocity_verified=supported_angular_velocity(descriptor))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    child = None
    try:
        sock.bind(("127.0.0.1", 0))
        sock.setblocking(False)
        child = await asyncio.create_subprocess_exec(
            executable, "--absolute", "--timestamped", "--device", device.path, "--port", str(sock.getsockname()[1]),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        reporter("waiting", "Waiting for valid Sony orientation reports")
        last_received = time.monotonic()
        peer = None
        while (not stop.is_set() and engine.connected and engine.device == key
               and engine.epoch == epoch and child.returncode is None):
            try:
                packet, sender = await asyncio.wait_for(loop.sock_recvfrom(sock, 256), 0.25)
                received_ns = time.monotonic_ns()
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
                pose, timing = decoder.feed(packet, received_ns)
            except ValueError:
                continue
            peer = sender
            last_received = time.monotonic()
            if source.sample(key, device.name or key, "earbud", pose, timing=timing):
                reporter("ready", "Android head-tracker reports received")
        if child.returncode is not None:
            raise RuntimeError(f"Sony helper exited ({child.returncode}); the packaged --absolute --timestamped modes are required")
    finally:
        sock.close()
        if child is not None:
            await stop_child(child)
        source.remove(key)


async def run(engine, stop, on_change=None, status=None, *, executable="sony-tracker",
              sysfs="/sys/class/hidraw", devdir="/dev", selected_bluetooth=None):
    source = EngineSource(engine, on_change)
    reporter = Reporter("sony_tracker", status)
    backoff = Backoff()
    activated_session = None
    try:
        while not stop.is_set():
            if not engine.connected:
                reporter("waiting", "Headphones disconnected")
                await pause(stop, 0.5)
                continue
            try:
                selected_address, selected_epoch = engine.device, engine.epoch
                found = inventory(sysfs, devdir)
                identity_present = any(device.bus == 5 and device.vendor == 0x054C
                                       and address(device.unique) == address(selected_address)
                                       for device in found)
                if identity_present:
                    # A later disappearance may require another activation.
                    activated_session = None
                devices = [device for device in found
                           if sony_candidate(device, selected_address)]
                if not devices:
                    reporter("waiting", "Waiting for this headphone's Android head-tracker HID")
                    if selected_bluetooth is not None and not identity_present:
                        selected = selected_bluetooth()
                        key = (selected_epoch, selected_address, selected.path if selected else None)
                        if activated_session != key and await connect_missing_hid(engine, selected_bluetooth):
                            activated_session = key
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
