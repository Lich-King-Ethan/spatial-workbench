# Install BudsLink Spatial Companion

From the extracted project directory in your normal KDE terminal:

```sh
bash install.sh
```

This command also works from fish: Bash interprets the installer while fish stays
your interactive shell. Do not `source install.sh` or run it with `fish install.sh`.
The installer includes nano and uses it for package-review editing when `EDITOR`
and `VISUAL` are unset. It preserves existing editor choices and does not change
your login shell or shell configuration. To select nano explicitly for this run:

```sh
env EDITOR=nano VISUAL=nano bash install.sh
```

Run this as your desktop user in a running KDE session. Your user needs normal
sudo access. The installer obtains missing official build tools, Python and Git
with `pacman -S --needed`, then lets makepkg resolve official package dependencies.
Keep Arch/CachyOS updated normally before installation; the script does not
perform a partial database-only update. An AUR helper is optional.

The installer builds the local pacman packages, installs upstream audio
packages, creates your user configuration, and enables `spatiald.service` for
your desktop session, then runs the real host acceptance checks automatically.
Standard package review and sudo prompts remain enabled. No system Python
packages are installed with pip.

Native source builds need substantial memory and free disk space. Prefer at least
16 GiB RAM, or enough already-configured swap for a lower-memory machine; close
other memory-heavy applications during compilation. Reducing build parallelism
does not cap the memory used by one compiler process: the September 28 VM audit
observed a single `rustc` use about 8.8 GiB while compiling the newer upstream
decoder. That measurement is not a minimum established for this release's pinned
decoder; the desktop and other build processes also need memory. The desktop test
VM uses 12 GiB RAM plus 8 GiB guest-only swap. The installer does not create swap
or change the host's memory configuration.

Your existing BudsLink installation remains the Sony-controls provider. The
built-in TIDAL client needs no separate music application; the full installer
includes `python-tidalapi`, and account sign-in remains an explicit user action.

Installation restarts the running WirePlumber user service once to load the
package's targeted live-audio routing guard; currently playing desktop audio can
pause briefly during that reload. The guard applies only to streams explicitly
redirected by the live module and prevents them falling through to another output
if its renderer disappears. It does not change the global default device. Live
application routing remains unavailable until the guard advertises readiness.

## Upgrading from Spatial Workbench

The core package is now `budslink-spatial-companion`; the widget package is
`plasma-budslink-spatial-companion`. They provide and conflict with the old package
names, so pacman handles the replacement through its normal transaction prompts.
They keep `spatialctl`, `spatiald.service`, D-Bus identifiers, configuration/state
directories and the existing Companion plugin ID. `budslink-spatial` is a new CLI
alias. Existing settings, saved tracker permissions and TIDAL sessions stay in place.

Do not manually rename configuration directories or remove the old package's
files. Build and inspect the new packages, then let pacman own their replacement.
The renamed packages and compatibility metadata pass local source/build checks;
an actual old-name upgrade transaction and physical acceptance remain separate
checks in [status](../STATUS.md).

## What the command installs

| Component | Source | Role |
|---|---|---|
| `budslink-spatial-companion` | This repository, built with makepkg | Daemon, CLI, diagnostics, user service |
| `plasma-budslink-spatial-companion` | Pinned upstream Companion plus included patch | Native tracking rows in your existing Companion |
| `python-dbus-next` | Official Arch repositories | Desktop D-Bus communication |
| `python-tidalapi` | Official Arch Extra | Built-in TIDAL client dependency; included by the full installer |
| `mpv-omniphony` | Reviewed AUR recipe pinned to 0.5.2-1 | Actual spatial playback; depends on `orender` |
| `orender-spatial` | Pinned Omniphony 0.5.2 plus included patches | Matching CLI/library with local OSC control and positioned 7.1 input |
| `harletty-bridge` | Reviewed AUR recipe pinned to 0.7.3-1 | Compatible format-decoding plugin |
| `sony-tracker-spatial` | Pinned SonyTrackerLinux 1.0.0 plus included patch | Version 1.0.0-2 or later; timestamped absolute Sony HID reports |
| `swh-plugins` | Official Arch Extra | Limiter for the optional headphone equalizer |

The AUR transaction for `mpv-omniphony` replaces stock `mpv` and supplies libmpv.
Review the package manager's transaction, including any applications that depend
on libmpv. The installer does not suppress conflict review or use `--overwrite`.

`orender-spatial` provides `orender=0.5.2` and is installed before the mpv AUR
transaction. A small included upstream patch adds an explicit loopback control
binding and a capability marker. A second patch declares the native 7.1 capture
positions so WirePlumber can preserve application channels; the renderer DSP
remains upstream. Both CLI and matching library are built together. The package runs
actual Rust bind-policy and serialized channel-format tests, and checks CLI/library markers during its
build. Its included checksummed lockfile fixes the Rust dependencies missing from
the upstream release archive. The runtime refuses an unpatched or mismatched engine, including a separate
Studio library, instead of starting network control on all interfaces. Compiling
this Rust package is the longest part of the installation.

The renderer, decoder bridge and player form one compatible release set:
Omniphony 0.5.2, Harletty 0.7.3 and mpv-omniphony 0.5.2. Their native audio route
has been tested together. The installer checks out the exact reviewed AUR commits
for the bridge and player and builds them with makepkg as your ordinary user,
including when yay or paru is installed. It records provenance and shows the
recipes for review; source checksums and package-manager prompts remain enabled.
The Sony helper is built from the checksummed local `sony-tracker-spatial`
recipe. It provides and conflicts with `sony-tracker` through normal pacman
prompts, retaining the same executable and legacy default behavior. The daemon
requires version 1.0.0-2 or later and its `--absolute --timestamped` modes;
an older helper cannot supply that contract. See [the helper's source and patch provenance](../packaging/sony-PROVENANCE.md).

This pairing matters: upstream Omniphony 0.6.0 changes the decoder bridge ABI from
0.3 to 0.4 and requires Harletty 0.8.0. A 0.8.0 bridge cannot load in this release's
0.5.2 renderer. The inspected mpv-omniphony 0.6.0 also requires `orender>=0.6.0`.
Upgrade the audio set together only after its renderer patches and complete audio
tests have been updated. The pin preserves the verified native decoding and
spatialization path; it does not remove a codec or substitute a test decoder.
Repeat installations require the exact supported bridge and player versions;
stock mpv does not stand in for mpv-omniphony.

An installed incompatible bridge, player or newer renderer stops the installer
before package transactions or compilation. It does not automatically downgrade
an existing newer audio stack. Resolve the displayed version conflict by choosing
a compatible set through a reviewed package-manager transaction, then rerun the
installer. Reuse of an exact supported package also requires `pacman -Qk` to find
its files intact. The renderer package declares version conflicts for unsupported
bridge and player packages so later package-manager upgrades cannot silently mix
the two ABI generations. The installer does not add global `IgnorePkg` rules.

Each successful stage is marked `PASS` only after its command finishes. Local and
direct AUR compilation/test output goes to the private log; source preparation,
package-manager prompts and AUR review remain visible. An existing AUR helper
retains its own interactive output. Full build output is retained in a file under
`~/.local/state/spatiald/install/` (or your `XDG_STATE_HOME`). On failure the
installer reports the failed stage and attempts to save a redacted diagnostic
report alongside the log. Existing user configuration is preserved. Completed
package transactions are not rolled back; for example, an accepted mpv replacement
can remain installed if a later stage fails. Correct the reported problem and
rerun the same command.

After activation, `spatial-verify` records the actual module and PipeWire checks.
Missing hardware or an idle player produces `WAIT`, without failing an otherwise
successful installation or claiming playback passed. A real verification error
returns failure and triggers diagnostic collection. The final output gives the
report and log locations.

## Smaller installs and build-only use

```sh
./build.sh                                      # build both local packages
./build.sh --install                            # install core + Companion only
./build.sh --install --core-only --with-audio    # retain your existing UI
```

Build-only mode still uses makepkg's ordinary `--syncdeps` prompts to obtain build
dependencies. It does not install the resulting application packages, start the
service, or edit your user configuration. Built packages and checksummed recipes
are under `dist/arch/core/` and `dist/arch/companion/`.
The additional renderer recipe is under `dist/arch/orender/`.

Without `--with-audio`, installation runs `spatial-verify --core-only`: configuration,
the running daemon and PipeWire must pass; optional audio checks explicitly report
`OFF`. Run `spatial-verify` without that flag for the full configured audio check.
This verification scope does not disable or reconfigure existing audio modules.

Core source archives are deterministic and include the actual build scripts,
tests, service, docs and configuration. Companion is pinned to
`31c6b3802071a6efc6fbd5a821e9c97f97240fd7` with a verified upstream archive checksum;
the patch and provenance notice have their own checksums. `makepkg` runs the test
suite under a private session bus, with live D-Bus tests explicitly enabled.

## First desktop session

The full installer preserves configuration already written to
`~/.config/spatiald/`. Existing user-local Companion files would shadow the new
pacman-owned widget, so the installer moves that user-local copy into a unique
backup directory under `~/.local/state/spatiald/plasma-backups/` and prints the
exact location. It retains the same plasmoid ID so panel placement survives.

Sign out and back in once after the first Companion install so Plasma loads the
new package. Connect the earbuds normally through KDE. The final output remains
the existing WF-1000XM5 device in System Settings → Sound. Waiting for BudsLink,
tracker discovery, or a renderer does not change the desktop's default output.

Tracker permissions use logind's `uaccess` for the active local desktop user,
with device mode 0660. Rules are limited to the documented SlimeVR USB ID families
and Sony Bluetooth HID devices; the Sony provider additionally checks the exact
connected address and sensor report descriptor before opening a tracker. The
installer reloads those rules and re-evaluates HID access. If a receiver was
connected before login, reconnect it once if diagnostics still shows it unreadable.

User service commands:

```sh
systemctl --user status spatiald.service
systemctl --user restart spatiald.service
journalctl --user -u spatiald.service -f
spatialctl doctor
spatialctl --help
```

The `--with-tidal` option installs the API module. Authenticate separately using
the TIDAL command shown by `spatialctl tidal --help`; login requires your own
account and approval at TIDAL's website. The provider validates returned format
information rather than treating a catalogue Atmos badge as proof of the stream.
See [TIDAL integration](tidal-runtime.md).

## Diagnostics

Run the actual host acceptance checks after installation:

```sh
spatial-verify
spatial-verify --media /path/to/known-atmos-sample.mka --require-spatial
```

The default command is read-only. It checks real configuration, installed decoder
features, the running daemon, fresh hardware telemetry and a fresh PipeWire graph.
The media command is an audible test using your actual file. It waits for loading
to finish, observes playback, checks real headphone links, and with
`--require-spatial` requires binaural engine readiness and decoded object counts.
An executable capability probe alone is never reported as an ABI playback pass.

Existing playback is preserved by default using an atomic idle check. Add
`--replace` only when you deliberately want the test to replace it. Cleanup uses
an atomic request-token check and cancels pending loading or stops only the
identified test player; a newer
user playback request is left alone. Replaced previous playback is not restored.
Use a sample longer than the default two-second observation interval.

Results are `PASS`, `FAIL`, `WAIT`, or `OFF`. Exit status is 0 for passed checks,
1 for errors, and 2 for incomplete hardware/playback evidence. Missing optional
hardware can leave checks waiting; this is not presented as verified operation.
Both verification and diagnostic reports are local, redacted JSON files with mode
0600. Set an explicit path with `--output ./acceptance.json`.

```sh
spatial-diagnostics
spatial-diagnostics --logs --output ./spatial-support.json
```

This gathers installed versions, service health, readable HID device information,
and the current PipeWire graph. It makes no routing changes and uploads nothing.
Reports use mode 0600. Known secrets, media URLs, Bluetooth addresses, home paths
and login identifiers are redacted; device identifiers use a random per-report
hash so relationships can be inspected within one report. Review a report before
sharing. Logs are included only with `--logs`; credential/configuration files are
never read.

## Removal and restoring your previous Companion

```sh
systemctl --user disable --now spatiald.service
sudo pacman -R budslink-spatial-companion plasma-budslink-spatial-companion
```

Your user configuration and saved Companion backup remain. To restore a previous
user-local Companion, move the exact backup path printed by the installer back to
`~/.local/share/plasma/plasmoids/com.github.maniacx.BudsLink-Companion`, then sign
out and back in. Remove optional upstream audio packages separately only if you
no longer need them; stock mpv can be restored through pacman.

## Verified packaging sources

- [Arch python-dbus-next](https://archlinux.org/packages/extra/any/python-dbus-next/)
- [Arch python-tidalapi](https://archlinux.org/packages/extra/any/python-tidalapi/)
- [mpv-omniphony upstream](https://github.com/mgth/mpv-omniphony)
- [Omniphony Arch recipes](https://github.com/mgth/Omniphony/tree/fc67346181f1c5908a1bc26968ff192eb09a531d/packaging/arch)
- [SonyTrackerLinux](https://github.com/kdani3/SonyTrackerLinux)
- [AUR package metadata](https://aur.archlinux.org/rpc/v5/info?arg%5B%5D=mpv-omniphony&arg%5B%5D=orender&arg%5B%5D=harletty-bridge&arg%5B%5D=sony-tracker)
- [Pinned mpv-omniphony AUR recipe](https://aur.archlinux.org/cgit/aur.git/tree/PKGBUILD?h=mpv-omniphony&id=f9e20fbbf55ca31da506ff770d1585bde11fbb89)
- [Pinned Harletty AUR recipe](https://aur.archlinux.org/cgit/aur.git/tree/PKGBUILD?h=harletty-bridge&id=29ea73c708454ca11b7c124a677ddf370f709c99)
- [Omniphony 0.6.0 ABI migration](https://github.com/mgth/Omniphony/releases/tag/v0.6.0)

Rechecked September 28, 2026 against the actual AUR Git repositories. The pinned
recipes above remain the supported release set even though AUR HEAD now offers
mpv-omniphony 0.6.0-1 and Harletty 0.8.0-1. Runtime capability and real playback
checks still apply; successful package installation alone is not audio evidence.
