"""Real policy/transaction tests using explicit PipeWire graph fixtures.

These are not evidence of headphone or desktop PipeWire validation.
"""
import asyncio
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from spatial.core import Sink
from spatial.audio_runtime import AudioError
from spatial.live_audio import (GUARD_KEY, LiveAudio, LiveTelemetry, Route,
                                _metadata, audit_snapshot, available_streams, live_config)


SINK = Sink("AA:BB:CC:DD:EE:FF", "bluez_output.AA_BB_CC_DD_EE_FF.1", "75", "a2dp", "idle")


def obj(kind, number, **props):
    return {"type": "PipeWire:Interface:" + kind, "id": number, "info": {"props": props}}


def metadata(*entries):
    result = obj("Metadata", 2, **{"metadata.name": "default"})
    result["metadata"] = [{"subject": 0, "key": "spatiald.live-guard", "type": "Spa:String", "value": "1"}]
    result["metadata"].extend(entries)
    return result


def entry(key, value, subject=10, type="Spa:String"):
    return {"subject": subject, "key": key, "type": type, "value": value}


def link(number, source, target, port, state="active"):
    return {"id": number, "type": "PipeWire:Interface:Link", "info": {
        "output-node-id": source, "input-node-id": target,
        "output-port-id": port, "state": state}}


def graph():
    return [
        obj("Client", 3, **{"application.process.id": 999}),
        obj("Node", 5, **{"media.class": "Audio/Sink", "node.name": SINK.name, "object.serial": "75"}),
        obj("Node", 10, **{"media.class": "Stream/Output/Audio", "application.name": "Actual game", "object.serial": "100"}),
        obj("Node", 20, **{"media.class": "Audio/Sink", "node.name": "spatial-live-test", "object.serial": "200", "client.id": 3}),
        obj("Node", 30, **{"media.class": "Stream/Output/Audio", "node.name": "omniphony", "object.serial": "300", "client.id": 3,
                          "target.object": SINK.name, "node.dont-fallback": True, "node.dont-move": True}),
        obj("Port", 101, **{"node.id": 10, "port.direction": "out"}),
        obj("Port", 102, **{"node.id": 10, "port.direction": "out"}),
        obj("Port", 301, **{"node.id": 30, "port.direction": "out"}),
        obj("Port", 302, **{"node.id": 30, "port.direction": "out"}),
        metadata(entry("target.object", "200", type="Spa:Id"), entry(GUARD_KEY, "100:200")),
        link(401, 10, 20, 101), link(402, 10, 20, 102),
        link(403, 30, 5, 301), link(404, 30, 5, 302),
    ]


def ready():
    value = LiveTelemetry("spatial-live-test")
    value.accept("/omniphony/state/capabilities", [json.dumps({
        "producer": "renderer", "variant": "standalone", "host": "cli"})])
    value.accept("/omniphony/state/renderer", [json.dumps({"binaural": {"outputMode": "binaural"}})])
    value.accept("/omniphony/state/input", [json.dumps({"activeMode": "pipewire"})])
    value.accept("/omniphony/state/input", [json.dumps({"applied": {
        "node": "spatial-live-test", "streamFormat": "pipewire-f32", "channels": 8}})])
    return value


def running(**kwargs):
    live = LiveAudio(**kwargs)
    live.process = SimpleNamespace(pid=999, returncode=None)
    live._sink = SINK
    live._input_node, live._input_serial = "spatial-live-test", "200"
    live._stream_serial, live._stream_name = "100", "Actual game"
    live._state = "playing"
    live._route = Route("100", "10", None, "200")
    live._route.applied = live._route.guarded = True
    live._before_muted = False
    live.telemetry = ready()
    return live


class LivePureTests(unittest.TestCase):
    def test_actual_movable_stream_names_and_stable_serials(self):
        objects = graph()
        self.assertEqual(available_streams(objects), [{"serial": "100", "name": "Actual game"}])
        objects[2]["info"]["props"]["node.dont-move"] = True
        self.assertEqual(available_streams(objects), [])

    def test_fixed_pcm_layout_and_no_unimplemented_command(self):
        config = live_config("/real/bridge.so", "selected input", "/private/a.fifo")
        self.assertIn("input_mode: live", config)
        self.assertIn("channels: 8", config)
        self.assertIn("map: seven-one-fixed", config)
        self.assertIn("output_mode: binaural", config)
        self.assertNotIn("input-live", config)

    def test_readiness_requires_actual_input_config_and_freshness(self):
        telemetry = ready()
        self.assertTrue(telemetry.ready)
        telemetry.expected_config = "/private/renderer.yaml"
        self.assertFalse(telemetry.ready)
        telemetry.config_path, telemetry.config_status = telemetry.expected_config, "loaded"
        self.assertTrue(telemetry.ready)
        telemetry.input["applied"]["node"] = "some other input"
        self.assertFalse(telemetry.ready)
        telemetry.input["applied"]["node"] = telemetry.input_node
        telemetry.last_seen = time.monotonic() - 5
        self.assertFalse(telemetry.ready)

    def test_entire_owned_channel_graph_and_guard_are_required(self):
        live, objects = running(), graph()
        self.assertEqual(live.audit(objects)["state"], "ready")
        self.assertEqual(live.verified_input_ids(objects), {"20"})
        objects.pop()
        self.assertEqual(live.audit(objects)["state"], "waiting")
        self.assertEqual(live.pending_input_ids(objects), {"20"})
        self.assertEqual(live.verified_input_ids(objects), set())
        objects[9]["metadata"] = []
        self.assertEqual(live.audit(objects)["state"], "violation")
        self.assertEqual(live.pending_input_ids(objects), set())

    def test_unsafe_edges_sink_replacement_and_missing_guards_reject(self):
        for mutate in (
            lambda g: g.append(link(999, 30, 99, 301)),
            lambda g: g.append(link(999, 88, 20, 880)),
            lambda g: g.append(link(999, 10, 99, 101)),
            lambda g: g[1]["info"]["props"].update({"object.serial": "76"}),
            lambda g: g[4]["info"]["props"].pop("node.dont-fallback"),
            lambda g: g[-1]["info"].update({"state": "error"}),
            lambda g: g[3]["info"]["props"].update({"object.serial": "201"}),
        ):
            objects = graph()
            mutate(objects)
            self.assertEqual(running().audit(objects)["state"], "violation")

    def test_initial_original_route_may_settle_but_cannot_claim_ready(self):
        live, objects = running(), graph()
        live._state = "starting"
        objects.append(link(999, 10, 5, 101))
        self.assertEqual(live.audit(objects)["state"], "waiting")
        live._state = "playing"
        self.assertEqual(live.audit(objects)["state"], "violation")

    def test_pending_eq_is_waiting_until_eq_proves_physical_destination(self):
        objects = graph()
        objects[-1]["info"]["input-node-id"] = 60
        objects[-2]["info"]["input-node-id"] = 60
        live = running(pending_filter_inputs=lambda _: {"60"})
        self.assertEqual(live.audit(objects)["state"], "waiting")
        self.assertEqual(live.verified_input_ids(objects), set())
        live._authorized_filter_inputs = lambda _: {"60"}
        self.assertEqual(live.audit(objects)["state"], "ready")

    def test_external_diagnostics_reuse_graph_audit_and_reject_stale_session(self):
        live, objects = running(), graph()
        live.audit(objects)
        status = live.status()
        result = audit_snapshot(objects, status, SINK)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["verified_inputs"], {"20"})
        objects[-1]["info"]["input-node-id"] = 99
        result = audit_snapshot(objects, status, SINK)
        self.assertEqual(result["state"], "violation")
        self.assertEqual(result["pending_inputs"], set())
        status["input_serial"] = "replaced"
        self.assertEqual(audit_snapshot(graph(), status, SINK)["state"], "violation")


class LiveAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_transaction_guard_precedes_move_and_restore_precedes_guard_removal(self):
        objects = graph()
        objects[9] = metadata(entry("target.object", "old-device", type="Spa:String"))
        route = Route("100", "10", {"type": "Spa:String", "value": "old-device"}, "200")
        with patch("spatial.live_audio._set_target", new_callable=AsyncMock) as change:
            await route.apply(objects)
            self.assertEqual(change.await_args_list[0].args, ("10", {"type": "Spa:String", "value": "100:200"}, GUARD_KEY))
            self.assertEqual(change.await_args_list[1].args, ("10", {"type": "Spa:Id", "value": "200"}))
            await route.restore(graph())
            self.assertEqual(change.await_args_list[2].args, ("10", route.before))
            self.assertEqual(change.await_args_list[3].args, ("10", None, GUARD_KEY))

    async def test_transaction_preserves_new_user_route_and_reused_node_id(self):
        route = Route("100", "10", None, "200")
        route.applied = route.guarded = True
        objects = graph()
        objects[9]["metadata"][1]["value"] = "user-choice"
        with patch("spatial.live_audio._set_target", new_callable=AsyncMock) as change:
            await route.restore(objects)
            change.assert_awaited_once_with("10", None, GUARD_KEY)
            change.reset_mock()
            route.applied = route.guarded = True
            objects[2]["info"]["props"]["object.serial"] = "reused-node"
            await route.restore(objects)
            change.assert_not_awaited()

    async def test_missing_wireplumber_guard_fails_before_any_metadata_write(self):
        objects = graph()
        objects[9] = metadata()
        objects[9]["metadata"] = []
        with patch("spatial.live_audio._set_target", new_callable=AsyncMock) as change:
            with self.assertRaisesRegex(AudioError, "WirePlumber"):
                await Route("100", "10", None, "200").apply(objects)
            change.assert_not_awaited()

    async def test_start_returns_before_probe_and_disconnect_cancels_owned_launch(self):
        live = LiveAudio()
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def slow_probe():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        live.probe = slow_probe
        result = await asyncio.wait_for(live.start(SINK, "100", graph()), .5)
        self.assertEqual(result["state"], "starting")
        await entered.wait()
        await asyncio.wait_for(live.update_sink(None), .5)
        self.assertTrue(cancelled.is_set())
        self.assertIsNone(live.process)
        self.assertEqual(live.status()["state"], "idle")

    async def test_failure_mutes_before_restore_and_termination_then_explicit_stop_restores_mute(self):
        live = running()
        events = []
        async def command(*args, **kwargs):
            events.append(args)
            return "Volume: 1.00 [MUTED]\n" if args[1] == "get-volume" else ""
        async def restore(objects):
            events.append(("restore",))
            live._route.applied = False
        async def kill(process):
            events.append(("kill",))
        live._route.restore = restore
        with patch("spatial.live_audio.pipewire.capture", AsyncMock(return_value=graph())), \
                patch("spatial.live_audio._command", command), patch("spatial.live_audio.stop_child", kill):
            await live.stop("headphones disconnected")
            self.assertLess(events.index(("wpctl", "set-mute", "10", "1")), events.index(("restore",)))
            self.assertLess(events.index(("restore",)), events.index(("kill",)))
            self.assertIsNotNone(live._failure_mute)
            await live.stop()
            self.assertIn(("wpctl", "set-mute", "10", "0"), events)

    async def test_failed_mute_verification_retains_capture_and_route(self):
        live = running()
        with patch("spatial.live_audio.pipewire.capture", AsyncMock(return_value=graph())), \
                patch("spatial.live_audio._command", AsyncMock(return_value="Volume: 1.00\n")), \
                patch("spatial.live_audio.stop_child", new_callable=AsyncMock) as kill, \
                patch.object(live._route, "restore", new_callable=AsyncMock) as restore:
            await live.stop("headphones disconnected")
            self.assertEqual(live.status()["state"], "error")
            self.assertTrue(live._route.applied)
            kill.assert_not_awaited()
            restore.assert_not_awaited()

    async def test_probe_rejects_unpatched_or_wrong_version(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = Path(root) / "bridge.so"
            bridge.touch()
            live = LiveAudio(bridge_path=bridge)
            with patch("spatial.live_audio.shutil.which", return_value="/usr/bin/tool"):
                for version in ("orender 0.5.2\n", "orender 0.5.3+spatial-loopback1\n"):
                    with patch("spatial.live_audio._command", AsyncMock(return_value=version)):
                        with self.assertRaises(AudioError):
                            await live.probe()
                with patch("spatial.live_audio._command", AsyncMock(side_effect=[
                        "orender 0.5.2+spatial-loopback1\n", "--output-device --osc-rx-port --continuous"])):
                    self.assertEqual(await live.probe(), "/usr/bin/tool")


if __name__ == "__main__":
    unittest.main()
