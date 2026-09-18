import asyncio
import json
import math
import os
import socket
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from spatial.audio_runtime import (
    AudioError, AudioRuntime, RendererTelemetry, config_text, media_source,
    playback_argv, renderer_pose, library_supports_loopback,
)
from spatial.core import Sink
from spatial.osc import decode, encode
from spatial.pose import IDENTITY, Quaternion


SINK = Sink("AA:BB:CC:DD:EE:FF", "bluez_output.AA_BB_CC_DD_EE_FF.1", "75", "a2dp", "idle")


def ready_telemetry():
    telemetry = RendererTelemetry()
    telemetry.accept("/omniphony/state/capabilities", [json.dumps(
        {"producer": "renderer", "variant": "embedded", "host": "mpv", "spatial": True})])
    telemetry.accept("/omniphony/state/renderer", [json.dumps(
        {"binaural": {"outputMode": "binaural", "hrirEffective": "saf"}})])
    return telemetry


class AudioPureTests(unittest.TestCase):
    def test_native_sink_and_private_control_config(self):
        argv = playback_argv("/usr/bin/mpv", "/tmp/private/r.yaml", "/tmp/private/m.sock", SINK, 1, 2)
        self.assertIn("--audio-device=pipewire/" + SINK.name, argv)
        self.assertIn("--ao=pipewire", argv)
        self.assertIn("--ad=orender", argv)
        self.assertIn("--no-config", argv)
        self.assertIn("--ad-orender-osc-bind=127.0.0.1", argv)
        self.assertNotIn("wpctl", argv)
        text = config_text('/tmp/library "with quotes".so')
        self.assertIn('bridge_path: "/tmp/library \\"with quotes\\".so"'.replace('\\\\', '\\'), text)
        self.assertIn("output_mode: binaural", text)
        self.assertIn("auto_gain_ceiling_db: -1.0", text)
        self.assertNotIn("master_gain", text)

    def test_safe_source_formats_and_secret_safe_errors(self):
        signed = "https://cdn.example/media.mpd?token=secret"
        self.assertEqual(media_source(signed), signed)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "path; with spaces.mpd"
            path.touch()
            self.assertEqual(media_source(path), str(path))
        for bad in ("av://lavfi:anullsrc?token=secret", "file:///etc/passwd?token=secret",
                    "https://", "/no-such-file-secret", "https://a/\nsecret", "-"):
            with self.subTest(bad=bad), self.assertRaises(AudioError) as error:
                media_source(bad)
            self.assertNotIn("secret", str(error.exception))

    def test_coordinate_rotation_for_each_physical_axis(self):
        v = math.sqrt(0.5)
        # Leftward head rotation around +up requires rightward world rotation.
        for canonical, expected in [((v, 0, v, 0), (v, 0, 0, -v)),
                                    ((v, v, 0, 0), (v, -v, 0, 0)),
                                    ((v, 0, 0, v), (v, 0, v, 0))]:
            actual = renderer_pose(Quaternion.parse(canonical))
            for a, b in zip(actual, expected):
                self.assertAlmostEqual(a, b)

    def test_only_embedded_binaural_handshake_is_ready(self):
        telemetry = ready_telemetry()
        self.assertTrue(telemetry.ready)
        telemetry.capabilities["variant"] = "standalone"
        self.assertFalse(telemetry.ready)
        telemetry.capabilities["variant"] = "embedded"
        telemetry.renderer["binaural"]["outputMode"] = "speaker"
        self.assertFalse(telemetry.ready)
        telemetry.renderer["binaural"]["outputMode"] = "binaural"
        telemetry.last_seen = time.monotonic() - 5
        self.assertFalse(telemetry.ready)

    def test_untrusted_datagrams_and_invalid_snapshots_do_not_create_readiness(self):
        telemetry = RendererTelemetry()
        telemetry.datagram_received(b"malformed", ("127.0.0.1", 5))
        telemetry.datagram_received(encode("/omniphony/state/capabilities", "{}"), ("192.168.1.1", 5))
        telemetry.datagram_received(encode("/omniphony/state/capabilities", "[]"), ("127.0.0.1", 5))
        telemetry.datagram_received(encode("/omniphony/state/renderer", '{"binaural":null}'), ("127.0.0.1", 5))
        self.assertFalse(telemetry.ready)

    def test_nonfinite_json_does_not_escape_to_companion_state(self):
        for address in ("/omniphony/state/renderer", "/omniphony/state/capabilities"):
            for invalid in ('{"value":NaN}', '{"value":Infinity}', '{"value":1e400}'):
                telemetry = RendererTelemetry()
                telemetry.datagram_received(encode(address, invalid), ("127.0.0.1", 7))
                self.assertEqual(telemetry.renderer, {})
                self.assertEqual(telemetry.capabilities, {})

    def test_actual_pipewire_graph_matches_owned_client_sink_serial_and_links(self):
        audio = AudioRuntime()
        audio.process = SimpleNamespace(pid=777, returncode=None)
        audio._sink = SINK
        def obj(kind, number, props):
            return {"type": "PipeWire:Interface:" + kind, "id": number, "info": {"props": props}}
        client = obj("Client", 3, {"application.process.id": 777})
        stream = obj("Node", 4, {"media.class": "Stream/Output/Audio", "client.id": 3,
                   "target.object": SINK.name, "node.dont-fallback": True, "node.dont-reconnect": "true"})
        sink = obj("Node", 5, {"node.name": SINK.name, "object.serial": SINK.serial})
        link = {"type": "PipeWire:Interface:Link", "id": 6,
                "info": {"output-node-id": 4, "input-node-id": 5}}
        self.assertEqual(audio.verify_output([client, stream, sink])["state"], "waiting")
        self.assertEqual(audio.verify_output([client, stream, sink, link])["state"], "verified")
        sink["info"]["props"]["object.serial"] = "new-session"
        self.assertEqual(audio.verify_output([client, stream, sink, link])["state"], "violation")
        sink["info"]["props"]["object.serial"] = SINK.serial
        del stream["info"]["props"]["node.dont-reconnect"]
        self.assertEqual(audio.verify_output([client, stream, sink, link])["state"], "violation")

    def test_known_pending_filter_is_waiting_until_its_own_audit_completes(self):
        audio = AudioRuntime()
        audio.process = SimpleNamespace(pid=777, returncode=None)
        audio._sink = SINK
        objects = [
            {"type": "PipeWire:Interface:Node", "id": 4, "info": {"props": {
                "application.process.id": 777, "media.class": "Stream/Output/Audio",
                "target.object": SINK.name, "node.dont-fallback": True, "node.dont-reconnect": True}}},
            {"type": "PipeWire:Interface:Link", "info": {"output-node-id": 4, "input-node-id": 20}},
        ]
        self.assertEqual(audio.verify_output(objects)["state"], "violation")
        self.assertEqual(audio.verify_output(objects, pending_filter_inputs={"20"})["state"], "waiting")
        self.assertEqual(audio.verify_output(objects, allowed_filter_inputs={"20"})["state"], "verified")
        objects.append({"type": "PipeWire:Interface:Link", "info": {"output-node-id": 4, "input-node-id": 99}})
        self.assertEqual(audio.verify_output(objects, pending_filter_inputs={"20"})["state"], "violation")

    def test_renderer_capability_and_container_hint_are_not_object_evidence(self):
        audio = AudioRuntime()
        audio.process = SimpleNamespace(returncode=None)
        audio._file_loaded = True
        audio.telemetry = ready_telemetry()
        selected = {"type": "audio", "selected": True, "decoder": "orender",
                    "codec-profile": "Dolby TrueHD + Dolby Atmos"}
        audio._properties["track-list"] = [selected]
        self.assertTrue(audio.status()["renderer_ready"])
        self.assertEqual(audio.status()["source_mode"], "stereo")
        selected["codec-profile"] = "Dolby TrueHD + Dolby Atmos · LFE+11 objects · DialNorm -27 dB"
        self.assertEqual(audio.status()["source_mode"], "spatial")
        self.assertEqual(audio.status()["object_count"], 11)
        selected["decoder"] = "truehd"
        self.assertFalse(audio.status()["renderer_ready"])
        self.assertEqual(audio.status()["source_mode"], "stereo")
        self.assertEqual(audio.status()["object_count"], 0)

    def test_tracking_off_sends_identity_and_recenter_uses_current_pose(self):
        audio = AudioRuntime()
        audio.telemetry = ready_telemetry()
        audio.telemetry.send = Mock()
        audio.set_pose(Quaternion.parse((1, 0, 1, 0)))
        audio.recenter()
        self.assertEqual(audio.telemetry.send.call_args.args, ("/omniphony/control/head/quat", 1., -0., 0., -0.))
        audio.set_pose(None)
        self.assertEqual(audio.telemetry.send.call_args.args, ("/omniphony/control/head/quat", 1., -0., 0., -0.))


class AudioAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_owned_player_environment_pins_serial_and_changes_only_explicit_pcm_mode(self):
        with tempfile.TemporaryDirectory() as root:
            media = Path(root) / "track.wav"
            media.touch()
            for allow_pcm in (False, True):
                audio = AudioRuntime(runtime_dir=root, allow_pcm_route=allow_pcm)
                audio.probe = AsyncMock(return_value={"available": True, "binary": "/usr/bin/mpv"})
                exited = SimpleNamespace(returncode=0, pid=77)
                with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=exited)) as spawn:
                    with self.assertRaises(AudioError):
                        await audio.start(media, SINK)
                environment = spawn.call_args.kwargs["env"]
                props = json.loads(environment["PIPEWIRE_PROPS"])
                self.assertEqual(props["target.object"], SINK.serial)
                self.assertTrue(props["node.dont-fallback"])
                self.assertEqual(props["node.dont-reconnect"], not allow_pcm)
                self.assertEqual(props["node.dont-move"], not allow_pcm)
                self.assertEqual(environment["OMNIPHONY_OSC_BIND"], "127.0.0.1")
                self.assertNotIn(str(media), spawn.call_args.args)

    @unittest.skipUnless(shutil.which("cc"), "C compiler needed for real shared-library marker check")
    async def test_exact_library_marker_is_checked_in_child_process(self):
        with tempfile.TemporaryDirectory() as root:
            source, library = Path(root) / "marker.c", Path(root) / "marker.so"
            for code, expected in [
                ("unsigned int unrelated(void) { return 1; }", False),
                ("unsigned int orender_spatial_loopback_supported(void) { return 0; }", False),
                ("unsigned int orender_spatial_loopback_supported(void) { return 1; }", True),
            ]:
                source.write_text(code)
                subprocess.run(["cc", "-shared", "-fPIC", str(source), "-o", str(library)],
                               check=True, capture_output=True)
                self.assertEqual(await library_supports_loopback(library), expected)

    async def test_stock_mpv_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            binary = Path(root) / "mpv"
            bridge = Path(root) / "bridge.so"
            bridge.touch()
            binary.write_text("#!/bin/sh\nprintf 'lavc: truehd\\n'\n")
            binary.chmod(0o700)
            result = await AudioRuntime(binary=binary, bridge_path=bridge).probe()
            self.assertFalse(result["available"])
            self.assertIn("lacks", result["reason"])

    async def test_disconnected_or_replaced_sink_stops_only_owned_player(self):
        audio = AudioRuntime()
        audio.process = SimpleNamespace(returncode=None)
        audio._sink = SINK
        audio.stop = AsyncMock()
        await audio.update_sink(SINK)
        audio.stop.assert_not_awaited()
        replacement = Sink(SINK.device, SINK.name, "80", "a2dp", "idle")
        await audio.update_sink(replacement)
        audio.stop.assert_awaited_once_with("headphones disconnected")
        audio.stop.reset_mock()
        await audio.update_sink(None)
        audio.stop.assert_awaited_once()

    async def test_playback_commands_validate_values(self):
        audio = AudioRuntime()
        audio.command = AsyncMock()
        await audio.seek(12)
        await audio.set_volume(77)
        await audio.pause(True)
        self.assertEqual(audio.command.await_args_list[0].args, ("seek", 12, "relative"))
        for value in (float("nan"), float("inf"), "1", True):
            with self.assertRaises(ValueError):
                await audio.seek(value)
        for value in (-1, 101, float("nan")):
            with self.assertRaises(ValueError):
                await audio.set_volume(value)

    async def test_required_spatial_never_accepts_native_fallback_or_metadata_hint(self):
        audio = AudioRuntime()
        audio.process = SimpleNamespace(returncode=None)
        audio._file_loaded = True
        audio.telemetry = ready_telemetry()
        track = {"type": "audio", "selected": True, "decoder": "truehd",
                 "codec-profile": "Dolby TrueHD + Dolby Atmos · LFE+11 objects"}
        audio._properties["track-list"] = [track]
        with self.assertRaises(AudioError):
            await audio._require_spatial(timeout=0.01)
        track["decoder"] = "orender"
        await audio._require_spatial(timeout=0.01)

    async def test_ipc_correlates_requests_and_clears_end_of_file_readiness(self):
        audio = AudioRuntime()
        audio.reader = asyncio.StreamReader()
        future = asyncio.get_running_loop().create_future()
        audio._pending[17] = future
        audio._file_loaded = True
        audio.telemetry = ready_telemetry()
        audio.reader.feed_data(b'not json\n{"request_id":17,"error":"success","data":44}\n'
                               b'{"event":"end-file","reason":"error","file_error":"signed-secret"}\n')
        audio.reader.feed_eof()
        await audio._read_ipc()
        self.assertEqual(future.result()["data"], 44)
        self.assertFalse(audio._file_loaded)
        self.assertEqual(audio.status()["end_reason"], "error")
        self.assertFalse(audio.telemetry.ready)
        self.assertNotIn("signed-secret", audio.status()["error"])

    async def test_live_loopback_osc_transport(self):
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.close()
        except PermissionError:
            self.skipTest("this execution environment prohibits socket creation")
        loop = asyncio.get_running_loop()
        telemetry = RendererTelemetry()
        transport, _ = await loop.create_datagram_endpoint(lambda: telemetry, local_addr=("127.0.0.1", 0))
        sender, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, remote_addr=transport.get_extra_info("sockname"))
        try:
            sender.sendto(encode("/omniphony/state/capabilities", json.dumps(
                {"producer": "renderer", "variant": "embedded", "host": "mpv"})))
            sender.sendto(encode("/omniphony/state/renderer", '{"binaural":{"outputMode":"binaural"}}'))
            for _ in range(20):
                if telemetry.ready:
                    break
                await asyncio.sleep(.01)
            self.assertTrue(telemetry.ready)
        finally:
            sender.close()
            transport.close()
