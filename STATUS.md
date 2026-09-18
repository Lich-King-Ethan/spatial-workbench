# Release status — 0.2.2

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
| TIDAL | Browser device authorization through tidalapi, protected session storage that respects concurrent login/logout, track/album/playlist resolution, strict Atmos manifest and renderer checks |
| Reference EQ | Device-scoped PipeWire smart filter, sourced AutoEQ five-band profile, separate SWH limiter, graph auditing and disconnect cleanup |
| Native UI | Revision-specific Companion patch with playback, application audio, fresh-device tracker rows and backend-confirmed state |
| Packaging | Checksummed Arch recipes, pinned patched engine and matching library, one-command install, user service, diagnostics and host verifier |
| Automatic diagnostics | Persistent-error detection, local redacted reports, private bounded retention, rate limiting and independent collector cleanup |

The runtime uses separately supervised tasks and external processes where
appropriate. It does not claim one container or process per Python module.
BudsLink controls, sensor permissions, source access and renderer readiness are
independent capabilities. See [architecture](docs/architecture.md).

## Evidence available

Hosted software validation is complete for commit [9d573ea](https://github.com/Lich-King-Ethan/spatial-workbench/commit/9d573ea2589fce99d4e1dee20b065545e1603938).

- [CI run 35379035113](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35379035113) passed on Python 3.11, 3.12, 3.13 and the Arch package lane. Each lane ran 256 tests with zero skips; the native Qt/Plasma smoke test reported five passes including setup and cleanup.
- [CachyOS integration run 35379035086](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35379035086) passed the installed package suite, genuine Harletty bridge build, native Qt cards, real PipeWire 1.6.8/WirePlumber 0.5.17 routing, WirePlumber guard policy, device-scoped EQ and SWH limiter, and 18 captured 48 kHz stereo PCM windows. The production route was exercised as renderer → EQ/limiter → synthetic WF-1000XM5 sink monitor, while actual Sony-helper UDP packets drove the production tracker/Engine/OSC path. Acoustic checks cover position, yaw, pitch, roll, recenter and mirror behavior.
- [Full installer VM run 35380898340](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35380898340) passed in a freshly booted minimal CachyOS guest. It ran the unchanged installer, real systemd/udev/PipeWire services, package ownership checks, daemon verification and the complete media route. The real E-AC-3 JOC Atmos fixture was decoded by mpv and the pinned orender/Harletty bridge, rendered to the post-EQ synthetic earbud monitor, and captured for neutral/repeat/yaw poses. Stop/restore checks returned the original source route and preserved the global default.
- The full installer artifact also records the intentional boundary: no physical headphones, XM5 HID or Slime receiver were attached, so hardware fields remain WAIT rather than being misreported as passes. The hosted endpoint is synthetic and headless; it proves the software chain, not listening quality or physical sensor axis orientation.

The runtime remains modular: discovery, tracker providers, playback/live PCM, renderer ownership, EQ, diagnostics and the native Companion are independently supervised and failure-scoped. The complete evidence artifacts are retained on the linked GitHub runs.

The user's Companion compatibility confirmation is recorded for September 17, 2026, Arch Linux, Midwest USA. Its upstream comment submission was rejected by GitHub with HTTP 403; maintainers have not received it through this session. The ready-to-submit report is in [upstream-report.md](docs/upstream-report.md).

## Checks still required on the target PC

These are the remaining physical/account acceptance checks; they are deliberately not represented by synthetic hosted evidence.

- **Target-PC install and desktop acceptance:** build/install the packages on the intended Arch/CachyOS machine, confirm package ownership/uninstall behavior, start the user service, and inspect KDE Plasma appearance, keyboard navigation and theme integration.
- **Physical XM5:** connect the actual WF-1000XM5, confirm BlueZ/HID exposure, A2DP sink identity, reconnects and the real final PipeWire route. Confirm listening localization, clipping behavior and recovery after stopping/restarting the renderer.
- **Physical SlimeVR (optional):** connect the receiver, check all three axes and recentering while the XM5 tracker is present, and verify independent provider failures/restarts do not disturb ordinary playback.
- **TIDAL account and entitlement:** authorize the account on the target machine and test an entitled Atmos track. Verify the returned manifest is genuinely E-AC-3/JOC and that unsupported/encrypted/stereo fallback paths are rejected.
- **Listening and desktop behavior:** select a real application stream, verify only that stream moves, and test deliberate output changes, disconnect/reconnect and renderer-crash recovery on the user's session.

Run 'spatial-verify' after installation for a read-only report. For an explicit media check, use 'spatial-verify --media /path/to/known-atmos-sample.mka --require-spatial'. The command preserves existing playback unless '--replace' is explicitly supplied and reports waiting/failed checks instead of substituting fixtures.
