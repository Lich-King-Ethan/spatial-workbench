#!/usr/bin/env python3
"""Real Atmos playback through AudioRuntime, mpv, renderer, EQ and sink monitor.

Called by audio-stack-smoke.py after it starts a private PipeWire session and
validates the full PCM path. The original source and LiveAudio must be stopped;
the synthetic headphone sink, production EQ and PCMRecorder remain active.
"""
from __future__ import annotations

import array
import asyncio
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import time


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def helper(name):
    path = Path(__file__).with_name(name)
    key = "spatial_ci_" + path.stem.replace("-", "_")
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def props(obj):
    return (obj.get("info") or {}).get("props") or {}


def owned_outputs(objects, pid):
    clients = {str(obj["id"]) for obj in objects
               if obj.get("type") == "PipeWire:Interface:Client"
               and str(props(obj).get("application.process.id")) == str(pid)}
    return [obj for obj in objects if obj.get("type") == "PipeWire:Interface:Node"
            and props(obj).get("media.class") == "Stream/Output/Audio"
            and (str(props(obj).get("application.process.id")) == str(pid)
                 or str(props(obj).get("client.id")) in clients)]


def stereo_linked(objects, source, destination):
    links = [obj["info"] for obj in objects
             if obj.get("type") == "PipeWire:Interface:Link"
             and str((obj.get("info") or {}).get("output-node-id")) == str(source)]
    return (links and all(str(link.get("input-node-id")) == str(destination) for link in links)
            and all(link.get("state") in ("active", "paused") for link in links)
            and len({link["output-port-id"] for link in links}) == 2
            and len({link["input-port-id"] for link in links}) == 2)


def pcm_metrics(path, period_frames):
    raw = path.read_bytes()
    require(len(raw) % 8 == 0, "Recorded media does not contain complete stereo float32 frames")
    samples = array.array("f")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    # A whole number of repeated fixture periods makes energy independent of
    # recorder/decoder start phase; the fixture is 47 complete E-AC-3 frames.
    frames = len(samples) // 2
    complete = frames // period_frames
    require(complete >= 1, "Recorded media window is shorter than one complete fixture period")
    samples = samples[:complete * period_frames * 2]
    require(all(math.isfinite(value) for value in samples), "Non-finite post-EQ audio")
    rms = [math.sqrt(sum(value*value for value in samples[channel::2])
                    / (len(samples) // 2)) for channel in (0, 1)]
    require(min(rms) > 1e-7, "The actual Atmos-to-headphones route is silent")
    peak = max(map(abs, samples))
    require(peak < 0.999, "Clipped post-EQ media cannot establish spatial response")
    return {"frames_analyzed": len(samples) // 2, "channel_rms": rms,
            "left_minus_right_db": 20 * math.log10(rms[0] / rms[1]),
            "peak": peak, "capture_sha256": hashlib.sha256(raw).hexdigest()}


async def run(gate, sink, bridge, recorder):
    from spatial.audio_runtime import AudioRuntime, renderer_pose

    fixture = helper("decoder-smoke.py")
    pose_fixture = helper("pose-fixtures.py")
    report = {"status": "failed", "hardware_validated": False,
              "fixture_url": fixture.FIXTURE_URL, "fixture_sha256": fixture.FIXTURE_SHA256,
              "input": "real E-AC-3 JOC Atmos, repeated upstream channel-check fixture",
              "output": "post-EQ synthetic earbud sink monitor, stereo float32, 48 kHz",
              "pose_transport": "Sony helper UDP → production adapter/Engine → AudioRuntime → OSC",
              "windows": {}}
    player = AudioRuntime(bridge_path=bridge)
    eq = gate.equalizer
    media_pid = None
    try:
        sample = await asyncio.to_thread(fixture.fetch_fixture)
        with tempfile.TemporaryDirectory(prefix="spatial-ci-media-") as tmp:
            media = Path(tmp) / "repeated-channel-check.eac3"
            # Concatenate only complete elementary-stream frames. This extends
            # playback without re-encoding or replacing any object metadata.
            media.write_bytes(sample * 32)
            await player.start(media, sink, require_spatial=True)
            media_pid = player.process.pid
            report["player"] = player.status()
            require(report["player"]["decoder"] == "orender"
                    and report["player"]["object_count"] > 0
                    and report["player"]["renderer_ready"],
                    "mpv did not verify real decoded objects through the embedded renderer")

            def full_graph(objects):
                status = player.status()
                require(status["running"] and not status["error"],
                        f"Atmos player failed: {status}")
                inputs = eq.verified_input_ids(objects)
                audit = player.verify_output(objects, allowed_filter_inputs=inputs,
                                              pending_filter_inputs=eq.pending_input_ids(objects))
                require(audit["state"] != "violation", f"Unsafe Atmos output graph: {audit}")
                outputs = owned_outputs(objects, media_pid)
                group = eq.status()["link_group"]
                eq_out = [obj for obj in objects
                          if obj.get("type") == "PipeWire:Interface:Node"
                          and props(obj).get("node.name") == group + ".output"]
                target = [obj for obj in objects
                          if obj.get("type") == "PipeWire:Interface:Node"
                          and props(obj).get("node.name") == sink.name
                          and str(props(obj).get("object.serial")) == sink.serial]
                return (audit["state"] == "verified" and len(inputs) == 1
                        and len(outputs) == len(eq_out) == len(target) == 1
                        and stereo_linked(objects, outputs[0]["id"], next(iter(inputs)))
                        and stereo_linked(objects, eq_out[0]["id"], target[0]["id"]))

            objects = await gate.until("actual-atmos-mpv-eq-earbud-chain", full_graph, timeout=20)
            output = owned_outputs(objects, media_pid)[0]
            eq_input_id = next(iter(eq.verified_input_ids(objects)))
            eq_input = next(obj for obj in objects if str(obj["id"]) == str(eq_input_id))
            source_serial = str(props(output)["object.serial"])
            input_serial = str(props(eq_input)["object.serial"])
            require(not any(link["source"] == source_serial and link["target"] != input_serial
                            for link in gate.watch.links),
                    "The media stream acquired an unexpected link before graph verification")
            gate.watch.arm(source_serial, input_serial)
            async with pose_fixture.SonyPoseFixtures() as tracker:
                for name in ("pose_neutral", "pose_neutral_repeat", "pose_yaw_plus90", "pose_yaw_minus90"):
                    pose = await tracker.pose(name)
                    player.set_pose(pose)
                    expected = renderer_pose(pose)
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        state = player.telemetry.renderer.get("binaural", {}).get("headPose", {})
                        if all(key in state for key in ("w", "x", "y", "z")):
                            values = [state[key] for key in ("w", "x", "y", "z")]
                            if min(max(abs(a-sign*b) for a,b in zip(values,expected))
                                   for sign in (-1,1)) < 1e-5:
                                break
                        # Explicit registration requests a fresh real state
                        # snapshot; static pose controls do not emit head_pose.
                        player.telemetry.send("/omniphony/register")
                        await gate.observe(0.1)
                    else:
                        raise RuntimeError("mpv renderer did not acknowledge production head pose")
                    await gate.observe(0.6)
                    require(full_graph(await gate.snapshot()), "Media graph changed before capture")
                    path = await recorder.window("atmos-" + name, seconds=3.25)
                    require(full_graph(await gate.snapshot()), "Media graph changed during capture")
                    report["windows"][name] = pcm_metrics(path, period_frames=72192)
                    report["windows"][name]["path"] = str(path.relative_to(gate.report_dir))
                    report["windows"][name]["renderer_pose"] = list(expected)
                report["sony_pose_evidence"] = tracker.records
            windows = report["windows"]
            neutral = windows["pose_neutral"]["left_minus_right_db"]
            repeat = windows["pose_neutral_repeat"]["left_minus_right_db"]
            turned = windows["pose_yaw_plus90"]["left_minus_right_db"]
            negative = windows["pose_yaw_minus90"]["left_minus_right_db"]
            repeat_error = abs(neutral - repeat)
            report["rotation"] = {"repeat_ild_error_db": repeat_error,
                                  "turned_ild_delta_db": abs(neutral - turned),
                                  "opposite_turns_ild_delta_db": abs(negative - turned)}
            require(repeat_error < 0.5, "Repeated neutral media capture is not stable")
            # This fixture begins with the front-left channel check. Positive
            # head yaw puts it on the right; measured SAF output must swap ears.
            require(neutral > 0.5 and turned < -0.5
                    and abs(neutral - turned) > max(2.0, 5 * repeat_error),
                    "Actual decoded Atmos PCM did not move between the simulated ears")
            require(negative > 0.5 and negative - turned > 2.0,
                    "The opposite head turn did not restore the expected ear-energy polarity")
            report["status"] = "passed"
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        gate.watch.protected = None
        try:
            await player.stop("media smoke complete")
        finally:
            (gate.report_dir / "media-spatial-smoke.json").write_text(json.dumps(report, indent=2) + "\n")
    if media_pid is not None:
        await gate.until("actual-atmos-player-cleaned-up",
                         lambda objects: not owned_outputs(objects, media_pid))
    return report
