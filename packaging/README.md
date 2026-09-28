# Arch package workflow

Use `./build.sh` to build both local packages, or
`bash install.sh` to install and activate the full
set of modules. See [installation](../docs/install.md) for prerequisites,
configuration, upstream package sources, rollback and diagnostics.

`PKGBUILD.in` builds a standard Python wheel with `python-build`, installs it with
`python-installer`, and adds the systemd user service and diagnostics executable.
The checks run under a private session bus with live D-Bus tests explicitly enabled.

`companion-PKGBUILD.in` builds the native Plasma integration from checksummed,
pinned upstream source plus `plasma/companion.patch`. It preserves upstream
licensing and author attribution. Generated recipes and complete core source are
under `dist/arch/`; `tools/make-release.py` refreshes all local checksums.

`orender-PKGBUILD.in` builds the pinned upstream CLI and matching shared library
with the included local-control patch. `--with-audio` builds and installs this
package before the AUR mpv transaction so its `orender=0.5.2` provision satisfies
that dependency. The upstream release archive omits its Rust lockfile, so the
package supplies the included checksummed `orender-Cargo.lock` for its `--locked`
build. The Rust bind-policy checks and binary markers are package gates.

The audio release set is Omniphony 0.5.2, Harletty 0.7.3 and mpv-omniphony 0.5.2.
`--with-audio` uses these reviewed AUR commits for the two decoder/player packages,
regardless of whether an AUR helper is installed:

| Package | Version | Reviewed AUR commit |
|---|---|---|
| `harletty-bridge` | 0.7.3-1 | [`29ea73c708454ca11b7c124a677ddf370f709c99`](https://aur.archlinux.org/cgit/aur.git/tree/?h=harletty-bridge&id=29ea73c708454ca11b7c124a677ddf370f709c99) |
| `mpv-omniphony` | 0.5.2-1 | [`f9e20fbbf55ca31da506ff770d1585bde11fbb89`](https://aur.archlinux.org/cgit/aur.git/tree/?h=mpv-omniphony&id=f9e20fbbf55ca31da506ff770d1585bde11fbb89) |

Their recipes retain checksum verification and ordinary user review. The mpv
recipe declares FFmpeg, libass and libplacebo SONAME dependencies, which make
pacman require a rebuild when those library ABIs change. Its `mpv=1:0.41.0`
provision includes the upstream mpv epoch; its own package version has no epoch.
Harletty's historical recipe lists Apache-2.0. Upstream's
[current bridge manifest](https://github.com/harletty/harletty-bridge/blob/v0.8.0/bridge/Cargo.toml)
clarifies that the bridge's source uses Apache-2.0 but its compiled library links
GPL-3.0-or-later Omniphony crates, so it declares the combined binary under the GPL.
The old recipe's Apache-only metadata does not describe that combined binary.

Do not replace either recipe with current AUR HEAD independently. Upstream
[Omniphony 0.6.0](https://github.com/mgth/Omniphony/releases/tag/v0.6.0) changes
`bridge_api` from 0.3 to 0.4, requires Harletty 0.8.0, and rejects the older bridge;
the newer bridge likewise cannot load in the 0.5.2 renderer. Current
mpv-omniphony 0.6.0 also requires `orender>=0.6.0`. A future upgrade must move the
renderer, its patches, the decoder and the native audio acceptance tests together.
These pins establish compatibility rather than reducing codec support.

The installer checks an existing audio set before any package transaction or
build. An incompatible installed bridge, player or newer renderer requires a
reviewed package-manager resolution; the installer never silently downgrades it.
Exact package reuse additionally runs `pacman -Qk`. `orender-spatial` revision 3
declares version conflicts for unsupported bridge and player packages, keeping
the compatibility boundary visible during later system upgrades without global
`IgnorePkg` settings. Update those bounds as part of any future release-set change.

For native compilation, prefer 16 GiB RAM or sufficient existing swap. A single
compiler process used about 8.8 GiB in the newer-decoder VM audit, alongside the
desktop and other builds, so limiting parallel jobs is not a complete memory
limit. The installer never configures host swap; the booted desktop test VM has
its own 12 GiB RAM and 8 GiB swap allocation.

The scripts never overwrite another package's files in place. A local Companion
copy that would shadow the packaged widget is moved to a recoverable user backup
only during an explicit `--install`. Existing user settings are retained.

The service runs as the desktop user and has no `Requires=` relationship to
BudsLink, PipeWire, trackers or renderer. Optional providers retry independently.
Arch clean-chroot and physical-desktop validation are reported separately from
the build recipe; inspect the release status for the actual evidence.
