#!/usr/bin/env bash
# Run native QML checks without activating the host's desktop services.
set -Eeuo pipefail
if (( $# < 2 )); then
    printf 'Usage: bash run-private.sh PRIVATE_BUS_CONFIG COMMAND [ARGUMENTS...]\n' >&2
    exit 2
fi
bus_config=$1
shift
runtime_dir=$(mktemp -d "${TMPDIR:-/tmp}/spatial-qml-runtime.XXXXXXXX")
chmod 0700 "$runtime_dir"
cleanup() {
    local result=$?
    trap - EXIT
    # Never recurse into a FUSE filesystem if a test unexpectedly created one.
    # Leaving a mounted directory behind is an error, not a reason to suppress
    # cleanup failure or unmount/kill any process in the desktop session.
    if ! find "$runtime_dir" -xdev -depth -delete; then
        printf 'Native QML runtime cleanup failed; retained path: %s\n' "$runtime_dir" >&2
        if (( result == 0 )); then result=1; fi
    fi
    exit "$result"
}
trap cleanup EXIT
env XDG_RUNTIME_DIR="$runtime_dir" QT_QPA_PLATFORM=offscreen \
    QT_QUICK_BACKEND=software SPATIAL_QML_PRIVATE_BUS=1 \
    dbus-run-session --config-file="$bus_config" -- "$@"
