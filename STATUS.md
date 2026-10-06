# BudsLink Spatial Companion — status

**October 5, 2026 · development branch `fix/positioned-surround-audit`**

The project is now named **BudsLink Spatial Companion** (formerly Spatial Workbench).
Original code now uses AGPL-3.0-only; compatibility names and user settings remain.
The repaired packages are installed. October 5 physical testing confirmed clear
channel direction changes and anchored left/right motion with prediction off.
Pitch, roll, off-center recenter and prediction comparisons remain pending.

## TIDAL quality and library access

The installed revision 5 update defaults to **Max (best available)** instead of requiring a
lossless rendition. It accepts a lower available stereo format, with explicit
CD, AAC 320 and AAC 96 ceilings. Atmos remains strict. Queue selections preserve
their quality, and the panel remembers the user's choice in its own Plasma
configuration entry. Actual stream tier and observed decoder format are displayed
separately from the selected maximum.

The library now includes playlist folders alongside owned and saved playlists;
saved tracks/albums and followed artists retain their own pages. Raw service
offsets preserve access to later rows when unknown collection types are filtered.

The complete source and package suites passed **509 tests**, and the native UI
package passed **8 checks with no skips**. Real-account silent probes loaded every library category
and decoded Max/FLAC, CD-request/AAC, AAC 320 and AAC 96 using the installed player.
The current account returned no root folders, so nested-folder acceptance uses
bounded fixtures, including 400 folders and later pages. Core `0.2.3-5` and
Companion `0.2.0.spatial0.2.3-5` are installed through normal package prompts;
their reviewed files match exactly and all 122 baseline files remain unchanged.
The first sudo prompt timed out; the retry succeeded. Both services are active
after the panel reload. All **15 installed audio gates passed** again.

The installed AAC 96 trial decoded AAC and reported approximately 96 kbps, but
stopped before route/listening acceptance because KDE restored a saved mute for
the BudsLink application stream. Cleanup removed owned nodes and preserved
defaults and settings. The mute's origin is unestablished; confirmation is pending
before changing that saved setting. Silent probes do not replace listening evidence.

For implementation commit `609ee88`, [CI](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/37391395844)
passed all three Python lanes and the Arch package job. The
[renderer build](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/37391398319)
was cancelled during compilation when the documentation follow-up superseded it.
The matching follow-up [CI](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/37391726857)
also passed. Its [renderer run](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/37391726891)
is still running; no result is assumed.

## October 5 physical checks

The connected WF-1000XM5 initially exposed LDAC audio but incomplete Bluetooth
service discovery. One targeted reconnect, after confirming no active application
audio, restored Companion controls and the real timestamped head tracker.

The user heard **clear direction changes** in the quiet channel clip and reported
that the yaw reference **stayed anchored, including while moving**. Both completed
30-second trials verified native 7.1 through the renderer and EQ to the physical
XM5s. Exact-request cleanup removed owned nodes and preserved default outputs and
settings. Prediction was off. The snapshots contain approximately 25 reports per
second with uninterrupted helper sequence numbers; they do not measure acoustic
latency.

Two yaw attempts stopped before a completed trial: one lacked a fresh tracker,
and one exposed the test runner reading the prior clip's cached clock while the
new player was loading. The runner now requires fresh playback ownership before
using that clock. It also marks an uncaptured defaults baseline as unavailable.
Both harness corrections passed offline regressions and independent review;
readiness deadlines and physical checks remain enforced.

The first pitch attempt refused playback because normal owned-player shutdown
could leave a stale renderer “stream ended” error. The installed repair now stops
the owned producer before releasing its capture route. All 15 installed native
audio gates passed, including a check of the first Stop before fallback cleanup
and a one-second watch for automatic restart. Failed attempts remain in private
local evidence; they are not listening passes.

## October 5 playback and Companion update

Core `0.2.3-4` and Companion `0.2.0.spatial0.2.3-4` were built and installed
through normal fish/Konsole package prompts. The package builds passed **489 core
tests** and **7 native Qt checks**, with no skips. All 122 baseline configuration
and widget files remained unchanged, and installed files match the reviewed source.

The compact TIDAL panel now brings search, library, artwork, scrolling collections,
transport and visible playback errors together. Lossless is its initial selection;
Atmos remains explicit. Native tests use Plasma's real theme and check control
contrast at narrow and normal widths. Plasma's panel service was reloaded after
installation; it and the audio service are active, with all 122 baseline files
still unchanged. Interactive acceptance of the refreshed panel remains pending.

Real account authorization and browsing work. The selected Atmos request was
rejected by the service. Lossless exposed a separate player defect: FFmpeg refused
HTTPS segments referenced by the validated local DASH manifest. The repair permits
those protocols only for that retained TIDAL manifest. A fresh real FLAC stream
then decoded successfully with null output. After installation, a read-only
ten-second observation of the user's TIDAL playback confirmed advancing FLAC stereo
through the binaural renderer, EQ and physical XM5 output with fresh tracking.
All 12 independent graph observations passed. Playback and volume were untouched.
The user confirmed **“Yes—music sounds good.”** A physical Stop check and Atmos
streaming remain pending.

## Validation matrix

| Area | Evidence | Remaining work |
|---|---|---|
| Surround channel integrity | Actual installed stack passes all 15 gates on private PipeWire: eight lanes, owned 7.1, 18 PCM windows/74 checks, Atmos objects and cleanup | Physical regression; earlier unexplained Atmos startup failure retained |
| Actual WF-1000XM5 output | October 5 native 7.1 → renderer → EQ → XM5 route; user confirmed clear channel direction changes | Selected-application and interruption/reconnect acceptance |
| Physical yaw | October 5 user reported anchored sound during left/right movement with prediction off | Prediction comparison and repeatability |
| Pitch, roll and off-center recenter | Software fixtures cover rotations and recenter order | Physical listening acceptance pending |
| Timestamp capture | October 5 channel/yaw captures contain fresh real HID reports at approximately 25 Hz, with no helper sequence gaps | These host timestamps do not establish acoustic latency |
| Prediction | Bounded predictor, latency evidence and all accepted selected-packet history pass software regression; disabled by default | Physical lag/overshoot acceptance |
| TIDAL mini-client | Real authorization/browsing, installed FLAC → renderer → EQ → XM5 route and user listening acceptance confirmed; compact native UI tests pass | Physical Stop, refreshed panel interaction and entitled Atmos acceptance |
| Rename and licensing | Old-name packages replaced through normal prompts; revision 4 installed; 122/122 baseline configuration/widget files unchanged | Remaining physical regression acceptance |
| Optional SlimeVR receiver | Protocol, identity, freshness and policy tests | Physical receiver acceptance |

Synthetic endpoints and protocol fixtures prove software behavior; they do not
establish headset acoustics, a real user's account entitlement, or a complete
physical acceptance pass. Timing snapshots describe host events, not a sensor
clock or a measured acoustic output timestamp.

## October 3 installation and software checks

The reviewed ten-package audio repair was installed through normal Konsole,
fish and nano package prompts. That installation included core
`budslink-spatial-companion 0.2.3-3`, widget
`plasma-budslink-spatial-companion 0.2.0.spatial0.2.3-3`,
`sony-tracker-spatial 1.0.0-2`, `orender-spatial 0.5.2-3`,
`harletty-bridge 0.7.3-1` and the existing `mpv-omniphony 0.5.2-1`.
The five missing mpv library packages were installed, and the installer now
requests them explicitly. All 122 baseline configuration/widget files are unchanged.

The first core installation exposed a real packaging defect: the local revision
2 build had used a virtual-environment Python, placing wheel files under its
builder-specific prefix and leaving `/usr/bin/spatialctl` missing after replacement.
Activation stopped before restarting services. Revision 3 uses `/usr/bin/python`
for build, check and installation, sets `--prefix=/usr`, and rejects incorrect
staging paths or CLI shebangs. The affected project virtual environment was
restored separately without global pip installation. Core was rebuilt with the
official `python-build` and `python-installer` packages; all **474 tests** passed.
The archive was checked for
an exclusively `/usr` payload and correct launchers before installation.

`spatiald.service` and WirePlumber are active. Non-audible `spatial-verify` checks
report software features **PASS** and hardware/playback **WAIT**, as expected with
the headphones disconnected. The actual installed audio stack passed all **15
native gates**, using its `/usr` modules, binaries and libraries on a private
D-Bus/PipeWire session without bind-over replacements. The report is
`build/local-validation/round2-20261003/installed-native-audio/report.json`: eight
input lanes, owned 7.1, 18 PCM windows/74 checks, decoded Atmos with 15 objects,
and verified cleanup/defaults. It explicitly records `hardware_validated: false`.

The Companion's **7 native Qt checks** pass; JavaScript checks are included in the
Python suite. They cover the real popup hiding, card recreation and recovered
sign-in completion without hidden polling or restoring a logged-out session.
Build concurrency was capped at 8 on the 32-thread, 31 GiB machine.

Hosted checks for commit `c680472` completed successfully in
[CI run 37161921772](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/37161921772)
and [renderer run 37161921733](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/37161921733).
Those runs predate the core revision 3 packaging and explicit mpv dependency
repairs; the local installation results above cover those follow-up changes.

## Earlier October 3 failures and isolated evidence

The first private PipeWire rerun failed before audio because installed renderer
and bridge versions had drifted and mpv could not load required libraries. Its
report remains in `build/local-validation/native-review-20261003.1hatAl/`.
That dependency failure was repaired by the subsequent package transaction.

The isolated compatible stack then exposed a WirePlumber 0.5.18 guard startup
race. The fix obtains the metadata manager from the event source, matching stock
hooks, with a regression test. All 15 gates subsequently passed twice in
`native-pinned-review-20261003.abukawxy/` and
`native-pinned-review-20261003.rbtb2hc1/`: eight independent lanes, owned 7.1,
18 PCM windows/74 checks, four Atmos captures with 15 objects and cleanup.
Those earlier runs used an isolated pinned stack and extracted signed libraries.

An earlier run, `native-pinned-review-20261003.ji60e8_s/`, failed an Atmos startup
graph-stability check. Its cause remains unestablished despite the successful
repeats. No acceptance predicate was relaxed, and none of these software runs
establish physical headphone or authenticated TIDAL acceptance.

## Evidence and history

- [September 28 physical-PC record](docs/local-pc-validation-20260928.md): installation,
  preserved settings, original rotation failure, corrected yaw and remaining delay.
- [Software validation details](docs/validation-environment.md): independent captures,
  withdrawn earlier claims, regression gates and limitations.
- [Release history](docs/release-history.md): exact prior hosted run links and tested
  commits. Those runs predate the current TIDAL, timestamp and branding changes.
- [PR #1](https://github.com/Lich-King-Ethan/spatial-workbench/pull/1): continuing branch review.
- [Development handoff](docs/local-development-handoff.md): current next steps followed
  by the retained historical procedure.

When physical testing resumes, finish pitch/roll/recenter, prediction comparison,
selected-application routing, disconnect/reconnect and recovery checks. TIDAL
Atmos requires both an eligible returned stream and actual decoded-object
confirmation. No account or physical result is assumed from catalogue labels.
