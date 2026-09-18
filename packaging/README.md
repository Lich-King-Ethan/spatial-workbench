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

The scripts never overwrite another package's files in place. A local Companion
copy that would shadow the packaged widget is moved to a recoverable user backup
only during an explicit `--install`. Existing user settings are retained.

The service runs as the desktop user and has no `Requires=` relationship to
BudsLink, PipeWire, trackers or renderer. Optional providers retry independently.
Arch clean-chroot and physical-desktop validation are reported separately from
the build recipe; inspect the release status for the actual evidence.
