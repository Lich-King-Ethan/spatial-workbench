# BudsLink Spatial Companion — status

**October 3, 2026 · development branch `fix/positioned-surround-audit`**

The project is now named **BudsLink Spatial Companion** (formerly Spatial Workbench).
Original code now uses AGPL-3.0-only; compatibility names and user settings remain.
Physical testing is **paused at the user's request**. The current source changes
must not be confused with the packages installed for the September 28 listening tests.

## Validation matrix

| Area | Evidence | Remaining work |
|---|---|---|
| Surround channel integrity | Two consecutive October 3 isolated runs pass all 15 gates: eight independent lanes, owned 7.1, 18 PCM windows/74 acoustic checks, four Atmos captures with 15 objects and cleanup | Live host needs dependency repair; earlier Atmos startup failure retained without a claimed cause |
| Actual WF-1000XM5 output | September 28 installed A2DP → renderer → EQ → headphones route; user heard clear channel direction changes | New build's physical regression pass |
| Physical yaw | September 28 corrected orientation compensates at unit gain; user reported near-centered anchoring that settles when still | Evaluate remaining motion lag |
| Pitch, roll and off-center recenter | Software fixtures cover rotations and recenter order | Physical listening acceptance pending |
| Timestamp capture | Packaged SPT1 helper passed native C/UDP checks; canonical pose, packet rejection and selected-report history checks pass in the full suite | Physical timing capture remains paused |
| Prediction | Bounded predictor, latency evidence and all accepted selected-packet history pass software regression; disabled by default | Physical lag/overshoot acceptance |
| TIDAL mini-client | Provider, D-Bus, credential ordering and catalogue/playback tests pass; Qt/JavaScript checks cover controls and sign-in recovery after browser handoff | Account authorization, real service playback and entitled Atmos acceptance |
| Rename and licensing | Renamed core/widget packages built; CLI aliases, compatibility metadata, source contents and retained licenses checked | Installed ownership and old-name upgrade acceptance |
| Optional SlimeVR receiver | Protocol, identity, freshness and policy tests | Physical receiver acceptance |

Synthetic endpoints and protocol fixtures prove software behavior; they do not
establish headset acoustics, a real user's account entitlement, or a complete
physical acceptance pass. Timing snapshots describe host events, not a sensor
clock or a measured acoustic output timestamp.

## October 3 software build

The full Python source suite passes **469 tests** without skips, including the
WirePlumber startup-race and TIDAL sign-in recovery regressions.
The Companion passes **7 native Qt checks**; its JavaScript state tests are included
in the suite. Recovery checks hide the actual popup window, recreate its card,
and handle sign-in completion while closed without restarting hidden polling or
restoring a logged-out session. Local package builds have completed for
`budslink-spatial-companion 0.2.3-2`,
`plasma-budslink-spatial-companion 0.2.0.spatial0.2.3-3`, and
`sony-tracker-spatial 1.0.0-2`. None was installed into the live desktop session.

Build concurrency was capped at 8 on the 32-thread, 31 GiB machine. The core
build used `makepkg --nodeps` only because pacman lacked records for
`python-build` and `python-installer`; the actual frontends were installed in the
private virtual environment. Other build dependencies were checked with pacman,
and the full package `check()` ran. This is local build evidence, not a fresh
clean-chroot or physical-upgrade result.

The October 3 private PipeWire rerun **failed before audio started**. The host now
has `omniphony-renderer v0.2.5` without the required patch marker, no installed
`orender-spatial`, Harletty `0.8.0-1` instead of pinned `0.7.3-1`, and an mpv binary
with unresolved shared-library dependencies. The installed audio stack is not
currently validated or working as the supported set. Failure evidence is retained
in `build/local-validation/native-review-20261003.1hatAl/`.

The isolated compatible stack then exposed a real WirePlumber 0.5.18 startup race
in the route guard. The fix obtains the metadata manager from the event's source,
matching the installed stock hooks, with a regression test. After that fix, all
15 native gates passed twice consecutively in `native-pinned-review-20261003.abukawxy/`
and `native-pinned-review-20261003.rbtb2hc1/`. An earlier
isolated run, `native-pinned-review-20261003.ji60e8_s/`, failed an Atmos startup
graph-stability check. Its evidence is retained. The successful repeats do not
establish that failure's root cause, and no acceptance predicate was relaxed.

This validation uses an isolated pinned renderer/bridge/player and extracted
signed dependency packages. It does **not** repair or validate the installed
host audio stack. No desktop repair or hardware test was performed.

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
