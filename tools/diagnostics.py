#!/usr/bin/env python3
"""Collect a local, redacted support report without changing audio or devices."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

MAX_OUTPUT = 256 * 1024
# Source-tree execution and the installed entry point share the packaged helper.
_source_root = Path(__file__).resolve().parents[1]
if not sys.flags.isolated and (_source_root / "spatial").is_dir():
    sys.path.insert(0, str(_source_root))
from spatial.diagnostics_privacy import Redactor


def probe(argv, redactor, timeout=10):
    if not shutil.which(argv[0]):
        return {"available": False}
    try:
        process = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                                 timeout=timeout, check=False,
                                 env={**os.environ, "LC_ALL": "C", "SYSTEMD_PAGER": "cat"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": True, "error": redactor.text(exc)}
    stdout = process.stdout[:MAX_OUTPUT]
    try:
        value = redactor.value(json.loads(stdout))
    except (ValueError, TypeError):
        value = redactor.text(stdout)
    return {"available": True, "exit_code": process.returncode, "output": value,
            "error": redactor.text(process.stderr[:MAX_OUTPUT]),
            "truncated": len(process.stdout) > MAX_OUTPUT or len(process.stderr) > MAX_OUTPUT}


def hardware(redactor):
    devices = []
    for node in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        try:
            properties = dict(line.split("=", 1) for line in (node / "device/uevent").read_text().splitlines() if "=" in line)
            devices.append({"node": f"/dev/{node.name}", "readable": os.access(f"/dev/{node.name}", os.R_OK),
                            "properties": redactor.value(properties)})
        except OSError as exc:
            devices.append({"node": node.name, "error": redactor.text(exc)})
    return devices


def collect(include_logs=False):
    redactor = Redactor()
    commands = {
        "doctor": ["spatialctl", "doctor"],
        "pipewire": ["pw-dump"],
        "wireplumber": ["wpctl", "status"],
        "user_services": ["systemctl", "--user", "show", "spatiald.service", "pipewire.service", "wireplumber.service",
                          "--property=Id,LoadState,ActiveState,SubState,Result,NRestarts"],
        "packages": ["pacman", "-Q", "spatial-workbench", "plasma-budslink-companion-spatial", "python-dbus-next",
                     "pipewire", "wireplumber", "bluez", "mpv-omniphony", "orender", "orender-spatial", "sony-tracker"],
        "budslink_install": ["flatpak", "info", "--show-version", "io.github.maniacx.BudsLink"],
    }
    if include_logs:
        commands["recent_service_log"] = ["journalctl", "--user", "-u", "spatiald.service", "-n", "200", "--no-pager", "-o", "cat"]
    return {
        "schema": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "read-only", "python": sys.version.split()[0],
        "session": {key: redactor.text(os.environ.get(key, "")) for key in ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP")},
        "privacy": "Local report; known secrets, URLs, addresses, home and login identifiers redacted. Review before sharing.",
        "hid_devices": hardware(redactor),
        "probes": {name: probe(argv, redactor) for name, argv in commands.items()},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(f"spatial-diagnostics-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json"))
    parser.add_argument("--logs", action="store_true", help="also include the last 200 redacted spatiald log entries")
    args = parser.parse_args(argv)
    report = collect(args.logs)
    output = args.output.expanduser().resolve()
    # Exclusive creation also refuses a pre-existing symlink; never overwrite a
    # user's file just because a command was run twice in the same second.
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
    except OSError as exc:
        print(f"spatial-diagnostics: {exc}", file=sys.stderr)
        return 1
    print(f"Report saved: {output}")
    print("Review the local report before sharing; no report has been uploaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
