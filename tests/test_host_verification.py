import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

spec = importlib.util.spec_from_file_location("host_verify", Path(__file__).resolve().parents[1] / "tools/verify.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class AcceptanceClassificationTests(unittest.TestCase):
    def test_missing_hardware_is_pending_not_passed(self):
        report = {"checks": {}}
        verify.assess_state(report, {"schema": 1, "connected": False},
                            SimpleNamespace(sony_enabled=True, slime_enabled=True))
        self.assertEqual(verify.overall(report["checks"]), ("waiting", 2))
        self.assertEqual(report["checks"]["headphones"]["status"], "wait")

    def test_disabled_tracker_is_distinct_from_broken_tracker(self):
        settings = SimpleNamespace(sony_enabled=False, slime_enabled=True)
        report = {"checks": {}}
        verify.assess_state(report, {"runtime": {"providers": {"optional_tracker": {
            "state": "unavailable", "detail": "permission denied"}}}}, settings)
        self.assertEqual(report["checks"]["sony_tracker"]["status"], "off")
        self.assertEqual(report["checks"]["optional_tracker"]["status"], "fail")
        self.assertEqual(verify.overall(report["checks"]), ("fail", 1))


class RealGraphAuditIntegrationTests(unittest.TestCase):
    @staticmethod
    def fixture():
        address, name = "AA:BB:CC:DD:EE:FF", "bluez_output.AA_BB_CC_DD_EE_FF.1"
        def obj(kind, number, **props):
            return {"type": "PipeWire:Interface:" + kind, "id": number, "info": {"state": "running", "props": props}}
        def link(number, source, destination, port):
            return {"type": "PipeWire:Interface:Link", "id": number, "info": {
                "output-node-id": source, "input-node-id": destination,
                "output-port-id": port, "state": "active"}}
        state = {"schema": 1, "connected": True, "device": address, "target": name,
                 "runtime": {"audio": {"running": True, "process_id": 777, "pcm_route_allowed": True}}}
        graph = [obj("Node", 5, **{"media.class": "Audio/Sink", "node.name": name, "object.serial": "75",
                                  "api.bluez5.address": address, "api.bluez5.profile": "a2dp"}),
                 obj("Node", 10, **{"media.class": "Stream/Output/Audio", "application.name": "Spatial Audio",
                                   "application.process.id": 777, "object.serial": "100", "target.object": name,
                                   "node.dont-fallback": True, "node.dont-reconnect": False}),
                 obj("Port", 101, **{"node.id": 10, "port.direction": "out"}),
                 obj("Port", 102, **{"node.id": 10, "port.direction": "out"}),
                 link(401, 10, 5, 101), link(402, 10, 5, 102)]
        return state, graph, obj, link

    def test_native_pcm_policy_is_observed_without_attribute_error(self):
        state, graph, _, _ = self.fixture()
        self.assertEqual(verify.audit_playback_graph(state, graph)[0], "pass")
        state["runtime"]["audio"]["pcm_route_allowed"] = False
        self.assertEqual(verify.audit_playback_graph(state, graph)[0], "fail")

    def test_actual_live_and_equalizer_chains_are_audited_before_allowing_them(self):
        from spatial.live_audio import GUARD_KEY
        state, graph, obj, link = self.fixture()
        name, group = state["target"], "spatial-eq-" + "a" * 32
        graph[-2]["info"]["input-node-id"] = 20
        graph[-1]["info"]["input-node-id"] = 20
        graph.extend([
            obj("Node", 20, **{"media.class": "Audio/Sink", "node.name": "spatial-live-test", "object.serial": "200",
                               "application.process.id": 999}),
            obj("Node", 30, **{"media.class": "Stream/Output/Audio", "node.name": "omniphony", "object.serial": "300",
                               "application.process.id": 999, "target.object": name,
                               "node.dont-fallback": True, "node.dont-move": True}),
            obj("Port", 301, **{"node.id": 30, "port.direction": "out"}),
            obj("Port", 302, **{"node.id": 30, "port.direction": "out"}),
            obj("Node", 40, **{"media.class": "Audio/Sink", "node.name": group + ".input", "application.process.id": 444,
                               "node.link-group": group, "filter.smart": True,
                               "filter.smart.target": {"node.name": name, "object.serial": "75"}}),
            obj("Node", 50, **{"media.class": "Stream/Output/Audio", "node.name": group + ".output",
                               "application.process.id": 444, "node.link-group": group, "target.object": "75",
                               "node.dont-fallback": True, "node.dont-reconnect": True}),
            link(403, 30, 40, 301), link(404, 30, 40, 302), link(405, 50, 5, 501),
        ])
        metadata = obj("Metadata", 2, **{"metadata.name": "default"})
        metadata["metadata"] = [
            {"subject": 0, "key": "spatiald.live-guard", "type": "Spa:String", "value": "1"},
            {"subject": 10, "key": "target.object", "type": "Spa:Id", "value": "200"},
            {"subject": 10, "key": GUARD_KEY, "type": "Spa:String", "value": "100:200"},
        ]
        graph.append(metadata)
        state["runtime"]["live"] = {"running": True, "process_id": 999, "input_node": "spatial-live-test",
                                      "input_serial": "200", "stream_serial": "100", "route_applied": True,
                                      "renderer_ready": True, "state": "playing"}
        state["runtime"]["equalizer"] = {"enabled": True, "process_id": 444, "link_group": group}
        result = verify.audit_playback_graph(state, graph)
        self.assertEqual(result[0], "pass", result[1])
        graph.append(link(406, 30, 987, 301))
        self.assertEqual(verify.audit_playback_graph(state, graph)[0], "fail")


class PlaybackOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_playback_is_never_replaced_by_default(self):
        import json
        class Interface:
            async def get_state(self):
                return json.dumps({"schema": 1, "audio_ready": True,
                                   "runtime": {"audio": {"running": True}}})
            async def call_play(self, media):
                raise AssertionError("Existing playback must not be replaced")
            async def call_stop(self):
                raise AssertionError("Existing playback must not be stopped")
        report = {"checks": {}}
        await verify.playback_test(report, Interface(), SimpleNamespace(replace=False), None)
        self.assertEqual(report["checks"]["test_playback"]["status"], "fail")

    async def test_atomic_idle_rejection_preserves_racing_playback(self):
        import json
        class Interface:
            async def get_state(self):
                return json.dumps({"schema": 1, "audio_ready": True,
                                   "runtime": {"audio": {"running": False}}})
            async def call_begin_test_playback(self, media, replace):
                return 0
            async def call_stop_if_request(self, token):
                raise AssertionError("No owned process exists")
        report = {"checks": {}}
        args = SimpleNamespace(replace=False, media=Path(__file__).resolve(), timeout=1)
        await verify.playback_test(report, Interface(), args, None)
        self.assertEqual(report["checks"]["test_playback"]["status"], "wait")

    async def test_timeout_cancels_owned_request_before_a_pid_exists(self):
        import json
        class Interface:
            def __init__(self):
                self.started = False
                self.stopped = []
            async def get_state(self):
                return json.dumps({"schema": 1, "audio_ready": True,
                                   "runtime": {"loading": self.started, "audio": {"running": False}}})
            async def call_begin_test_playback(self, media, replace):
                self.started = True
                return 42
            async def call_stop_if_request(self, token):
                self.stopped.append(token)
                return True
        report, interface = {"checks": {}}, Interface()
        args = SimpleNamespace(replace=False, media=Path(__file__).resolve(), timeout=0.001,
                               require_spatial=False, seconds=2)
        with patch.object(verify, "capture_graph", new=AsyncMock()):
            await verify.playback_test(report, interface, args,
                                       SimpleNamespace(sony_enabled=False, slime_enabled=False))
        self.assertEqual(interface.stopped, [42])
        self.assertEqual(report["checks"]["test_cleanup"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
