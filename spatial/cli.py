"""Command-line setup, playback, and diagnostics for Spatial Audio."""
import argparse
import asyncio
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__, pipewire
from .settings import Settings, config_directory, setup


def parser():
    p = argparse.ArgumentParser(description="Modular spatial audio for Sony headphones on Plasma")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the live service (normally managed by systemd --user)")
    run.add_argument("--config", type=Path)
    run.add_argument("--verbose", action="store_true")
    initialize = sub.add_parser("setup", help="create missing user configuration without overwriting settings")
    initialize.add_argument("--config", type=Path)
    sub.add_parser("status", help="show live device, tracker, renderer, and module state")
    play = sub.add_parser("play", help="play a local file, HTTP media URL, or TIDAL track/album/playlist")
    play.add_argument("media")
    for name, help_text in (("stop", "stop playback and release its process"),
                            ("pause", "pause playback"), ("resume", "resume playback"),
                            ("next", "play the next queued track"), ("previous", "play the previous track"),
                            ("recenter", "center the currently active permitted tracker")):
        sub.add_parser(name, help=help_text)
    enabled = sub.add_parser("enable", help="remember permission for an actual tracker ID from status")
    enabled.add_argument("tracker_id")
    enabled.add_argument("permission", choices=("on", "off"))
    volume = sub.add_parser("volume", help="set player volume, from 0 to 100 percent")
    volume.add_argument("percent", type=float)
    seek = sub.add_parser("seek", help="seek by a number of seconds")
    seek.add_argument("seconds", type=float)
    doctor = sub.add_parser("doctor", help="read tool/package versions and renderer capabilities")
    doctor.add_argument("--bluetooth-address", type=pipewire.address)
    doctor.add_argument("--config", type=Path)
    sub.add_parser("tidal", help="TIDAL device login, search, and stream inspection")
    live = sub.add_parser("live", help="spatialize one explicitly selected application's PCM audio")
    live_sub = live.add_subparsers(dest="live_command", required=True)
    live_sub.add_parser("list", help="list actual movable application streams")
    live_start = live_sub.add_parser("start", help="start application audio for an exact stream serial")
    live_start.add_argument("serial")
    live_sub.add_parser("stop", help="stop application processing and restore its previous route")
    replay = sub.add_parser("replay", help="developer policy regression tool; does not play audio")
    replay.add_argument("scenario", type=Path)
    replay.add_argument("--json", action="store_true")
    return p


def media_reference(value):
    """Resolve a terminal's relative filename before crossing into the daemon."""
    if not value or any(ord(c) < 32 for c in value):
        raise ValueError("a valid media file or URL is required")
    if urlsplit(value).scheme:
        return value
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("select a regular media file")
    return str(path)


async def client(command, *args):
    from dbus_next import Variant
    from dbus_next.aio import MessageBus
    from .desktop import BUS, PATH
    from .mpris import SERVICE, PATH as MEDIA_PATH
    mpris = command in ("pause", "resume", "next", "previous", "volume", "seek")
    service, path = (SERVICE, MEDIA_PATH) if mpris else (BUS, PATH)
    bus = await MessageBus().connect()
    try:
        node = await asyncio.wait_for(bus.introspect(service, path), 5)
        proxy = bus.get_proxy_object(service, path, node)
        if command == "volume":
            percent = args[0]
            if not 0 <= percent <= 100:
                raise ValueError("volume must be between 0 and 100")
            props = proxy.get_interface("org.freedesktop.DBus.Properties")
            await props.call_set("org.mpris.MediaPlayer2.Player", "Volume", Variant('d', percent / 100))
            return
        interface = proxy.get_interface("org.mpris.MediaPlayer2.Player" if mpris else BUS)
        methods = {"play": "call_play", "stop": "call_stop", "pause": "call_pause",
                   "resume": "call_play", "next": "call_next", "previous": "call_previous",
                   "recenter": "call_recenter", "enable": "call_set_tracker_enabled", "seek": "call_seek",
                   "live-start": "call_start_live", "live-stop": "call_stop_live"}
        if command == "status":
            return json.loads(await interface.get_state())
        if command == "seek":
            import math
            if not math.isfinite(args[0]) or abs(args[0]) > 2**62 / 1_000_000:
                raise ValueError("seek seconds must be finite and within range")
            args = (int(args[0] * 1_000_000),)
        await asyncio.wait_for(getattr(interface, methods[command])(*args), 120)
    finally:
        bus.disconnect()


def doctor(selected_address=None, config=None):
    settings = Settings.load(config)
    result = {"mode": "read-only", "version": __version__, "python": sys.version.split()[0],
              "tools": {}, "packages": {}, "config": str(config or config_directory() / "config.toml")}
    for tool in ("pw-dump", "wpctl", "busctl", settings.mpv_binary, "orender", settings.sony_binary):
        result["tools"][tool] = shutil.which(tool)
    if shutil.which("pacman"):
        for pkg in ("pipewire", "pipewire-audio", "wireplumber", "bluez", "mpv-omniphony",
                    "orender", "orender-spatial", "harletty-bridge", "sony-tracker", "python-dbus-next", "python-tidalapi"):
            try:
                proc = subprocess.run(["pacman", "-Q", pkg], capture_output=True, text=True, timeout=3)
                result["packages"][pkg] = proc.stdout.strip() if proc.returncode == 0 else None
            except subprocess.TimeoutExpired:
                result["packages"][pkg] = "query timed out"
    from .audio_runtime import AudioRuntime
    audio = AudioRuntime(binary=settings.mpv_binary, bridge_path=settings.resolve_bridge(),
                         library_path=settings.library_path)
    try:
        result["renderer_probe"] = asyncio.run(audio.probe())
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        result["renderer_probe"] = {"available": False, "error": str(exc)}
    selected_address = selected_address or settings.bluetooth_address
    if selected_address:
        try:
            objects = asyncio.run(pipewire.capture())
            result["sinks"] = [s.__dict__ for s in pipewire.parse_sinks(objects, selected_address)]
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            result["pipewire_error"] = str(exc)
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] == "tidal":
            from .tidal import main as tidal_main
            return tidal_main(argv[1:])
        args = parser().parse_args(argv)
        if args.command == "setup":
            path, created = setup(args.config)
            print(f"{'Created' if created else 'Kept existing'} configuration: {path}")
        elif args.command == "run":
            from .runtime import Runtime
            logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                                format="%(levelname)s %(name)s: %(message)s")
            asyncio.run(Runtime(Settings.load(args.config)).run())
        elif args.command == "doctor":
            print(json.dumps(doctor(args.bluetooth_address, args.config), indent=2))
        elif args.command == "live":
            if args.live_command == "list":
                state = asyncio.run(client("status"))
                print(json.dumps(state.get("runtime", {}).get("live", {}), indent=2, ensure_ascii=False))
            elif args.live_command == "start":
                asyncio.run(client("live-start", args.serial))
            else:
                asyncio.run(client("live-stop"))
        elif args.command == "replay":
            from .core import Engine
            from . import replay
            for event, state in replay.run(Engine(), args.scenario):
                if args.json:
                    print(json.dumps({"event": event, "state": state}, ensure_ascii=False))
                else:
                    print(f"{event['at']:6.2f}s  {event['type']:12}  audio={state['audio_state']:12}"
                          f" tracker={state['active_name'] or 'None'}"
                          f" optional_rows={len(state['optional_trackers'])}")
        else:
            values = ()
            if args.command == "play":
                values = (media_reference(args.media),)
            elif args.command == "enable":
                values = (args.tracker_id, args.permission == "on")
            elif args.command == "volume":
                values = (args.percent,)
            elif args.command == "seek":
                values = (args.seconds,)
            result = asyncio.run(client(args.command, *values))
            if result is not None:
                print(json.dumps(result, indent=2, ensure_ascii=False))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        message = str(exc)
        if "ServiceUnknown" in message or "not provided by any .service" in message:
            message = "Spatial Audio is not running; run systemctl --user start spatiald.service"
        print(f"spatialctl: {message}", file=sys.stderr)
        return 1
    return 0
