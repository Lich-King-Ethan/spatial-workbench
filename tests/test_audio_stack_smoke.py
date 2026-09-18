"""Regression checks for the integration gate's event evidence collector."""
import importlib.util
import json
from pathlib import Path
import unittest


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


class AudioGateEvidenceTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
