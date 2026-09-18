#!/usr/bin/env bash
# Read-only provenance collection; called even after a failed build or test.
set -euo pipefail
evidence=${1:?Usage: cachyos-provenance.sh EVIDENCE_DIRECTORY}
mkdir -p "$evidence"
{
    printf 'container_image=%s\n' "${CACHYOS_IMAGE:-unknown}"
    printf 'source_commit=%s\n' "${GITHUB_SHA:-unknown}"
    printf 'run_url=https://github.com/%s/actions/runs/%s\n' "${GITHUB_REPOSITORY:-unknown}" "${GITHUB_RUN_ID:-unknown}"
    printf 'run_attempt=%s\n' "${GITHUB_RUN_ATTEMPT:-unknown}"
    printf 'recorded_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '%s\n' 'scope=official CachyOS userspace; kernel belongs to the GitHub hosted runner'
    printf '%s\n' 'hardware=synthetic audio endpoints; no Bluetooth, HID, headset, or physical desktop validation'
    uname -a
} > "$evidence/provenance.txt"
cp /etc/os-release "$evidence/os-release.txt"
pacman --version > "$evidence/pacman-version.txt"
pacman -Q > "$evidence/installed-packages.txt"
pacman-conf > "$evidence/pacman-config.txt"
pacman-conf --repo-list > "$evidence/repositories.txt"
if [[ -f /var/log/pacman.log ]]; then
    cp /var/log/pacman.log "$evidence/pacman.log"
fi
for mirrorlist in /etc/pacman.d/*mirrorlist; do
    [[ -f $mirrorlist ]] && cp "$mirrorlist" "$evidence/${mirrorlist##*/}.txt"
done
if [[ -d dist/arch ]]; then
    for package in core companion orender; do
        if [[ -f dist/arch/$package/PKGBUILD ]]; then
            cp "dist/arch/$package/PKGBUILD" "$evidence/$package-PKGBUILD.txt"
        fi
    done
fi
