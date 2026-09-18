#!/usr/bin/env python3
"""Decode real E-AC-3 JOC through installed liborender and harletty-bridge.

Uses the production config and coordinate mapping, with private loopback OSC
ports. No audio device, user configuration or desktop session is touched.
The upstream fixture is fetched only on explicit --download-fixture; it is
not redistributed in this repository. A successful run requires decoded
objects, finite non-silent PCM and a measured binaural response to head pose.
"""
from __future__ import annotations

import argparse
import array
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import urllib.request

from spatial.audio_runtime import config_text, renderer_pose
from spatial.osc import decode, encode
from spatial.pose import IDENTITY, Quaternion

FIXTURE_COMMIT = "ff98334f00bef130fa93e2590fcd5bd50961e0d2"
FIXTURE_URL = (
    "https://raw.githubusercontent.com/harletty/harletty-bridge/"
    + FIXTURE_COMMIT + "/harletty/tests/fixtures/joc_atmos_1s.eac3"
)
FIXTURE_SHA256 = "3f4d165fa732a4d3ac8e914572f32b0d3c631f77b935fc714ad15d8d72534280"


def fetch_fixture():
    """Fetch the immutable upstream regression asset, verifying its bytes."""
    with urllib.request.urlopen(FIXTURE_URL, timeout=30) as response:
        sample = response.read(1024 * 1024)
    if hashlib.sha256(sample).hexdigest() != FIXTURE_SHA256:
        raise RuntimeError("upstream fixture checksum mismatch")
    return sample


class Config(C.Structure):
    # Frozen OrenderConfig layout, ABI major 0; see upstream orender.h.
    _fields_ = [
        ("sample_rate", C.c_uint32), ("config_yaml_path", C.c_char_p),
        ("speaker_layout_path", C.c_char_p), ("bridge_path", C.c_char_p),
        ("codec", C.c_char_p), ("osc_enabled", C.c_int),
        ("osc_port_in", C.c_uint16), ("osc_port_out", C.c_uint16),
        ("osc_bind", C.c_char_p), ("osc_host", C.c_char_p),
    ]


def load_library(path):
    lib = C.CDLL(str(path))
    signatures = {
        "orender_version_major": (C.c_uint32, []),
        "orender_version_minor": (C.c_uint32, []),
        "orender_build_id": (C.c_char_p, []),
        "orender_spatial_loopback_supported": (C.c_uint32, []),
        "orender_create": (C.c_void_p, [C.POINTER(Config)]),
        "orender_destroy": (None, [C.c_void_p]),
        "orender_has_objects": (C.c_int, [C.c_void_p]),
        "orender_object_count": (C.c_int, [C.c_void_p]),
        "orender_channel_count": (C.c_uint32, [C.c_void_p]),
        "orender_process": (C.c_int, [
            C.c_void_p, C.c_void_p, C.c_size_t, C.c_int64,
            C.POINTER(C.c_float), C.c_size_t, C.POINTER(C.c_size_t),
            C.POINTER(C.c_uint32), C.POINTER(C.c_int64),
        ]),
    }
    for name, (result, arguments) in signatures.items():
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = result, arguments
    if lib.orender_version_major() != 0:
        raise RuntimeError("unsupported liborender ABI major (expected 0)")
    if lib.orender_spatial_loopback_supported() != 1:
        raise RuntimeError("liborender does not confirm private OSC bind support")
    return lib


def receive(sock, timeout):
    sock.settimeout(timeout)
    try:
        data, _ = sock.recvfrom(65535)
        return decode(data)
    except (socket.timeout, BlockingIOError):
        return []


def set_pose(sock, port, pose):
    """Wait for real renderer telemetry acknowledging the production mapping."""
    expected = renderer_pose(pose)
    packet = encode("/omniphony/control/head/quat", *map(float, expected))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        sock.sendto(packet, ("127.0.0.1", port))
        sock.sendto(encode("/omniphony/register", sock.getsockname()[1]),
                    ("127.0.0.1", port))
        for address, args in receive(sock, 0.1):
            if address == "/omniphony/state/renderer" and len(args) == 1:
                state = json.loads(args[0]).get("binaural", {}).get("headPose", {})
                if all(key in state for key in ("w", "x", "y", "z")):
                    address = "/omniphony/state/head_pose"
                    args = [state[key] for key in ("w", "x", "y", "z")]
            if address == "/omniphony/state/head_pose" and len(args) == 4:
                # q and -q describe the same orientation.
                error = min(max(abs(a - s*b) for a, b in zip(args, expected))
                            for s in (-1, 1))
                if error < 1e-5:
                    return list(args)
    raise RuntimeError("renderer did not acknowledge mapped head pose over OSC")


def render(lib, bridge, sample, directory, name, mode, pose):
    config_path = directory / (name + ".yaml")
    text = config_text(bridge)
    if mode == "speaker":
        text = text.replace("output_mode: binaural", "output_mode: speaker")
    config_path.write_text(text, encoding="utf-8")
    # Bind port 0, then release only the control port for the native listener.
    # The receive port stays held so monitoring cannot be delivered elsewhere.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        cfg = Config(48000, os.fsencode(config_path), None, os.fsencode(bridge),
                     b"eac3", 1, port, sock.getsockname()[1],
                     b"127.0.0.1", b"127.0.0.1")
        renderer = lib.orender_create(C.byref(cfg))
        if not renderer:
            raise RuntimeError("liborender could not create a real bridge session")
        try:
            expected_channels = 12 if mode == "speaker" else 2
            # Upstream commits the configured output mode on the first render;
            # its initial count may still describe the default speaker layout.
            initial_channels = lib.orender_channel_count(renderer)
            acknowledged = set_pose(sock, port, pose)
            pcm = array.array("f")
            # 4096 input bytes represent at most several E-AC-3 frames; this
            # buffer comfortably holds them even at 12 output channels. Never
            # retry an already-consumed packet on the upstream capacity error.
            out = (C.c_float * (48000 * 12))()
            frames, channels, pts = C.c_size_t(), C.c_uint32(), C.c_int64()
            max_objects = 0
            object_packets = 0
            for offset in range(0, len(sample), 4096):
                packet = C.create_string_buffer(sample[offset:offset + 4096])
                status = lib.orender_process(
                    renderer, packet, len(packet) - 1, 0, out, len(out),
                    C.byref(frames), C.byref(channels), C.byref(pts))
                if status != 0:
                    raise RuntimeError(f"orender_process returned {status}")
                if frames.value:
                    if channels.value != expected_channels:
                        raise RuntimeError(f"{mode}: output channel count changed")
                    pcm.extend(out[:frames.value * channels.value])
                objects = lib.orender_object_count(renderer)
                max_objects = max(max_objects, objects)
                if lib.orender_has_objects(renderer) == 1 and objects > 0:
                    object_packets += 1
                # Drain real monitoring output to avoid overflowing its queue.
                while receive(sock, 0):
                    pass
            if not pcm or not all(math.isfinite(value) for value in pcm):
                raise RuntimeError(f"{name}: absent or non-finite decoded PCM")
            peak = max(map(abs, pcm))
            rms = math.sqrt(sum(value*value for value in pcm) / len(pcm))
            channel_rms = [math.sqrt(sum(value*value for value in pcm[channel::expected_channels])
                           / (len(pcm) // expected_channels)) for channel in range(expected_channels)]
            if peak <= 1e-6 or rms <= 1e-7:
                raise RuntimeError(f"{name}: decoder produced silent PCM")
            if not object_packets:
                raise RuntimeError(f"{name}: decoder never reported real objects")
            return pcm, {
                "channels": expected_channels,
                "initial_channels": initial_channels,
                "frames": len(pcm) // expected_channels,
                "peak": peak, "rms": rms, "max_objects": max_objects,
                "channel_rms": channel_rms,
                "object_packets": object_packets,
                "acknowledged_head_quaternion": acknowledged,
                "pcm_sha256": hashlib.sha256(pcm.tobytes()).hexdigest(),
            }
        finally:
            lib.orender_destroy(renderer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--download-fixture", action="store_true")
    source.add_argument("--sample", type=Path,
                        help="local E-AC-3 JOC elementary stream with audible objects")
    parser.add_argument("--library", type=Path, default=Path("/usr/lib/liborender.so"))
    parser.add_argument("--bridge", type=Path,
                        default=Path("/usr/lib/orender/libharletty_bridge.so"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = {"status": "failed", "physical_audio_verified": False}
    try:
        library = args.library.resolve(strict=True)
        bridge = args.bridge.resolve(strict=True)
        if args.download_fixture:
            sample = fetch_fixture()
            source_name = FIXTURE_URL
        else:
            sample = args.sample.read_bytes()
            source_name = str(args.sample.resolve())
        if not sample:
            raise RuntimeError("empty encoded sample")
        report.update({"sample_source": source_name,
                       "sample_sha256": hashlib.sha256(sample).hexdigest(),
                       "library": str(library), "bridge": str(bridge),
                       "library_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
                       "bridge_sha256": hashlib.sha256(bridge.read_bytes()).hexdigest()})
        # This additive downstream switch is required before any listener starts.
        os.environ["OMNIPHONY_OSC_BIND"] = "127.0.0.1"
        lib = load_library(library)
        report["abi"] = [lib.orender_version_major(), lib.orender_version_minor()]
        report["renderer_build"] = lib.orender_build_id().decode()
        with tempfile.TemporaryDirectory(prefix="spatial-decoder-smoke-") as tmp:
            directory = Path(tmp)
            outputs = {}
            for name, mode, pose in [
                ("speaker", "speaker", IDENTITY),
                ("binaural", "binaural", IDENTITY),
                ("binaural_repeat", "binaural", IDENTITY),
                ("binaural_turned", "binaural",
                 Quaternion(math.sqrt(0.5), 0, math.sqrt(0.5), 0)),
            ]:
                outputs[name], report[name] = render(
                    lib, bridge, sample, directory, name, mode, pose)
            base, repeat, turned = (outputs[name] for name in
                                   ("binaural", "binaural_repeat", "binaural_turned"))
            if not len(base) == len(repeat) == len(turned):
                raise RuntimeError("head pose changed output frame count")
            repeat_rms = math.sqrt(sum((a-b)**2 for a, b in zip(base, repeat)) / len(base))
            delta_rms = math.sqrt(sum((a-b)**2 for a, b in zip(base, turned)) / len(base))
            report["rotation"] = {"repeat_delta_rms": repeat_rms,
                                  "turned_delta_rms": delta_rms,
                                  "relative_delta_rms": delta_rms / report["binaural"]["rms"]}
            if delta_rms <= max(1e-6, repeat_rms * 100, report["binaural"]["rms"] * 0.01):
                raise RuntimeError("head rotation did not measurably change decoded binaural PCM")
        report["status"] = "passed"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
