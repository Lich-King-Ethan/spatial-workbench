# Audio runtime

`spatial.audio_runtime.AudioRuntime` runs an installed **mpv-omniphony** and
communicates over its JSON IPC and the embedded renderer's loopback OSC port.
It is an operational adapter, not an audio renderer implementation. The decoder
bridge and player remain separately maintained distribution/AUR packages. The
engine is packaged as `orender-spatial` from the pinned upstream 0.5.2 source
with a small OSC bind-address patch; its DSP and existing library ABI are unchanged.

The output is the existing WF-1000XM5 PipeWire sink. The module creates no
virtual output and never changes the desktop default. Other applications keep
their existing routing and continue if this module stops.

## Supported playback

* Local regular media files, including local DASH manifests.
* Explicit HTTP or HTTPS media URLs that the user is authorized to play.
* Object audio supported by the installed decoder bridge, rendered through
  Omniphony's measured KEMAR binaural stage.
* Ordinary audio through mpv's native decoders when the engine does not accept
  its codec. This is reported as ordinary audio, with no claim of head tracking.

mpv-omniphony 0.5.2 registers `orender` for TrueHD, E-AC-3, AC-3 and DTS. A FLAC
or AAC file can play normally but does not acquire the renderer's head tracking
merely because this player is being used. A stereo recording is never called
Atmos. The separate TIDAL provider owns authentication and stream selection.
It asks this module for strict spatial playback only after inspecting the
returned stream; this module performs its own decoder and object checks too.

## Startup and evidence

1. A usable A2DP sink must already exist; idle and suspended sinks are valid.
2. `mpv --list-options` must succeed and advertise the exact compiled
   `ad-orender` controls. Stock mpv is rejected. Upstream's opt-in decoder is
   absent from `--ad=help`, so that generic list is not a capability requirement.
3. The configured bridge must exist. A child process checks the engine library's
   `orender_spatial_loopback_supported()` marker; a plugin is never loaded into
   spatiald. mpv is given that exact library and performs upstream's ABI handshake.
4. A private configuration enables binaural mode before the decoder negotiates
   its channels. It requests measured KEMAR, automatic gain protection with a
   -1 dB ceiling, no additional room reverb, and no loudness compensation.
5. mpv starts paused. Media is supplied over private IPC, never appended to
   process arguments. The application uses generic player/stream titles, and
   child stdout/stderr are not exposed in status or journal output.
6. Readiness requires all of: an actively loaded track using the `orender`
   decoder, OSC capabilities declaring embedded/mpv, binaural output state,
   the exact generated configuration path, a `loaded` config status, and
   telemetry received within four seconds.
7. Object playback is reported only when the track profile contains the decoded
   nonzero object count that `ad_orender` publishes. The capabilities field
   `spatial: true` is never treated as proof that the source contains objects.

`start(..., require_spatial=True)` retains the initial pause until both renderer
readiness and decoded objects are confirmed. Failure to confirm them within
20 seconds stops the player and returns an explicit error. Ordinary local file
playback can use the default non-strict mode and reports its actual decoder path.

## Routing and disconnects

The selected native sink is passed as `--audio-device=pipewire/<node.name>`.
The owned process receives `PIPEWIRE_PROPS` pinning `target.object` to that
sink's exact current **object serial**, with `node.dont-fallback=true`.
Upstream PipeWire's `pw_stream` creation reads these environment properties into
the actual stream after the player's initial properties.

The standalone adapter defaults to `allow_pcm_route=False`, which also sets
`node.dont-move` and `node.dont-reconnect` true. The desktop configuration enables
live PCM and stereo spatialization by default, so it passes
`allow_pcm_route=True` and sets those two properties false. This lets the live PCM
module deliberately redirect this one owned stream. Both must be false:
WirePlumber otherwise refuses to move an already linked stream even after its
target metadata changes. The physical serial pin and no-fallback property remain
in place. The live module must additionally confirm the installed WirePlumber
guard before writing its temporary target; that policy hook prevents fallback
at target-selection time if the live renderer dies. The graph auditor checks
the expected reconnect policy for the configured mode, exposed in status as
`pcm_route_allowed`. The daemon enables this mode only when both live PCM and
stereo spatialization are configured. Disabling either restores the adapter's
fixed-routing policy on the next player launch.

`verify_output(pw_dump_objects)` additionally examines the live graph. It finds
the owned player's streams by process ID, including through their PipeWire
client, verifies their target and protection properties, and checks that every
outgoing link reaches the selected physical sink's current serial and name.
The desktop runtime stops playback on a violation. An independently audited
equalizer can supply its verified input IDs through `allowed_filter_inputs`;
that module must first verify its entire output chain reaches the same sink.

`update_sink(None)` or a different device, name, or object serial stops only
the owned player. Reusing a node name after reconnect does not authorize
continuing the old session. Playback never falls back to HDMI or speakers.

## Head orientation

Core poses are unit quaternions `(w,x,y,z)` describing head-to-world orientation
in right/up/back coordinates. Omniphony expects world-to-head orientation in
right/front/up ADM coordinates. The adapter converts them to `(w,-x,z,-y)` and
sends `/omniphony/control/head/quat`.

The daemon recenters in its policy core, preserving per-source offsets and
handoff continuity, then calls `set_pose(engine.pose)`. `set_pose(None)` sends
identity, producing static binaural audio. The last pose (including identity)
is retransmitted with the one-second heartbeat to recover from a lost UDP
datagram. `AudioRuntime.recenter()` is available for standalone callers; do not
also call it when the policy core is handling recentering.

The adapter does not start or stop physical trackers and does not claim them
exclusively. Permissions, tracker availability and user opt-in remain the
tracking module's responsibility.

## API

```python
audio = AudioRuntime(binary="mpv", bridge_path=None)
await audio.probe()  # capability report; does not play audio
await audio.start(media, selected_sink, require_spatial=False)
audio.set_pose(pose_or_none)
await audio.pause(True)
await audio.seek(5)  # relative seconds; absolute=True is supported
await audio.set_volume(70)  # player volume, 0..100; not desktop volume
await audio.update_sink(current_sink_or_none)
audio.verify_output(pipewire_objects)
state = audio.status()
await audio.stop()
```

`bridge_path=None` discovers the standard Harletty path, or a single unambiguous
`*_bridge.so` under `/usr/lib/orender`. It never guesses among multiple plugins.
`library_path=` can explicitly select a compatible patched liborender. The
default is `/usr/lib/liborender.so.0`, deliberately passed to mpv to avoid an
unpatched Studio-deployed user library taking precedence. `runtime_dir=` controls the
parent directory for a fresh mode-0700 session directory; generated config is
mode 0600. Files and IPC sockets are removed on stop. Credentials are not
included in the status dictionary.

Queue consumers use `end_reason == "eof"` for confirmed natural completion.
`running` alone is insufficient because mpv's idle process remains available
after its last file. Errors and disconnects do not advance a queue as though
the track had completed naturally.

## Validation and remaining physical checks

Tests cover OSC wire format and malformed input, real UDP loopback delivery,
coordinate conversion on all three axes, stock-player rejection, actual graph
auditing, stale/config-mismatched renderer state, strict object requirements,
IPC completion handling, and sink replacement/disconnect policy.

The development environment has no PipeWire daemon, Bluetooth controller, XM5,
decoder binaries, or desktop session; Unix socket creation is blocked here.
Consequently actual mpv IPC startup, rendering, audible head tracking,
headphone reconnect behavior, and post-filter clipping need the target PC's
diagnostic checks. A passing protocol test is not a claim of that hardware
validation.

The engine ceiling protects its own output. A later EQ or other postprocessor
needs its own verified headroom/limiting; engine clipping telemetry alone cannot
measure clipping introduced after the engine. This adapter does not add
unverified loudness boosts to compensate for headroom.

## Contracts inspected

* [Omniphony v0.5.2 binaural implementation](https://github.com/mgth/Omniphony/blob/v0.5.2/omniphony-renderer/BINAURAL.md)
* [Omniphony v0.5.2 state snapshots](https://github.com/mgth/Omniphony/blob/v0.5.2/omniphony-renderer/runtime_control/src/snapshot.rs)
* [mpv-omniphony v0.5.2 decoder](https://github.com/mgth/mpv-omniphony/blob/v0.5.2/src/ad_orender.c)
* [mpv v0.41.0 PipeWire output](https://github.com/mpv-player/mpv/blob/v0.41.0/audio/out/ao_pipewire.c)
* [PipeWire node policy properties](https://docs.pipewire.org/page_man_pipewire-props_7.html)

The release contracts were checked against engine tag
`a8018cd78f813d5c760b40d49e60fb12afc22526` and player tag
`6b474e387d30fabec8927880a25075f288d675d3`. Capability checks are still performed
on the installed binaries; version strings alone do not enable a feature.
