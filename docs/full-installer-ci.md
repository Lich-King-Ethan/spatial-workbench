# Full installer integration test

The manually dispatched **Full installer in a CachyOS VM** GitHub Actions workflow
runs the unchanged `bash install.sh` as an ordinary user in a newly booted VM.
The guest has its own CachyOS kernel, systemd, udev, user manager, D-Bus and
PipeWire/WirePlumber services. All local packages and the actual maintained AUR
recipes are built and installed through the normal installer. The terminal
driver answers only the expected package-manager and AUR review prompts and
records the displayed recipes and actual upstream commit identities.

The guest is a minimal system assembled from the pinned official
[`cachyos/cachyos` userspace image](https://github.com/CachyOS/docker) and current
signed CachyOS/Arch packages. It is not the CachyOS desktop ISO or a graphical KDE
session. Installation of the native Companion package is exercised; interactive
widget behavior, Bluetooth, HID permissions on a physical receiver and headphone
playback still require the desktop and actual devices. The acceptance artifact
requires those absent hardware/playback checks to remain `WAIT`.

After installation, the job also decodes a checksummed upstream E-AC-3 JOC
fixture through the installed genuine decoder bridge and renderer, requiring
decoded objects, non-silent stereo PCM and a binaural response to head rotation.
The separate private PipeWire gate exercises the real installed player,
renderer and equalizer with generated head poses and simulated audio endpoints.
Its captured audio and metrics are software integration evidence, not proof of
physical earbud playback.

The job uses a standard public `ubuntu-24.04` runner and requires usable KVM,
16GB host RAM and at least 22GiB free disk before provisioning. It gives the guest
4 virtual CPUs, 10GiB RAM and an 18GiB disk, with a 90-minute workflow limit.
The preflight can remove only the unused preinstalled Android SDK and GHC from
the disposable runner to recover disk space. When the runner cannot meet the
requirements the job fails explicitly as an unavailable test environment; it
does not claim installation passed. Hardware acceleration is checked at runtime
because GitHub does not guarantee arbitrary nested-VM configurations.

Only the checked-out Git commit enters the guest; repository credentials,
secrets, host files and physical devices are not shared. QEMU uses outbound NAT
with no forwarded ports. Serial boot output, exact package versions, package
configuration, journals, installer logs and acceptance JSON are retained as a
workflow artifact on success or failure. The VM disk is temporary.

The temporary image-assembly container receives `CAP_SYS_ADMIN` so pacman can
create the network namespaces that isolate its installation hooks. Docker's
default seccomp/AppArmor policies and pacman's sandbox stay enabled. Assembly
checks namespace creation and fails on any rejected package hook; it never
disables package sandboxing or ignores a failed `depmod`/initramfs operation.
Only the offline bootstrap transaction uses systemd's documented
`SYSTEMD_OFFLINE=1` image-build mode, so hooks install their files without trying
to start services against Docker's PID 1. The booted guest requires normal live
systemd operation and runs the actual installer without that environment.

This complements the faster Arch packaging and CachyOS userspace checks. A
successful VM job proves the real installer reaches service activation and
passes its available software checks. It does not establish real headphone
audio or Atmos rendering.
