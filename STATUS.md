# BudsLink Spatial Companion — status

**October 3, 2026 · development branch `fix/positioned-surround-audit`**

The project is now named **BudsLink Spatial Companion** (formerly Spatial Workbench).
Original code now uses AGPL-3.0-only; compatibility names and user settings remain.
The user authorized installation and preparation for a second physical smoke test.
The repaired packages are installed; the headphones are disconnected and the user
will connect them later. Physical listening and movement checks remain pending.

## Validation matrix

| Area | Evidence | Remaining work |
|---|---|---|
| Surround channel integrity | Actual installed stack passes all 15 gates on private PipeWire: eight lanes, owned 7.1, 18 PCM windows/74 checks, Atmos objects and cleanup | Physical regression; earlier unexplained Atmos startup failure retained |
| Actual WF-1000XM5 output | September 28 installed A2DP → renderer → EQ → headphones route; user heard clear channel direction changes | New build's physical regression pass |
| Physical yaw | September 28 corrected orientation compensates at unit gain; user reported near-centered anchoring that settles when still | Evaluate remaining motion lag |
| Pitch, roll and off-center recenter | Software fixtures cover rotations and recenter order | Physical listening acceptance pending |
| Timestamp capture | Packaged SPT1 helper passed native C/UDP checks; canonical pose, packet rejection and selected-report history checks pass in the full suite | Physical timing capture awaits the connected headphones |
| Prediction | Bounded predictor, latency evidence and all accepted selected-packet history pass software regression; disabled by default | Physical lag/overshoot acceptance |
| TIDAL mini-client | Provider, D-Bus, credential ordering and catalogue/playback tests pass; Qt/JavaScript checks cover controls and sign-in recovery after browser handoff | Account authorization, real service playback and entitled Atmos acceptance |
| Rename and licensing | Old-name packages replaced through normal prompts; core 0.2.3-3 fixes a real staging defect; 122/122 baseline configuration/widget files unchanged | Loaded desktop widget and physical regression acceptance |
| Optional SlimeVR receiver | Protocol, identity, freshness and policy tests | Physical receiver acceptance |

Synthetic endpoints and protocol fixtures prove software behavior; they do not
establish headset acoustics, a real user's account entitlement, or a complete
physical acceptance pass. Timing snapshots describe host events, not a sensor
clock or a measured acoustic output timestamp.

## October 3 installation and software checks

The reviewed ten-package audio repair was installed through normal Konsole,
fish and nano package prompts. The current set includes core
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
