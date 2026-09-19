# Local development handoff — September 19, 2026

Continue the existing project on the user's CachyOS/KDE computer. Read STATUS.md,
CONTRIBUTING.md and the relevant module docs before changing behavior. This is a
working implementation being validated, not an invitation to replace it with a demo.

## Repository and current work

- Repository: https://github.com/Lich-King-Ethan/spatial-workbench
- Branch: `fix/positioned-surround-audit`
- Draft PR: https://github.com/Lich-King-Ethan/spatial-workbench/pull/1
- Implementation commit: `50d542bec9aaca145d75b6e9124ebafe9156695f`
- Release version: 0.2.3; native renderer package: `orender-spatial` 0.5.2-2.
- Main remains at `f490ac333831298350577f9c81f0eab11f7e771d`. Automatic approval
  review rejected a direct main update and recommended a feature branch/PR.
  Continue on the PR branch; obtain explicit user approval before merging to main.
- This handoff and accompanying documentation updates do not change the tested code.

The user authorized building, running, diagnosing and correcting this project,
including required development software, on their machine. Respect actual local
permission prompts. Use CPU/RAM-aware build concurrency, retaining enough memory
and CPU for the active desktop and audio session. Do not infer model settings or
available account quota from this document.

## User intent

Deliver a finished, one-command-build/install Arch/CachyOS application using the
existing native BudsLink Companion, with independently supervised modules for
discovery, tracking, media/ordinary application audio, rendering, EQ and diagnostics.
The ordinary physical WF-1000XM5 must remain the final output. Optional SlimeVR
providers must fail independently. The user canceled the speculative latency
equilibrium/sport-mode feature; do not implement it.

The user confirmed existing Companion operation on their XM5 on September 17,
2026, on Arch in the Midwest USA. This does not validate new spatial features.
An upstream compatibility submission previously failed with HTTP 403;
docs/upstream-report.md contains the report, but do not claim it was delivered.

## Critical audit findings and fixes already implemented

The previous green live PCM spatial verdict was false. Saved PipeWire evidence
showed native 7.1 PCM adapted to only two graph ports; Omniphony's eight inputs
were unpositioned because upstream omitted SPA AudioPosition. WirePlumber retained
the old stereo downmix. Assertions had incorrectly been relaxed to accept nearly
identical front/rear signals. Preserve the corrected validation standards.

- `packaging/orender-live-channels.patch` declares and roundtrip-tests positioned
  FL, FR, FC, LFE, SL, SR, RL, RR native PCM. Both patches apply cleanly to upstream.
  The annotated v0.5.2 tag `a8018cd...` resolves to commit `f9a7972...`.
- `spatial/live_audio.py` checks the actual native format, source channel
  preservation and one-to-one same-channel links. It rejects the old saved graph.
- `tools/ci/audio-stack-smoke.py` requires all eight links, records independent
  frequency tags at the renderer input, and captures actual post-EQ stereo PCM
  for 18 position/rotation/recenter windows. The source deliberately starts as a
  native 7.1 stream downmixed to stereo, so rerouting must reconfigure it correctly.
- `tools/ci/spatial-metrics.py` uses complete 100 ms stimulus periods. The old Hann
  estimator depended on capture phase. Restored geometry checks reject the old
  captures on seven assertions. New negative controls catch collapsed/swapped
  channels, ignored tracking/roll/recenter and incorrect rotation signs.
- Renderer shutdown/unknown heartbeat/IPC loss clears stale readiness; bridge
  errors block readiness. Media output requires complete, matching stereo links.
- Media reports now contain verified tracking/routing snapshots, actual pose
  acknowledgements and cleanup status, not only startup state.
- Reduced installs run explicit core-only verification. Full verification stays
  strict. VM tests check real installed-file ownership and preserved config,
  preferences and Companion backups, including a repeat core installation.
- The previous isolated EQ config fix and genuine encoded Atmos bridge work
  survived the audit. Do not revert all earlier changes wholesale.

## Validation at handoff

These jobs test implementation commit `50d542b`. Inspect their actual current
results and artifacts; their state may have changed since this file was written.

| Gate | Run | State at handoff |
|---|---|---|
| Python 3.11–3.13, Arch core and Companion | https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35410036216 | Passed: 296 tests per Python lane; Arch suite and 5 native Qt passes |
| Native patched CLI/FFI and Rust tests | https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35410036439 | Running |
| CachyOS installed package/audio integration | https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35410253252 | Running |
| Booted CachyOS full installer VM | https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35410104281 | Running |

The cloud local run passed 287 of 296 tests, with nine explicit native-environment
skips. Hosted CI supplied those dependencies and passed without skips. The cloud
workspace blocks Unix sockets and lacks physical devices; your desktop does not
inherit those limitations. Confirm its actual capabilities rather than assuming.

Historical green integration runs do not validate the repaired multichannel path.
Inspect `source-lane-evidence.json`, `spatial-acoustics.json`, the full-chain graph,
`media-spatial-smoke.json` and installer state artifacts in the new runs. Do not
change acoustic limits solely to accommodate a failing run; trace the signal and
verify geometry independently. Synthetic tests do not establish listening quality
or the physical sensor's axis orientation.

## Next work on this machine

1. Inspect the checkout and any user edits; fast-forward the PR branch if safe.
   Check the running GitHub jobs before starting duplicate expensive builds.
2. Inspect the actual OS/session, CPUs, available RAM, packages, user services,
   PipeWire graph, BudsLink/BlueZ and attached XM5/Slime devices. Keep credentials
   and private account/media files out of diagnostics shared upstream.
3. Resolve any newly failed hosted gate. Build/install on this desktop using
   `bash install.sh` as the ordinary user, retaining normal sudo/package/AUR review
   prompts. The VM provisioning scripts under tools/ci are for disposable CI
   guests, not for installation on the user's desktop.
4. Run the complete suite with native dependencies and a private test D-Bus using
   CONTRIBUTING.md. Run `spatial-verify` and inspect actual module and graph states.
   Core-only verification intentionally excludes optional audio; it is insufficient
   evidence for the complete audio installation.
5. Verify actual XM5 output identity, native Companion cards, source selection,
   post-renderer EQ, restore behavior and absence of unintended speaker fallback.
   Coordinate physical yaw/pitch/roll and recenter checks with the user. Confirm
   earbud/optional-provider permissions and independent failure recovery.
6. Test real encoded Atmos separately from ordinary PCM. TIDAL requires the user's
   own authorization/entitlement; do not manufacture a passed account test. Use
   `spatial-verify --media /path/to/known-atmos-sample.mka --require-spatial` for
   an explicitly selected local sample. Preserve other playback by default.
7. Commit focused corrections to the existing PR branch, inspect fresh evidence,
   and update STATUS.md and docs/validation-environment.md with exact tested
   commits and remaining limits. Keep the PR draft until the repaired native
   chain passes. Ask the user to approve the concrete tested PR before merge.

The user uses fish. Codex CLI 0.155.1 installed successfully at
`~/.local/bin/codex`; `fish_add_path ~/.local/bin` resolved command discovery.
Prefer shell-independent executable paths or explicitly invoke bash for bash
scripts. This is a new local session; the cloud conversation and tool state do
not transfer automatically.
