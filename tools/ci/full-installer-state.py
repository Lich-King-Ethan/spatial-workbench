#!/usr/bin/env python3
"""Check real package ownership and preserved user files in the disposable VM."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


COMPANION = "com.github.maniacx.BudsLink-Companion"
SAVED_FILES = {
    "config.toml": b"# Existing desktop settings must survive installation.\nschema = 1\nsample_timeout = 0.75\n",
    "preferences.json": b'{"schema":1,"earbuds":{},"optional":{"ci-preserved-tracker":false}}\n',
}
COMPANION_CONTENT = b"Existing user-local Companion fixture; preserve in the installer backup.\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("seed", "check"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 1000 or os.environ.get("XDG_RUNTIME_DIR") != "/run/user/1000":
        raise RuntimeError("This check requires the disposable VM desktop account")
    if not sys.flags.isolated:
        raise RuntimeError("Use python -I to exclude source-tree imports")
    home = Path.home()
    config = home / ".config/spatiald"
    local_companion = home / ".local/share/plasma/plasmoids" / COMPANION
    if args.operation == "seed":
        config.mkdir(parents=True, mode=0o700)
        for name, content in SAVED_FILES.items():
            with (config / name).open("xb") as stream:
                stream.write(content)
            (config / name).chmod(0o600)
        local_companion.mkdir(parents=True)
        (local_companion / "ci-existing-companion.txt").write_bytes(COMPANION_CONTENT)
        return
    if args.report is None:
        parser.error("check requires --report")

    preserved = {}
    for name, expected in SAVED_FILES.items():
        path = config / name
        assert path.read_bytes() == expected, f"Installer changed existing {name}"
        assert path.stat().st_uid == os.getuid(), f"Installer changed ownership of {name}"
        assert path.stat().st_mode & 0o777 == 0o600, f"Installer changed permissions of {name}"
        preserved[name] = hashlib.sha256(expected).hexdigest()
    assert not local_companion.exists(), "User-local Companion still shadows the package"
    backups = list((home / ".local/state/spatiald/plasma-backups").glob(
        f"companion.*/{COMPANION}/ci-existing-companion.txt"))
    assert len(backups) == 1 and backups[0].read_bytes() == COMPANION_CONTENT, \
        "Existing Companion was lost, changed, or backed up repeatedly"

    # Query ownership of actual executable/module/service/UI files, not merely
    # whether pacman's database contains a package with the expected name.
    import spatial
    installed_module = Path(spatial.__file__).resolve()
    assert not installed_module.is_relative_to(Path(__file__).resolve().parents[2]), \
        "Ownership check imported the source checkout instead of the installed package"
    paths = {
        "spatial-workbench": [str(installed_module), "/usr/bin/spatialctl",
            "/usr/bin/spatial-diagnostics", "/usr/bin/spatial-verify",
            "/usr/lib/systemd/user/spatiald.service", "/usr/lib/udev/rules.d/69-spatiald-trackers.rules",
            "/usr/share/wireplumber/scripts/spatial-live-guard.lua",
            "/usr/share/wireplumber/wireplumber.conf.d/90-spatial-live-guard.conf"],
        "plasma-budslink-companion-spatial": [f"/usr/share/plasma/plasmoids/{COMPANION}/metadata.json"],
        "orender-spatial": ["/usr/bin/orender", "/usr/lib/liborender.so.0"],
        "mpv-omniphony": ["/usr/bin/mpv"],
        "harletty-bridge": ["/usr/lib/orender/libharletty_bridge.so"],
        "sony-tracker": ["/usr/bin/sony-tracker"],
    }
    ownership = {}
    for expected_owner, files in paths.items():
        for filename in files:
            owner = subprocess.check_output(["pacman", "-Qqo", filename], text=True).strip()
            assert owner == expected_owner, (filename, expected_owner, owner)
            ownership[filename] = owner
    subprocess.run(["pacman", "-Qk", *paths], check=True)
    report = {"status": "pass", "preserved_config_sha256": preserved,
              "companion_backups": len(backups), "package_file_ownership": ownership}
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
