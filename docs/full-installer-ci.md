# Full installer integration test

The manually dispatched **Full installer in a CachyOS VM** GitHub Actions workflow
runs `bash install.sh` from a login fish shell as an ordinary user in a newly
booted VM. The application's Bash scripts keep their declared interpreter;
fish is the desktop account's actual default shell.
The guest has its own CachyOS kernel, systemd, udev, user manager, D-Bus and
PipeWire/WirePlumber services. All local packages and the actual maintained AUR
recipes are built and installed through the normal installer. The terminal
driver answers only the expected package-manager and AUR review prompts and
records the displayed recipes and actual upstream commit identities.

The guest is assembled from the pinned official
[`cachyos/cachyos` userspace image](https://github.com/CachyOS/docker) and current
signed CachyOS/Arch packages, including the complete `plasma-meta` desktop,
SDDM, Konsole, Dolphin, Spectacle, Mesa, RTKit, fish and nano. It boots to a real SDDM-autologin
**Plasma Wayland session** on a virtual DRM display. Mesa software rendering is
used because this runner has no passed-through GPU. This is a booted desktop
system assembled from official packages, not a CachyOS ISO/Calamares installation
or a physical GPU/driver test.

Desktop acceptance requires an active local Wayland session on `seat0`, the
desktop user's real KWin and plasmashell D-Bus owners, an existing Wayland
socket, and a connected kernel DRM output. It does not substitute an offscreen
Qt process, nested compositor or private D-Bus session. Before and after the
installer, the guest opens actual Konsole windows, edits and saves a file with
nano launched from login fish, and records Spectacle screenshots. After the
package installation it also opens the installed Companion with
`plasmawindowed`; premature exit or QML load/runtime errors fail the gate.
The real session's AT-SPI accessibility tree must belong to that exact client
process and contain visible, nonzero-size disconnected-state labels inside its
window. Service and module text are checked against the actual daemon snapshot;
a blank applet cannot pass because the desktop background is visible. The gate
invokes the enabled **Refresh status** button's exported press action and checks
the content again. This establishes actual control invocation; the separate
native Qt test verifies the handler's D-Bus refresh behavior. Screenshots,
before/after accessibility content, per-window logs, session details and KWin
support information are retained under `desktop/`. Device controls still have
separate tests and require real compatible devices for hardware acceptance.

`EDITOR` and `VISUAL` are set to nano in the disposable VM only. The product
installer does not change the user's existing shell or preferred editor.
Screen locking and idle display power-off are disabled in this unattended
guest. The installed Plasma Welcome release is marked as already seen using
its normal `plasma-welcomerc` setting so the first-login tour cannot obscure
application screenshots; the Welcome Center remains installed. RTKit uses its
normal packaged service and policy; its D-Bus availability and the guest's
initial thread scheduling are retained without suppressing fallback warnings
or granting extra application privileges. No passwords, host credentials or
physical devices are introduced.
Bluetooth, HID permissions on a physical receiver and headphone playback still
require actual devices. The acceptance artifact requires those absent hardware
checks to remain `WAIT`.

The full installation is followed by a real core-only reinstall, verifying
package ownership, original user configuration content and permissions, the
single Companion backup, and running services. Desktop verification runs again
after both installations.

The dispatch input `validation_scope` defaults to `full`. An explicit `desktop`
precheck uses the same genuine booted Plasma system and login-fish entry point
but runs `bash build.sh --install` to build and install only the core and
Companion packages. It runs the desktop, nano, service and installed-window
content checks and writes `desktop-acceptance.json`. That report explicitly
marks the full installer, repeat installation and audio validation `not_run`;
it is not a substitute for a successful full run. Both host and guest validate
the scope, and `validation-scope.txt` preserves the selection in the artifact.
This shorter mode is intended to catch desktop integration defects before
rebuilding the native audio stack.

After installation, the job also decodes a checksummed upstream E-AC-3 JOC
fixture through the installed genuine decoder bridge and renderer, requiring
decoded objects, non-silent stereo PCM and a binaural response to head rotation.
The separate private PipeWire gate exercises the real installed player,
renderer and equalizer with generated head poses and simulated audio endpoints.
Its captured audio and metrics are software integration evidence, not proof of
physical earbud playback.

The job uses a standard public `ubuntu-24.04` runner and requires usable KVM,
16GB host RAM and at least 45GiB free disk before provisioning. It gives the guest
4 virtual CPUs, 12GiB RAM and a 36GiB ext4 disk, with a 120-minute workflow limit
(95 minutes for the VM, 90 minutes for its test service).
The booted guest creates and verifies an 8GiB swapfile inside its own filesystem
before building packages. Host swap, limits and compiler optimization flags are
not changed. A measured `harletty-bridge 0.8.0` build exhausted the previous
10GiB guest: its `truehd 0.7.2` compiler process alone reached 9,232,508KiB
resident memory (about 8.8GiB), in addition to the desktop and other processes.
Reducing build concurrency cannot by itself bound a single compiler process.
Memory, active swap, disk use and largest processes are captured at boot,
before building, after installation/reinstallation/audio tests, and on failure.
The preflight can remove only the unused preinstalled Android SDK, GHC, .NET and PowerShell from
the disposable runner to recover disk space. When the runner cannot meet the
requirements the job fails explicitly as an unavailable test environment; it
does not claim installation passed. Hardware acceleration is checked at runtime
because GitHub does not guarantee arbitrary nested-VM configurations.

Only the checked-out Git commit enters the guest; repository credentials,
secrets, host files and physical devices are not shared. QEMU uses outbound NAT
with no forwarded ports. Serial boot output, exact package versions, package
configuration, journals, installer logs, desktop screenshots and acceptance
JSON are retained as a workflow artifact on success or failure. The VM disk is
temporary. Network setup uses systemd-networkd and systemd-resolved; desktop
installation does not replace these with competing network managers.

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
passes its available software checks in the actual Plasma session. Encoded
fixture and generated-pose tests establish only the tested software routes;
they do not establish physical headphone playback or perceptual Atmos accuracy.
