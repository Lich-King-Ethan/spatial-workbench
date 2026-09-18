# Spatial workbench

Version 0.2.1 adds native spatial playback, headphone tracking, application audio,
and device-wide reference EQ to BudsLink Companion on Arch/CachyOS and KDE Plasma.
The ordinary **WF-1000XM5 remains the final audio output**. BudsLink continues
owning Sony controls; separate modules own sources, sensors, rendering, and EQ.

## Build and install

Clone this repository or download and extract its source ZIP. With Git installed:

```sh
git clone https://github.com/Lich-King-Ethan/spatial-workbench.git
cd spatial-workbench
```

From this directory in your normal KDE terminal:

```sh
bash install.sh
```

Requires an updated Arch/CachyOS system, a normal KDE desktop session with sudo
access, and your working BudsLink installation. The command installs missing
official build tools automatically. It uses yay/paru when available, or shows
the actual AUR recipes for review and builds them directly with makepkg.

It builds the daemon, patched native Companion and pinned Omniphony engine;
installs the player, decoder bridge, tracker helper and optional TIDAL module;
creates user configuration; starts the service; and runs real host diagnostics.
Missing hardware appears as `WAIT`; failures retain full private build logs and
trigger a redacted diagnostic report. `mpv-omniphony` replaces stock mpv through
the normal package-manager transaction. See [installation and removal](docs/install.md).

## Use it

Connect the earbuds normally and open BudsLink Companion. Its native cards provide
file/TIDAL playback, application selection, tracker permissions and recentering.
System media controls handle pause, seek and queued tracks. Sign out and back in
once after installing the patched Companion so Plasma loads its updated files.

```sh
spatialctl play /path/to/music.flac
spatialctl tidal login
spatialctl play 'https://tidal.com/browse/track/YOUR_TRACK_ID'
spatialctl status
spatialctl recenter
spatialctl stop
```

TIDAL login opens TIDAL's own device authorization page. Atmos requests are strict
by default: a catalogue badge or substituted stereo stream is insufficient. Set
`tidal_require_atmos = false` in `~/.config/spatiald/config.toml` to request ordinary
lossless TIDAL audio instead, then restart `spatiald.service`. Account access and
actual Atmos delivery still need verification with your account.

- **Encoded spatial audio:** mpv-omniphony, the decoder bridge and Omniphony preserve
  supported object metadata and render binaural audio. Atmos status requires
  actual decoder/object evidence.
- **Ordinary music:** this player's decoded PCM is routed through the separate
  binaural renderer when `stereo_spatialization` is enabled, as it is by default.
  Stereo recordings remain stereo sources; they do not become Atmos.
- **Games and other applications:** explicitly select one current stream in the
  Application Audio card, or use `spatialctl live list`, `spatialctl live start SERIAL`
  and `spatialctl live stop`. Only that selection is rerouted; no global default
  changes. See [application audio and recovery](docs/live-audio.md).
- **Head tracking:** new earbud and optional tracker identities start with audio
  permission OFF. Enable the source you want. Fresh enabled optional tracking takes
  priority, enabled earbud tracking is the fallback, and otherwise rendering stays
  static. Receiver names come from actual received identities.
- **Reference EQ:** a sourced AutoEQ/DHRME WF-1000XM5 five-band profile is enabled by
  default on this physical device, followed by its own limiter. Other applications
  targeting the XM5 receive the same correction. Select a custom profile or disable
  the module in configuration. [EQ details](docs/equalizer.md).

## Verify your desktop

```sh
spatial-verify
spatial-verify --media /path/to/known-atmos-sample.mka --require-spatial
spatial-diagnostics
```

The first check is read-only; the media check plays your supplied file. Reports
are local and redacted. They distinguish passed, failed, waiting and disabled
checks instead of substituting generated sensor or playback data.

Persistent module failures also trigger a bounded local diagnostic collection
automatically. Reports stay private on your computer; nothing is uploaded.
See [automatic diagnostics](docs/automatic-diagnostics.md) for recovery behavior,
report locations and the configuration switch.

The implementation and automated tests are included. **Arch installation, Plasma
rendering, physical XM5/receiver behavior and authenticated TIDAL Atmos have not
been verified in this development environment.** Its Unix-socket restriction
prevents a real PipeWire/session-bus test. See [release status](STATUS.md) and
[validation evidence](docs/validation-environment.md) for precise limits.

The patched Rust renderer and matching shared library have compiled successfully;
their actual package checks and runtime library probe passed. Version 0.2.1 fixes
the missing upstream lockfile, source-archive version stamp and help/version exit
codes found during that native build.

More detail: [architecture](docs/architecture.md), [audio](docs/audio-runtime.md),
[trackers](docs/tracker-runtime.md), [TIDAL](docs/tidal-runtime.md),
[dependencies and source pins](docs/dependencies.md), and
[additional XM5 controls](docs/feature-modules.md).

To publish an extracted source archive as a new public GitHub repository under your signed-in
account, run `bash tools/publish-github.sh`. Use `--dry-run` to inspect the source
file list first. See [publishing instructions](docs/publishing.md) for account
selection and authentication; existing repositories are never overwritten.

Core: MIT. Companion integration: GPL-3.0-or-later, matching upstream.
The renderer, bridge, player and existing device tools retain their own licenses.
