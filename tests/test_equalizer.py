import asyncio
import ctypes
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from spatial.core import Sink
from spatial.equalizer import (Equalizer, EqualizerError, audit_graph,
                               filter_config, find_limiter, read_profile, reference_profile)


SINK = Sink("AA:BB:CC:DD:EE:FF", "bluez_output.AA_BB_CC_DD_EE_FF.1", "99", "a2dp-sink", "idle", "ldac")
GROUP = "spatial-eq-" + "1" * 32


def node(number, **props):
    return {"id": number, "type": "PipeWire:Interface:Node", "info": {"props": props}}


def link(source, destination):
    return {"type": "PipeWire:Interface:Link", "info": {"output-node-id": source, "input-node-id": destination}}


def graph(group=GROUP, sink=SINK):
    return [
        {"id": 10, "type": "PipeWire:Interface:Client", "info": {"props": {"application.process.id": 123}}},
        node(20, **{"client.id": 10, "node.name": group + ".input", "media.class": "Audio/Sink",
                   "node.link-group": group, "filter.smart": True,
                   "filter.smart.target": json.dumps({"node.name": sink.name, "object.serial": sink.serial})}),
        node(21, **{"client.id": 10, "node.name": group + ".output", "media.class": "Stream/Output/Audio",
                   "node.link-group": group, "target.object": sink.serial,
                   "node.dont-fallback": True, "node.dont-reconnect": True}),
        node(30, **{"node.name": sink.name, "object.serial": sink.serial, "media.class": "Audio/Sink"}),
        link(21, 30),
    ]


class Profiles(unittest.TestCase):
    def test_bundled_profile_matches_documented_measurement(self):
        profile = read_profile(reference_profile())
        self.assertEqual(profile.preamp_db, -2.6)
        self.assertEqual(profile.bands, 5)
        metadata = json.loads(reference_profile().with_suffix(".json").read_text())
        self.assertEqual(metadata["upstream_commit"], "7ae0f56d53074872b028649617a22bbb4232feb7")
        self.assertIn("DHRME", metadata["measurement_source"])

    def test_invalid_profiles_never_become_silent_flat_eq(self):
        examples = ["", "Preamp: 2 dB\nFilter 1: ON PK Fc 100 Hz Gain 1 dB Q 1",
                    "Preamp: -2 dB\nFilter 1: ON INVALID Fc 100 Hz Gain 1 dB Q 1",
                    "Preamp: -2 dB\nFilter 1: ON PK Fc 99999 Hz Gain 1 dB Q 1",
                    "Preamp: -2 dB\nFilter 1: ON PK Fc 100 Hz Gain 99 dB Q 1",
                    "Preamp: -2 dB\nFilter 1: ON PK Fc 100 Hz Gain 1 dB Q 0",
                    "Preamp: -2 dB\nFilter 1: OFF PK Fc 100 Hz Gain 1 dB Q 1"]
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / "profile.txt"
            for example in examples:
                with self.subTest(example=example):
                    file.write_text(example)
                    with self.assertRaises(EqualizerError):
                        read_profile(file)

    def test_comment_and_disabled_band_normalization(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / "profile.txt"
            file.write_text("# Reference\nPreamp: -3.0 dB\nFilter 1: OFF PK Fc 100 Hz Gain 1 dB Q 1\nFilter 2: ON LSC Fc 105 Hz Gain -1 dB Q 0.7\n")
            parsed = read_profile(file)
            self.assertEqual(parsed.text, "Preamp: -3 dB\nFilter 1: ON LSC Fc 105 Hz Gain -1 dB Q 0.7\n")


class FilterConfiguration(unittest.TestCase):
    def test_native_target_and_downstream_limiter(self):
        config = filter_config(Path("/tmp/profile.txt"), SINK, GROUP, "/usr/lib/ladspa/fast_lookahead_limiter_1913.so")
        data = config["context.modules"][-1]["args"]
        self.assertEqual(data["capture.props"]["filter.smart.target"], {"node.name": SINK.name, "object.serial": "99"})
        self.assertEqual(data["playback.props"]["target.object"], "99")
        self.assertTrue(data["playback.props"]["node.dont-fallback"])
        self.assertTrue(data["playback.props"]["node.dont-reconnect"])
        filters = data["filter.graph"]
        self.assertEqual(filters["nodes"][0]["label"], "param_eq")
        self.assertEqual(filters["nodes"][1]["label"], "fastLookaheadLimiter")
        self.assertEqual(filters["nodes"][1]["control"]["Input gain (dB)"], 0)
        self.assertEqual(filters["nodes"][1]["control"]["Limit (dB)"], -1)
        self.assertEqual(filters["links"][0], {"output": "eq:Out 1", "input": "limit:Input 1"})
        self.assertNotIn("set-default", json.dumps(config))

    def test_speakers_and_stale_unusable_sink_cannot_receive_headphone_profile(self):
        for sink in (Sink(SINK.device, "alsa_output.speakers", "99", "a2dp", "idle"),
                     Sink(SINK.device, SINK.name, "99", "headset-head-unit", "running")):
            with self.subTest(sink=sink), self.assertRaises(EqualizerError):
                filter_config(Path("/tmp/profile.txt"), sink, GROUP, "limiter")


class GraphAudit(unittest.TestCase):
    def test_complete_owned_chain_to_current_physical_device_approved(self):
        result = audit_graph(graph(), pid=123, group=GROUP, sink=SINK)
        self.assertEqual(result["state"], "verified")
        self.assertEqual(result["input_node_ids"], ["20"])

    def test_spoofed_node_names_from_other_process_never_approved(self):
        result = audit_graph(graph(), pid=456, group=GROUP, sink=SINK)
        self.assertEqual(result["input_node_ids"], [])

    def test_output_to_speaker_rejected_even_if_also_linked_to_headphones(self):
        objects = graph() + [node(40, **{"media.class": "Audio/Sink"}), link(21, 40)]
        result = audit_graph(objects, pid=123, group=GROUP, sink=SINK)
        self.assertEqual(result["state"], "violation")

    def test_missing_output_safety_or_wrong_smart_target_rejected(self):
        for field, value in (("node.dont-fallback", False), ("node.link-group", "other"), ("target.object", "100")):
            objects = graph()
            objects[2]["info"]["props"][field] = value
            with self.subTest(field=field):
                self.assertEqual(audit_graph(objects, pid=123, group=GROUP, sink=SINK)["state"], "violation")
        objects = graph()
        objects[1]["info"]["props"]["filter.smart.target"] = {"node.name": "speakers"}
        self.assertEqual(audit_graph(objects, pid=123, group=GROUP, sink=SINK)["state"], "violation")

    def test_stale_sink_serial_or_unlinked_filter_never_approved(self):
        objects = graph()
        objects[3]["info"]["props"]["object.serial"] = "100"
        self.assertEqual(audit_graph(objects, pid=123, group=GROUP, sink=SINK)["input_node_ids"], [])
        self.assertEqual(audit_graph(graph()[:-1], pid=123, group=GROUP, sink=SINK)["state"], "waiting")

    def test_owned_input_before_output_is_pending_only(self):
        objects = graph()
        objects.pop(2)
        objects = objects[:-1]
        result = audit_graph(objects, pid=123, group=GROUP, sink=SINK)
        self.assertEqual(result["state"], "waiting")
        self.assertEqual(result["input_node_ids"], [])
        self.assertEqual(result["pending_input_node_ids"], ["20"])


class FakeProcess:
    def __init__(self):
        self.pid = 123
        self.returncode = None
        self.terminated = False
    def terminate(self):
        self.terminated = True
        self.returncode = -15
    def kill(self):
        self.returncode = -9
    async def wait(self):
        return self.returncode


class Lifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_removes_owned_process_and_config(self):
        eq = Equalizer(enabled=True, bluetooth_address=SINK.device)
        eq._dependencies = AsyncMock(return_value="/tmp/limiter.so")
        proc = FakeProcess()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)) as launch:
            await eq.update_sink(SINK)
            root = Path(eq._directory.name)
            self.assertTrue(root.exists())
            self.assertEqual(eq.status()["state"], "starting")
            self.assertEqual(launch.await_args.args[0], "pipewire")
            self.assertEqual(launch.await_args.args[1], "-c")
            self.assertNotIn("PIPEWIRE_PROPS", launch.await_args.kwargs["env"])
            self.assertEqual(eq.verified_input_ids(graph(eq._group)), {"20"})
            self.assertEqual(eq.status()["state"], "active")
            await eq.update_sink(None)
            self.assertTrue(proc.terminated)
            self.assertFalse(root.exists())
            self.assertEqual(eq.status()["state"], "waiting")

    async def test_reconnect_with_new_serial_creates_fresh_exact_target(self):
        eq = Equalizer(enabled=True)
        eq._dependencies = AsyncMock(return_value="/tmp/limiter.so")
        first, second = FakeProcess(), FakeProcess()
        with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=[first, second])):
            await eq.update_sink(SINK)
            old_group = eq._group
            reconnected = Sink(SINK.device, SINK.name, "100", SINK.profile, SINK.state)
            await eq.update_sink(reconnected)
            self.assertTrue(first.terminated)
            self.assertNotEqual(old_group, eq._group)
            config = json.loads((Path(eq._directory.name) / "filter.conf").read_text())
            self.assertEqual(config["context.modules"][-1]["args"]["playback.props"]["target.object"], "100")
            await eq.stop()

    async def test_missing_dependency_removes_only_eq_and_backs_off(self):
        eq = Equalizer(enabled=True)
        eq._dependencies = AsyncMock(side_effect=EqualizerError("missing limiter"))
        await eq.update_sink(SINK)
        await eq.update_sink(SINK)
        self.assertEqual(eq._dependencies.await_count, 1)
        self.assertEqual(eq.status()["state"], "error")
        self.assertEqual(eq.status()["reason"], "missing limiter")
        self.assertIsNone(eq.process)

    async def test_route_violation_terminates_eq_immediately(self):
        eq = Equalizer(enabled=True)
        eq._dependencies = AsyncMock(return_value="/tmp/limiter.so")
        proc = FakeProcess()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            await eq.update_sink(SINK)
            self.assertEqual(eq.verified_input_ids(graph(eq._group) + [link(21, 444)]), set())
            self.assertTrue(proc.terminated)
            self.assertEqual(eq.status()["state"], "error")
            await eq.stop()

    async def test_disabled_or_other_headphone_never_launches(self):
        eq = Equalizer(enabled=False)
        eq._dependencies = AsyncMock()
        await eq.update_sink(SINK)
        eq._dependencies.assert_not_awaited()
        eq.enabled = True
        eq.bluetooth_address = "00:11:22:33:44:55"
        await eq.update_sink(SINK)
        eq._dependencies.assert_not_awaited()
        self.assertEqual(eq.status()["state"], "error")

    async def test_pending_used_filter_has_bounded_output_link_timeout(self):
        eq = Equalizer(enabled=True)
        eq._dependencies = AsyncMock(return_value="/tmp/limiter.so")
        proc = FakeProcess()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            await eq.update_sink(SINK)
            objects = graph(eq._group)[:-1] + [link(777, 20)]
            self.assertEqual(eq.pending_input_ids(objects), {"20"})
            self.assertFalse(proc.terminated)
            eq._pending_since -= 6
            self.assertEqual(eq.pending_input_ids(objects), set())
            self.assertTrue(proc.terminated)
            self.assertIn("five seconds", eq.status()["reason"])
            await eq.stop()


class ActualLimiterPlugin(unittest.TestCase):
    def test_ladspa_abi_ports_and_real_dsp_sample_ceiling(self):
        path = os.environ.get("SPATIAL_TEST_LIMITER_PLUGIN")
        if not path:
            try:
                path = find_limiter()
            except EqualizerError:
                self.skipTest("SWH limiter is not installed; real DSP test requires swh-plugins")
        class Descriptor(ctypes.Structure):
            pass
        ptr = ctypes.c_void_p
        data = ctypes.POINTER(ctypes.c_float)
        instantiate = ctypes.CFUNCTYPE(ptr, ctypes.POINTER(Descriptor), ctypes.c_ulong)
        connect = ctypes.CFUNCTYPE(None, ptr, ctypes.c_ulong, data)
        activate = ctypes.CFUNCTYPE(None, ptr)
        run = ctypes.CFUNCTYPE(None, ptr, ctypes.c_ulong)
        Descriptor._fields_ = [
            ("id", ctypes.c_ulong), ("label", ctypes.c_char_p), ("properties", ctypes.c_int),
            ("name", ctypes.c_char_p), ("maker", ctypes.c_char_p), ("copyright", ctypes.c_char_p),
            ("port_count", ctypes.c_ulong), ("port_descriptors", ctypes.POINTER(ctypes.c_int)),
            ("port_names", ctypes.POINTER(ctypes.c_char_p)), ("range_hints", ptr), ("implementation", ptr),
            ("instantiate", instantiate), ("connect", connect), ("activate", activate),
            ("run", run), ("run_adding", ptr), ("set_gain", ptr), ("deactivate", activate),
            ("cleanup", activate)]
        library = ctypes.CDLL(path)
        library.ladspa_descriptor.argtypes = [ctypes.c_ulong]
        library.ladspa_descriptor.restype = ctypes.POINTER(Descriptor)
        descriptor_pointer = library.ladspa_descriptor(0)
        descriptor = descriptor_pointer.contents
        self.assertEqual(descriptor.id, 1913)
        self.assertEqual(descriptor.label, b"fastLookaheadLimiter")
        ports = {descriptor.port_names[i].decode(): i for i in range(descriptor.port_count)}
        expected = {"Input gain (dB)", "Limit (dB)", "Release time (s)",
                    "Attenuation (dB)", "Input 1", "Input 2", "Output 1", "Output 2", "latency"}
        self.assertEqual(set(ports), expected)
        count, rate = 48000, 48000
        buffers = {}
        for name, index in ports.items():
            audio = bool(descriptor.port_descriptors[index] & 8)
            buffers[name] = (ctypes.c_float * (count if audio else 1))()
        buffers["Input gain (dB)"][0] = 0
        buffers["Limit (dB)"][0] = -1
        buffers["Release time (s)"][0] = 0.05
        for i in range(count):
            amplitude = 0.1 if i < count // 2 else 8.0
            buffers["Input 1"][i] = amplitude * math.sin(2 * math.pi * 997 * i / rate)
            buffers["Input 2"][i] = amplitude * math.sin(2 * math.pi * 503 * i / rate)
        instance = descriptor.instantiate(descriptor_pointer, rate)
        self.assertTrue(instance)
        try:
            for name, index in ports.items():
                descriptor.connect(instance, index, buffers[name])
            if descriptor.activate:
                descriptor.activate(instance)
            descriptor.run(instance, count)
            ceiling = 10 ** (-1 / 20)
            for name in ("Output 1", "Output 2"):
                peak = max(abs(value) for value in buffers[name])
                self.assertLessEqual(peak, ceiling + 1e-6)
                self.assertGreater(peak, 0.5)
                quiet_peak = max(abs(value) for value in buffers[name][2000:12000])
                self.assertAlmostEqual(quiet_peak, 0.1, places=4)
            self.assertEqual(int(buffers["latency"][0]), 240)
        finally:
            if descriptor.deactivate:
                descriptor.deactivate(instance)
            descriptor.cleanup(instance)


if __name__ == "__main__":
    unittest.main()
