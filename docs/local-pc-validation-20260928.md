# CachyOS local acceptance — September 28, 2026

> Historical physical evidence. The package names below are the names actually
> installed that day, before the BudsLink Spatial Companion rename. The user
> paused further hardware testing on October 3. Later code, timestamp, prediction
> and TIDAL changes are not physically validated by this record.


The local PR branch fast-forwarded to `9740dd5`. Previous unpushed work is retained
in local checkpoint `87133e9` on `preserve/local-pc-20260928`, plus a private patch
backup. The newer upstream routing implementation remains the baseline. The
installed build additionally contains selected-device Sony HID profile recovery,
native owned-player PCM preservation, source-bound PCM routing authorization,
and stricter audits of extra unsettled source/renderer links.

## Installation and software evidence

The normal installer ran from Konsole using fish, nano and eight build jobs on a
32-logical-CPU, 31 GiB RAM desktop. Sudo, package transaction and recipe-review
prompts were retained. Installation completed successfully. No disposable VM
provisioning script ran on the desktop.

Installed and package-owned components:

| Package | Version |
|---|---|
| spatial-workbench | 0.2.3-1 |
| plasma-budslink-companion-spatial | 0.2.0.spatial0.2.3-2 |
| orender-spatial | 0.5.2-3 |
| harletty-bridge | 0.7.3-1 |
| mpv-omniphony | 0.5.2-1 |
| sony-tracker-spatial | 1.0.0-1 |

Initial core packaging ran 348 tests successfully without skips; the subsequent
owned-PCM correction rebuilt and installed the core with 352 passing tests.
Installed production module and verifier hashes match the checkout. Native Companion ran
six Qt checks successfully. The renderer's four targeted Rust checks passed.
All 116 files of the previous user-local Companion match their preserved backup.
No pre-existing spatiald configuration files existed; the installer created its
normal defaults. Existing PipeWire configuration was retained.

The installed-package integration gate ran with both `--require-renderer-audio`
and `--require-media-audio` on an isolated PipeWire instance with hardware
monitors disabled. It passed all 14 top-level checks, retained eight independent
surround lanes, passed all 74 acoustic assertions across 18 captured PCM windows,
and passed four genuine encoded Atmos captures through mpv, renderer and EQ.
Cleanup restored routes and preserved that isolated graph's global default.
These captured results establish software behavior, not physical listening.

The expanded gate subsequently passed all 15 checks in
`native-owned-pcm-20260928.QyLQY0/`. It used the exact rebuilt core wheel installed
in a temporary venv, private PipeWire/D-Bus and synthetic endpoints. The ordinary
system package installation completed separately with normal prompts; its
production files match that wheel's source. The new owned-player test exercises
actual automatic daemon routing and supervision, requires native 7.1 Format,
checks 44 verified observations over five seconds, and captures eight independent
input tags plus all eight post-EQ tags. The existing 18 PCM windows and four
encoded Atmos windows also passed. Encoded playback now uses the daemon's
`allow_pcm_route=True` launch policy while retaining strict binaural stereo and
decoded-object checks. No acoustic thresholds or production audits were relaxed.

Two earlier expanded-gate failures are retained with private analyses. One
incorrectly treated the graph monitor's partial metadata update as a full
snapshot; the other cancelled supervisors with an active captured player,
deliberately invoking the production failure mute and contaminating the next
test. The harness now uses fresh complete graphs for authorization and explicit
user-style playback/capture stop before ending supervisors. Neither failure is
represented as a successful run.

Private evidence lives under the checkout's ignored `build/local-validation/`:
`native-chain-20260928.KAfNtx/` contains the full audio reports and raw captures.
The normal install log is retained in the user's private spatiald state directory.
Reports have not been uploaded.

## Actual WF-1000XM5 observations

The first current device observation was paired but disconnected. The user
connected the earbuds and confirmed low volume. After installation, the device
remained in mono headset/mSBC mode with unresolved BlueZ services; the verifier
correctly withheld A2DP and tracking readiness. A single targeted reconnect,
performed only after confirming no active application audio, restored resolved
services, the actual A2DP sink and fresh supported Android sensor telemetry.

The service now reports `connected`, `audio_ready` and `controls_ready`. Earbud
tracking permission was enabled using the actual freshly reported identity;
`active_id` matched `earbud.id`. The EQ is active and its audited output reaches
the selected physical headphones. No synthetic tracker was injected into the
physical session. Ordinary user streams were not selected or rerouted for a test.

A freshly launched installed Companion window visibly renders the physical
headphone name, batteries, existing Sony controls and Playback card. This is not
a claim that the already-running panel has reloaded; a sign-out/sign-in remains
pending. A first screenshot captured the foreground Codex window and is not
accepted as UI evidence. The subsequent explicitly focused Companion screenshot
is retained locally in `companion-view-ms6r1n07/companion.png`.

## Generated listening clips

At the user's request, three 60-second, 48 kHz WAVE_FORMAT_EXTENSIBLE 7.1 PCM
clips were generated in ignored local storage. The actual encoded peak is
-24.44 dBFS. `front-reference.wav` supplies FC for yaw/pitch/recenter;
`left-reference-for-roll.wav` supplies FL for an off-axis tilt check;
`channel-check.wav` cycles front, front-left, front-right, rear-left and rear-right
at five-second intervals. LFE stays silent. Standard WAV order is
FL, FR, FC, LFE, BL, BR, SL, SR (mask 0x63f).

Independent ffmpeg decoding confirmed duration, channel layout, intended isolated
lanes and level. These files contain PCM, not encoded Atmos. The user has no
local encoded Atmos file available. An attempted physical test was refused before
playback because an existing Spatial Audio session was active; that session was
preserved. No listening pass is implied by generated or decoded fixture checks.

The next physical channel trial reached the actual tracker/renderer/EQ/XM5 chain
briefly, then the production player audit stopped playback after approximately
two seconds. It incorrectly required stereo at the native PCM capture input.
Closer inspection also found that mpv's forced stereo option had already
downmixed the 7.1 file: eight adapted graph ports did not prove native lanes.
This trial failed; it is not listening or surround-preservation evidence.
Its before/after node sets and global defaults matched, and no test stream was
linked to speakers. Private evidence is `physical-channel-check-bql_f44c/`.

The correction preserves native mpv PCM channels and accepts them only through
a source-session-bound route independently audited in the same graph snapshot.
Final renderer and EQ output audits remain strictly stereo. Additional tests
reject stale identities, wrong destinations, lost/duplicated/swapped channels
and incomplete extra links. The native owned-player integration described above
passed actual source format, eight independent input tones, automatic daemon
routing, rendered output and cleanup before the next physical clip trial.

The corrected installed application's 30-second physical channel trial
(`physical-channel-check-h9kdd6lc/`) passed playback, native 7.1 routing, live
renderer, EQ, physical tracker and owned cleanup checks. Independent inspection
confirmed actual 8-channel S16LE/48 kHz Format, eight matching active source
links, unmuted source, strict stereo renderer/EQ/physical links, and no unexpected
destinations. Before/after graph node sets and global default metadata matched.
The user reported: "Audible, with clear direction changes."

The overall verifier exit remained 2/WAIT for the absent optional tracker and
embedded media-renderer ABI, which this ordinary PCM trial does not exercise.
The standalone PCM renderer was ready. This is a physical PCM/channel listening
pass, not encoded Atmos or movement/recenter acceptance.

The subsequent front-reference movement check (`physical-front-reference-u3e46dfv/`)
passed routing but **failed physical direction acceptance**. The user clarified
that turning left made the sound move further left, turning right moved it
further right, and up/down also followed the head. A later raw-HID/read-only OSC
capture showed actual orientation reaching the renderer at unit gain, not frozen
tracking. Its chat-timed cue was missed, so it cannot establish labeled axes.
Spoken cues were then generated locally; a first 45-second attempt was refused
by the verifier's unchanged 30-second limit and never played.

The 30-second spoken-cue capture is retained as
`physical-guided-yaw-short-yz372da3/`, with synchronized actual HID and renderer
poses in `physical-pose-observer-s8ipw01t/`. The earlier code misinterpreted
Android's active head orientation as its inverse. The correction uses
an additive helper `--absolute` mode retains the sensor frame, the adapter maps
it to canonical head-to-world coordinates, and Engine alone recenters there.
The renderer's canonical rotation/acoustic expectations are unchanged.

The correction has now been built and installed. The checksummed Sony package
recompiles the pinned upstream C after removing its archived executable. Its
native UDP test covers nine absolute samples and eight unchanged legacy samples,
including a nonidentity startup reference; independent matrix error stays below
8e-16. Legacy helper behavior remains the default unless `--absolute` is requested.
The app explicitly requires that mode and rejects an older helper instead of
silently accepting incorrectly referenced poses.

The final core package passed 356 tests. The corrected tracking/routing source
also passed all 15 isolated native gates in
`native-orientation-20260928.jMwupF/`, including unchanged canonical acoustic
targets, native owned PCM, and genuine encoded Atmos. A final diagnostics-only
package inventory update was followed by a full core rebuild. Normal pacman
prompts replaced the legacy Sony package and reinstalled the core; actual
installed Python hashes match the checkout, package ownership checks pass, both
existing spatiald configuration files retain their exact hashes, and fresh
physical earbud tracking resumed.

The corrected guided yaw trial (`physical-guided-yaw-short-bc4zelcb/`, raw sensor
and renderer evidence `physical-pose-observer-5786jwje/`) passed the physical
route and cleanup checks. The user reported the sound was close to centered and
anchored, with slight lag, and confirmed that it settled when they held still.
Independent pose comparison confirms opposite compensation and essentially
unit gain: a left head hold near -40 degrees places the rendered front near
+39 degrees, and a right hold near +42 degrees places it near -43 degrees.
The mid-clip recenter restores the front to about 0.04 degrees. These readings
support yaw direction and settled anchoring, not zero-latency performance.
The actual LDAC sink reports 210.7 ms input latency in PipeWire; that is reported
transport latency, not a direct acoustic measurement. Pitch, roll and deliberate
off-center recenter listening remain pending.

An upstream checksummed E-AC-3/JOC fixture was subsequently prepared locally:
32 repetitions of complete frames produce 48.128 seconds without reencoding.
Offline native decoding reports 15 objects. Its -8.46 dBFS peak is higher than
the quiet generated PCM, so it has not been played physically; initial test
attenuation must be established before playback. Fixture bytes and manifests
remain in private ignored storage.

## Still pending

- Physical yaw/pitch/roll and recenter checks using the generated clips.
- Physical encoded Atmos listening, if a suitable sample is available.
- Disconnect during actual test playback with no speaker fallback, then recovery.
- Selection and restoration of one actual application stream.
- Reloaded panel inspection and user confirmation of listening/desktop behavior.
- Optional SlimeVR and TIDAL acceptance; neither is represented as tested.

Idle playback and absent optional hardware remain explicit WAIT results. Fresh
sensor packets alone do not establish physical axis signs or audible localization.
