#!/usr/bin/env bash
# Only for an ephemeral GitHub Actions container, never a desktop installer.
set -euo pipefail

if [[ ${GITHUB_ACTIONS:-} != true || $EUID -ne 0 ]]; then
    printf '%s\n' 'CachyOS preparation requires the disposable root CI container.' >&2
    exit 1
fi
: "${GITHUB_WORKSPACE:?GitHub workspace is required}"

# Pacman can return zero after refusing every hook when namespace creation is
# unavailable. Prove its isolation prerequisite before the first transaction;
# never set DisableSandboxNetwork to turn a refused hook into an unsafe one.
outer_netns=$(readlink /proc/self/ns/net)
isolated_netns=$(unshare --net readlink /proc/self/ns/net)
if [[ $outer_netns == "$isolated_netns" || -z $isolated_netns ]]; then
    printf '%s\n' 'Pacman hook network isolation is unavailable in this CI container.' >&2
    exit 1
fi
printf 'Pacman hook network namespace check: %s -> %s\n' "$outer_netns" "$isolated_netns"

# The official baseline image inherits Arch's os-release. Validate the actual
# signed package repositories and keyring instead of inventing an OS identifier.
pacman-conf --repo-list | grep -Fx cachyos
pacman -Q cachyos-keyring
pacman -Syu --noconfirm --needed \
    base-devel git python sudo dbus nodejs lua pipewire pipewire-audio \
    wireplumber swh-plugins python-numpy

useradd --create-home builder
printf '%s\n' 'builder ALL=(ALL) NOPASSWD: /usr/bin/pacman' > /etc/sudoers.d/spatial-build
chmod 0440 /etc/sudoers.d/spatial-build
visudo --check --file /etc/sudoers.d/spatial-build
chown -R builder:builder "$GITHUB_WORKSPACE"
