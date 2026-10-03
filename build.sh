#!/usr/bin/env bash
# BudsLink Spatial Companion package build and install. Run as the desktop user, never as root.
set -Eeuo pipefail
umask 077
# Scope editor defaults to this invocation and its package-review subprocesses.
# Do not rewrite the desktop account's shell or existing editor preferences.
export EDITOR="${EDITOR:-nano}"
export VISUAL="${VISUAL:-$EDITOR}"

project_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
install_packages=0
with_companion=1
with_audio=0
with_tidal=0
usage() {
    cat <<'USAGE'
Usage: bash install.sh
       ./build.sh [--install] [--core-only] [--with-audio] [--with-tidal]

install.sh sets up the full application. build.sh without --install builds the
core and native Companion packages only. Missing official build tools are
installed through pacman; standard sudo and package-review prompts stay visible.

--with-audio builds the pinned local-control renderer and matching reviewed AUR
mpv-omniphony 0.5.2-1 and harletty-bridge 0.7.3-1 recipes. These two packages use
the same pinned makepkg path whether yay/paru is present or not. Sony tracking
uses the checksummed local sony-tracker-spatial recipe with opt-in host-timestamped absolute
orientation; its legacy CLI behavior remains the default. --with-tidal installs
official python-tidalapi. Existing incompatible audio packages are reported
before package transactions; the installer never silently downgrades that stack.
Both options require --install. Existing user settings are preserved.
USAGE
}
for argument in "$@"; do
    case "$argument" in
        --install) install_packages=1 ;;
        --core-only) with_companion=0 ;;
        --with-audio) with_audio=1 ;;
        --with-tidal) with_tidal=1 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$argument" >&2; usage >&2; exit 2 ;;
    esac
done
if (( (with_audio || with_tidal) && ! install_packages )); then
    printf '%s\n' '--with-audio and --with-tidal require --install.' >&2
    exit 2
fi
if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
    printf 'Run this command as your normal desktop user. Only pacman needs elevated privileges.\n' >&2
    exit 1
fi
if ! command -v pacman >/dev/null 2>&1; then
    printf 'This installer requires Arch Linux or CachyOS with pacman.\n' >&2
    exit 1
fi
if ! command -v sudo >/dev/null 2>&1; then
    printf 'sudo is required for package transactions. Install/configure sudo for your desktop account first.\n' >&2
    exit 1
fi

spatial_install_dir="${XDG_STATE_HOME:-$HOME/.local/state}/spatiald/install"
mkdir -p -- "$spatial_install_dir"
chmod 700 -- "$spatial_install_dir"
spatial_install_log=$(mktemp "$spatial_install_dir/install-$(date -u +%Y%m%dT%H%M%S).XXXXXXXX.log")
spatial_current_step='initialization'
spatial_green=''
spatial_reset=''
if [[ -t 1 && ${TERM:-dumb} != dumb && -z ${NO_COLOR:-} ]]; then
    spatial_green=$'\033[32m'
    spatial_reset=$'\033[0m'
fi
message() {
    printf '%s\n' "$*" | tee -a "$spatial_install_log"
}
passed() {
    printf '%s[PASS]%s %s\n' "$spatial_green" "$spatial_reset" "$1"
    printf '[PASS] %s\n' "$1" >> "$spatial_install_log"
}
run_step() {
    local description="$1"
    shift
    spatial_current_step="$description"
    message "[RUN ] $description"
    # tee preserves package review/sudo prompts while keeping a private full log.
    # pipefail makes either a failed command or a failed log write an error.
    if "$@" 2>&1 | tee -a "$spatial_install_log"; then
        passed "$description"
    else
        return "$?"
    fi
}
run_logged_step() {
    local description="$1"
    shift
    spatial_current_step="$description"
    message "[RUN ] $description (details in the private build log)"
    if "$@" >> "$spatial_install_log" 2>&1; then
        passed "$description"
    else
        return "$?"
    fi
}
failure_diagnostics() {
    local report="$spatial_install_dir/failure-$(date -u +%Y%m%dT%H%M%S)-$$.json"
    if command -v spatial-diagnostics >/dev/null 2>&1; then
        spatial-diagnostics --logs --output "$report" >> "$spatial_install_log" 2>&1 && message "[INFO] Redacted failure report: $report"
    elif command -v python3 >/dev/null 2>&1; then
        python3 "$project_dir/tools/diagnostics.py" --logs --output "$report" >> "$spatial_install_log" 2>&1 && message "[INFO] Redacted failure report: $report"
    fi
}
on_error() {
    local result="$1"
    trap - ERR
    set +e
    if (( result == 130 )); then
        message '[STOP] Installation cancelled.'
    else
        message "[FAIL] $spatial_current_step (exit $result). Completed package transactions have not been rolled back; existing user configuration has been preserved."
        failure_diagnostics
    fi
    message "[INFO] Full private build log: $spatial_install_log"
    message '[INFO] Correct the reported error, then rerun the same command. No failed step was treated as success.'
    exit "$result"
}
trap 'on_error "$?"' ERR

pinned_aur_recipe() {
    # package commit exact-version PKGBUILD-sha256 .SRCINFO-sha256
    case "$1" in
        harletty-bridge)
            printf '%s\n' '29ea73c708454ca11b7c124a677ddf370f709c99 0.7.3-1 da65b4e5d898a5b761e60ff96006e2ac7722e81beb6156a217951a1c613c4392 e6b8b980e774d5bfa72e6437a845afafd888d38250c7f3dbdefbabb8d64a8b6b' ;;
        mpv-omniphony)
            printf '%s\n' 'f9e20fbbf55ca31da506ff770d1585bde11fbb89 0.5.2-1 951fd09dce6a0e385ccd65e42140a487dd092156e3b83b8b58eb1ef2d598c2f5 be02fda8ca8e7b74f3871584d977bde69b82383085975310ef36d3cf0d1b923c' ;;
        *) return 1 ;;
    esac
}
installed_package_version() {
    local package="$1" record
    record=$(pacman -Q "$package" 2>/dev/null) || return 1
    [[ "$record" == "$package "* ]] || return 1
    printf '%s\n' "${record#"$package "}"
}
preflight_audio_stack() {
    local package installed commit expected recipe_hash metadata_hash mismatch=0
    for package in harletty-bridge mpv-omniphony; do
        read -r commit expected recipe_hash metadata_hash < <(pinned_aur_recipe "$package")
        if installed=$(installed_package_version "$package") && [[ "$installed" != "$expected" ]]; then
            message "[FAIL] Installed $package $installed does not match the supported $expected package."
            mismatch=1
        fi
    done
    for package in orender orender-spatial; do
        if installed=$(installed_package_version "$package"); then
            # The known earlier downstream revisions can safely be upgraded.
            # A different upstream renderer cannot build our pinned mpv ABI.
            if [[ "$package" == orender && $(vercmp "$installed" 0.5.2) == 0 ]] || \
               [[ "$package" == orender-spatial && "$installed" =~ ^0\.5\.2-[123]$ ]]; then
                continue
            fi
            message "[FAIL] Installed $package $installed is outside the supported Omniphony 0.5.2 stack."
            mismatch=1
        fi
    done
    if (( mismatch )); then
        message '[INFO] No package transaction has started. Use a separate clean build environment or restore the compatible renderer/player/bridge group through reviewed pacman transactions before retrying.'
        message '[INFO] Required group: orender-spatial 0.5.2-3, mpv-omniphony 0.5.2-1, harletty-bridge 0.7.3-1. Newer versions cannot be mixed into this group.'
        return 1
    fi
}

message "[INFO] Full private build log: $spatial_install_log"
if (( with_audio )); then
    spatial_current_step='Check installed audio version compatibility'
    preflight_audio_stack
fi
if (( install_packages )); then
    if ! systemctl --user show-environment >/dev/null 2>&1; then
        message '[FAIL] No user systemd session. Run bash install.sh inside your normal KDE terminal.'
        exit 1
    fi
fi

# No separate -Sy: use the user's existing coherent package databases. Ordinary
# system updates remain under the user's package-management policy.
run_step 'Ensure official build tools and review editor' sudo pacman -S --needed base-devel python git nano
if (( with_tidal || with_audio )); then
    distro_modules=()
    if (( with_tidal )); then distro_modules+=(python-tidalapi); fi
    if (( with_audio )); then distro_modules+=(pipewire-audio swh-plugins); fi
    run_step 'Install official optional modules' sudo pacman -S --needed "${distro_modules[@]}"
fi
run_logged_step 'Generate complete checksummed package sources' python3 "$project_dir/tools/make-release.py"

package_files=()
build_package() {
    local build_dir="$1"
    local label="$2"
    # Resolve dependencies with visible pacman prompts before quiet compilation.
    # --nobuild still verifies/extracts sources and runs prepare(); --noextract
    # reuses that prepared tree without applying downstream patches twice.
    run_step "Prepare sources and dependencies: $label" bash -c \
        'cd -- "$1" && makepkg --syncdeps --needed --force --cleanbuild --nobuild --check' bash "$build_dir"
    run_logged_step "$label" bash -c \
        'cd -- "$1" && makepkg --force --noextract --check' bash "$build_dir"
    local package_file package_list
    package_list=$(cd "$build_dir" && makepkg --packagelist)
    if [[ -z "$package_list" ]]; then
        message "[FAIL] makepkg returned no package paths for $label"
        return 1
    fi
    while IFS= read -r package_file; do
        if [[ ! -f "$package_file" ]]; then
            message "[FAIL] Expected package missing: $package_file"
            return 1
        fi
        package_files+=("$package_file")
    done <<< "$package_list"
}
build_package "$project_dir/dist/arch/core" 'Build and test BudsLink Spatial Companion'
if (( with_companion )); then
    build_package "$project_dir/dist/arch/companion" 'Build the native BudsLink Spatial Companion widget'
fi
if (( with_audio )); then
    build_package "$project_dir/dist/arch/orender" 'Build and test the pinned renderer'
    build_package "$project_dir/dist/arch/sony" 'Build and test the pinned Sony tracker'
fi
for package_file in "${package_files[@]}"; do message "[INFO] Package: $package_file"; done
if (( ! install_packages )); then
    passed 'Requested packages built successfully'
    message '[INFO] Install the complete application with: bash install.sh'
    exit 0
fi

# Install the patched renderer before mpv, whose orender dependency it provides.
run_step 'Install locally built packages through pacman' sudo pacman -U "${package_files[@]}"

installed_aur_satisfies() {
    local package="$1" requirement="$2"
    # A generic virtual provide such as "mpv" is insufficient: the intended
    # package must actually be installed under its exact maintained name.
    pacman -Q "$package" >/dev/null 2>&1 && pacman -T "$requirement" >/dev/null 2>&1
}
build_aur_package() {
    local package="$1"
    local directory specification pinned_commit='' pinned_version='' recipe_hash metadata_hash
    directory=$(mktemp -d "$spatial_install_dir/aur-${package}.XXXXXXXX")
    if specification=$(pinned_aur_recipe "$package"); then
        read -r pinned_commit pinned_version recipe_hash metadata_hash <<< "$specification"
        run_logged_step "Fetch pinned AUR recipe: $package $pinned_version" git clone --no-checkout \
            "https://aur.archlinux.org/${package}.git" "$directory/source"
        run_logged_step "Select reviewed AUR commit: $package" git -C "$directory/source" checkout --detach "$pinned_commit"
        if [[ $(git -C "$directory/source" rev-parse HEAD) != "$pinned_commit" ]]; then
            message "[FAIL] AUR checkout does not match the reviewed commit for $package"
            return 1
        fi
        printf '%s  %s\n' "$recipe_hash" "$directory/source/PKGBUILD" \
            "$metadata_hash" "$directory/source/.SRCINFO" > "$directory/recipe.sha256"
        run_logged_step "Verify reviewed AUR recipe hashes: $package" sha256sum --check "$directory/recipe.sha256"
    else
        run_logged_step "Fetch maintained AUR recipe: $package" git clone --depth 1 --single-branch \
            "https://aur.archlinux.org/${package}.git" "$directory/source"
    fi
    if ! grep -Eq "^[[:space:]]*pkgname[[:space:]]*=[[:space:]]*${package}$" "$directory/source/.SRCINFO"; then
        message "[FAIL] Fetched AUR metadata does not declare the expected package: $package"
        return 1
    fi
    message "[INFO] AUR checkout: $directory/source"
    run_logged_step "Record AUR source identity: $package" git -C "$directory/source" log -1 --format='%H %s'
    local version release epoch requirement
    version=$(awk '$1 == "pkgver" && $2 == "=" {print $3; exit}' "$directory/source/.SRCINFO")
    release=$(awk '$1 == "pkgrel" && $2 == "=" {print $3; exit}' "$directory/source/.SRCINFO")
    epoch=$(awk '$1 == "epoch" && $2 == "=" {print $3; exit}' "$directory/source/.SRCINFO")
    if [[ -z "$version" || -z "$release" ]]; then
        message "[FAIL] AUR metadata does not declare a package version: $package"
        return 1
    fi
    version="${epoch:+${epoch}:}${version}-${release}"
    requirement="${package}>=$version"
    if [[ -n "$pinned_version" ]]; then
        if [[ "$version" != "$pinned_version" ]]; then
            message "[FAIL] Reviewed AUR recipe version for $package is not $pinned_version"
            return 1
        fi
        requirement="${package}=$pinned_version"
    fi
    if { [[ -n "$pinned_version" ]] && [[ $(installed_package_version "$package" || true) == "$pinned_version" ]]; } || \
       { [[ -z "$pinned_version" ]] && installed_aur_satisfies "$package" "$requirement"; }; then
        run_logged_step "Check installed package files: $package" pacman -Qk "$package"
        passed "Reuse installed $package satisfying $requirement"
        return 0
    fi
    cat -- "$directory/source/PKGBUILD" "$directory/source/.SRCINFO" | tee -a "$spatial_install_log"
    if [[ ! -t 0 ]]; then
        message '[FAIL] AUR recipe review requires a terminal. Run bash install.sh in your KDE terminal.'
        return 1
    fi
    local answer
    read -r -p "Build and install $package from the recipe above? [Y/n] " answer
    case "$answer" in
        ''|y|Y|yes|YES) ;;
        *) return 130 ;;
    esac
    local first_package=${#package_files[@]}
    build_package "$directory/source" "Build and test AUR package: $package"
    run_step "Install AUR package: $package" sudo pacman -U --needed "${package_files[@]:first_package}"
}
install_audio_modules() {
    message '[INFO] mpv-omniphony replaces stock mpv through the package manager; review its transaction.'
    message '[INFO] The renderer, player and decoder bridge use one tested version group. Pinned recipes are used even when an AUR helper is installed.'
    build_aur_package harletty-bridge
    build_aur_package mpv-omniphony
    # Sony's checksummed absolute-mode package was built and installed with the
    # local packages above. A helper must not replace it with the legacy recipe.
}
if (( with_audio )); then
    install_audio_modules
fi
run_step 'Reload tracker access rules' sudo udevadm control --reload-rules
run_step 'Apply tracker access to connected HID devices' sudo udevadm trigger --action=change --subsystem-match=hidraw
run_step 'Activate the targeted WirePlumber routing guard' systemctl --user try-restart wireplumber.service
if (( with_companion )); then
    companion_id=com.github.maniacx.BudsLink-Companion
    local_companion="${XDG_DATA_HOME:-$HOME/.local/share}/plasma/plasmoids/$companion_id"
    if [[ -e "$local_companion" || -L "$local_companion" ]]; then
        backup_base="${XDG_STATE_HOME:-$HOME/.local/state}/spatiald/plasma-backups"
        mkdir -p -- "$backup_base"
        backup_dir=$(mktemp -d "$backup_base/companion.XXXXXXXX")
        mv -- "$local_companion" "$backup_dir/$companion_id"
        message "[INFO] Previous user-installed Companion saved at: $backup_dir/$companion_id"
    fi
fi
run_logged_step 'Create or validate user configuration' spatialctl setup
run_logged_step 'Reload user services' systemctl --user daemon-reload
run_logged_step 'Enable BudsLink Spatial Companion for this desktop account' systemctl --user enable spatiald.service
run_logged_step 'Start BudsLink Spatial Companion' systemctl --user restart spatiald.service

spatial_current_step='Wait for the actual daemon control interface'
spatial_ready_deadline=$((SECONDS + 20))
while ! spatialctl status >/dev/null 2>> "$spatial_install_log"; do
    if (( SECONDS >= spatial_ready_deadline )); then
        message '[FAIL] The daemon did not establish its control interface within 20 seconds.'
        false
    fi
    sleep 0.5
done
passed 'Live daemon control interface is ready'

verification_report="$spatial_install_dir/verification-$(date -u +%Y%m%dT%H%M%S)-$$.json"
spatial_current_step='Verify the real host installation'
message '[RUN ] Verify the real host installation'
verification_arguments=(--output "$verification_report")
if (( ! with_audio )); then
    verification_arguments+=(--core-only)
    message '[INFO] Verifying the selected core installation; optional audio checks will be OFF. Run spatial-verify for full host verification.'
fi
if spatial-verify "${verification_arguments[@]}" 2>&1 | tee -a "$spatial_install_log"; then
    passed 'Host acceptance checks passed'
else
    verification_result=$?
    if (( verification_result == 2 )); then
        message '[WAIT] Packages and service are installed. Some hardware or playback checks still need real device evidence.'
    else
        message "[FAIL] Packages were installed, but host verification found errors. Review $verification_report"
        on_error "$verification_result"
    fi
fi
passed 'Package installation and service activation completed'
message "[INFO] Redacted verification report: $verification_report"
message "[INFO] Full private build log: $spatial_install_log"
if (( with_companion )); then
    message '[INFO] Sign out and back in once so Plasma loads the packaged Companion update.'
fi
message '[INFO] Connect the headphones normally. Use spatial-verify to repeat the real host checks.'
