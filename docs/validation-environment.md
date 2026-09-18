# Validation environment and remaining hardware checks

## What this environment can establish

The development runtime is Ubuntu 24.04, with Python 3.12. The project's supported
installation target is Arch/CachyOS with a user session running KDE Plasma,
PipeWire, WirePlumber, and BlueZ. Those are different environments.

Pure Python tests, binary protocol fixtures, real child-process lifecycle checks,
source-matched patch checks, archive checksums, and wheel construction can run
here. Tests which replace an external service with a fixture establish the
client's behavior against that fixture; they do not establish hardware or upstream
interoperability.

The automated suite is run with the separately installed `dbus-next` test
dependency:

```sh
PYTHONPATH=../test-deps:. \
  SPATIAL_TEST_LIMITER_PLUGIN=../eq-test-deps/fast_lookahead_limiter_1913.so \
  python3 -m unittest discover -s tests -v
```

Use the release's test report for the final count. Tests were added while the
implementation was being integrated.

## Completed hosted CI checks

[CI run 35337361174](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35337361174)
passed for commit `cbc9d7db505216b37cc6350f6aa1d97edd307493` on September 18, 2026.
The Python 3.11, 3.12 and 3.13 jobs ran the suite with a real private D-Bus
session and installed native Lua, compiler, JavaScript and limiter dependencies;
these jobs reject skipped tests.

The Arch job used Python 3.14.7 and Lua 5.5.1. Its actual `makepkg` check ran
all 240 tests successfully, including the seven D-Bus tests that cannot run in
the development sandbox. It produced `spatial-workbench 0.2.1-1` and
`plasma-budslink-companion-spatial 0.2.0.spatial0.2.1-1` package artifacts.
The Lua test harness supports both the older exported `luaL_openlibs` function
and Lua 5.5's `luaL_openselectedlibs` API; the same guard assertions also passed
locally against actual Lua 5.3, 5.4.8 and 5.5.1 libraries.

These are real package builds and native service tests. They do not establish
physical Bluetooth/HID behavior, a rendered Plasma widget, a running PipeWire
audio graph, or acoustic playback. The local environment restrictions below
still apply to the development sandbox.

The separate [renderer run 35337138960](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35337138960)
also completed successfully: clean Arch `makepkg` compiled the pinned patched CLI
and FFI library, passed the two Rust bind-policy tests and package CLI/ABI checks,
and uploaded the actual `orender-spatial` package artifact.

## Completed local checks

Independent packaging checks generated a source archive, verified its recipe
checksum, checked required service/rules/diagnostics/source files and executable
permissions, built the Python wheel, and installed that wheel into an isolated
scratch target without resolving dependencies. This establishes wheel/archive
integrity, not an Arch `makepkg` or `pacman` installation.

Subsequent native validation compiled the real patched Omniphony 0.5.2 CLI and FFI
library with privately installed Rust/Cargo 1.98.1 and extracted official Ubuntu
PipeWire/libclang development packages. Both Rust bind-policy tests and the
package's real CLI/ABI checks passed. The application accepted that compiled
library through its actual child-process capability probe. The renderer's
`package()` staged its executable, library, headers, layouts and license.
See [the exact native build evidence](orender-loopback.md).

The core PKGBUILD's actual `build()` and `package()` functions were also executed
against exported source, installing 124 staged files. Companion's actual
`prepare()` and `package()` functions ran against its checksum-verified upstream
archive, installing 86 staged files with all three new native cards. No system
installation or hardware operation is implied by these staging directories.

The tests include an actual child process transmitting the Sony helper's binary
protocol over a private UDP port, an actual OSC UDP exchange, and a pipe-backed
file-descriptor receiver test. Their telemetry is a declared test fixture; no
physical sensor or decoder is involved. Runtime tests inject a player recorder
to exercise delayed readiness, wrong output rejection, disconnect/stop races,
source cleanup, EOF queue advancement, and isolation of optional provider faults.
The shipped WirePlumber guard is executed by an actual Lua shared library against
declared event fixtures, including missing targets, changed user destinations and
reused node IDs. This validates the script's parsing and control flow; it does not
substitute for executing the policy inside a real WirePlumber session.

The official Arch `swh-plugins` 0.4.17-7 package was extracted into a test-only
directory. Its actual `fast_lookahead_limiter_1913.so` was loaded through the
LADSPA ABI and processed real sample buffers. The test verified its ports,
unity response for quiet samples, overload output ceiling, and reported 5 ms
latency. That establishes the plugin's tested DSP behavior, not successful
PipeWire filter installation or an end-to-end intersample true-peak guarantee.
To enable this test with an alternate extracted library, set
`SPATIAL_TEST_LIMITER_PLUGIN` to its absolute path. On the intended Arch system,
installing `swh-plugins` provides the normal library location.

The read-only `spatial-verify` command was also executed against this development
runtime. It returned exit code 1 and reported the absent user bus, PipeWire,
player, sensor helper and renderer dependencies. This confirms its blocked-host
reporting path; it is not a diagnosis of the user's computer.

## Explicitly blocked here

`dbus-run-session -- python3 -c 'print("private-bus-started")'` fails before the
application is started:

```text
dbus-daemon: Failed to start message bus: Failed to open socket: Operation not permitted
dbus-run-session: EOF reading address from bus daemon
```

A direct AF_UNIX stream socket creation fails with `PermissionError: [Errno 1]
Operation not permitted`. The process has a seccomp filter and no
permitted/effective capabilities. This is a runtime permission restriction. No
escalation, namespace, container, or alternate D-Bus transport workaround was
attempted. A separate probe confirmed that **AF_INET UDP loopback sockets are
permitted**, so actual Sony-helper and OSC UDP tests can run here.

Consequently a real local D-Bus, PipeWire server, or mpv Unix IPC socket cannot be
used for integration testing here. Installing another Python package does not
remove that limitation. There are also no Sony earbuds, Slime receivers,
authenticated TIDAL account, or usable
KDE Plasma display attached to this runtime. `pipewire`, `pw-cli`, `wpctl`, Qt 6 QML
tools, and `makepkg` were absent at the initial check.

## Required checks on the intended desktop

The following are acceptance checks, not claims of completed validation:

Run `spatial-verify` for a read-only report of the actual installed daemon,
connected hardware, decoder capability query and PipeWire graph. To exercise
real playback, supply your own media with
`spatial-verify --media /path/to/known-atmos-sample.mka --require-spatial`.
The audible test is opt-in, preserves existing playback unless `--replace` is
specified, and reports waiting/failed checks rather than supplying fixtures.
Its report is local and redacted. A successful graph inspection still cannot
establish perceived direction, physical axis signs, or listening quality.

1. Build and install through the provided Arch package workflow. Confirm package
   ownership and uninstall behavior, with no overwritten BudsLink-owned files.
2. Run the automated suite inside a private session bus with
   `SPATIAL_TEST_DBUS=1 dbus-run-session -- python3 -m unittest discover -s tests -v`.
   This environment flag is required; a bus alone does not enable the live tests.
3. Render the patched Companion in Plasma and check its existing theme, keyboard
   navigation, toggle behavior, literal receiver-reported identity, and removal
   of stale optional tracker rows.
4. Start with the earbuds disconnected, connect them, and verify that late BlueZ,
   PipeWire, Sony HID, and BudsLink readiness does not block unrelated capabilities.
5. Confirm all three physical axes for the earbuds and an optional tracker:
   turn left/right, nod up/down, and tilt. Check that the rendered soundstage stays
   stationary and recenter works. Binary packet decoding alone cannot prove a
   headset's axis convention or mounting orientation.
6. Confirm that the final renderer's actual PipeWire stream targets the current
   real WF-1000XM5 sink and forbids fallback. Native media movement requires
   reconnect/movement to remain enabled on the source player; its temporary live
   target must carry the matching WirePlumber guard. Disconnect the earbuds and
   separately kill the live renderer while audio is playing: no automatic route
   may send audio to monitor speakers. Reconnect and verify a new session's sink
   identity is used. Deliberately choosing another output is a separate user action.
7. Disable each tracker independently, unplug its receiver, restart its service,
   and stop telemetry. Only fresh enabled optional telemetry may take priority;
   the earbud fallback must remain off when its own toggle is off.
8. Stop/restart BudsLink, PipeWire, Sony tracker, and the renderer independently;
   unplug/reconnect the optional HID receiver. Run SlimeVR concurrently to check
   passive-reader coexistence. Check that retries recover each provider and
   ordinary desktop playback remains controlled by the user's existing output
   selection.
9. Use known Atmos media to verify decoder/bridge ABI, object metadata, binaural
   output, tracking, clipping telemetry, and audible playback. Repeat separately
   for authorized TIDAL Atmos playback. A quality flag or successful login is not
   proof that the returned stream contains Atmos.
10. Verify the actual optional EQ chain belongs to its owned process and reaches
    only the current headphones. Measure normal and overload playback separately;
    the standalone limiter sample test cannot prove the full gain path.
11. Select a real application stream for live PCM rendering. Check that only the
    chosen stream moves, other application outputs and the global default remain
    unchanged, and stop/disconnect/fault behavior preserves intentional routing
    and prevents automatic speaker playback. Live PCM must remain labelled PCM;
    it does not establish that an application exposes Atmos object metadata.

The project should not be described as hardware-validated or fully complete until
these checks have actually passed on the intended desktop.
