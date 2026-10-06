# Local development handoff

## Current handoff — October 5, 2026

The quality/library update is installed as core **0.2.3-5** and Companion
**0.2.0.spatial0.2.3-5**. The first sudo prompt timed out; the normal retry succeeded.
Installed files match the reviewed packages, all 122 baseline files are unchanged,
both services are active, and all 15 installed private audio gates passed.
Max now accepts
the best available stereo rendition; CD/AAC 320/AAC 96 caps and strict Atmos are
selectable. The widget persists its quality choice through Plasma's configuration.
Library playlist pages include owned/saved playlists and folders. Source tests
and package suites passed 509 checks, native UI passed 8 with no skips, and real-account silent probes
decoded all four stereo quality choices. The CD request legitimately returned AAC
and was accepted. The installed AAC 96 trial decoded AAC but found the application
stream muted by KDE's saved state; it stopped and cleaned up without changing
settings or defaults. Its private report is
`build/local-validation/tidal-library-quality-20261005/tidal-aac96-2wqgm8b2/report.json`.
The user was asked whether this saved BudsLink mute is intentional. Resolve that
answer before a new listening trial; never substitute decoder progress for audible
acceptance. Hosted CI passed all Python lanes and the Arch package job for
`609ee88`; its renderer was cancelled by the newer documentation push. The matching
follow-up CI also passed; its renderer remains pending. See exact links in
[STATUS.md](../STATUS.md).


Continue on `fix/positioned-surround-audit` and [PR #1](https://github.com/Lich-King-Ethan/spatial-workbench/pull/1).
Preserve local work, saved settings, compatibility identifiers, fish/nano and
normal package prompts. The user connected the WF-1000XM5 and approved quiet
physical tests. Check fresh readiness before any new audible run, and coordinate
movement/listening cues. Testing paused to repair playback failures. The installed
private audio gate now passes, and the actual TIDAL lossless route and listening
are accepted. Atmos streaming, physical Stop and refreshed panel interaction
still need acceptance.

### Installed and tested

Core **0.2.3-4** and Companion **0.2.0.spatial0.2.3-4** are installed through normal
package prompts. The core package build passed **489 tests**; Companion passed
**7 native Qt checks with zero skips**. Sony **1.0.0-2**, renderer **0.5.2-3**,
Harletty bridge **0.7.3-1** and mpv **0.5.2-1** remain the compatible audio group.
All **15 installed audio gates passed** on private D-Bus/PipeWire with process
exit 0. The report is `build/local-validation/round2-20261005/installed-native-audio/report.json`.
Normal owned playback stopped cleanly before fallback cleanup: source and
renderer nodes were gone, no stale error remained, and supervision did not
respawn them. The report correctly leaves `hardware_validated` false. All **122
baseline settings/widget files remain unchanged**; seven reviewed installed
source files exactly match their intended versions.

| Check | October 5 result |
|---|---|
| Quiet channel clip, 30 seconds | User heard clear direction changes; native 7.1 reached renderer, EQ and physical XM5 |
| Left/right reference, 30 seconds | User reported anchored sound, including while moving; prediction off |
| Pitch, roll, off-center recenter, prediction comparison | Pending |
| TIDAL account | Sign-in and browsing work on the actual account |
| TIDAL Atmos selection | Service returned `StreamNotAvailable`; no Atmos playback established |
| TIDAL Lossless selection | Real stereo FLAC passed 12 fresh route audits through renderer/EQ to XM5 with fresh tracking; user confirmed the music sounds good |
| Updated panel | Shell refreshed and active, with no relevant QML journal errors; interactive acceptance pending |
| Revised installed audio path | All 15 private integration gates pass, including clean shutdown; synthetic endpoints do not establish headset listening |

### Repairs and next steps

Normal owned playback shutdown could leave a stale renderer “stream ended” error,
which blocked the first pitch attempt. The repair stops the owned source before
tearing down capture and waits for capture cleanup. Real retained renderer
failures still block a new start; tests cover both normal shutdown and failure.

Lossless failed for a separate reason: the private local DASH file inherited
mpv/FFmpeg's local-file protocol allowlist, which rejected its HTTPS segments.
The repair permits the required file/HTTPS/TLS/TCP protocols only for a retained
TIDAL manifest that passed URL, codec and encryption checks. The actual returned
FLAC DASH then decoded one second to null output. Ordinary files and URLs retain
their previous policy. No Atmos fallback or encryption workaround was added.

The installed music panel now combines Search/Library, artwork, bounded results,
collection navigation and transport controls. Lossless is the initial choice;
Atmos-only is explicit. Opening and decoder errors appear inside the panel,
including errors reported after Play was accepted. Native tests use Plasma's
actual palette and check contrast; interactive desktop acceptance remains separate.

The read-only TIDAL observation passed 12 strict snapshots over 10.12 seconds,
with media advancing 9.99 seconds. Every fresh audit confirmed player ownership,
renderer readiness, EQ and fresh XM5 tracking. The report is
`build/local-validation/round2-20261005/tidal-readonly-l4yqe14c/report.json`.
The user then confirmed: “Yes—music sounds good.” This establishes real account,
lossless route and listening acceptance for this playback. The observation changed
no playback, volume or settings and left the user's music running; physical Stop
acceptance remains pending.

Only `plasma-plasmashell.service` was then reloaded to load the updated UI. The
panel and audio service are active, all 122 baseline files remain byte-identical,
and no relevant QML errors appeared in the journal. A read-only follow-up ended
neutrally because the previous player had changed. The latest state is idle with
a TIDAL `StreamNotAvailable` provider error, not a renderer error. The cause of
that new request is unknown; do not attribute it to the shell reload or claim
uninterrupted playback.

Next, check refreshed panel interaction and coordinate any new playback and
physical Stop test. Then resume pitch, roll, recenter and prediction checks one at a time. Preserve the failed
attempts and the October 3 package/layout failures below. [Status](../STATUS.md),
[validation history](validation-environment.md) and [TIDAL usage](tidal-runtime.md)
carry the evidence boundaries; raw reports remain private under
`build/local-validation/`.

## Retained installation handoff — October 3, 2026

At this earlier handoff, the user authorized installation and preparation for a
second physical smoke test after the code review, **BudsLink Spatial Companion** rename and native TIDAL work.
Stay on `fix/positioned-surround-audit` and preserve local work, settings, normal
package prompts and runtime identifiers. At that point the headphones were
disconnected, and the user planned to connect them later. The second physical
round remained pending.

The reviewed ten-package repair was installed through normal Konsole/fish/nano
prompts. Core `0.2.3-3`, Companion `0.2.0.spatial0.2.3-3`, Sony `1.0.0-2`,
renderer `0.5.2-3`, bridge `0.7.3-1` and existing mpv `0.5.2-1` are now the installed
set. Five missing mpv library packages were added. All 122 baseline
configuration/widget files are unchanged; `spatiald.service` and WirePlumber are active.

Core revision 2 exposed a real local packaging error: a virtual-environment Python
installed the wheel under the builder's prefix, leaving the CLI missing after
old-name replacement. Activation stopped before service restart. Revision 3 now
uses system Python and `--prefix=/usr`, with a staging guard for module paths and
both launcher shebangs. Its rebuild used official Python frontends and passed
all 474 tests. The archive layout was verified before the corrected package was
installed. The affected project virtual environment was restored separately
without global pip installation. Preserve this failure record; the earlier
passing unit suite did not validate the installed package layout.

Non-audible `spatial-verify` reports software features PASS and hardware/playback
WAIT with the headphones disconnected. The actual installed stack passed all 15
native gates on private D-Bus/PipeWire using its `/usr` modules and libraries
without replacements. The final report is
`build/local-validation/round2-20261003/installed-native-audio/report.json`:
eight lanes, owned 7.1, 18 PCM windows/74 checks, decoded Atmos with 15 objects
and verified cleanup/defaults; `hardware_validated` is false. The Companion's
7 native Qt checks cover window hiding, card recreation and recovered sign-in
completion.

Earlier isolated pinned-stack runs passed all 15 gates in
`native-pinned-review-20261003.abukawxy/` and
`native-pinned-review-20261003.rbtb2hc1/`. They cover eight lanes, owned 7.1,
18 PCM windows/74 checks, four Atmos captures with 15 objects and cleanup.
The initial dependency failure in `native-review-20261003.1hatAl/` led to the
installed repair. The intermediate Atmos graph-stability failure in
`native-pinned-review-20261003.ji60e8_s/` remains unexplained. These reports live
under `build/local-validation/`; no tests were weakened.

The next step was to verify actual A2DP/HID readiness and low volume after
connection. September 28's corrected yaw was close to anchored and settled when
still, with perceived delay. Its baseline needed repeating before pitch, roll,
off-center recenter and prediction comparisons. Real TIDAL account/Atmos acceptance
was also open. October 5's current results above supersede that waiting state.

Start with [current status](../STATUS.md), [architecture](architecture.md),
[timing](head-tracking-timing.md), [TIDAL](tidal-runtime.md) and
[licensing](licensing.md). The material below is retained historical context;
its old package names and exact local checkout path describe that session.


## Actual-PC continuation — September 28, 2026

See [the local acceptance record](local-pc-validation-20260928.md) before replaying
the procedure below. This checkout preserved earlier work on
`preserve/local-pc-20260928`, fast-forwarded to `9740dd5`, built/installed the
stack with normal prompts, and established actual WF-1000XM5 HID/A2DP/EQ readiness.
The user confirmed clear channel direction changes after the owned PCM fixes.
Physical movement then exposed a separate reversed Sony orientation mapping,
despite green synthetic renderer tests. Its absolute-helper/reference correction
was installed, with 356 core tests and all 15 native gates passing. The later
corrected yaw check passed with perceived lag that settled when the user held
still. Pitch, roll and off-center recenter remain unaccepted. See the latest
[physical record](local-pc-validation-20260928.md); private raw evidence remains
under ignored `build/local-validation/`.

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
