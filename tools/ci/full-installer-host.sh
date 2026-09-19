#!/usr/bin/env bash
# Runs only on a disposable GitHub-hosted runner. No host credentials enter the VM.
set -Eeuo pipefail
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted ]] || {
    printf 'This provisioning script requires a disposable GitHub-hosted runner.\n' >&2
    exit 1
}
project_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
evidence_dir="$project_dir/installer-evidence"
mkdir -p "$evidence_dir"
exec > >(tee "$evidence_dir/host.log") 2>&1
vm_dir=$(mktemp -d "$RUNNER_TEMP/spatial-installer.XXXXXXXX")
vm_root="$vm_dir/root"
container_name="spatial-installer-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
mounted=0
qemu_pid=''

finish() {
    local result=$?
    trap - EXIT
    set +e
    if [[ -n "$qemu_pid" ]]; then
        sudo kill "$qemu_pid" 2>/dev/null
        wait "$qemu_pid" 2>/dev/null
    fi
    if (( mounted )); then sudo umount "$vm_root"; fi
    docker rm -f "$container_name" >/dev/null 2>&1
    sudo chown -R "$(id -u):$(id -g)" "$evidence_dir"
    printf '\nHost result: %s\n' "$result"
    exit "$result"
}
trap finish EXIT

printf 'Source commit: %s\nOfficial userspace image: %s\n' "$GITHUB_SHA" "$CACHYOS_IMAGE"
lscpu
free -h
df -h "$RUNNER_TEMP"
if [[ ! -c /dev/kvm ]]; then
    printf 'ENVIRONMENT UNAVAILABLE: this runner exposes no /dev/kvm. A real booted VM is required.\n' >&2
    exit 1
fi
# Check the actual KVM API, without substituting slow software emulation or
# passing a userspace-only container off as a booted installation.
sudo python3 - <<'PY'
import fcntl
import os
fd = os.open('/dev/kvm', os.O_RDWR | os.O_CLOEXEC)
try:
    assert fcntl.ioctl(fd, 0xAE00) == 12, 'Unsupported KVM API version'
finally:
    os.close(fd)
PY
(( $(awk '/MemTotal:/ {print $2}' /proc/meminfo) >= 14000000 )) || {
    printf 'ENVIRONMENT UNAVAILABLE: this job requires the public 16GB runner.\n' >&2
    exit 1
}
required_kib=$((22 * 1024 * 1024))
available_kib=$(df --output=avail -k "$RUNNER_TEMP" | tail -n1 | tr -d ' ')
if (( available_kib < required_kib )); then
    # These unrelated SDKs are preinstalled on this disposable runner and are
    # never used by this job. Do not prune Docker globally or touch the checkout.
    printf 'Reclaiming unused runner Android SDK and GHC installations.\n'
    sudo rm -rf -- /usr/local/lib/android /opt/ghc
    available_kib=$(df --output=avail -k "$RUNNER_TEMP" | tail -n1 | tr -d ' ')
fi
printf 'VM disk preflight: available=%s KiB; required=%s KiB\n' "$available_kib" "$required_kib"
(( available_kib >= required_kib )) || {
    printf 'ENVIRONMENT UNAVAILABLE: insufficient disk space for native dependencies and builds.\n' >&2
    exit 1
}
sudo apt-get update
sudo apt-get install --no-install-recommends -y qemu-system-x86 e2fsprogs
docker pull "$CACHYOS_IMAGE"
docker image inspect "$CACHYOS_IMAGE" --format '{{json .RepoDigests}}' > "$evidence_dir/image-digests.json"
git -C "$project_dir" archive --format=tar HEAD > "$vm_dir/source.tar"
# pacman isolates installation hooks with a network namespace. Give only this
# image-assembly container the capability needed to create that namespace;
# retain Docker's default seccomp/AppArmor policy and pacman's own sandbox.
docker run --detach --cap-add=SYS_ADMIN --name "$container_name" "$CACHYOS_IMAGE" sleep infinity
docker cp "$vm_dir/source.tar" "$container_name:/source.tar"
docker cp "$project_dir/tools/ci/full-installer-prepare.sh" "$container_name:/prepare.sh"
docker exec "$container_name" bash /prepare.sh
docker stop "$container_name"

# Export directly into the guest disk, avoiding a second unpacked rootfs.
truncate -s 18G "$vm_dir/root.raw"
mkfs.ext4 -F -L spatial-ci "$vm_dir/root.raw"
mkdir "$vm_root"
sudo mount -o loop "$vm_dir/root.raw" "$vm_root"
mounted=1
docker export "$container_name" | sudo tar -xpf - -C "$vm_root"
sudo cp "$vm_root/boot/ci-vmlinuz" "$vm_dir/vmlinuz"
sudo cp "$vm_root/boot/ci-initramfs.img" "$vm_dir/initramfs.img"
# Container runtime metadata must not identify the booted system as Docker.
sudo rm -f "$vm_root/.dockerenv" "$vm_root/run/.containerenv"
# /etc/resolv.conf is a Docker bind mount during image assembly; configure the
# guest's own resolver only after export into its independent filesystem.
sudo ln -sf /run/systemd/resolve/stub-resolv.conf "$vm_root/etc/resolv.conf"
sudo umount "$vm_root"
mounted=0
docker rm "$container_name"

printf 'Booting 4 vCPU / 10GiB minimal CachyOS guest with its own kernel and udev.\n'
: > "$evidence_dir/serial.log"
# NAT supplies outbound package/source downloads. There are no forwarded ports,
# host filesystem shares, SSH credentials, Bluetooth devices or audio hardware.
sudo timeout --signal=TERM --kill-after=20s 75m qemu-system-x86_64 \
    -machine q35,accel=kvm -cpu host -smp 4 -m 10G \
    -kernel "$vm_dir/vmlinuz" -initrd "$vm_dir/initramfs.img" \
    -append 'root=/dev/vda rw console=ttyS0 systemd.unit=multi-user.target' \
    -drive "file=$vm_dir/root.raw,format=raw,if=virtio,cache=writeback" \
    -netdev user,id=network -device virtio-net-pci,netdev=network \
    -display none -monitor none -serial stdio -no-reboot \
    > "$evidence_dir/serial.log" 2>&1 &
qemu_pid=$!
# Follow serial output while retaining the bounded VM process's actual status.
tail --pid="$qemu_pid" -n +1 -f "$evidence_dir/serial.log" &
tail_pid=$!
vm_result=0
wait "$qemu_pid" || vm_result=$?
qemu_pid=''
wait "$tail_pid" || true

# Also recover partial diagnostics after timeout/crash without journal replay.
sudo mount -o loop,ro,noload "$vm_dir/root.raw" "$vm_root"
mounted=1
if [[ -d "$vm_root/ci-output" ]]; then
    sudo cp -a "$vm_root/ci-output/." "$evidence_dir/"
fi
if [[ -d "$vm_root/home/builder/.local/state/spatiald/install" ]]; then
    sudo find "$vm_root/home/builder/.local/state/spatiald/install" -maxdepth 1 -type f \
        \( -name '*.log' -o -name '*.json' \) -exec cp -t "$evidence_dir/" -- {} +
fi
sudo umount "$vm_root"
mounted=0
sudo chown -R "$(id -u):$(id -g)" "$evidence_dir"
if (( vm_result )); then
    printf 'VM failed or timed out (exit %s); inspect serial.log and guest evidence.\n' "$vm_result" >&2
    exit "$vm_result"
fi
[[ -f "$evidence_dir/result" ]] || {
    printf 'The guest stopped without completing the installer test.\n' >&2
    exit 1
}
[[ $(cat "$evidence_dir/result") == 0 ]] || {
    printf 'The actual installer or its acceptance checks failed.\n' >&2
    exit 1
}
printf 'PASS: full installer completed in the booted VM; physical headphone checks remain WAIT.\n'
cat "$evidence_dir/acceptance.json"
