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
    pipewire pipewire-audio pipewire-pulse wireplumber rtkit dbus \
    plasma-meta sddm konsole dolphin spectacle mesa xorg-xwayland ttf-dejavu \
    fish nano python-pillow 2>&1 | tee /ci-output/bootstrap-pacman.log
# pacman can return success after a failed post-transaction hook. Missing
# depmod/systemd hooks would leave an incomplete guest, so fail at the cause.
if grep -Eq '^error: command failed to execute correctly|refusing to run ' /ci-output/bootstrap-pacman.log; then
    printf 'Package setup or a post-transaction hook failed; refusing to boot an incomplete image.\n' >&2
    exit 1
fi
pacman-conf --repo-list | grep -Fx cachyos
pacman -Q cachyos-keyring linux-cachyos systemd
for desktop_command in fish nano spectacle konsole plasmawindowed kscreen-doctor; do
    command -v "$desktop_command"
done
grep -Fx /usr/bin/fish /etc/shells
# Keep password authentication locked. SDDM's packaged autologin PAM stack
# authenticates via pam_permit; pam_unix account management checks expiry, not
# the locked password marker. Do not clear the password or modify PAM policy.
useradd --create-home --uid 1000 --shell /usr/bin/fish builder
passwd --status builder
mkdir -p /ci-output/desktop-pam
cp /etc/pam.d/sddm-autologin /etc/pam.d/system-local-login \
    /etc/pam.d/system-login /etc/pam.d/system-auth /ci-output/desktop-pam/
printf '%s\n' 'builder ALL=(ALL) NOPASSWD: /usr/bin/pacman, /usr/bin/udevadm' > /etc/sudoers.d/spatial-ci
chmod 0440 /etc/sudoers.d/spatial-ci
visudo -cf /etc/sudoers.d/spatial-ci
mkdir -p /home/builder/spatial-workbench /ci-output /var/lib/systemd/linger
tar -xf /source.tar -C /home/builder/spatial-workbench
chown -R builder:builder /home/builder/spatial-workbench
rm /source.tar
touch /var/lib/systemd/linger/builder
# These are defaults for the disposable guest only; the application installer
# must not change a real user's shell, editor or desktop preferences.
install -d /etc/environment.d /etc/fish/conf.d /etc/sddm.conf.d
cat > /etc/environment.d/90-spatial-ci.conf <<'EOF'
EDITOR=nano
VISUAL=nano
LIBGL_ALWAYS_SOFTWARE=1
QT_QUICK_BACKEND=software
EOF
cat /etc/environment.d/90-spatial-ci.conf >> /etc/environment
cat > /etc/fish/conf.d/spatial-ci.fish <<'EOF'
set -gx EDITOR nano
set -gx VISUAL nano
EOF
# Select the installed Wayland session explicitly; do not silently substitute
# an X11, nested, offscreen or headless Plasma session if startup fails.
test -f /usr/share/wayland-sessions/plasma.desktop
cat > /etc/sddm.conf.d/spatial-ci.conf <<'EOF'
[General]
DisplayServer=wayland
[Wayland]
CompositorCommand=kwin_wayland --drm --no-lockscreen --no-global-shortcuts --locale1
[Autologin]
User=builder
Session=plasma.desktop
Relogin=false
EOF
install -d -o builder -g builder /home/builder/.config
# Plasma Welcome is launched by its KDED module, not an XDG autostart entry.
# Record the installed release as already seen in this unattended VM so its
# first-login tour cannot cover the Companion screenshot. Keep it installed.
# https://github.com/KDE/plasma-welcome/blob/v6.7.5/src/kded/daemon.cpp
plasma_welcome_version=$(pacman -Q plasma-welcome | awk '{print $2}')
plasma_welcome_version=${plasma_welcome_version%-*}
plasma_welcome_version=${plasma_welcome_version#*:}
[[ "$plasma_welcome_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
cat > /home/builder/.config/plasma-welcomerc <<EOF
[General]
LastSeenVersion=$plasma_welcome_version
ShowUpdatePage=false
LiveEnvironment=false
EOF
cat > /home/builder/.config/kscreenlockerrc <<'EOF'
[Daemon]
Autolock=false
LockOnResume=false
EOF
# Keep the virtual monitor awake throughout long native builds. PowerDevil is
# still installed; its idle actions are disabled only in this disposable VM.
cat > /home/builder/.config/powerdevilrc <<'EOF'
[AC][Display]
TurnOffDisplayWhenIdle=false
TurnOffDisplayIdleTimeoutSec=-1
DimDisplayWhenIdle=false
DimDisplayIdleTimeoutSec=-1
[AC][SuspendAndShutdown]
AutoSuspendAction=0
EOF
chown builder:builder /home/builder/.config/plasma-welcomerc \
    /home/builder/.config/kscreenlockerrc /home/builder/.config/powerdevilrc
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
systemctl enable sddm.service
systemctl set-default graphical.target
cat > /etc/systemd/system/spatial-installer-ci.service <<'EOF'
[Unit]
Description=Exercise the full installer from fish in a booted CachyOS Plasma VM
Wants=network-online.target
Requires=user@1000.service
After=network-online.target systemd-udev-trigger.service user@1000.service sddm.service
[Service]
Type=oneshot
ExecStart=/usr/bin/bash /home/builder/spatial-workbench/tools/ci/full-installer-guest.sh
TimeoutStartSec=90min
StandardOutput=journal+console
StandardError=journal+console
[Install]
WantedBy=graphical.target
EOF
systemctl enable spatial-installer-ci.service
# Docker's kernel is irrelevant. Build an initramfs for the installed CachyOS
# kernel, with its virtio disk modules explicitly available at early boot.
cat > /ci-mkinitcpio.conf <<'EOF'
MODULES=(virtio_pci virtio_blk virtio_net virtio_gpu ext4)
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
