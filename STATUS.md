# Release status — 0.2.1

This release contains operational modules and a single-command Arch installation
workflow. It is ready for building and desktop acceptance testing; it is **not
hardware-validated or production-certified**. No software has been installed on
the user's computer from this workspace.

## Implemented

| Component | Current implementation |
|---|---|
| Desktop discovery | Live BlueZ and BudsLink D-Bus observation, bounded PipeWire snapshots, reconnect epochs and independent retry |
| Tracking | Identity-checked Sony HID helper, passive SlimeVR nRF HID readers, fresh-packet selection, independent permissions and recentering |
| Media playback | Owned mpv-omniphony process, protected native headphone targeting, verified renderer telemetry, queue and MPRIS controls |
| Ordinary music and applications | Standalone Omniphony PCM rendering; automatic routing of this player's native PCM and explicit selection for another application's stream |
| TIDAL | Official device authorization through tidalapi, protected session storage, track/album/playlist resolution, strict Atmos manifest and renderer checks |
| Reference EQ | Device-scoped PipeWire smart filter, sourced AutoEQ five-band profile, separate SWH limiter, graph auditing and disconnect cleanup |
| Native UI | Revision-specific Companion patch with playback, application audio, fresh-device tracker rows and backend-confirmed state |
| Packaging | Checksummed Arch recipes, pinned patched engine and matching library, one-command install, user service, diagnostics and host verifier |
| Automatic diagnostics | Persistent-error detection, local redacted reports, private bounded retention, rate limiting and independent collector cleanup |

The runtime uses separately supervised tasks and external processes where
appropriate. It does not claim one container or process per Python module.
BudsLink controls, sensor permissions, source access and renderer readiness are
independent capabilities. See [architecture](docs/architecture.md).

## Evidence available

The local suite ran 240 tests: 233 passed and 7 native D-Bus checks were skipped
because this environment prohibits their sockets. Downloaded release archives
include the exact `test-results.txt`; GitHub Actions records subsequent CI checks.
The suite covers policy, real UDP and
child-process transports, source validation, graph audits, reconnection races,
playback request ownership, native presentation logic and independent failures.
Service and hardware fixtures in these tests are identified as test data.

Additional checks performed include source-matched Companion/engine patch checks,
checksummed source archive and wheel construction, isolated wheel installation,
and package/script validation. The actual Arch SWH limiter library processed
sample buffers through its LADSPA ABI: its ports, quiet-signal gain, overload
sample ceiling and 240-sample delay at 48 kHz were checked. These results establish
that plugin's DSP behavior, not the complete desktop audio path.

The actual pinned, patched Omniphony CLI and matching FFI library compiled with
Rust/Cargo 1.98.1. The package's two Rust bind-policy tests, CLI probes and exported
marker checks passed, as did the application's real child-process library probe
and package staging. The build exposed and fixed the upstream archive's missing
lockfile, unusable version stamp and help/version failure exit codes. See
[native renderer evidence](docs/orender-loopback.md).

The core package's real `build()` and `package()` stages and Companion's real
`prepare()` and `package()` stages also passed against exported source. These are
actual staging checks on Ubuntu; they are not a completed Arch installation.

A real unauthenticated TIDAL device-authorization request succeeded. No account
was signed in and no media entitlement or Atmos playback was tested.

## Checks still required on the target PC

- **Arch package installation:** native renderer compilation is verified on
  Ubuntu. A complete Arch `makepkg`/`pacman` installation remains pending; the
  provided recipes run their own checks on the target machine.
- **Session services and native UI:** AF_UNIX socket creation fails here with
  `Operation not permitted`. Real D-Bus, PipeWire and mpv IPC tests cannot run.
  Qt/Plasma rendering and keyboard/theme behavior also remain untested.
- **Actual sensors:** no XM5 or Slime receiver is attached. WF-1000XM5 HID exposure,
  firmware layout, physical axes and concurrent SlimeVR use require hardware tests.
  Working Companion controls alone do not prove motion-sensor support.
- **Audio graph and listening:** verify the exact physical final sink, object
  metadata, binaural tracking, EQ insertion, channel negotiation and reconnects.
  Test selected-application stop/failure recovery, including abrupt renderer loss;
  a graph fixture is not proof of the desktop policy's behavior.
- **TIDAL account:** authorize your account, choose an entitled Atmos track, and
  verify the actual clear E-AC-3 manifest and decoded objects. Unsupported formats,
  encrypted streams and stereo substitution are rejected rather than mislabelled.

Run `spatial-verify` after installation, then the explicit media test described in
[installation](docs/install.md). The full acceptance procedure and environmental
evidence are in [validation-environment.md](docs/validation-environment.md).

The user's Companion compatibility confirmation is recorded for September 17,
2026, Arch Linux, Midwest USA. Its upstream comment submission was rejected by
GitHub with HTTP 403; maintainers have **not** received it through this session.
The ready-to-submit report is in [upstream-report.md](docs/upstream-report.md).
