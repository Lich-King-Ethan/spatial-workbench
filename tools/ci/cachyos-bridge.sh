#!/usr/bin/env bash
# Build the reviewed genuine decoder package; never substitute a test bridge.
set -euo pipefail

if [[ ${GITHUB_ACTIONS:-} != true || $EUID -eq 0 ]]; then
    printf '%s\n' 'The bridge build requires the unprivileged CI builder.' >&2
    exit 1
fi
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
evidence="$root/dist/cachyos-evidence"
build="$root/dist/aur/harletty-bridge"
commit=29ea73c708454ca11b7c124a677ddf370f709c99
mkdir -p "$evidence" "${build%/*}"
git clone --no-checkout https://aur.archlinux.org/harletty-bridge.git "$build"
git -C "$build" checkout --detach "$commit"
test "$(git -C "$build" rev-parse HEAD)" = "$commit"
cd "$build"
{
    printf '%s\n' 'repository=https://aur.archlinux.org/harletty-bridge.git'
    printf 'commit=%s\n' "$(git rev-parse HEAD)"
    sha256sum PKGBUILD .SRCINFO
} > "$evidence/bridge-source-provenance.txt"
cp PKGBUILD "$evidence/harletty-bridge-PKGBUILD.txt"
cp .SRCINFO "$evidence/harletty-bridge-SRCINFO.txt"

# This upstream recipe has no check() function. The subsequent required live
# audio gate establishes that the installed genuine bridge works with orender.
makepkg --syncdeps --noconfirm --cleanbuild
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
cp src/harletty-bridge-0.7.3/Cargo.lock "$evidence/harletty-bridge-Cargo.lock"
sudo pacman --upgrade --needed --noconfirm "${package_files[@]}"
pacman -Qk harletty-bridge
test "$(pacman -Qqo /usr/lib/orender/libharletty_bridge.so)" = harletty-bridge
python -I - <<'PY'
import ctypes
ctypes.CDLL("/usr/lib/orender/libharletty_bridge.so")
print("Loaded the installed genuine Harletty decoder shared library")
PY
