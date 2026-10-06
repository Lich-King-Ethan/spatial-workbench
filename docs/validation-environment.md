# Validation evidence and environment history

## TIDAL quality and library update — October 5, 2026

The source and core package suites passed 509 tests. The Companion package's native
UI passed 8 checks with no skips. Both revision 5 archives match the reviewed code;
the new per-widget quality schema has the expected Max default.
New regressions cover quality ceilings, real SDK unavailable-rendition fallback,
strict legacy formats, queue isolation, observed bitrate freshness, mixed playlist
folders, raw paging offsets and popup recreation with a retained widget preference.

An authenticated silent probe used the checkout provider and installed mpv. All
four library categories loaded. Max returned FLAC; CD returned lower-quality AAC,
which decoded successfully, as did explicit AAC 320 and AAC 96. These are real
service/player results, without headset output. No root folders were returned for
this account; nested-folder checks use explicit fixtures, including 400 root
folders and a nested page beyond the SDK constructor's first 50 items. An initial
probe accidentally imported the older installed provider; its signature errors
remain in private evidence, and the corrected probe explicitly used the checkout.
Installation and installed AAC routing are the next checks. The first sudo prompt
timed out without a package transaction; its log is retained. A new normal Konsole
prompt is open, with revision 4 still installed. Hosted CI for `609ee88` passed
the Python 3.11–3.13 lanes and Arch package job in
[run 37391395844](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/37391395844).
The matching renderer run remains in progress. Restricted standalone queue-test attempts stalled;
the exact test passed in 0.01 seconds in both approved unrestricted contexts, with
and without a private bus. Those interrupted logs are retained alongside the
passing full/package suites; no assertion was changed to obtain a pass.

## Installed update and physical checks — October 5, 2026

Core **0.2.3-4** and Companion **0.2.0.spatial0.2.3-4** were installed through normal
package prompts. Their full builds passed **489 core tests** and **7 native Qt
checks, zero skips**. The native UI harness now initializes Plasma's actual theme;
control contrast, narrow layouts and asynchronous playback-error states are
checked without changing the user's theme.

The user accepted two completed 30-second physical WF-1000XM5 trials: clear
channel direction changes, then a front reference that stayed anchored during
left/right movement. Prediction was off. The measured route retained native 7.1
through the renderer and EQ to the real headset. Pitch, roll, off-center recenter,
prediction comparison and interruption/reconnect checks remain pending.

Two incomplete yaw attempts remain in the private evidence: one lacked fresh
tracking; another exposed a cached previous-player clock in the smoke runner.
The runner now requires fresh ownership before using that clock. A later pitch
attempt was blocked by a stale renderer error after normal owned-player shutdown.
The production repair stops the source producer before capture cleanup and waits
for that cleanup to finish. Actual renderer failure evidence remains blocking;
the fix does not discard failures to make the next run pass.

Actual TIDAL authorization and browsing succeeded. An Atmos-only selection
returned `StreamNotAvailable` from the service, so Atmos acceptance is unproven.
The separate Lossless request returned clear FLAC DASH, then failed in the player:
mpv/FFmpeg treated the private local manifest as a local-only source and rejected
its HTTPS segments. A scoped allowance for retained, validated TIDAL DASH files
resolved that rejection; the actual returned media decoded for one second with
null output. This establishes stream decoding, not acoustic/headset playback.

The revised installed stack then passed **all 15 native audio gates**, with every
check passing and process exit 0. The report is
`build/local-validation/round2-20261005/installed-native-audio/report.json`.
Owned PCM stopped cleanly before any fallback cleanup: its source and renderer
nodes were gone, idle state had no stale error, and 1.11 seconds of supervision
showed no respawn. The report records `hardware_validated: false`. All **122
baseline configuration/widget files remain unchanged**, and seven reviewed
installed source files match their intended versions.

A read-only observation of the user's real TIDAL Lossless playback then passed
**12 strict snapshots over 10.12 seconds**, with 9.99 seconds of media advance.
Each fresh audit confirmed actual stereo FLAC source ownership, player routing,
live renderer readiness, verified EQ and fresh XM5 tracking. The report is
`build/local-validation/round2-20261005/tidal-readonly-l4yqe14c/report.json`.
No playback, volume or configuration was changed; the user's music remained
running. This is not a physical Stop acceptance check.

The user then confirmed: “Yes—music sounds good.” Real account access, the
lossless route and TIDAL listening are accepted for this playback. Atmos
streaming, physical Stop and refreshed panel interaction remain pending.
Physical motion acceptance remains limited to the completed yaw clip; channel
direction changes were accepted in the separate local clip.

The shell alone was subsequently reloaded to load the new UI. Panel and audio
services were active, all 122 baseline files were still byte-identical, and
no relevant QML journal errors appeared. A read-only follow-up ended neutrally
when the previous player changed. The latest state was idle with a TIDAL
`StreamNotAvailable` provider error, not a renderer error; the new request's
cause is unknown. This neither establishes uninterrupted music nor attributes
the provider error to the shell reload.
[Current status](../STATUS.md) tracks the next results; raw device/account evidence
stays in ignored local validation directories.

## Installed repair and preparation — October 3, 2026

The user authorized the second physical smoke-test preparation. A reviewed
10-package transaction repaired the audio group and installed the renamed core,
Companion and timestamped Sony helper through normal Konsole/fish/nano prompts.
All 122 baseline configuration/widget files remained unchanged.

The first core revision 2 installation exposed a packaging defect despite passing
source tests: Python's installer ran from a virtual environment and staged the
wheel under the builder's environment prefix. Replacing the old package then
left `/usr/bin/spatialctl` missing. Activation stopped before service restart.
Revision 3 explicitly uses `/usr/bin/python` for build/check/package, installs
with `--prefix=/usr`, and checks the staged system module and executable CLI
shebangs. Regression fixtures reject virtual-environment paths and malformed
launchers. The official `python-build` and `python-installer` packages were
installed, the repaired package build passed all **474 tests**, and its archive
contained only `/usr` files with correct CLI shebangs before installation. The
affected project virtual environment was restored separately; no global pip
installation was used. Error traces from deliberately rejected D-Bus requests
remain in the test log; the final suite result was 474 tests, OK, with exit 0.

The installed set at that point was core `0.2.3-3`, Companion `0.2.0.spatial0.2.3-3`, Sony
`1.0.0-2`, renderer `0.5.2-3`, bridge `0.7.3-1` and mpv `0.5.2-1`. Five missing
mpv library packages were restored. `spatiald.service` and WirePlumber are active;
non-audible `spatial-verify` reports software features PASS and hardware/playback
WAIT. The headphones are disconnected, and the user will connect them later.

The actual installed stack then passed all **15 native gates**, with every check
reporting `passed` and process exit 0. Its `/usr` modules, binaries and libraries
ran on private D-Bus/PipeWire without bind-over replacements. The report at
`build/local-validation/round2-20261003/installed-native-audio/report.json` records
eight input lanes, owned 7.1, 18 PCM windows/74 checks, actual Atmos decoding with
15 objects, and verified route cleanup/defaults. `hardware_validated` is false.
Physical listening, motion and real TIDAL account acceptance remain open.

The Companion passes 7 native Qt checks, including actual window hiding, card
recreation and recovered sign-in completion. These reject hidden polling and
stale-session restoration after logout. Sony 1.0.0-2 package checks verify legacy,
absolute and timestamped modes. Builds use up to 8 jobs on the 32-thread, 31 GiB PC.

## Earlier isolated software validation — October 3, 2026

The initial staged-wheel audio rerun failed before audio: the installed renderer
lacked the required patch marker, the renderer/bridge versions no longer matched
the supported group, and mpv had unresolved shared libraries. That failure remains
in `build/local-validation/native-review-20261003.1hatAl/`; the later transaction
repaired those dependencies.

The isolated compatible stack uncovered a real WirePlumber 0.5.18 guard startup
race (`native-pinned-review-20261003.9ziqmjq3/`). The production fix obtains the
metadata manager from the event source; a Lua regression preserves the fail-closed
routing contract. The pinned bridge 0.7.3 was rebuilt, the pinned player binary
was verified against pacman's checksum, and missing libraries were extracted
from signed packages for the isolated runs. Host packages were unchanged at that
point.

All 15 gates subsequently passed twice consecutively, in
`native-pinned-review-20261003.abukawxy/` and
`native-pinned-review-20261003.rbtb2hc1/`: eight independent input lanes, automatic
owned 7.1 routing, 18 PCM capture windows with 74 acoustic assertions, four genuine
Atmos captures retaining 15 objects, and cleanup/route invariants. A previous run,
`native-pinned-review-20261003.ji60e8_s/`, failed the Atmos graph-stability gate.
Detailed instrumentation was added without changing acceptance predicates; the
successful follow-up does not establish that earlier failure's cause.

The earlier core revision 2 build used virtual-environment frontends and
`makepkg --nodeps` for their missing pacman records. Its full tests ran, but did
not detect the incorrect installation prefix. The revision 3 system-Python build
and staging checks address that separate failure; no tests were skipped or
weakened to make either audio or package validation pass.

Snapshot history records accepted selected-provider packets, not a lossless
Bluetooth transport capture. Host-monotonic timestamps do not establish the
earbud's sensor clock or acoustic output delay.


## Original cloud environment — September 2026

The initial development runtime was Ubuntu 24.04 with Python 3.12. Development
continued on the actual CachyOS desktop on September 28, October 3 and October 5. The supported
installation target is Arch/CachyOS with KDE Plasma, PipeWire, WirePlumber and BlueZ.
This reference retains dated evidence from each environment; [current status](../STATUS.md)
summarizes the latest branch. The limitations below describe the original cloud
runtime, not the current CachyOS desktop.

Pure Python tests, binary protocol fixtures, real child-process lifecycle checks,
source-matched patch checks, archive checksums, and wheel construction ran
in that cloud environment. Tests which replace an external service with a fixture establish the
client's behavior against that fixture; they do not establish hardware or upstream
interoperability.

The original cloud suite used separately installed test dependencies as below.
For current development, use the [contributor commands](../CONTRIBUTING.md).

```sh
PYTHONPATH=../test-deps:. \
  SPATIAL_TEST_LIMITER_PLUGIN=../eq-test-deps/fast_lookahead_limiter_1913.so \
  python3 -m unittest discover -s tests -v
```

Use the hosted CI links below for exact tested commits and counts. Local
developer runs may also save `test-results.txt`; that generated log is not tracked.

## Completed hosted CI checks

[CI run 35410414111](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35410414111)
passed for commit `2f20be1`. Python 3.11, 3.12 and 3.13 plus the Arch package lane
each ran 296 tests. The Python matrix requires zero skips; the native Qt/Plasma
smoke reported five passes and zero skips. Hosted jobs supply the private D-Bus
session, Lua, compiler, JavaScript and limiter dependencies unavailable together
in the development sandbox.

The suite covers Sony protocol/axis and recenter fixtures, TIDAL credential
races, source validation, graph audits, reconnection races, playback ownership,
native presentation logic and independent provider failures. These fixtures prove
the client behavior and package checks; they are not physical Bluetooth or
listening tests.

## September 19 channel-preservation audit

The earlier claim that the hosted PCM test proved a complete 7.1 spatial path is
withdrawn. Saved artifacts from [run 35382912735](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35382912735)
showed native eight-channel source PCM adapted into only two graph ports, linked
to the first two of eight **unpositioned** renderer inputs. The pinned upstream
capture code omitted SPA channel positions. WirePlumber kept the source's
existing stereo format because the new target was unpositioned.

Reanalysis using complete 100 ms stimulus periods measured rear/front spectral
differences of only 0.000044 dB (left) and 0.000229 dB (right). The earlier Hann
window estimator was phase-sensitive for this repeating fixture, adding about
0.239 dB artificial variation in a cyclic-shift check. Restored geometric checks
reject those old captures on seven assertions. Assertions accepting rear/front
equality and removed roll/compound-pose equivalences were incorrect.

Version 0.2.3 adds the native position declaration and validates it by parsing the
exact serialized SPA format in Rust tests. The Python auditor requires named,
matching source-to-renderer links and preservation of the source's native channel
set. The hosted gate must reproduce a source initially downmixed to stereo, then
prove it reconfigures into eight channels after selection. Eight different tones
are recorded from the renderer input monitor and checked for missing, swapped,
duplicated or mixed lanes. Post-EQ broadband recordings separately test actual
spatial DSP with restored position, yaw, pitch, roll and recenter assertions.

## Confirmed repaired software path

The fresh native checks completed successfully after the September 19 handoff:

- [Renderer build 35410414135](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35410414135), `2f20be1`, compiled the CLI and FFI library and passed two OSC bind-policy plus two native channel-format/PCM-lane tests.
- [CachyOS integration 35410253252](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35410253252), implementation `50d542b`, passed the installed suite, native Qt cards, real routing, EQ/limiter, independent lanes and spatial captures.
- [Booted CachyOS installer 35410104281](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35410104281), the same implementation, passed full installation, real file ownership, retained user settings/permissions/Companion backup and a repeat core install, followed by PCM and encoded Atmos routes.

On September 28, both artifact archives were downloaded and their SHA256 digests
checked against GitHub. Independent inspection found eight active matching
FL/FR/FC/LFE/SL/SR/RL/RR input links. Direct sinusoid projection of actual PCM
confirmed the input tones with maximum unwanted/wanted amplitude ratio
2.70e-9. All eight source tones also survived the post-EQ stereo output; the
smallest observed per-ear tone RMS was 0.003012. These are fixture measurements,
not advertised hardware audio specifications.

Raw-capture hashes were checked and all 18 windows/74 acoustic assertions rerun
for each artifact. Rear/front spectral differences were 3.479 dB (left) and
5.618 dB (right). Deliberately replacing rear captures with front captures,
freezing head-pose captures, or reversing yaw caused the repaired analyzer to
reject the recordings. The old incorrectly approved artifact still failed seven
geometry checks.

The booted VM's separate encoded Atmos route produced four real captures with
15 decoded objects, tracking active and routing verified. Independent left/right
energy measurements reproduced the reported opposite-yaw difference of 8.585 dB.
The final cleanup graph contained no nodes belonging to the stopped media player.
Media reports record actual pose acknowledgements and require cleanup before pass.

These tests used a synthetic headphone endpoint and helper UDP telemetry. They do
not establish physical Bluetooth/HID behavior, Slime receiver operation, listener
translation, subjective localization or TIDAL account entitlement. The September
19 VM was a minimal booted system, not a full graphical desktop.

## September 28 continuation

No changes from the user's local session had been pushed when the repository was
inspected; the audit branch still pointed to `2f20be1` and main to `f490ac3`.
The continuation preserves the verified fixes, tightens incomplete live/EQ output
readiness and adds a real Plasma Wayland session with fish and nano to the VM.
The continuing validation record, exact tested commits, run results and artifact
links are maintained in [pull request #1](https://github.com/Lich-King-Ethan/spatial-workbench/pull/1).
See [the VM procedure](full-installer-ci.md).

The first full-desktop boot established the real SDDM Wayland/KWin/plasmashell
session, but exposed a brittle nano Save As prompt matcher. Its replacement
requires nano to save exact new bytes and exit successfully, retaining a terminal
transcript. The next boot passed nano and desktop screenshot checks and reached
the real installer, exposing desktop-portal activation during the isolated
Companion package check. These failures are kept as evidence; neither is counted
as a completed installer or application pass.

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

A subsequent independent local check used the genuine Harletty 0.7.3 bridge
with Omniphony 0.5.2's real FFI and a pinned E-AC-3 JOC Atmos fixture. It decoded
15 objects and 72,192 frames, checked finite non-silent output with twelve
speaker channels and two binaural channels, and verified real OSC pose
acknowledgement plus a changed binaural waveform after head rotation. This
check did not require a PipeWire server or mpv IPC and therefore ran despite the
local Unix-socket restriction. It does not establish the full media route,
physical headphone output or perceived localization. See
[decoder and fixture details](decoder-validation.md).

The core PKGBUILD's actual `build()` and `package()` functions were also executed
against exported source, installing 124 staged files. Companion's actual
`prepare()` and `package()` functions ran against its checksum-verified upstream
archive, installing 86 staged files with all three new native cards. No system
installation or hardware operation is implied by these staging directories.

The tests include an actual child process transmitting the Sony helper's binary
protocol over a private UDP port, an actual OSC UDP exchange, and a pipe-backed
file-descriptor receiver test. Their telemetry is a declared test fixture; no
physical sensor is involved. The new CI pose utility separately passed 13
simulated orientations through the actual Sony subprocess/UDP adapter and
Engine, including compound recentering and subsequent relative yaw. That local
check verifies pose delivery and policy, not captured audio. Runtime tests inject
a player recorder
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

## Restrictions in the original cloud runtime

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
