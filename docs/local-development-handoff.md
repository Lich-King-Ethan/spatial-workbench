# Local development handoff

## Physical WF-1000XM5 smoke test — September 28, 2026

The current implementation is on `fix/positioned-surround-audit`, at `9656676`
before this documentation update. PR #1 remains the review location. Hosted
software tests do not establish that the real earbuds expose the supported HID
sensor, or that physical movement produces the correct audible direction.
The cloud workspace does not have access to this computer's Bluetooth devices.
Run the following work in the user's local Konsole/Codex session, as the desktop
user. These commands work in fish; the installer explicitly invokes Bash and
uses nano for package review.

### 1. Preserve local work and install

Inspect the existing checkout before updating it:

```fish
cd ~/spatial-workbench-audit
git status --short --branch
git log -3 --oneline
git fetch origin fix/positioned-surround-audit
```

Only when this is the expected branch with no local changes or unreviewed local
commits, fast-forward and install. If local work exists, inspect and preserve it
first; do not reset, clean, or automatically stash it. The full installer retains
configuration and normal sudo/package-review prompts. It may replace stock mpv
with the compatible mpv-omniphony package through the displayed transaction.

```fish
git merge --ff-only origin/fix/positioned-surround-audit
and env EDITOR=nano VISUAL=nano bash install.sh
```

Do not run the disposable VM provisioning scripts on the desktop. After the
first Companion installation, sign out and back in so Plasma loads the packaged
widget. Continue in Konsole after login.

### 2. Check the actual device before playing anything

Connect the WF-1000XM5 through KDE. Set that physical device's volume low in KDE
Sound before the first audible test. Neither the installer nor this procedure
changes the global default output. Leave unrelated applications alone.

```fish
set -l xm5_reports (mktemp -d "$HOME/spatial-xm5-20260928.XXXXXX")
systemctl --user is-active spatiald.service
spatialctl doctor
spatialctl status
spatial-verify --output "$xm5_reports/connected.json"
```

Require `connected`, `audio_ready`, and `controls_ready` to reflect the physical
XM5. Confirm `target` is its actual A2DP sink. Inspect the installed Companion's
real device controls and new cards. `spatialctl status` and `doctor` are local
inspection output and may contain device identifiers; share the redacted reports
instead.

`WAIT` is incomplete evidence, not a hidden pass: an idle player has no active
decoder ABI proof, and an absent optional Slime tracker can remain waiting. A
`FAIL` needs its actual cause resolved. Do not disable checks just to turn the
overall result green.

### 3. Establish real earbud tracking

Look for a fresh `earbud` row in `spatialctl status`. Enable **Use Earbud Head
Tracking** in Companion. For this XM5-only test, leave optional tracker audio
permission off; an enabled fresh optional tracker otherwise takes priority.
The CLI equivalent is `spatialctl enable 'ACTUAL_ID_FROM_STATUS' on`, using the
actual freshly reported earbud ID. Require `active_id` to equal `earbud.id`.

If no earbud row appears, inspect `runtime.providers.sony_tracker`. Missing HID,
HID permission failure, and an unsupported report layout are different issues.
The provider validates the exact connected device and supported report format;
Companion control compatibility alone does not validate the WF sensor format.
Reconnect once after installation if access rules need to take effect. If the
layout is unsupported, retain diagnostics and inspect it before adapting the
decoder. Do not bypass the descriptor check or inject simulated poses.

### 4. Exercise PCM and physical motion

Choose an ordinary local audio file longer than 45 seconds. Stop existing Spatial
Audio playback yourself first; the verifier preserves it by default. In this
Konsole, run:

```fish
read -P 'Path to a local music file: ' xm5_pcm
spatial-verify --media "$xm5_pcm" --seconds 30 --timeout 90 --output "$xm5_reports/pcm.json"
```

During that interval, use a second Konsole for `spatialctl status` and
`spatialctl recenter`. Require the real standalone PCM module's
`runtime.live.renderer_ready`, `runtime.live.routing.state` of `ready`, an
active earbud source, and the fresh graph check's `playback_route: PASS`.
With EQ enabled, its state must also be active and its graph must reach the same
physical headphones. The media decoder's separate `renderer_abi` can remain
`WAIT` for ordinary PCM; inspect the standalone module instead of claiming Atmos.

Face forward and recenter. Slowly turn left/right, nod up/down, and tilt each
way, returning to center between movements. The scene should stay fixed relative
to the reference established by recentering. Record what you actually hear;
some recordings reveal elevation or tilt poorly. Recenter while facing a new
direction and confirm the new reference. Verify both earbuds sound correct, no
speaker receives the test audio, and playback stops when the owned test ends.
The D-Bus status intentionally omits sensor-rate orientation. Neither a fresh
tracker row nor the verifier's snapshot proves physical axis signs or listening
quality by itself.

### 5. Check encoded spatial audio separately

Use a known, legitimately available encoded Atmos file longer than 45 seconds:

```fish
read -P 'Path to a known encoded Atmos file: ' xm5_atmos
spatial-verify --media "$xm5_atmos" --require-spatial --seconds 30 --timeout 90 --output "$xm5_reports/atmos.json"
```

Require `spatial_content`, `renderer_abi`, and `playback_route` to pass with a
positive decoded object count. During playback, require
`runtime.audio.tracking: true` and the intended active earbud identity, then repeat
the physical movement/recenter check. `--require-spatial` proves object rendering
and routing; it does not itself require or validate physical tracking. If no
known encoded sample is available, leave this check pending. Ordinary PCM and an
Atmos catalogue label cannot substitute for decoded objects.

### 6. Check disconnect/reconnect and an actual application

Repeat the owned PCM test using a new output filename, then disconnect the
earbuds through KDE while it plays. That interrupted verification is expected
to report incomplete/failed playback. Confirm the test stops and never moves to
speakers. Reconnect; require readiness against the new physical output session,
then explicitly start another owned test and confirm recovery. Do not expect
automatic replay of a stopped test.

For an ordinary application, finish the owned media test first, start quiet
playback in the chosen application, then inspect `spatialctl live list`. Select
the exact actual stream serial using Companion or
`spatialctl live start 'ACTUAL_STREAM_SERIAL'`. Run `spatial-verify` with a new
report filename while it is active. Confirm the selected stream alone passes
through the renderer/EQ to the XM5, and that other streams and the global default
are unchanged. `spatialctl live stop` should restore the selected application's
previous route. A game's speaker/surround mix is appropriate; already-binaural
audio does not expose its original object or speaker positions.

### 7. Retain the physical result

```fish
spatial-diagnostics --logs --output "$xm5_reports/diagnostics.json"
printf 'Local reports: %s\n' "$xm5_reports"
```

Reports are private, redacted, and remain local until the user shares them.
Review them first. Record the installed commit/packages, physical headphone
model, actual source mode, tested axes, listening result, and any pending checks.
Do not describe SlimeVR, TIDAL authorization, or physical motion as tested unless
those checks actually ran. Existing report filenames are deliberately not
overwritten; use a new directory or a new filename for retries.

## Historical local handoff — September 19, 2026

Historical handoff snapshot. The cloud audit resumed September 28; consult
[STATUS.md](../STATUS.md) for current validation and work. The runs below that
were running at handoff have since completed; this section records their original state.

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
