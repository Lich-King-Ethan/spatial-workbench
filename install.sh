#!/usr/bin/env bash
# Full local install from a checked-out repository or extracted release archive.
set -Eeuo pipefail
project_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ! -f "$project_dir/build.sh" ]]; then
    printf 'Extract the complete release or clone the repository, then run bash install.sh from it.\n' >&2
    exit 1
fi
exec bash "$project_dir/build.sh" --install --with-audio --with-tidal "$@"
