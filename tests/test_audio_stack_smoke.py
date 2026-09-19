"""Regression checks for the integration gate's event evidence collector."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np


path = Path(__file__).resolve().parents[1] / "tools/ci/audio-stack-smoke.py"
spec = importlib.util.spec_from_file_location("audio_stack_smoke", path)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def node(identifier, serial):
    return {"id": identifier, "type": "PipeWire:Interface:Node", "info": {
        "props": {"object.serial": serial, "node.name": "node-" + str(identifier)}}}


def link(identifier, target, state="active"):
    return {"id": identifier, "type": "PipeWire:Interface:Link", "info": {
        "output-node-id": 10, "input-node-id": target, "state": state}}


def port(identifier, node_identifier, direction, channel, monitor=False):
    return {"id": identifier, "type": "PipeWire:Interface:Port", "info": {
        "direction": direction, "props": {"node.id": node_identifier,
        "audio.channel": channel, "port.monitor": monitor}}}


def channel_graph(channels=smoke.SURROUND_CHANNELS):
    source, target = node(10, 100), node(20, 200)
    for obj in (source, target):
        obj["info"]["params"] = {"Format": [{"mediaType": "audio", "mediaSubtype": "raw",
            "rate": 48000, "channels": len(channels), "position": list(channels)}]}
    result = [source, target]
    for index, channel in enumerate(channels):
        result.extend([port(100 + index, 10, "output", channel),
                       port(200 + index, 20, "input", channel),
                       {"id": 300 + index, "type": "PipeWire:Interface:Link", "info": {
                           "output-node-id": 10, "output-port-id": 100 + index,
                           "input-node-id": 20, "input-port-id": 200 + index,
                           "state": "active"}}])
    return result


class AudioGateEvidenceTests(unittest.TestCase):
    def test_surround_requires_all_eight_actual_same_channel_links(self):
        self.assertTrue(smoke.surround_input_preserved(channel_graph(), 10, 20))
        # This reproduces the hosted run: native PCM is 7.1 but the source's
        # actual graph has already downmixed it to two ports.
        objects = [obj for obj in channel_graph()
                   if obj["id"] not in set(range(102, 108)) | set(range(302, 308))]
        self.assertFalse(smoke.surround_input_preserved(objects, 10, 20))

    def test_rejects_unknown_renderer_layout_even_with_eight_links(self):
        objects = channel_graph()
        del objects[1]["info"]["params"]["Format"][0]["position"]
        for obj in objects:
            if obj.get("type") == "PipeWire:Interface:Port" and obj["info"]["direction"] == "input":
                obj["info"]["props"]["audio.channel"] = "UNK"
        self.assertFalse(smoke.surround_input_preserved(objects, 10, 20))

    def test_rejects_swapped_channels_despite_correct_link_count(self):
        objects = channel_graph()
        for obj in objects:
            if obj["id"] == 300:
                obj["info"]["input-port-id"] = 201
            elif obj["id"] == 301:
                obj["info"]["input-port-id"] = 200
        self.assertFalse(smoke.surround_input_preserved(objects, 10, 20))

    def test_rejects_side_routes_before_they_become_active(self):
        objects = channel_graph()
        objects.append(link(999, 40, "init"))
        self.assertFalse(smoke.surround_input_preserved(objects, 10, 20))

    def test_rejects_duplicate_links_and_ambiguous_channel_ports(self):
        objects = channel_graph()
        duplicate = json.loads(json.dumps(objects[-1]))
        duplicate["id"] = 999
        self.assertFalse(smoke.surround_input_preserved(objects + [duplicate], 10, 20))
        self.assertFalse(smoke.surround_input_preserved(
            objects + [port(999, 10, "output", "FL")], 10, 20))

    def test_stereo_graph_cannot_be_satisfied_by_arbitrary_ports(self):
        objects = channel_graph(("FL", "FR"))
        self.assertTrue(smoke.stereo_linked(objects, 10, 20))
        next(obj for obj in objects if obj["id"] == 201)["info"]["props"]["audio.channel"] = "UNK"
        self.assertFalse(smoke.stereo_linked(objects, 10, 20))

    def test_ports_for_selects_stereo_monitor_channels(self):
        objects = [port(11, 10, "output", "FL", True),
                   port(12, 10, "output", "FR", True),
                   port(13, 10, "output", "AUX", True),
                   port(14, 10, "input", "FL"),
                   port(15, 20, "output", "FL", True)]
        self.assertEqual(set(smoke.ports_for(objects, 10, direction="output", monitor=True)),
                         {"FL", "FR"})
        self.assertEqual(set(smoke.ports_for(objects, 10, direction="input")), {"FL"})

    def test_consecutive_arrays_split_at_every_character_and_both_removal_forms(self):
        events = [[node(10, 100), node(20, 200), link(30, 20)],
                  [{"id": 20, "info": None}], [{"id": 30, "props": None}]]
        sequence = smoke.JSONSequence()
        parsed = []
        for char in "\n".join(json.dumps(event) for event in events):
            parsed.extend(sequence.feed(char))
        self.assertEqual(parsed, events)
        watch = smoke.GraphWatch()
        for event in parsed:
            watch.feed(event)
        self.assertEqual(set(watch.objects), {"10"})
        self.assertEqual(watch.removals, 2)

    def test_transient_forbidden_link_remains_a_failure_after_its_removal(self):
        watch = smoke.GraphWatch()
        watch.feed([node(10, 100), node(20, 200), node(40, 400), link(30, 20)])
        watch.arm(100, 200)
        watch.feed([link(31, 40, "init")])
        watch.feed([{"id": 31, "info": None}])
        self.assertEqual(len(watch.violations), 1)
        self.assertEqual(watch.violations[0]["target"], "400")

    def test_removed_endpoint_state_event_keeps_only_that_links_original_identity(self):
        watch = smoke.GraphWatch()
        watch.feed([node(10, 100), node(20, 200), link(30, 20)])
        watch.arm(100, 200)
        watch.feed([{"id": 20, "info": None}])
        watch.feed([link(30, 20, "unlinked")])
        self.assertFalse(watch.violations)
        watch.feed([{"id": 30, "info": None}, node(20, 201), link(30, 20, "init")])
        self.assertEqual(watch.violations[0]["target"], "201")

    def test_unknown_destination_does_not_inherit_deleted_link_authorization(self):
        watch = smoke.GraphWatch()
        watch.feed([node(10, 100), node(20, 200), link(30, 20)])
        watch.arm(100, 200)
        watch.feed([{"id": 20, "info": None}, {"id": 30, "info": None}])
        watch.feed([link(30, 20, "init")])
        self.assertEqual(len(watch.violations), 1)


class InputLaneEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.capture = Path(self.directory.name) / "input.f32le"
        self.samples = np.frombuffer(smoke.lane_fixture() * 10, dtype="<f4").reshape(-1, 8).copy()

    def analyze(self, samples):
        self.capture.write_bytes(samples.astype("<f4").tobytes())
        return smoke.analyze_input_lanes(self.capture)

    def test_independent_lanes_survive_arbitrary_capture_phase_and_gain(self):
        samples = np.roll(self.samples, 177, axis=0) * np.arange(1, 9)[None, :] / 8
        result = self.analyze(samples)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["channels"], list(smoke.SURROUND_CHANNELS))

    def test_swapped_rear_and_front_channels_fail(self):
        self.samples[:, [0, 6]] = self.samples[:, [6, 0]]
        with self.assertRaisesRegex(smoke.Failure, "loses, swaps, or mixes"):
            self.analyze(self.samples)

    def test_stereo_downmix_and_reexpansion_fail(self):
        self.samples[:, 0] += self.samples[:, 6]
        self.samples[:, 1] += self.samples[:, 7]
        self.samples[:, 6:] = self.samples[:, :2]
        with self.assertRaisesRegex(smoke.Failure, "loses, swaps, or mixes"):
            self.analyze(self.samples)

    def test_dropped_lfe_fails(self):
        self.samples[:, 3] = 0
        with self.assertRaises(smoke.Failure) as caught:
            self.analyze(self.samples)
        self.assertEqual(caught.exception.report["status"], "failed")

    def test_identical_signal_copied_to_every_lane_fails(self):
        self.samples[:] = self.samples[:, 0, None]
        with self.assertRaises(smoke.Failure):
            self.analyze(self.samples)

    def test_nan_cannot_be_accepted_as_tone_evidence(self):
        self.samples[24000, 3] = np.nan
        with self.assertRaisesRegex(smoke.Failure, "non-finite"):
            self.analyze(self.samples)


if __name__ == "__main__":
    unittest.main()
