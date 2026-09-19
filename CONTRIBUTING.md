# Contributing

Keep changes scoped to one provider or interface where possible. Hardware
discovery, routing, decoding, tracking, account access, and Companion UI remain
independent modules. Reuse maintained upstream tools and preserve the existing
physical headphone endpoint. Include the relevant regression test when changing
connection recovery, routing ownership, tracker selection, or credential handling.

## Run the tests

Use Python 3.11 or later in a virtual environment. The complete suite needs a
private session D-Bus, Node.js, a Lua shared library, a C compiler, and the SWH
LADSPA limiter. On Arch, install the test dependencies with:

```sh
sudo pacman -S --needed python python-pip base-devel dbus nodejs lua swh-plugins
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[tidal]' build numpy
SPATIAL_TEST_DBUS=1 dbus-run-session -- python -m unittest discover -s tests -v
luac -p wireplumber/scripts/spatial-live-guard.lua
python -m build
```

The D-Bus tests must use a private bus, not the active desktop session. The Lua,
JavaScript, native-library, and private-bus tests exercise real local runtimes.
They do not validate a Bluetooth connection, a Plasma popup, or acoustic output.
Read `STATUS.md` and the module documentation for outstanding hardware checks.

Generate package sources with `python tools/make-release.py`. On Arch, build
inside each generated `dist/arch/core` and `dist/arch/companion` directory using
`makepkg --syncdeps --cleanbuild`. Run makepkg as an ordinary user. The separate
`dist/arch/orender` recipe compiles the pinned renderer and runs its Rust tests
and binary/library marker checks; this build takes longer.

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
