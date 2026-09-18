#!/usr/bin/env bash
# Image assembly only. Installation under test runs later, after a genuine boot.
set -Eeuo pipefail
export LC_ALL=C
mkdir -p /ci-output
outer_network=$(readlink /proc/self/ns/net)
isolated_network=$(unshare --net readlink /proc/self/ns/net)
[[ "$outer_network" != "$isolated_network" ]]
printf 'Package-hook network namespace: %s -> %s\n' "$outer_network" "$isolated_network"
# Stable, readable English prompt transcripts; normal package review stays on.
sed -i '/^[[:space:]]*Color[[:space:]]*$/s/^/# CI transcript: /' /etc/pacman.conf
# Official systemd image-build mode: package hooks may install units and data,
# but must not try to start kernel/service units against Docker's PID 1.
# This environment applies only to this offline package transaction; it is
# neither persisted in the image nor set for the real booted installer below.
# https://github.com/systemd/systemd/blob/main/docs/ENVIRONMENT.md
SYSTEMD_OFFLINE=1 pacman -Syu --noconfirm --needed base-devel git python python-pexpect python-numpy sudo \
    systemd systemd-sysvcompat mkinitcpio linux-cachyos \
    pipewire pipewire-audio wireplumber dbus 2>&1 | tee /ci-output/bootstrap-pacman.log
# pacman can return success after a failed post-transaction hook. Missing
# depmod/systemd hooks would leave an incomplete guest, so fail at the cause.
if grep -Eq '^error: command failed to execute correctly|refusing to run ' /ci-output/bootstrap-pacman.log; then
    printf 'Package setup or a post-transaction hook failed; refusing to boot an incomplete image.\n' >&2
    exit 1
fi
pacman-conf --repo-list | grep -Fx cachyos
pacman -Q cachyos-keyring linux-cachyos systemd
useradd --create-home --uid 1000 --shell /bin/bash builder
printf '%s\n' 'builder ALL=(ALL) NOPASSWD: /usr/bin/pacman, /usr/bin/udevadm' > /etc/sudoers.d/spatial-ci
chmod 0440 /etc/sudoers.d/spatial-ci
visudo -cf /etc/sudoers.d/spatial-ci
mkdir -p /home/builder/spatial-workbench /ci-output /var/lib/systemd/linger
tar -xf /source.tar -C /home/builder/spatial-workbench
chown -R builder:builder /home/builder/spatial-workbench
rm /source.tar
touch /var/lib/systemd/linger/builder
printf 'LANG=C.UTF-8\n' > /etc/locale.conf
printf 'spatial-installer-ci\n' > /etc/hostname
printf '/dev/vda / ext4 defaults 0 1\n' > /etc/fstab
printf 'MAKEFLAGS="-j4"\n' >> /etc/makepkg.conf
mkdir -p /etc/systemd/network
cat > /etc/systemd/network/20-ci.network <<'EOF'
[Match]
Type=ether
[Network]
DHCP=yes
EOF
systemctl unmask systemd-udevd.service systemd-udevd-control.socket systemd-udevd-kernel.socket \
    systemd-networkd.service systemd-resolved.service systemd-logind.service
systemctl enable systemd-networkd.service systemd-networkd-wait-online.service systemd-resolved.service
cat > /etc/systemd/system/spatial-installer-ci.service <<'EOF'
[Unit]
Description=Exercise the unchanged full installer in a booted CachyOS VM
Wants=network-online.target
Requires=user@1000.service
After=network-online.target systemd-udev-trigger.service user@1000.service
[Service]
Type=oneshot
ExecStart=/usr/bin/bash /home/builder/spatial-workbench/tools/ci/full-installer-guest.sh
TimeoutStartSec=70min
StandardOutput=journal+console
StandardError=journal+console
[Install]
WantedBy=multi-user.target
EOF
systemctl enable spatial-installer-ci.service
# Docker's kernel is irrelevant. Build an initramfs for the installed CachyOS
# kernel, with its virtio disk modules explicitly available at early boot.
cat > /ci-mkinitcpio.conf <<'EOF'
MODULES=(virtio_pci virtio_blk virtio_net ext4)
BINARIES=()
FILES=()
HOOKS=(base systemd modconf block filesystems)
EOF
mapfile -t kernel_images < <(find /usr/lib/modules -mindepth 2 -maxdepth 2 -name vmlinuz -type f)
[[ ${#kernel_images[@]} == 1 ]]
kernel_version=$(basename "$(dirname "${kernel_images[0]}")")
[[ -s /usr/lib/modules/"$kernel_version"/modules.dep.bin ]]
modinfo -k "$kernel_version" virtio_blk
cp "${kernel_images[0]}" /boot/ci-vmlinuz
mkinitcpio -k "$kernel_version" -c /ci-mkinitcpio.conf -g /boot/ci-initramfs.img
pacman -Q > /ci-output/bootstrap-packages.txt
pacman-conf > /ci-output/pacman-configuration.txt
printf '%s\n' "$kernel_version" > /ci-output/kernel-version.txt
# Keep signature verification; remove only downloaded packages, not databases.
pacman -Scc --noconfirm
rm -f /etc/machine-id /var/lib/dbus/machine-id
touch /etc/machine-id
