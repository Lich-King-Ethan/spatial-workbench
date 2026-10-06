#!/usr/bin/env bash
# Build the same reviewed player as the desktop installer for owned PCM gates.
set -euo pipefail

if [[ ${GITHUB_ACTIONS:-} != true || $EUID -eq 0 ]]; then
    printf '%s\n' 'The player build requires the unprivileged CI builder.' >&2
    exit 1
fi
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
evidence="$root/dist/cachyos-evidence"
build="$root/dist/aur/mpv-omniphony"
commit=f9e20fbbf55ca31da506ff770d1585bde11fbb89
mkdir -p "$evidence" "${build%/*}"
git clone --no-checkout https://aur.archlinux.org/mpv-omniphony.git "$build"
git -C "$build" checkout --detach "$commit"
test "$(git -C "$build" rev-parse HEAD)" = "$commit"
cd "$build"
printf '%s\n' \
    '951fd09dce6a0e385ccd65e42140a487dd092156e3b83b8b58eb1ef2d598c2f5  PKGBUILD' \
    'be02fda8ca8e7b74f3871584d977bde69b82383085975310ef36d3cf0d1b923c  .SRCINFO' | sha256sum --check
{
    printf '%s\n' 'repository=https://aur.archlinux.org/mpv-omniphony.git'
    printf 'commit=%s\n' "$(git rev-parse HEAD)"
    sha256sum PKGBUILD .SRCINFO
} > "$evidence/player-source-provenance.txt"
cp PKGBUILD "$evidence/mpv-omniphony-PKGBUILD.txt"
cp .SRCINFO "$evidence/mpv-omniphony-SRCINFO.txt"
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
pacman -Qk mpv-omniphony
test "$(pacman -Q mpv-omniphony)" = 'mpv-omniphony 0.5.2-1'
test "$(pacman -Qqo /usr/bin/mpv)" = mpv-omniphony
mpv --list-options > "$evidence/mpv-options.txt"
grep -- '--ad-orender-config' "$evidence/mpv-options.txt" > /dev/null
