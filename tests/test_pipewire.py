import copy
import unittest
from spatial.pipewire import parse_sinks, address

MAC = "AA:BB:CC:DD:EE:FF"


def fixture():
    return [
        {"id": 10, "type": "PipeWire:Interface:Device", "info": {"props": {
            "device.api": "bluez5", "api.bluez5.address": MAC}}},
        {"id": 20, "type": "PipeWire:Interface:Node", "info": {"state": "suspended", "props": {
            "device.id": 10, "media.class": "Audio/Sink", "object.serial": 100,
            "node.name": "bluez_output.test", "api.bluez5.profile": "a2dp-sink",
            "api.bluez5.codec": "ldac"}}},
        {"id": 30, "type": "PipeWire:Interface:Node", "info": {"state": "running", "props": {
            "media.class": "Audio/Sink", "node.name": "alsa_output.hdmi", "object.serial": 101}}},
    ]


class PipeWire(unittest.TestCase):
    def test_match_device_identity(self):
        sinks = parse_sinks(fixture(), MAC.lower())
        self.assertEqual(len(sinks), 1)
        self.assertEqual(sinks[0].name, "bluez_output.test")
        self.assertTrue(sinks[0].usable)
        self.assertEqual(sinks[0].codec, "ldac")

    def test_missing_address_is_not_guessed_from_node_name(self):
        objects = fixture()
        objects[0]["info"]["props"].pop("api.bluez5.address")
        self.assertEqual(parse_sinks(objects, MAC), [])

    def test_microphone_is_not_output(self):
        objects = fixture()
        objects[1]["info"]["props"]["media.class"] = "Audio/Source"
        self.assertEqual(parse_sinks(objects, MAC), [])

    def test_reconnect_replaces_serial(self):
        old = fixture()
        new = copy.deepcopy(old)
        new[1]["info"]["props"]["object.serial"] = 300
        self.assertNotEqual(parse_sinks(old, MAC)[0].serial, parse_sinks(new, MAC)[0].serial)

    def test_no_codec_is_not_claimed_as_ldac(self):
        objects = fixture()
        objects[1]["info"]["props"].pop("api.bluez5.codec")
        self.assertIsNone(parse_sinks(objects, MAC)[0].codec)

    def test_invalid_address_rejected(self):
        with self.assertRaises(ValueError):
            address("WF-1000XM5")
