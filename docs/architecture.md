# How BudsLink Spatial Companion fits together

The Plasma widget is the control surface. A user service coordinates independent
modules for sources, head pose, rendering and the final headphone route.
BudsLink continues to own Sony device controls.

```text
Local file / TIDAL ──► owned mpv ──► spatial decoder or native PCM ─┐
Selected application ─────────────► positioned PCM renderer ─────┤
                                                               ▼
Sony HID / SlimeVR ──► pose policy ──► renderer orientation     EQ + limiter
                                                               │
                                                               ▼
                                                        physical headphones
```

## Module guide

| Module | Responsibility | Reference |
|---|---|---|
| `plasma/` | Native playback, TIDAL, application and tracker controls | [Desktop](desktop-runtime.md) |
| `desktop.py`, `cli.py`, `mpris.py` | Session D-Bus, commands and system media controls | [Desktop API](desktop-runtime.md) |
| `runtime.py` | Coordinate work, playback ownership, retries and cancellation | [Detailed contracts](architecture-reference.md) |
| `discovery.py`, `pipewire.py` | Current Bluetooth identity and audio graph evidence | [Audio](audio-runtime.md) |
| `trackers/`, `core.py`, `pose.py` | Decode real packets, select permitted fresh sources, recenter | [Tracking](tracker-runtime.md) |
| `timing.py` | Bounded timing history and optional motion prediction | [Timing](head-tracking-timing.md) |
| `tidal.py` | Explicit account authorization, catalogue and original stream retrieval | [TIDAL](tidal-runtime.md) |
| `audio_runtime.py`, `playback.py` | Owned player process and decoder/renderer evidence | [Playback](audio-runtime.md) |
| `live_audio.py`, `wireplumber/` | One selected PCM stream and conditional route restoration | [Application audio](live-audio.md) |
| `equalizer.py` | Device-scoped reference correction and limiter | [EQ](equalizer.md) |
| `health.py`, `diagnostics_privacy.py` | Local failure evidence with bounded retention and redaction | [Diagnostics](automatic-diagnostics.md) |

## Contracts worth keeping

- **Ownership:** only the explicitly selected stream or owned player may move.
  Global output defaults stay under the user's control. New device sessions do
  not inherit permission to resume old playback.
- **Evidence:** strict Atmos requires supported original media and decoded objects.
  PCM channel labels and actual links must agree; an adapted eight-port node
  cannot excuse a stereo downmix.
- **Tracking:** canonical quaternions describe head-to-world orientation in
  right/up/back coordinates. Recenter happens in that frame. Stale, disabled or
  replaced sources cannot steer audio.
- **Timing:** host-monotonic capture and receipt times share a clock. The predictor
  needs fresh, credible motion and latency evidence; unknown timing leaves the
  observed pose unchanged.
- **Isolation:** unavailable TIDAL, optional trackers or EQ do not own other
  modules' availability. Blocking network work stays outside the desktop loop.
- **Privacy:** account tokens and signed media URLs stay out of normal status.
  Credentials are protected local files, not encrypted storage. Reports remain
  local unless the user chooses to share them.

## Compatibility and state

The public name and package names changed; these stable identifiers did not:
`spatialctl`, Python module `spatial`, `spatiald.service`, `org.spatiald.Control1`,
`org.mpris.MediaPlayer2.spatiald`, `~/.config/spatiald/`, and the upstream Companion
plugin ID. `budslink-spatial` is an additional CLI entry point.

Read [the detailed architecture reference](architecture-reference.md) for routing,
reference-frame and persistence contracts, and [status](../STATUS.md) for actual
validation. Source structure alone is never proof of hardware acceptance.
