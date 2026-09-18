# Architecture and behavior contract

Spatial workbench 0.2 coordinates the existing Linux desktop audio stack. It does
not replace BlueZ, PipeWire, WirePlumber, BudsLink, or the renderer's DSP. The
physical WF-1000XM5 stays the final endpoint; source access, tracking, rendering,
EQ and native UI expose their own readiness and error states.

## Module and process boundaries

| Module | Actual execution boundary | Responsibility and failure scope |
|---|---|---|
| BudsLink | Existing upstream process | Sony settings and controls; losing it removes control availability |
| Core and desktop discovery | `spatiald` process with independent async tasks | Preferences, connection epochs, current output, capabilities and tracker choice |
| Sony tracking | Adapter task plus owned upstream `sony-tracker` process | Identity-checked HID orientation; helper loss removes the earbud source |
| Optional tracking | Independent nonblocking HID readers/tasks in spatiald | Actual receiver identities and orientation; one failed reader does not stop others |
| Encoded playback | Owned mpv-omniphony process with matching liborender and bridge | Media decoding, object metadata, binaural rendering and private IPC |
| Live PCM | Owned standalone orender process plus routing task | Temporary input for one selected stream, binaural PCM and route restoration |
| TIDAL | Lazy provider with bounded network work in worker threads | Account state, source resolution and manifest validation; failure affects TIDAL requests |
| Reference EQ | Owned PipeWire filter-chain client process | Device-specific correction and limiter; missing backend does not block ordinary playback |
| Companion UI | Existing Plasma process, patched native QML | State display and user commands through session D-Bus |
| Media controls | Separately supervised task in spatiald | MPRIS interface for the owned player |

These are practical fault boundaries, not containers or security sandboxes.
Optional tasks catch failures, clear their own readiness and retry with bounded
backoff. Renderer and filter processes have explicit cleanup. Python adapters
share the daemon's process and can share its failure if the whole daemon exits.
The systemd user service restarts the daemon; it has no `Requires=` dependency
on BudsLink, trackers or the audio server. The reusable helper supervisor is a
library primitive, not a separate service-manager product.

Only BudsLink owns its Sony control connection. Existing sound/ANC/multipoint
features remain upstream API responsibilities. The Sony motion helper uses a
distinct HID sensor interface; coexistence and WF firmware support still require
hardware validation. No module restarts the entire desktop audio stack to repair
its own state.

## Discovery and session lifetime

BlueZ connection events establish the selected headphone session. Automatic
selection requires the actual `WF-1000XM5` model name and an unambiguous device;
an explicit Bluetooth address can select one of several matches. BudsLink device
paths are taken from its real manager and matched to that identity. The UI does
not construct paths from a guessed name.

PipeWire snapshots resolve the current physical device and A2DP output. Idle or
suspended output is usable; requiring an already-running stream would prevent
startup. Node IDs and serials are session observations, never persistent device
identities. A disconnect/reconnect increments the epoch, and callbacks from the
previous session cannot attach stale nodes or telemetry.

BlueZ, BudsLink, PipeWire and tracker readiness are independent. Discovery calls
have deadlines and retry without fixed startup sleeps. A BudsLink outage does
not imply that audio disconnected. A PipeWire outage does not imply that the
optional receiver disappeared. LDAC is reported only when observed; the project
does not promise or force a negotiated 990 kbps rate.

## Tracking policy

1. Use the explicitly enabled optional tracker when it supplies fresh valid poses.
2. Otherwise use the earbuds when their own permission is enabled and poses are fresh.
3. Otherwise send a neutral pose and render statically.

New physical identities start with audio permission **OFF**, even though sensor
provider discovery is enabled by default. Preferences persist by stable identity.
Enabling a different optional tracker disables the previous optional choice;
the earbud permission remains independent. No selection is inferred from a body
role, device nickname, or receiver presence.

The visible optional label is `Use Optional Motion Tracker:`. Its plain-text
identifier comes from the actual receiver registration. SlimeVR nRF hardware
addresses use the upstream uppercase 12-digit representation. A registration
without orientation does not create a ready tracker, and a stale sample removes
availability. Receiver slots are transient and never substitute for hardware IDs.

Packets require finite valid orientation, increasing sequence numbers and the
current provider generation. Freshness uses local monotonic receipt time, with a
default 500 ms timeout. Reopening a source changes its generation. Recenter and
handoff alignment occur in the policy core; no reset is sent to the receiver.
Physical turn/nod/tilt and mounting checks remain necessary despite protocol tests.

The canonical quaternion is normalized `(w,x,y,z)`, head-to-world, with X right,
Y up and Z back. The renderer adapter converts it to world-to-head ADM coordinates
as `(w,-x,z,-y)`. It sends low-overhead pose updates over local OSC; UI state omits
the sensor-rate stream. Disabling a permission stops audio's use of that source;
it does not unpair hardware or disable another application's tracker use.

See [tracker contracts and supported HID devices](tracker-runtime.md).

## Playback and routing

Encoded spatial sources go through mpv-omniphony's `orender` decoder and the
format bridge. Strict spatial startup stays paused until fresh matching renderer
configuration, binaural state and nonzero decoded object counts are confirmed.
A capability flag or Atmos catalogue tag alone cannot prove an Atmos stream.
The installed patched engine's capability marker is verified; its control socket
binds to IPv4 loopback. The exact compatible library is passed to the player.

The player initially targets the native XM5 sink using its current name and
serial. Owned player streams require `node.dont-fallback`. The standalone adapter
also disables moving/reconnecting by default; the daemon permits those operations
when its configured PCM route needs to move the stream. Graph audits verify the
corresponding expected properties, process ownership and actual outgoing links.
An unexpected target stops the owned player. A newly reconnected endpoint does
not authorize the previous session's playback.

For codecs decoded natively by mpv, such as FLAC or AAC, the daemon can route its
own current PCM stream through the separate live renderer. This is controlled by
`stereo_spatialization = true` and `live_enabled = true`, both defaulting on.
The embedded renderer is not applied twice to sources already using `orender`.
If another selected application occupies the live module, automatic routing waits.

Games and other applications require an explicit current stream selection. The
live module creates a temporary internal input and changes only that stream's
`target.object` metadata. It uses the pinned 0.5.2 engine's implemented
`render.input_mode: live` configuration. The later inspected development snapshot
renames that mode to `pipewire`; the unimplemented `orender input-live` command
is not used. It does not capture the headphone monitor or mix a second copy with
the application's direct path. Clear PCM has channel positions, not recovered
Atmos object metadata, and is reported accordingly.

Stop restores the prior metadata only while it is still the module's route; a
newer user routing choice takes precedence. A packaged WirePlumber policy hook
blocks fallback for the exact protected source/target serials if its temporary
input disappears. The runtime refuses application routing until that hook
advertises readiness. This handles a renderer exit inside policy rather than
relying on a polling deadline. Failure handling also checks/mutes the selected
stream before restoring a potentially different destination. Existing unowned
applications do not inherit the renderer's process properties. Actual abrupt
renderer loss, policy timing and channel negotiation still require live host
tests; they are not established by mocked graph observations. See
[application routing and recovery](live-audio.md).

No module changes the global default sink. Internal input/filter nodes can appear
in graph tools; this is compatible with the physical headphones remaining the
final endpoint. A complete independently audited intermediary may be accepted by
the player auditor. An owned input still waiting for its final output link remains
pending and can never count as a verified route.

## Reference correction and gain

Device-wide correction uses a separate PipeWire filter-chain client and
WirePlumber smart filters. Applications keep targeting the physical XM5; policy
inserts the matching correction chain. The target includes both current node name
and serial. Disconnect removes the module process; reconnection creates a fresh
chain. Missing plugins or an invalid profile affect EQ readiness independently.

The processing order is application or binaural output → profile preamp and PEQ
→ SWH limiter → physical headphones. The default is AutoEQ's sourced DHRME
WF-1000XM5 first-five-filter recommendation, including its actual -2.6 dB preamp.
The downstream limiter uses a -1 dBFS sample ceiling and zero makeup gain. This
is not a true-peak measurement or compensation for every downstream volume or
hardware processing setting. Sony's own EQ is independent and is not changed.

`equalizer_enabled = false` disables the whole module; `equalizer_profile` can
select a validated AutoEQ text file. Full profile provenance, gain bounds and real
limiter test evidence are in [equalizer.md](equalizer.md).

## Source, API and persistence boundaries

TIDAL device authorization runs only on an explicit `spatialctl tidal login`,
opening TIDAL's own page. The daemon restores protected local tokens but never
opens a login browser on its own. Token files are mode 0600 in a private state
directory; these permissions are not encryption. Bounded network workers retain
ownership of temporary prepared sources through cancellation and cleanup.

`tidal_require_atmos = true` is the default. Manifest mode and actual E-AC-3 codecs
must agree; encrypted, AC-4 and substituted stereo streams are refused. Setting
it false explicitly requests ordinary lossless audio. Decoder readiness provides
a separate final evidence gate. Album/playlist queues advance on confirmed EOF,
not a process crash or lost headphone connection. See [TIDAL](tidal-runtime.md).

Session service/interface `org.spatiald.Control1` lives at
`/org/spatiald/Control1`. Its read-only `State` is schema-1 JSON. Methods cover
tracker permissions, recentering, playback and explicit application routing.
Diagnostic start/stop methods use request tokens so late cleanup cannot stop a
newer user request. Player media controls use `org.mpris.MediaPlayer2.spatiald`.
The complete signatures are in [desktop-runtime.md](desktop-runtime.md).

User configuration is `~/.config/spatiald/config.toml`; permissions are saved
separately in `preferences.json`. Failed writes restore the previous in-memory
choice. Optional dependencies are loaded when needed. Public status excludes
credentials and signed media URLs; diagnostic reports redact identifying data
and stay local. None of these contracts implies a completed physical desktop test;
[release status](../STATUS.md) records what has actually been verified.
