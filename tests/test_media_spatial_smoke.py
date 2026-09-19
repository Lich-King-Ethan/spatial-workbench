"""Media gate evidence must describe the verified capture and cleanup result."""
import copy
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from spatial.audio_runtime import renderer_pose
from spatial.core import Sink
from spatial.pose import IDENTITY, Quaternion


path = Path(__file__).resolve().parents[1] / "tools/ci/media-spatial-smoke.py"
spec = importlib.util.spec_from_file_location("media_spatial_smoke", path)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
SINK = Sink("AA:BB:CC:DD:EE:FF", "earbuds", "99", "a2dp", "idle")


def node(identifier, **properties):
    return {"id": identifier, "type": "PipeWire:Interface:Node",
            "info": {"props": properties}}


def port(identifier, owner, channel, direction):
    return {"id": identifier, "type": "PipeWire:Interface:Port", "info": {
        "direction": direction, "props": {"node.id": owner, "audio.channel": channel}}}


def links(source, target, first_port):
    result = []
    for offset, channel in enumerate(("FL", "FR")):
        output, input_ = first_port + offset, first_port + offset + 2
        result.extend((port(output, source, channel, "output"),
                       port(input_, target, channel, "input"),
                       {"type": "PipeWire:Interface:Link", "info": {
                           "output-node-id": source, "input-node-id": target,
                           "output-port-id": output, "input-port-id": input_, "state": "active"}}))
    return result


def graph():
    return [node(1, **{"media.class": "Stream/Output/Audio", "application.process.id": 77,
                       "object.serial": "10"}),
            node(2, **{"object.serial": "20"}),
            node(3, **{"node.name": "eq.output", "object.serial": "30"}),
            node(4, **{"node.name": SINK.name, "object.serial": SINK.serial}),
            *links(1, 2, 100), *links(3, 4, 200)]


class StereoEvidenceTests(unittest.TestCase):
    def test_exact_stereo_channels_and_every_link_are_required(self):
        objects = graph()
        self.assertTrue(smoke.stereo_linked(objects, 1, 2))
        for mutation in ("swapped", "unknown", "duplicate", "side_link", "inactive", "wrong_node"):
            changed = copy.deepcopy(objects)
            ports = {obj.get("id"): obj for obj in changed if obj.get("type").endswith(":Port")}
            edges = [obj for obj in changed if obj.get("type").endswith(":Link")]
            if mutation == "swapped":
                ports[102]["info"]["props"]["audio.channel"] = "FR"
                ports[103]["info"]["props"]["audio.channel"] = "FL"
            elif mutation == "unknown":
                del ports[102]["info"]["props"]["audio.channel"]
            elif mutation == "duplicate":
                changed.append(copy.deepcopy(edges[0]))
            elif mutation == "side_link":
                changed.append({"type": "PipeWire:Interface:Link", "info": {
                    "output-node-id": 1, "input-node-id": 99, "state": "init"}})
            elif mutation == "inactive":
                edges[0]["info"]["state"] = "error"
            else:
                ports[102]["info"]["props"]["node.id"] = 99
            with self.subTest(mutation=mutation):
                self.assertFalse(smoke.stereo_linked(changed, 1, 2))


class FakePlayer:
    def __init__(self):
        self.process = SimpleNamespace(pid=77)
        self.pose = None
        self.ready = True
        self.routing = "waiting"
        self.telemetry = SimpleNamespace(renderer={"binaural": {}}, send=lambda *args: None)
        self.start = AsyncMock()
        self.stop = AsyncMock()

    def status(self):
        return {"running": True, "error": "", "decoder": "orender", "object_count": 11,
                "renderer_ready": self.ready, "source_mode": "spatial" if self.ready else "stereo",
                "tracking": self.ready and self.pose is not None, "routing": {"state": self.routing}}

    def verify_output(self, objects, **kwargs):
        self.routing = "verified"
        return {"state": self.routing}

    def set_pose(self, pose):
        self.pose = pose
        self.telemetry.renderer["binaural"]["headPose"] = dict(zip("wxyz", renderer_pose(pose)))


class Tracker:
    records = {"adapter": "fixture"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def pose(self, name):
        if name == "pose_yaw_plus90":
            return Quaternion.parse((1, 0, 1, 0))
        if name == "pose_yaw_minus90":
            return Quaternion.parse((1, 0, -1, 0))
        return IDENTITY


class MediaReportTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, directory, *, cleanup_failure=False, lose_readiness=False):
        player = FakePlayer()
        objects = graph()
        async def until(name, predicate, **kwargs):
            if name == "actual-atmos-player-cleaned-up":
                if cleanup_failure:
                    raise RuntimeError("owned player nodes remain")
                self.assertTrue(predicate([]))
            else:
                self.assertTrue(predicate(objects))
            return objects
        async def window(name, **kwargs):
            capture = directory / (name + ".f32le")
            left, right = (0.1, 0.2) if name.endswith("yaw_plus90") else (0.2, 0.1)
            capture.write_bytes(struct.pack("<ff", left, right) * 72192)
            if lose_readiness:
                player.ready = False
            return capture
        gate = SimpleNamespace(report_dir=directory,
            equalizer=SimpleNamespace(verified_input_ids=lambda objects: {"2"},
                pending_input_ids=lambda objects: set(), status=lambda: {"link_group": "eq"}),
            watch=SimpleNamespace(links=[], arm=lambda *args: None, protected=None),
            until=until, observe=AsyncMock(), snapshot=AsyncMock(return_value=objects))
        fixture = SimpleNamespace(FIXTURE_URL="https://example.test/fixture", FIXTURE_SHA256="pinned",
                                  fetch_fixture=lambda: b"fixture")
        def helper(name):
            return fixture if name == "decoder-smoke.py" else SimpleNamespace(SonyPoseFixtures=Tracker)
        with patch("spatial.audio_runtime.AudioRuntime", return_value=player), patch.object(smoke, "helper", helper):
            result = await smoke.run(gate, SINK, "bridge.so", SimpleNamespace(window=window))
        player.stop.assert_awaited_once()
        return result

    async def test_report_records_verified_capture_state_and_acknowledged_pose(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result = await self.exercise(directory)
            saved = json.loads((directory / "media-spatial-smoke.json").read_text())
            self.assertEqual(saved, result)
            self.assertEqual(saved["status"], "passed")
            self.assertFalse(saved["initial_player"]["tracking"])
            self.assertEqual(saved["initial_player"]["routing"]["state"], "waiting")
            self.assertTrue(saved["player"]["tracking"])
            self.assertEqual(saved["player"]["routing"]["state"], "verified")
            for window in saved["windows"].values():
                self.assertTrue(window["player"]["renderer_ready"])
                self.assertTrue(window["player"]["tracking"])
                self.assertEqual(window["renderer_pose"], window["expected_renderer_pose"])

    async def test_cleanup_failure_cannot_leave_a_passed_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "nodes remain"):
                await self.exercise(directory, cleanup_failure=True)
            saved = json.loads((directory / "media-spatial-smoke.json").read_text())
            self.assertEqual(saved["status"], "failed")
            self.assertIn("nodes remain", saved["cleanup_error"])

    async def test_renderer_losing_readiness_during_capture_fails_the_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "during capture"):
                await self.exercise(directory, lose_readiness=True)
            saved = json.loads((directory / "media-spatial-smoke.json").read_text())
            self.assertEqual(saved["status"], "failed")
            self.assertIn("during capture", saved["error"])


if __name__ == "__main__":
    unittest.main()
