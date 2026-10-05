# Contributing to BudsLink Spatial Companion

Keep changes scoped to one provider or interface where possible. Hardware
discovery, routing, decoding, tracking, account access, and Companion UI remain
independent modules. Reuse maintained upstream tools and preserve the existing
physical headphone endpoint. Include the relevant regression test when changing
connection recovery, routing ownership, tracker selection, or credential handling.

## Run the tests

Use Python 3.11 or later in a virtual environment. The complete suite needs a
private session D-Bus, Node.js, a Lua shared library, a C compiler, and the SWH
LADSPA limiter. On Arch, install the dependencies and run the checks below.
These commands work in fish and Bash without activating a shell-specific script:

```sh
sudo pacman -S --needed python python-pip base-devel dbus nodejs lua swh-plugins git
python -m venv .venv
.venv/bin/python -m pip install -e '.[tidal]' build numpy
env SPATIAL_TEST_DBUS=1 dbus-run-session -- .venv/bin/python -m unittest discover -s tests -v
luac -p wireplumber/scripts/spatial-live-guard.lua
.venv/bin/python -m build
```

The D-Bus tests must use a private bus, not the active desktop session. The Lua,
JavaScript, native-library, and private-bus tests exercise real local runtimes.
They do not validate a Bluetooth connection, a Plasma popup, or acoustic output.
Read `STATUS.md` and the module documentation for outstanding hardware checks.

Generate package sources with `.venv/bin/python tools/make-release.py`. On Arch, build
inside each generated `dist/arch/core` and `dist/arch/companion` directory using
`makepkg --syncdeps --cleanbuild`. Run makepkg as an ordinary user. The separate
`dist/arch/orender` recipe compiles the pinned renderer and runs its Rust tests
and binary/library marker checks; this build takes longer. The Companion's native
UI gate needs Plasma 6.7 or later, Kirigami Platform and `extra-cmake-modules`,
listed in its package recipe. It initializes the actual Plasma theme and checks
control contrast; a plain offscreen Qt palette is not representative of Plasma.

## Report a reproducible problem

Include the commit or package version, Arch/CachyOS and Plasma versions, the
component affected, exact reproduction steps, expected behavior, and observed
behavior. State whether the source was ordinary PCM/stereo or whether decoded
spatial objects were actually confirmed. For reconnect problems, include which
device or service disconnected and the order in which it returned.

Create a local report with the installed `spatial-diagnostics` command, or
`python tools/diagnostics.py` from the checkout. Add `--logs` only when recent
service logs help reproduce the failure. Reports redact known credentials,
identifiers, addresses, URLs, and home paths, but review the file before sharing.
Include only the relevant reviewed sections in an issue. Never attach a TIDAL
session file, authorization code, access/refresh token, signed media URL, full
home-directory archive, or private audio file.

`spatial-verify --media /path/to/sample` performs an audible local acceptance
check. It preserves existing playback by default; `--replace` explicitly allows
replacement. Use a sample you may share, and describe the result without
uploading licensed media. Its output is evidence for that host and session, not
for every headset or codec combination.

## CI scope

The main workflow runs Python 3.11/3.12/3.13, requires the installed native test
dependencies, builds wheel/source distributions, and builds Arch core/Companion
packages as a non-root user. Renderer compilation has its own manual or
renderer-path-triggered workflow. Actions are pinned to commit IDs, checkout
credentials are not persisted, and workflows request read-only repository access.
CI artifacts are unsigned test builds. A configured workflow is not evidence
that a GitHub run passed; use the actual run logs when reporting results.

## Licensing and compatibility

Original contributions use AGPL-3.0-only. Preserve upstream GPL/MIT notices and
attribution; read the [license map](docs/licensing.md) before importing code.
Do not relicense the derived Plasma widget or third-party assets by changing a
project-wide label.

The Python/Arch distribution is `budslink-spatial-companion`. Keep the `spatial`
module, `spatialctl` alias, `spatiald.service`, D-Bus names and saved config paths
compatible. Package ownership checks must use actual renamed package owners.
The repository URL has not been renamed.

## Current validation boundaries

On October 5, the installed core/Companion package builds passed **489 core tests**
and **7 native UI checks**. All **15 installed audio gates** also passed on private
PipeWire, including clean owned playback shutdown before fallback cleanup. All
122 baseline settings/widget files remained unchanged. The user accepted channel
direction changes and yaw anchoring with prediction off. Pitch, roll, recenter
and prediction comparisons remain pending. TIDAL account authorization and browsing work, and a read-only
observation confirmed actual stereo FLAC through the renderer/EQ/headphone route
with fresh XM5 tracking. The user confirmed that TIDAL music sounds good. Atmos
streaming, refreshed panel interaction and physical Stop still need acceptance.
The shell was refreshed successfully; the latest player state is idle.

Use private buses, synthetic protocol fixtures and isolated audio endpoints for
development. Keep failures in the evidence record: normal playback shutdown must
stop its producer before capture cleanup, while actual renderer failures must
remain visible. TIDAL format checks must not silently accept stereo for Atmos or
lossy audio for Lossless. Never weaken acoustic assertions to make a run green.
Coordinate audible playback and movement checks with the user; fixture results
cannot establish their listening experience. See [current status](STATUS.md).
