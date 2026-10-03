# Historical release evidence — 0.2.3

> Archived September release record. Exact commits, hosted run links and former
> project/package names are retained. Current behavior and outstanding acceptance
> are summarized in [STATUS.md](../STATUS.md). No result here validates the later
> October timestamp, TIDAL mini-client, rename or license migration.


This release contains operational modules and a single-command Arch installation
workflow. It is ready for building and desktop acceptance testing; it is **not
hardware-validated or production-certified**. The complete package stack is now
installed on the target CachyOS PC; physical listening acceptance remains pending.

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
independent capabilities. See [architecture](architecture.md).

## Evidence available

The September 19 audit withdrew the earlier **complete PCM spatial validation**
claim. The supposedly 7.1 source had only two graph outputs; the renderer exposed
eight unpositioned inputs. Front and rear source captures were effectively
identical because WirePlumber retained a stereo downmix. Earlier assertions had
incorrectly been changed to accept that result. A phase-sensitive spectral
estimator also introduced false differences between repeated captures.

Version 0.2.3 repairs the native SPA position declaration, rejects lost or swapped
channels in production, and measures complete stimulus periods. The integration
gate now requires eight matching channel links and records eight independently
frequency-tagged input lanes before running the spatial position/rotation checks.
Regression tests deliberately reject downmix, tracking/roll/recenter no-ops,
wrong rotation signs, and swapped ears. The repaired native software path passed
fresh hosted validation; its retained graphs and raw PCM were independently
rechecked on September 28.

- [CI run 35410414111](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35410414111) passed at `2f20be1`: 296 tests without skips on each Python 3.11–3.13 lane, Arch core/Companion packages, and five native Qt test passes.
- [Renderer run 35410414135](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35410414135) built the actual patched CLI and FFI library at `2f20be1`. Both bind-policy tests and both positioned-format/PCM-lane tests passed.
- [CachyOS integration run 35410253252](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35410253252), implementation `50d542b`, retained eight correctly named active source-to-renderer links and independent input tones. All 18 post-EQ capture windows passed 74 acoustic checks. Independent raw-audio reanalysis confirmed these results and rejected deliberately collapsed rear channels, frozen tracking and reversed yaw.
- [Booted installer VM run 35410104281](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35410104281), also `50d542b`, passed the full installer, actual file-ownership checks, and a repeat core install preserving settings and the Companion backup. Its separate genuine E-AC-3 JOC/mpv/renderer/EQ route retained 15 decoded objects, verified routing/tracking across four captures and successful player cleanup.

The September 28 continuation adds stricter live/EQ stereo-output audits and a
full Plasma Wayland VM with fish and nano. The continuing audit, exact tested
commits, fresh run results and artifact links are recorded in
[pull request #1](https://github.com/Lich-King-Ethan/spatial-workbench/pull/1).
Earlier minimal-VM evidence must not be described as a full desktop test.

The hosted runs used synthetic endpoints and had no physical devices or account.
On September 28, local installation and isolated full-chain validation passed;
the actual WF-1000XM5 now supplies fresh sensor telemetry and has a verified A2DP
and EQ route. A corrected 30-second physical PCM trial passed routing and cleanup;
the user confirmed audible, clear channel direction changes. The following
movement check failed: sound moved with head turns instead of compensating.
The Sony active-orientation interpretation and helper startup reference have
been corrected and installed. The updated core passes 356 tests and the native
helper's absolute/legacy UDP checks pass. Corrected physical yaw now compensates
at unit gain; the user confirms near-centered anchoring that settles after a
turn, with noticeable delay. Pitch, roll and off-center recenter acceptance
remain pending.
See [the local acceptance record](local-pc-validation-20260928.md).
The physical PCM trial also exposed an owned-player gap: mpv forced a stereo
downmix before capture, then the player's audit rejected the renderer's eight
input lanes. The local correction preserves native PCM and authorizes it only
through the independently verified source session. The expanded isolated gate
now exercises automatic owned-player routing with eight independent tones,
alongside the existing PCM geometry and encoded Atmos checks; all 15 gates pass.
See [the audit and validation details](validation-environment.md).

The user's Companion compatibility confirmation is recorded for September 17, 2026, Arch Linux, Midwest USA. Its upstream comment submission was rejected by GitHub with HTTP 403; maintainers have not received it through this session. The ready-to-submit report is in [upstream-report.md](upstream-report.md).

## Checks still required on the target PC

These are the remaining physical/account acceptance checks; they are deliberately not represented by synthetic hosted evidence.

- **Target-PC install and desktop acceptance:** build/install the packages on the intended Arch/CachyOS machine, confirm package ownership/uninstall behavior, start the user service, and inspect KDE Plasma appearance, keyboard navigation and theme integration.
- **Physical XM5:** connect the actual WF-1000XM5, confirm BlueZ/HID exposure, A2DP sink identity, reconnects and the real final PipeWire route. Confirm listening localization, clipping behavior and recovery after stopping/restarting the renderer.
- **Physical SlimeVR (optional):** connect the receiver, check all three axes and recentering while the XM5 tracker is present, and verify independent provider failures/restarts do not disturb ordinary playback.
- **TIDAL account and entitlement:** authorize the account on the target machine and test an entitled Atmos track. Verify the returned manifest is genuinely E-AC-3/JOC and that unsupported/encrypted/stereo fallback paths are rejected.
- **Listening and desktop behavior:** select a real application stream, verify only that stream moves, and test deliberate output changes, disconnect/reconnect and renderer-crash recovery on the user's session.

Run 'spatial-verify' after installation for a read-only report. For an explicit media check, use 'spatial-verify --media /path/to/known-atmos-sample.mka --require-spatial'. The command preserves existing playback unless '--replace' is explicitly supplied and reports waiting/failed checks instead of substituting fixtures.
