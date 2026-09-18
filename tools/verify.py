#!/usr/bin/env python3
"""Verify this computer's real Spatial Audio installation and optional playback."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import uuid

_source_root = Path(__file__).resolve().parents[1]
if (_source_root / "spatial").is_dir():
    sys.path.insert(0, str(_source_root))
from spatial.diagnostics_privacy import Redactor


def add(report, name, status, detail):
    report["checks"][name] = {"status": status, "detail": str(detail)}


def overall(checks):
    states = {value["status"] for value in checks.values()}
    return ("fail", 1) if "fail" in states else ("waiting", 2) if "wait" in states else ("pass", 0)


def assess_state(report, state, settings):
    for name in ("playback_error", "renderer_error", "loaded_audio", "clipping"):
        report["checks"].pop(name, None)
    runtime = state.get("runtime") or {}
    audio = runtime.get("audio") or {}
    connected = state.get("connected") is True
    add(report, "headphones", "pass" if connected else "wait",
        "Selected physical headphones connected" if connected else "Connect the WF-1000XM5 through KDE")
    add(report, "headphone_output", "pass" if state.get("audio_ready") else "wait",
        "Real A2DP output ready" if state.get("audio_ready") else "Waiting for the selected headphones' usable A2DP output")
    add(report, "budslink", "pass" if state.get("controls_ready") else "wait",
        "Companion device matched" if state.get("controls_ready") else "BudsLink controls are not ready; ordinary audio remains independent")
    for module, enabled, rows in (
        ("sony_tracker", settings.sony_enabled, [state["earbud"]] if state.get("earbud") else []),
        ("optional_tracker", settings.slime_enabled, state.get("optional_trackers") or []),
    ):
        provider = (runtime.get("providers") or {}).get(module, {})
        if not enabled:
            add(report, module, "off", "Module disabled in configuration")
        elif rows:
            permitted = any(row.get("enabled") for row in rows)
            add(report, module, "pass" if permitted else "off",
                "Fresh telemetry received and permitted" if permitted else "Fresh telemetry received; audio use switched off")
        elif provider.get("state") in ("error", "unavailable"):
            add(report, module, "fail", provider.get("detail") or "Provider unavailable")
        else:
            add(report, module, "wait", provider.get("detail") or "No fresh physical tracker telemetry; connect a tracker to verify it")
    for module, enabled in (("equalizer", getattr(settings, "equalizer_enabled", False)),
                            ("live", getattr(settings, "live_enabled", False))):
        current = runtime.get(module) or {}
        if not enabled:
            add(report, module, "off", "Module disabled in configuration")
        elif current.get("error") or current.get("state") in ("error", "unavailable", "failed"):
            add(report, module, "fail", current.get("error") or current.get("reason") or "Module unavailable")
        elif current.get("state") in ("active", "running", "ready", "playing"):
            add(report, module, "pass", current.get("reason") or "Module reports a verified active session")
        else:
            add(report, module, "wait", current.get("reason") or "Module idle; actual processing has not been exercised")
    if runtime.get("playback_error"):
        add(report, "playback_error", "fail", runtime["playback_error"])
    if audio.get("error"):
        add(report, "renderer_error", "fail", audio["error"])
    if audio.get("running"):
        add(report, "loaded_audio", "pass" if audio.get("loaded") else "wait",
            "Player loaded real media" if audio.get("loaded") else "Player is still loading")
        add(report, "renderer_abi", "pass" if audio.get("renderer_ready") else "wait",
            "Actual decoder, engine capabilities and binaural configuration verified" if audio.get("renderer_ready")
            else "No live binaural renderer confirmation; an idle capability probe cannot establish ABI compatibility")
        if audio.get("clipping"):
            add(report, "clipping", "fail", "Renderer reports clipping")
        elif audio.get("clipping") is False:
            add(report, "clipping", "pass", "Live renderer telemetry reports no clipping")
    else:
        add(report, "renderer_abi", "wait", "No playback is active; use --media with a known spatial sample to exercise the decoder and engine")


def audit_playback_graph(state, objects):
    """Apply the runtime's actual routing audits to a fresh, independent graph."""
    from spatial.audio_runtime import AudioRuntime
    from spatial import pipewire
    runtime = state.get("runtime") or {}
    audio = runtime.get("audio") or {}
    if not state.get("connected") or not state.get("device"):
        return "wait", "Headphones are not connected"
    sinks = [sink for sink in pipewire.parse_sinks(objects, state["device"])
             if sink.usable and sink.name == state.get("target")]
    if len(sinks) != 1:
        return "wait", "The physical output session is not uniquely present in the current graph"
    sink = sinks[0]
    allowed, pending = [], []
    equalizer = runtime.get("equalizer") or {}
    if equalizer.get("enabled") and equalizer.get("process_id") and equalizer.get("link_group"):
        from spatial.equalizer import audit_graph
        result = audit_graph(objects, pid=equalizer["process_id"], group=equalizer["link_group"], sink=sink)
        if result["state"] == "violation":
            return "fail", result["reason"]
        allowed = result.get("input_node_ids") or []
        pending = result.get("pending_input_node_ids") or []
    live = runtime.get("live") or {}
    live_audit = None
    if live.get("running"):
        from spatial.live_audio import audit_snapshot
        live_audit = audit_snapshot(objects, live, sink, allowed_filter_inputs=allowed,
                                    pending_filter_inputs=pending)
        if live_audit["state"] == "violation":
            return "fail", live_audit["reason"]
        allowed = [*allowed, *(live_audit.get("verified_inputs") or ())]
        pending = [*pending, *(live_audit.get("pending_inputs") or ())]
    pending = set(map(str, pending)) - set(map(str, allowed))
    if not audio.get("running") or not audio.get("process_id"):
        if live_audit is not None:
            return ("pass" if live_audit["state"] == "ready" else "wait"), live_audit["reason"]
        return "wait", "Physical headphones found; no owned playback stream is active"
    # This object invokes the read-only public audit; it never starts a process,
    # changes a link, or tells a player to move outputs.
    observer = SimpleNamespace(process=SimpleNamespace(pid=audio["process_id"]), _sink=sink,
                               allow_pcm_route=audio.get("pcm_route_allowed") is True)
    result = AudioRuntime.verify_output(observer, objects, allowed_filter_inputs=allowed,
                                        pending_filter_inputs=pending)
    return {"verified": "pass", "violation": "fail"}.get(result["state"], "wait"), result["reason"]


async def connect():
    from dbus_next.aio import MessageBus
    from spatial.desktop import BUS, PATH
    bus = await asyncio.wait_for(MessageBus().connect(), 5)
    try:
        node = await asyncio.wait_for(bus.introspect(BUS, PATH), 5)
        proxy = bus.get_proxy_object(BUS, PATH, node)
        return bus, proxy.get_interface(BUS)
    except BaseException:
        bus.disconnect()
        raise


async def snapshot(interface):
    value = json.loads(await asyncio.wait_for(interface.get_state(), 5))
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError("Unsupported daemon state schema")
    return value


async def capture_graph(report, state):
    from spatial import pipewire
    try:
        objects = await pipewire.capture()
        if not isinstance(objects, list):
            raise ValueError("pw-dump did not return an object list")
        add(report, "pipewire_graph", "pass", f"Read {len(objects)} actual PipeWire objects")
        status, detail = audit_playback_graph(state, objects)
        add(report, "playback_route", status, detail)
    except Exception as exc:
        add(report, "pipewire_graph", "fail", f"Cannot inspect the real graph: {type(exc).__name__}: {exc}")


async def playback_test(report, interface, args, settings):
    before = await snapshot(interface)
    previous_audio = (before.get("runtime") or {}).get("audio") or {}
    if ((before.get("runtime") or {}).get("loading") or previous_audio.get("running")) and not args.replace:
        add(report, "test_playback", "fail", "Existing playback or load was preserved. Retry while idle, or explicitly use --replace.")
        return before
    if not before.get("audio_ready"):
        add(report, "test_playback", "wait", "Connect the headphones and wait for A2DP readiness before the audible test")
        return before
    if not hasattr(interface, "call_stop_if_request") or not hasattr(interface, "call_begin_test_playback"):
        add(report, "test_playback", "fail", "Installed daemon lacks conditional cleanup; upgrade it before starting an owned test")
        return before
    # The unique symlink name lets State identify this exact request without
    # exporting or logging the user's media path. It never modifies the media.
    with tempfile.TemporaryDirectory(prefix="spatial-verify-") as temporary:
        media = Path(temporary) / ("verify-" + uuid.uuid4().hex + args.media.suffix)
        media.symlink_to(args.media)
        owned_pid = None
        request_token = 0
        last = before
        deadline = asyncio.get_running_loop().time() + args.timeout
        ready_at = None
        try:
            request_token = await asyncio.wait_for(interface.call_begin_test_playback(str(media), args.replace), 10)
            if not request_token:
                add(report, "test_playback", "wait", "Another playback request arrived; it was preserved")
                return before
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.2)
                last = await snapshot(interface)
                runtime = last.get("runtime") or {}
                audio = runtime.get("audio") or {}
                if runtime.get("playback_error"):
                    add(report, "test_playback", "fail", runtime["playback_error"])
                    break
                own_title = (runtime.get("track") or {}).get("title") == media.name
                if not runtime.get("loading") and own_title and audio.get("running") and audio.get("loaded"):
                    if owned_pid is None:
                        owned_pid = audio.get("process_id")
                        ready_at = asyncio.get_running_loop().time()
                    elif audio.get("process_id") != owned_pid:
                        add(report, "test_playback", "wait", "Playback changed during verification; the new player will be left alone")
                        break
                    if ready_at is not None and asyncio.get_running_loop().time() - ready_at >= args.seconds:
                        if args.require_spatial and not (audio.get("renderer_ready") and
                                audio.get("source_mode") == "spatial" and audio.get("object_count", 0) > 0):
                            continue
                        add(report, "test_playback", "pass", "Owned test media loaded and remained active for the observation interval")
                        break
                elif owned_pid is not None and not runtime.get("loading"):
                    add(report, "test_playback", "wait", "Test ended or playback changed before the observation interval completed")
                    break
            else:
                add(report, "test_playback", "fail", "Timed out waiting for verified spatial decoding" if args.require_spatial
                    else "Timed out waiting for this test to finish loading real audio")
            assess_state(report, last, settings)
            await capture_graph(report, last)
            audio = (last.get("runtime") or {}).get("audio") or {}
            if args.require_spatial:
                verified = (owned_pid is not None and audio.get("renderer_ready") is True
                            and audio.get("source_mode") == "spatial" and audio.get("object_count", 0) > 0
                            and report["checks"].get("playback_route", {}).get("status") == "pass")
                add(report, "spatial_content", "pass" if verified else "fail",
                    f"Live decoder reported {audio.get('object_count', 0)} rendered objects with verified headphone routing"
                    if verified else "Spatial rendering is unconfirmed: require loaded orender, ready binaural engine, decoded objects and verified headphone links")
            return last
        finally:
            if request_token:
                try:
                    stopped = await asyncio.wait_for(interface.call_stop_if_request(request_token), 10)
                    add(report, "test_cleanup", "pass" if stopped else "wait",
                        "Cancelled/stopped only this test's owned playback request" if stopped else "Playback ownership changed; current playback was left alone")
                except Exception as exc:
                    add(report, "test_cleanup", "wait", f"Could not confirm conditional cleanup: {type(exc).__name__}; check Companion playback")
            else:
                add(report, "test_cleanup", "off", "No test request was accepted; no playback was stopped")


async def verify(args):
    report = {"schema": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
              "mode": "audible-test" if args.media else "read-only", "checks": {},
              "scope": "Actual local service, devices and graph; no synthetic events or simulated renderer"}
    try:
        from spatial.settings import Settings, config_directory
        settings = Settings.load(args.config)
        path = args.config or config_directory() / "config.toml"
        add(report, "configuration", "pass" if path.exists() else "wait",
            "User configuration parsed successfully" if path.exists() else "Configuration is using defaults; run spatialctl setup")
    except Exception as exc:
        add(report, "configuration", "fail", f"Cannot load configuration: {type(exc).__name__}: {exc}")
        return report
    for tool in ("pw-dump", settings.mpv_binary, settings.sony_binary if settings.sony_enabled else None):
        if tool:
            add(report, "tool:" + tool, "pass" if shutil.which(tool) else "fail",
                "Executable available" if shutil.which(tool) else "Required configured executable is missing")
    try:
        from spatial.audio_runtime import AudioRuntime
        probe = await AudioRuntime(binary=settings.mpv_binary, bridge_path=settings.resolve_bridge(),
                                   library_path=settings.library_path).probe()
        add(report, "decoder_features", "pass" if probe.get("available") else "fail", probe.get("reason", "No result"))
    except Exception as exc:
        add(report, "decoder_features", "fail", f"Capability query failed: {type(exc).__name__}: {exc}")
    if getattr(settings, "live_enabled", False):
        try:
            from spatial.live_audio import LiveAudio
            await LiveAudio(binary=settings.orender_binary, bridge_path=settings.resolve_bridge()).probe()
            add(report, "live_features", "pass", "Pinned live renderer, local-control marker, bridge and PipeWire tools available")
        except Exception as exc:
            add(report, "live_features", "fail", f"Live capability query failed: {type(exc).__name__}: {exc}")
    if getattr(settings, "equalizer_enabled", False):
        try:
            from spatial.equalizer import Equalizer, read_profile
            equalizer = Equalizer(settings.equalizer_profile, enabled=True)
            await equalizer._dependencies()
            read_profile(equalizer.profile_path)
            add(report, "equalizer_features", "pass", "Measured profile, limiter plugin and required session-manager version available")
        except Exception as exc:
            add(report, "equalizer_features", "fail", f"Equalizer capability query failed: {type(exc).__name__}: {exc}")
    bus = None
    try:
        bus, interface = await connect()
        state = await snapshot(interface)
        add(report, "daemon", "pass", "Live org.spatiald.Control1 responded")
        assess_state(report, state, settings)
        await capture_graph(report, state)
        if args.media:
            state = await playback_test(report, interface, args, settings)
        report["observed_state"] = state
    except Exception as exc:
        add(report, "daemon", "fail", f"Live verification failed: {type(exc).__name__}: {exc}")
        await capture_graph(report, {})
    finally:
        if bus is not None:
            bus.disconnect()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--media", type=Path, help="play this real local sample through the daemon, then conditionally stop only that player")
    parser.add_argument("--replace", action="store_true", help="explicitly allow replacing existing playback for the audible test")
    parser.add_argument("--require-spatial", action="store_true", help="require actual decoded objects and binaural rendering during --media")
    parser.add_argument("--timeout", type=float, default=60, help="maximum media startup wait in seconds (1-300)")
    parser.add_argument("--seconds", type=float, default=2, help="loaded playback observation interval (0.5-30 seconds)")
    parser.add_argument("--output", type=Path, default=Path(f"spatial-verification-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json"))
    args = parser.parse_args(argv)
    if (args.replace or args.require_spatial) and args.media is None:
        parser.error("--replace and --require-spatial require --media")
    if not 1 <= args.timeout <= 300 or not 0.5 <= args.seconds <= 30:
        parser.error("timeout must be 1-300 seconds and observation 0.5-30 seconds")
    if args.media is not None:
        args.media = args.media.expanduser().resolve()
        if not args.media.is_file():
            parser.error("--media must be an existing regular local file")
    try:
        report = asyncio.run(verify(args))
    except KeyboardInterrupt:
        return 130
    result, code = overall(report["checks"])
    report["result"], report["exit_code"] = result, code
    report = Redactor().value(report)
    for name, check in report["checks"].items():
        print(f"[{check['status'].upper():4}] {name}: {check['detail']}")
    output = args.output.expanduser().resolve()
    try:
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except OSError as exc:
        print(f"spatial-verify: cannot save report: {exc}", file=sys.stderr)
        return 1
    print(f"Report: {output}\nResult: {result}; exit 0=passed, 1=failed, 2=waiting for evidence. No report uploaded.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
