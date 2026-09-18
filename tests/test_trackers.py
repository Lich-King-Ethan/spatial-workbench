"""Protocol/identity tests use explicit wire fixtures, never claimed live hardware."""
import math
from pathlib import Path
import random
import struct
import tempfile
import unittest
from unittest.mock import patch

from spatial.core import Engine
from spatial.pose import Quaternion
from spatial.trackers.common import EngineSource
from spatial.trackers.hid import inventory, sony_candidate, slime_candidate
from spatial.trackers.slime import AXES_OFFSET, Decoder, decode_rotation
from spatial.trackers.sony import decode_pose, supported_layout


ADDRESS = "AA:BB:CC:DD:EE:FF"
HARDWARE = "71C391F2ABCD"


def registration(slot=2, hardware=HARDWARE):
    return bytes((255, slot)) + int(hardware, 16).to_bytes(6, "little") + bytes(8)


def orientation(slot=2, xyzw=(0, 0, 0, 32767), packet_type=1):
    return struct.pack("<BB4h6x", packet_type, slot, *xyzw)


def sony_descriptor():
    # Source decoder's required logical/physical scale and 14-byte input report.
    return (b"\x05\x20\x09\xe1\xa1\x01\x85\x01\x75\x10\x95\x03"
            + b"\x16" + (-32767).to_bytes(2, "little", signed=True)
            + b"\x26" + (32767).to_bytes(2, "little")
            + b"\x37" + (-314159264).to_bytes(4, "little", signed=True)
            + b"\x47" + (314159265).to_bytes(4, "little")
            + b"\x55\x08\x81\x02\x75\x08\x95\x07\x81\x02\xc0")


class IdentityTests(unittest.TestCase):
    def test_sysfs_binds_only_selected_bluetooth_sensor(self):
        with tempfile.TemporaryDirectory() as tmp:
            for number, bus, unique, descriptor in [
                (0, "0005", ADDRESS.lower(), sony_descriptor()),
                (1, "0005", "11:22:33:44:55:66", sony_descriptor()),
                (2, "0003", ADDRESS, sony_descriptor()),
                (3, "0005", ADDRESS, b"\x05\x01")]:
                path = Path(tmp) / f"hidraw{number}" / "device"
                path.mkdir(parents=True)
                (path / "uevent").write_text(
                    f"HID_ID={bus}:0000054C:00000DF1\nHID_UNIQ={unique}\nHID_NAME=WF-1000XM5\n")
                (path / "report_descriptor").write_bytes(descriptor)
            devices = inventory(tmp, "/test-dev")
            chosen = [device for device in devices if sony_candidate(device, ADDRESS)]
            self.assertEqual([device.path for device in chosen], ["/test-dev/hidraw0"])
            self.assertEqual(chosen[0].name, "WF-1000XM5")

    def test_receiver_discovery_requires_verified_product(self):
        from spatial.trackers.hid import HidDevice
        def device(vendor, product, bus=3):
            return HidDevice("/dev/test", Path("/test"), bus, vendor, product, "", "")
        for vendor, product in [(0x1209, 0x7690), (0x1209, 0x7692),
                                (0x4E76, 0xD2D0), (0x4E76, 0xD2DF)]:
            self.assertTrue(slime_candidate(device(vendor, product)))
        self.assertFalse(slime_candidate(device(0x1209, 0x7691)))
        self.assertFalse(slime_candidate(device(0x4E76, 0xD2E0)))
        self.assertFalse(slime_candidate(device(0x1209, 0x7690, bus=5)))


class SonyTests(unittest.TestCase):
    def test_descriptor_layout_is_checked_not_only_android_marker(self):
        desc = sony_descriptor()
        self.assertTrue(supported_layout(desc))
        self.assertFalse(supported_layout(desc.replace(b"\x75\x10", b"\x75\x20")))
        self.assertFalse(supported_layout(desc.replace(b"\x55\x08", b"\x55\x00")))
        self.assertFalse(supported_layout(desc[:-6]))
        self.assertFalse(supported_layout(b"\x05\x20\x09\xe1"))

    def test_zyx_inverse_and_canonical_axis_mapping(self):
        sine = math.sqrt(0.5)
        for angles, expected in [
                ((90, 0, 0), (sine, 0, sine, 0)),
                ((0, 90, 0), (sine, 0, 0, -sine)),
                ((0, 0, 90), (sine, sine, 0, 0))]:
            actual = decode_pose(struct.pack("=6d", 0, 0, 0, *angles))
            for left, right in zip(actual.values(), expected):
                self.assertAlmostEqual(left, right)
        # Combined rotations exercise the order, rather than testing only axes.
        a, b, c = (math.radians(v) / 2 for v in (34, -17, 53))
        original = (Quaternion(math.cos(a), 0, 0, math.sin(a)) *
                    Quaternion(math.cos(b), 0, math.sin(b), 0) *
                    Quaternion(math.cos(c), math.sin(c), 0, 0))
        actual = decode_pose(struct.pack("=6d", 0, 0, 0, 34, -17, 53))
        for left, right in zip(actual.values(), (original.w, original.x, original.z, -original.y)):
            self.assertAlmostEqual(left, right)

    def test_rejects_truncated_nonfinite_and_wrong_units(self):
        for packet in (b"\0" * 47, b"\0" * 49,
                       struct.pack("=6d", 0, 0, 0, float("nan"), 0, 0),
                       struct.pack("=6d", 0, 0, 0, 900, 0, 0)):
            with self.assertRaises(ValueError):
                decode_pose(packet)


class SlimeTests(unittest.TestCase):
    def test_identity_is_receiver_reported_address_not_slot_or_alias(self):
        decoder = Decoder()
        self.assertEqual(decoder.feed(orientation()), [])
        self.assertTrue(decoder.unknown_rotation)
        self.assertEqual(decoder.feed(registration()), [])
        event, = decoder.feed(orientation())
        self.assertEqual(event.hardware_id, HARDWARE)
        self.assertEqual(event.key, "slime:" + HARDWARE)
        self.assertEqual(event.action, "sample")

    def test_reregistration_never_creates_or_refreshes_pose(self):
        decoder = Decoder()
        self.assertEqual(decoder.feed(registration() * 4), [])
        self.assertEqual(decoder.feed(bytes((3, 2, 1)) + bytes(13)), [])
        self.assertEqual(decoder.feed(bytes((0, 2)) + bytes(14)), [])

    def test_receiver_slot_reuse_removes_only_previous_identity(self):
        decoder = Decoder()
        decoder.feed(registration())
        removed, = decoder.feed(registration(hardware="010203040506"))
        self.assertEqual((removed.action, removed.hardware_id), ("remove", HARDWARE))
        sample, = decoder.feed(orientation())
        self.assertEqual(sample.hardware_id, "010203040506")

    def test_disconnect_error_and_unpaired_identity_remove_tracker(self):
        for frame in (bytes((3, 2, 0)) + bytes(13),
                      bytes((3, 2, 3)) + bytes(13),
                      registration(hardware="000000000000")):
            decoder = Decoder()
            decoder.feed(registration())
            event, = decoder.feed(frame)
            self.assertEqual(event.action, "remove")

    def test_q15_and_compressed_packets_match_upstream_axes(self):
        for packet_type in (1, 4):
            q = decode_rotation(orientation(packet_type=packet_type))
            for actual, expected in zip(q.values(), AXES_OFFSET.values()):
                self.assertAlmostEqual(actual, expected)
        # nRF exp-map identity: quantization origin is 512,1024,1024.
        packed = 512 | (1024 << 10) | (1024 << 21)
        for packet_type in (2, 7):
            packet = bytes((packet_type, 2)) + bytes(3) + struct.pack("<I", packed) + bytes(7)
            q = decode_rotation(packet)
            for actual, expected in zip(q.values(), AXES_OFFSET.values()):
                self.assertAlmostEqual(actual, expected)

    def test_corrupt_quaternion_is_not_normalized_into_valid_data(self):
        decoder = Decoder()
        decoder.feed(registration())
        self.assertEqual(decoder.feed(orientation(xyzw=(0, 0, 0, 0))), [])
        self.assertEqual(decoder.feed(orientation(xyzw=(32767,) * 4)), [])
        self.assertEqual(decoder.feed(bytes((2, 2)) + bytes(14)), [])
        self.assertEqual(decoder.invalid_packets, 3)

    def test_malformed_reports_do_not_escape_parser(self):
        decoder = Decoder()
        generator = random.Random(104)
        for length in range(260):
            decoder.feed(generator.randbytes(length))
        self.assertEqual(decoder.feed(bytes(4112)), [])


class ProviderOwnershipTests(unittest.TestCase):
    def test_real_sample_creates_row_timeout_hides_it_and_off_is_non_destructive(self):
        engine = Engine()
        source = EngineSource(engine)
        with patch("spatial.trackers.common.time.monotonic", return_value=10):
            source.sample("slime:" + HARDWARE, HARDWARE, "optional", AXES_OFFSET)
        row, = engine.snapshot()["optional_trackers"]
        self.assertEqual(row["name"], HARDWARE)
        engine.set_enabled(row["id"], False)
        self.assertEqual(len(engine.trackers), 1)
        engine.advance(10.51)
        self.assertEqual(engine.snapshot()["optional_trackers"], [])

    def test_late_earbud_sample_after_disconnect_does_not_announce(self):
        engine = Engine()
        engine.connect(ADDRESS)
        source = EngineSource(engine)
        with patch("spatial.trackers.common.time.monotonic", return_value=10):
            source.sample(ADDRESS, "WF-1000XM5", "earbud", AXES_OFFSET)
            engine.disconnect()
            self.assertFalse(source.sample(ADDRESS, "WF-1000XM5", "earbud", AXES_OFFSET))
        self.assertEqual(engine.trackers, {})

    def test_old_session_cleanup_cannot_remove_new_provider(self):
        engine = Engine()
        old, new = EngineSource(engine), EngineSource(engine)
        with patch("spatial.trackers.common.time.monotonic", return_value=10):
            old.sample("slime:" + HARDWARE, HARDWARE, "optional", AXES_OFFSET)
            new.sample("slime:" + HARDWARE, HARDWARE, "optional", AXES_OFFSET)
        old.clear()
        self.assertEqual(len(engine.snapshot()["optional_trackers"]), 1)
        new.clear()
        self.assertEqual(engine.snapshot()["optional_trackers"], [])
