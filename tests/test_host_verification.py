import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

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


class InstallationSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def inspect(self, *, core_only=False, graph_error=None, daemon_error=None):
        import json
        from spatial.settings import Settings
        settings = Settings()
        interface = SimpleNamespace(get_state=AsyncMock(return_value=json.dumps({
            "schema": 1, "runtime": {"providers": {"sony_tracker": {
                "state": "unavailable", "detail": "optional tracker helper not installed"}}}})))
        bus = Mock()
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text("schema=1\n")
            args = SimpleNamespace(config=config, media=None, core_only=core_only)
            with patch("spatial.settings.Settings.load", return_value=settings), \
                    patch.object(verify.shutil, "which", side_effect=lambda name: "/usr/bin/pw-dump" if name == "pw-dump" else None), \
                    patch("spatial.audio_runtime.AudioRuntime.probe", new=AsyncMock(return_value={
                        "available": False, "reason": "optional decoder not installed"})) as probe, \
                    patch("spatial.live_audio.LiveAudio.probe", new=AsyncMock(side_effect=RuntimeError("renderer not installed"))) as live_probe, \
                    patch("spatial.equalizer.Equalizer._dependencies", new=AsyncMock(side_effect=RuntimeError("limiter not installed"))) as eq_probe, \
                    patch.object(verify, "connect", new=AsyncMock(return_value=(bus, interface), side_effect=daemon_error)), \
                    patch("spatial.pipewire.capture", new=AsyncMock(return_value=[], side_effect=graph_error)):
                report = await verify.verify(args)
                if core_only:
                    probe.assert_not_awaited()
                    live_probe.assert_not_awaited()
                    eq_probe.assert_not_awaited()
                else:
                    probe.assert_awaited_once()
                    live_probe.assert_awaited_once()
                    eq_probe.assert_awaited_once()
        return report

    async def test_core_install_checks_only_selected_capabilities(self):
        report = await self.inspect(core_only=True)
        self.assertEqual(report["selection"], "core")
        self.assertEqual(verify.overall(report["checks"]), ("pass", 0))
        for name in ("configuration", "daemon", "pipewire_graph", "tool:pw-dump"):
            self.assertEqual(report["checks"][name]["status"], "pass")
        for name in ("decoder_features", "live_features", "equalizer_features", "audio_modules", "playback_route"):
            self.assertEqual(report["checks"][name]["status"], "off")
        self.assertNotIn("tool:mpv", report["checks"])
        self.assertNotIn("tool:sony-tracker", report["checks"])

    async def test_full_verification_still_fails_for_missing_audio_dependencies(self):
        report = await self.inspect()
        self.assertEqual(report["selection"], "all")
        self.assertEqual(verify.overall(report["checks"]), ("fail", 1))
        for name in ("decoder_features", "live_features", "equalizer_features", "tool:mpv", "tool:sony-tracker", "sony_tracker"):
            self.assertEqual(report["checks"][name]["status"], "fail")

    async def test_core_verification_keeps_daemon_and_graph_failures(self):
        for argument, check in (("daemon_error", "daemon"), ("graph_error", "pipewire_graph")):
            with self.subTest(check=check):
                report = await self.inspect(core_only=True, **{argument: RuntimeError("unavailable")})
                self.assertEqual(report["checks"][check]["status"], "fail")
                self.assertEqual(verify.overall(report["checks"]), ("fail", 1))


class RealGraphAuditIntegrationTests(unittest.TestCase):
    @staticmethod
    def fixture():
        address, name = "AA:BB:CC:DD:EE:FF", "bluez_output.AA_BB_CC_DD_EE_FF.1"
        def obj(kind, number, **props):
            return {"type": "PipeWire:Interface:" + kind, "id": number, "info": {"state": "running", "props": props}}
        def link(number, source, destination, port, input_port=None):
            return {"type": "PipeWire:Interface:Link", "id": number, "info": {
                "output-node-id": source, "input-node-id": destination,
                "output-port-id": port, "input-port-id": input_port, "state": "active"}}
        state = {"schema": 1, "connected": True, "device": address, "target": name,
                 "runtime": {"audio": {"running": True, "process_id": 777, "pcm_route_allowed": True}}}
        graph = [obj("Node", 5, **{"media.class": "Audio/Sink", "node.name": name, "object.serial": "75",
                                  "api.bluez5.address": address, "api.bluez5.profile": "a2dp"}),
                 obj("Node", 10, **{"media.class": "Stream/Output/Audio", "application.name": "Spatial Audio",
                                   "application.process.id": 777, "object.serial": "100", "target.object": name,
                                   "node.dont-fallback": True, "node.dont-reconnect": False}),
                 obj("Port", 101, **{"node.id": 10, "port.direction": "out", "audio.channel": "FL"}),
                 obj("Port", 102, **{"node.id": 10, "port.direction": "out", "audio.channel": "FR"}),
                 obj("Port", 51, **{"node.id": 5, "port.direction": "in", "audio.channel": "FL"}),
                 obj("Port", 52, **{"node.id": 5, "port.direction": "in", "audio.channel": "FR"}),
                 link(401, 10, 5, 101, 51), link(402, 10, 5, 102, 52)]
        return state, graph, obj, link

    def test_native_pcm_policy_is_observed_without_attribute_error(self):
        state, graph, _, _ = self.fixture()
        self.assertEqual(verify.audit_playback_graph(state, graph)[0], "pass")
        state["runtime"]["audio"]["pcm_route_allowed"] = False
        self.assertEqual(verify.audit_playback_graph(state, graph)[0], "fail")

    def test_actual_live_and_equalizer_chains_are_audited_before_allowing_them(self):
        from spatial.live_audio import GUARD_KEY, LIVE_CHANNELS
        state, graph, obj, link = self.fixture()
        name, group = state["target"], "spatial-eq-" + "a" * 32
        graph[-2]["info"]["input-node-id"] = 20
        graph[-1]["info"]["input-node-id"] = 20
        graph[-2]["info"]["input-port-id"] = 201
        graph[-1]["info"]["input-port-id"] = 202
        graph[2]["info"]["props"]["audio.channel"] = "FL"
        graph[3]["info"]["props"]["audio.channel"] = "FR"
        def pcm_format(channels):
            return {"mediaType": "audio", "mediaSubtype": "raw", "format": "F32LE",
                    "rate": 48000, "channels": len(channels), "position": list(channels)}
        graph[1]["info"]["params"] = {"Format": [pcm_format(("FL", "FR"))]}
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
        next(o for o in graph if o["id"] == 20)["info"]["params"] = {
            "Format": [pcm_format(LIVE_CHANNELS)]}
        graph.extend(obj("Port", 201 + index, **{"node.id": 20, "port.direction": "in",
                                                  "audio.channel": channel})
                     for index, channel in enumerate(LIVE_CHANNELS))
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
