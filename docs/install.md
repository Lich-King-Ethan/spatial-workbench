# Install on Arch Linux or CachyOS

From the extracted project directory in your normal KDE terminal:

```sh
bash install.sh
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

Your existing BudsLink installation remains the Sony-controls provider. You have
already confirmed it works with your WF-1000XM5. This project neither replaces
the BudsLink backend nor signs into your TIDAL account for you.

Installation restarts the running WirePlumber user service once to load the
package's targeted live-audio routing guard; currently playing desktop audio can
pause briefly during that reload. The guard applies only to streams explicitly
redirected by the live module and prevents them falling through to another output
if its renderer disappears. It does not change the global default device. Live
application routing remains unavailable until the guard advertises readiness.

## What the command installs

| Component | Source | Role |
|---|---|---|
| `spatial-workbench` | This repository, built with makepkg | Daemon, CLI, diagnostics, user service |
| `plasma-budslink-companion-spatial` | Pinned upstream Companion plus included patch | Native tracking rows in your existing Companion |
| `python-dbus-next` | Official Arch repositories | Desktop D-Bus communication |
| `python-tidalapi` | Official Arch Extra | Optional TIDAL account and stream API |
| `mpv-omniphony` | AUR, through yay/paru or reviewed makepkg | Actual spatial playback; depends on `orender` |
| `orender-spatial` | Pinned Omniphony 0.5.2 plus included patches | Matching CLI/library with local OSC control and positioned 7.1 input |
| `harletty-bridge` | AUR, through yay/paru or reviewed makepkg | Renderer format-decoding plugin |
| `sony-tracker` | AUR, through yay/paru or reviewed makepkg | Sony HID orientation helper |
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

If yay or paru is available, the installer uses its normal review workflow.
Otherwise it clones each actual AUR repository, records the commit, shows the
recipe for review, and builds it with makepkg as your ordinary user. Dependencies
are installed in order: the patched renderer, format bridge, player and Sony
tracker. AUR source builds require accepting the displayed review prompt.
Repeat installs reuse an AUR package only when its exact package name is installed
and pacman confirms its version satisfies the fetched recipe. Stock mpv does not
stand in for mpv-omniphony.

Each successful stage is marked `PASS` only after its command finishes. Local and
direct AUR compilation/test output goes to the private log; source preparation,
package-manager prompts and AUR review remain visible. An existing AUR helper
retains its own interactive output. Full build output is retained in a file under
`~/.local/state/spatiald/install/` (or your `XDG_STATE_HOME`). On failure the
installer reports the failed stage and attempts to save a redacted diagnostic
report alongside the log. Existing packages and configuration are retained;
correct the reported problem and rerun the same command.

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
sudo pacman -R spatial-workbench plasma-budslink-companion-spatial
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

Inspected September 18, 2026. The AUR metadata reported mpv-omniphony and orender
0.5.2-1, harletty-bridge 0.7.3-1, and sony-tracker 1.0.0-1. The helper resolves
current package revisions when you install; runtime capability checks still apply.
