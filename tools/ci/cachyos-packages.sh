#!/usr/bin/env bash
# Build and install one generated package in the disposable CachyOS container.
set -euo pipefail

if [[ ${GITHUB_ACTIONS:-} != true || $EUID -eq 0 ]]; then
    printf '%s\n' 'Package builds require the unprivileged CI builder.' >&2
    exit 1
fi
case ${1:-} in
    core|companion|orender) package=$1 ;;
    *) printf '%s\n' 'Usage: cachyos-packages.sh core|companion|orender' >&2; exit 2 ;;
esac
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
evidence="$root/dist/cachyos-evidence"
mkdir -p "$evidence"
cd "$root/dist/arch/$package"

makepkg --syncdeps --noconfirm --cleanbuild --check
package_list=$(makepkg --packagelist)
mapfile -t package_files <<< "$package_list"
for archive in "${package_files[@]}"; do
    test -f "$archive"
    archive_name=${archive##*/}
    pacman -Qip "$archive"
    bsdtar -xOf "$archive" .BUILDINFO > "$evidence/$archive_name.BUILDINFO"
    bsdtar -xOf "$archive" .PKGINFO > "$evidence/$archive_name.PKGINFO"
    sha256sum "$archive" >> "$evidence/package-sha256sums.txt"
done
sudo pacman --upgrade --needed --noconfirm "${package_files[@]}"
