#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C.UTF-8
mkdir -p /ci-output
exec > >(tee /ci-output/guest.log) 2>&1

desktop_user() {
    runuser -u builder -- env XDG_RUNTIME_DIR=/run/user/1000 \
        DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
        CARGO_BUILD_JOBS=3 CMAKE_BUILD_PARALLEL_LEVEL=4 TERM=dumb NO_COLOR=1 "$@"
}
finish() {
    local result=$?
    trap - EXIT
    set +e
    journalctl --no-pager -b > /ci-output/system-journal.log
    desktop_user journalctl --user --no-pager -b > /ci-output/user-journal.log
    pacman -Q > /ci-output/installed-packages.txt
    if [[ -d /home/builder/.local/state/spatiald/install ]]; then
        # Source worktrees/build objects are large and irrelevant to failure
        # diagnosis. Keep all installer logs and redacted verification reports.
        find /home/builder/.local/state/spatiald/install -maxdepth 1 -type f \
            \( -name '*.log' -o -name '*.json' \) -exec cp -t /ci-output/ -- {} +
    fi
    printf '%s\n' "$result" > /ci-output/result
    sync
    systemctl --no-block poweroff
    exit "$result"
}
trap finish EXIT

printf 'Guest PID 1: '
cat /proc/1/comm
[[ $(cat /proc/1/comm) == systemd ]]
[[ $(systemd-detect-virt) == kvm ]]
uname -a
systemctl is-active systemd-udevd.service
udevadm control --reload-rules
findmnt /
findmnt /sys
desktop_user systemctl --user show-environment
desktop_user systemctl --user start pipewire.service wireplumber.service
desktop_user systemctl --user is-active pipewire.service wireplumber.service
desktop_user pw-dump > /ci-output/pipewire-before.json

desktop_user python /home/builder/spatial-workbench/tools/ci/full-installer-pty.py

desktop_user systemctl --user is-enabled spatiald.service
desktop_user systemctl --user is-active spatiald.service pipewire.service wireplumber.service
desktop_user spatialctl status > /ci-output/daemon-status.json
desktop_user pw-dump > /ci-output/pipewire-after.json
systemctl is-active systemd-udevd.service
pacman -Q spatial-workbench plasma-budslink-companion-spatial orender-spatial \
    harletty-bridge mpv-omniphony sony-tracker python-tidalapi
python - <<'PY'
import json
from pathlib import Path

reports = sorted(Path('/home/builder/.local/state/spatiald/install').glob('verification-*.json'))
assert reports, 'Installer did not produce its actual host acceptance report'
report = json.loads(reports[-1].read_text())
checks = report['checks']
failed = {name: check for name, check in checks.items() if check['status'] == 'fail'}
assert not failed, failed
for name in ('configuration', 'decoder_features', 'daemon', 'pipewire_graph'):
    assert checks[name]['status'] == 'pass', (name, checks[name])
for name in ('headphones', 'headphone_output', 'renderer_abi'):
    assert checks[name]['status'] == 'wait', (name, checks[name])
acceptance = {
    'installer': 'pass',
    'environment': 'Minimal CachyOS userspace and CachyOS kernel, booted with KVM',
    'systemd_udev_pipewire': 'real running services',
    'physical_headphones_and_spatial_playback': 'waiting; no physical hardware is attached',
    'host_checks': checks,
}
Path('/ci-output/acceptance.json').write_text(json.dumps(acceptance, indent=2) + '\n')
print(json.dumps(acceptance, indent=2))
PY
