#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C.UTF-8
mkdir -p /ci-output
exec > >(tee /ci-output/guest.log) 2>&1

desktop_user() {
    runuser -u builder -- env -u PYTHONPATH XDG_RUNTIME_DIR=/run/user/1000 \
        DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
        CARGO_BUILD_JOBS=3 CMAKE_BUILD_PARALLEL_LEVEL=4 TERM=dumb NO_COLOR=1 "$@"
}
snapshot_resources() {
    local phase=$1
    {
        date --iso-8601=seconds
        free -h
        cat /proc/meminfo
        swapon --show --bytes
        df -hT /
        ps -eo pid,comm,rss,vsz --sort=-rss | sed -n '1,21p'
    } > "/ci-output/resources-$phase.txt" 2>&1
}
finish() {
    local result=$?
    trap - EXIT
    set +e
    if (( result )); then snapshot_resources failure; else snapshot_resources complete; fi
    journalctl --no-pager -b > /ci-output/system-journal.log
    desktop_user journalctl --user --no-pager -b > /ci-output/user-journal.log
    loginctl list-sessions --no-pager > /ci-output/login-sessions.txt
    if [[ -f /home/builder/.local/share/sddm/wayland-session.log ]]; then
        cp /home/builder/.local/share/sddm/wayland-session.log /ci-output/
    fi
    pacman -Q > /ci-output/installed-packages.txt
    if [[ -d /home/builder/.local/state/spatiald/install ]]; then
        # Source worktrees/build objects are large and irrelevant to failure
        # diagnosis. Keep all installer logs and redacted verification reports.
        find /home/builder/.local/state/spatiald/install -maxdepth 1 -type f \
            \( -name '*.log' -o -name '*.json' \) -exec cp -t /ci-output/ -- {} +
    fi
    if (( result )); then
        # Workflow logs remain usable when artifact downloads are unavailable.
        # Read only CI build/service logs, never configuration or credentials.
        journalctl --no-pager -b -p warning -n 60 \
            -u systemd-udevd.service -u systemd-networkd.service \
            -u user@1000.service -u sddm.service -u spatial-installer-ci.service \
            > /ci-output/system-errors.log
        desktop_user journalctl --user --no-pager -b -p warning -n 60 \
            -u spatiald.service -u pipewire.service -u wireplumber.service \
            -u plasma-kwin_wayland.service -u plasma-plasmashell.service \
            > /ci-output/user-errors.log
        printf '\n[CI failure] Exit %s; bounded, redacted diagnostic tails follow.\n' "$result"
        python - <<'PY'
from pathlib import Path
import sys

sys.path.insert(0, '/home/builder/budslink-spatial-companion')
from spatial.diagnostics_privacy import Redactor

redactor = Redactor()
logs = sorted(Path('/ci-output').glob('install-*.log'))[-3:]
logs += [Path('/ci-output/system-errors.log'), Path('/ci-output/user-errors.log')]
for path in logs:
    if not path.is_file():
        continue
    print(f'--- {path.name}: last 100 lines, at most 64KiB ---', flush=True)
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - 65536))
        lines = stream.read(65536).decode('utf-8', errors='replace').splitlines()[-100:]
    print(redactor.text('\n'.join(lines)), flush=True)
PY
    fi
    printf '%s\n' "$result" > /ci-output/result
    sync
    systemctl --no-block poweroff
    exit "$result"
}
trap finish EXIT

ci_validation_scope=$(cat /ci-validation-scope)
case "$ci_validation_scope" in
    full|desktop) ;;
    *) printf 'Unknown guest validation scope: %s\n' "$ci_validation_scope" >&2; exit 2 ;;
esac
printf 'Guest validation scope: %s\n' "$ci_validation_scope"
printf 'Guest PID 1: '
cat /proc/1/comm
[[ $(cat /proc/1/comm) == systemd ]]
[[ $(systemd-detect-virt) == kvm ]]
[[ ${SYSTEMD_OFFLINE:-0} == 0 ]]
uname -a
snapshot_resources boot
# The upstream truehd release build used 9,232,508KiB RSS in one rustc
# process. A complete desktop needs memory too; fewer Cargo jobs alone cannot
# bound that process. Allocate swap only in this disposable, booted ext4 VM.
[[ $(findmnt -n -o FSTYPE /) == ext4 ]]
swap_file=/ci-swapfile
[[ ! -e "$swap_file" && ! -L "$swap_file" ]]
fallocate --length 8G "$swap_file"
chmod 0600 "$swap_file"
mkswap "$swap_file"
swapon "$swap_file"
python - <<'PY'
from pathlib import Path

entries = [line.split() for line in Path('/proc/swaps').read_text().splitlines()[1:]]
active = [entry for entry in entries if entry[0] == '/ci-swapfile']
assert len(active) == 1 and active[0][1] == 'file', entries
assert int(active[0][2]) >= 8 * 1024 * 1024 - 1024, active
print('Guest-only 8GiB swapfile is active; host swap and limits are unchanged.')
PY
snapshot_resources swap-ready
systemctl is-active systemd-udevd.service
udevadm control --reload-rules
findmnt /
findmnt /sys
desktop_user systemctl --user show-environment
desktop_user systemctl --user start pipewire.service wireplumber.service
desktop_user systemctl --user is-active pipewire.service wireplumber.service
busctl --system get-property org.freedesktop.RealtimeKit1 /org/freedesktop/RealtimeKit1 \
    org.freedesktop.RealtimeKit1 MaxRealtimePriority > /ci-output/rtkit-status.txt
systemctl is-active rtkit-daemon.service
ps -eLo pid,tid,comm,cls,rtprio,ni > /ci-output/thread-scheduling-before.txt
desktop_user pw-dump > /ci-output/pipewire-before.json
systemctl is-active sddm.service
install -d -o builder -g builder /ci-output/desktop
desktop_user python -I /home/builder/budslink-spatial-companion/tools/ci/full-installer-desktop.py \
    --report-dir /ci-output/desktop/before
snapshot_resources before-build

if [[ "$ci_validation_scope" == desktop ]]; then
    desktop_user python /home/builder/budslink-spatial-companion/tools/ci/full-installer-pty.py --desktop-only
    snapshot_resources after-desktop-install
    desktop_user systemctl --user is-enabled spatiald.service
    desktop_user systemctl --user is-active spatiald.service pipewire.service wireplumber.service
    desktop_user spatialctl status > /ci-output/daemon-status.json
    pacman -Qk budslink-spatial-companion plasma-budslink-spatial-companion
    desktop_user python -I /home/builder/budslink-spatial-companion/tools/ci/full-installer-desktop.py \
        --installed --report-dir /ci-output/desktop/installed
    python - <<'PY'
import json
from pathlib import Path

reports = sorted(Path('/home/builder/.local/state/spatiald/install').glob('verification-*.json'))
assert reports, 'The desktop installer did not produce a host verification report'
host = json.loads(reports[-1].read_text())
assert host['selection'] == 'core' and host['result'] == 'pass', host
assert host['checks']['audio_modules']['status'] == 'off', host
desktop_reports = {}
for phase in ('before', 'installed'):
    report = json.loads((Path('/ci-output/desktop') / phase / 'desktop.json').read_text())
    assert report['status'] == 'pass', report
    desktop_reports[phase] = report
acceptance = {
    'scope': 'desktop',
    'desktop_precheck': 'pass',
    'core_and_companion_install': 'pass',
    'full_installer': 'not_run',
    'audio': 'not_run',
    'repeat_core_install': 'not_run',
    'physical_hardware': 'not_tested',
    'desktop': desktop_reports,
    'host_verification': host,
}
Path('/ci-output/desktop-acceptance.json').write_text(json.dumps(acceptance, indent=2) + '\n')
print(json.dumps(acceptance, indent=2))
PY
    exit 0
fi

install -d -o builder -g builder /ci-output/install-state
desktop_user python -I /home/builder/budslink-spatial-companion/tools/ci/full-installer-state.py seed
desktop_user python /home/builder/budslink-spatial-companion/tools/ci/full-installer-pty.py
snapshot_resources after-full-install
desktop_user python -I /home/builder/budslink-spatial-companion/tools/ci/full-installer-state.py check \
    --report /ci-output/install-state/first-install.json
python - <<'PY'
from pathlib import Path
import shutil

reports = sorted(Path('/home/builder/.local/state/spatiald/install').glob('verification-*.json'))
assert reports, 'Installer did not produce its actual host acceptance report'
shutil.copyfile(reports[-1], '/ci-output/full-install-verification.json')
PY

# Re-run the real reduced installer over the live full installation. This
# exercises package replacement, configuration preservation and service restart
# without spending another renderer build on the same pinned source.
desktop_user python /home/builder/budslink-spatial-companion/tools/ci/full-installer-pty.py --core-only
snapshot_resources after-repeat-install
desktop_user python -I /home/builder/budslink-spatial-companion/tools/ci/full-installer-state.py check \
    --report /ci-output/install-state/repeat-core-install.json

desktop_user systemctl --user is-enabled spatiald.service
desktop_user systemctl --user is-active spatiald.service pipewire.service wireplumber.service
desktop_user spatialctl status > /ci-output/daemon-status.json
desktop_user pw-dump > /ci-output/pipewire-after.json
systemctl is-active systemd-udevd.service
pacman -Q budslink-spatial-companion plasma-budslink-spatial-companion orender-spatial \
    harletty-bridge mpv-omniphony sony-tracker-spatial python-tidalapi
desktop_user python -I /home/builder/budslink-spatial-companion/tools/ci/full-installer-desktop.py \
    --installed --report-dir /ci-output/desktop/installed

# Exercise the installed native decoder and complete software audio path. The
# private PipeWire endpoints and head-pose packets remain explicit test inputs;
# they do not turn the separate physical-host checks below into hardware PASSes.
install -d -o builder -g builder /ci-output/audio
desktop_user python -I /home/builder/budslink-spatial-companion/tools/ci/decoder-smoke.py \
    --download-fixture --report /ci-output/audio/decoder-smoke.json
desktop_user timeout --signal=TERM --kill-after=10s 10m dbus-run-session -- \
    python -I /home/builder/budslink-spatial-companion/tools/ci/audio-stack-smoke.py \
    --require-media-audio --bridge /usr/lib/orender/libharletty_bridge.so \
    --report-dir /ci-output/audio/pipewire
snapshot_resources after-audio
python - <<'PY'
import json
from pathlib import Path

report = json.loads(Path('/ci-output/full-install-verification.json').read_text())
checks = report['checks']
failed = {name: check for name, check in checks.items() if check['status'] == 'fail'}
assert not failed, failed
for name in ('configuration', 'decoder_features', 'daemon', 'pipewire_graph'):
    assert checks[name]['status'] == 'pass', (name, checks[name])
for name in ('headphones', 'headphone_output', 'renderer_abi'):
    assert checks[name]['status'] == 'wait', (name, checks[name])
reports = sorted(Path('/home/builder/.local/state/spatiald/install').glob('verification-*.json'))
repeat = json.loads(reports[-1].read_text())
assert repeat['selection'] == 'core' and repeat['result'] == 'pass', repeat
assert repeat['checks']['audio_modules']['status'] == 'off', repeat
desktop_reports = {}
for phase in ('before', 'installed'):
    desktop = json.loads((Path('/ci-output/desktop') / phase / 'desktop.json').read_text())
    assert desktop['status'] == 'pass', desktop
    desktop_reports[phase] = desktop
acceptance = {
    'scope': 'full',
    'installer': 'pass',
    'repeat_core_install': 'pass; existing settings, permissions and Companion backup preserved',
    'package_file_ownership': 'pass; installed files belong to the expected pacman packages',
    'environment': 'CachyOS kernel and complete Plasma desktop, booted with KVM; SDDM Wayland login',
    'desktop': desktop_reports,
    'shell_and_editor': 'fish login shell, nano EDITOR/VISUAL; real installer invoked from fish',
    'systemd_udev_pipewire': 'real running services',
    'physical_headphones_and_spatial_playback': 'waiting; no physical hardware is attached',
    'host_checks': checks,
}
Path('/ci-output/acceptance.json').write_text(json.dumps(acceptance, indent=2) + '\n')
print(json.dumps(acceptance, indent=2))
PY
