import struct
import unittest

from spatial.osc import decode, encode


def bundle(*packets):
    return b"#bundle\0" + struct.pack(">Q", 1) + b"".join(
        struct.pack(">I", len(packet)) + packet for packet in packets)


class OscTests(unittest.TestCase):
    def test_reference_wire_encoding(self):
        packet = b"/test\0\0\0,ifs\0\0\0\0" + struct.pack(">if", 7, 0.5) + b"ok\0\0"
        self.assertEqual(encode("/test", 7, 0.5, "ok"), packet)
        self.assertEqual(decode(packet), [("/test", [7, 0.5, "ok"])])

    def test_renderer_bundle_flattens_with_types_int64_and_unicode(self):
        packet = bundle(encode("/omniphony/state/capabilities", '{"host":"mpv"}'),
                        bundle(encode("/omniphony/spatial/frame", 2**40, 8, 3, 0)),
                        encode("/unicode", "耳機", True, False, None))
        messages = decode(packet)
        self.assertEqual(messages[1][1], [2**40, 8, 3, 0])
        self.assertEqual(messages[2][1], ["耳機", True, False, None])

    def test_rejects_truncation_nonfinite_bad_padding_and_trailing_data(self):
        valid = encode("/head", 1.0, 0.0, 0.0, 0.0)
        invalid = [valid[:end] for end in range(len(valid))]
        invalid += [valid + b"\0\0\0\0", b"/x\0x,f\0\0" + struct.pack(">f", 1),
                    b"/x\0\0,f\0\0" + struct.pack(">f", float("nan")),
                    b"/x\0\0,z\0\0", b"/x\0\0,s\0\0\xff\0\0\0"]
        for packet in invalid:
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                decode(packet)

    def test_bundles_have_size_depth_and_message_limits(self):
        packet = encode("/a")
        for _ in range(10):
            packet = bundle(packet)
        for bad in [packet, bundle(*([encode("/a")] * 513)),
                    b"#bundle\0" + struct.pack(">Q", 42),
                    bundle(encode("/a")) + b"\0", b"#bundle\0" + b"\0" * 8 + struct.pack(">I", 900)]:
            with self.assertRaises(ValueError):
                decode(bad)

    def test_encode_rejects_unsafe_values(self):
        for value in (float("inf"), 2**65, "NUL\0injection", object(), 1e300):
            with self.assertRaises(ValueError):
                encode("/head", value)
        with self.assertRaises(ValueError):
            encode("head", 1)
