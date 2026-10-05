# BudsLink Spatial Companion

Spatial audio, head tracking and a native TIDAL mini-client for **Arch/CachyOS + KDE Plasma**.
Play local files, select an application's audio, or browse TIDAL from the Companion widget.
Your headphones remain the final output; BudsLink retains the Sony device controls.

> Development build · October 5, 2026. The user confirmed clear channel
> direction changes and anchored left/right motion on WF-1000XM5 with prediction
> off. Updated packages pass 489 core tests, 7 native UI checks and all 15 installed
> audio gates on private PipeWire. Real TIDAL lossless FLAC now reaches the verified
> renderer/EQ/headphone route, and the user confirmed it sounds good. Atmos,
> refreshed panel interaction, physical Stop, pitch, roll, recenter and prediction
> checks remain open. See [current evidence and limits](STATUS.md).

## Install

From your normal KDE terminal:

```sh
git clone https://github.com/Lich-King-Ethan/spatial-workbench.git budslink-spatial-companion
cd budslink-spatial-companion
env EDITOR=nano VISUAL=nano bash install.sh
```

The repository URL has not changed. For an existing checkout, run the last command
from its directory. The installer builds pacman packages, includes the TIDAL
client dependency, preserves settings, and keeps normal sudo/package prompts.
It runs in Bash while your interactive shell can stay fish.

You need an updated Arch/CachyOS desktop and normal sudo access. Native builds
need substantial RAM; see [installation, upgrades and removal](docs/install.md).
The pinned `mpv-omniphony` package replaces stock mpv through pacman.

## Listen

1. Connect your headphones normally and open **BudsLink Spatial Companion**.
2. Choose a local file, an application stream, or the **TIDAL** card.
3. Enable permission for the tracker you want, face forward, and select **Recenter**.

After installing updated widget files, sign out and back in when convenient so
Plasma loads them. The existing widget identity and panel placement are retained.

The TIDAL card combines Search and Library navigation, artwork, collection
browsing, playback controls and visible playback errors. Authorization happens on TIDAL's own page in your browser;
reopen the Companion after authorizing to finish sign-in. No separate music
application is required. Your library includes saved tracks/albums, followed
artists and owned/saved playlists, including folders. The panel starts with
**Max (best available)** and uses a lower available rendition when needed. Choose
CD quality, AAC 320/96 kbps or strict **Atmos only** from the quality menu. This is an unofficial
client: desktop Atmos availability is not guaranteed, and a catalogue badge does
not prove that TIDAL returned an Atmos stream. See [TIDAL usage and limits](docs/tidal-runtime.md).

Ordinary stereo/surround sources remain channel audio. Supported encoded spatial
sources retain object metadata through the decoder. Device-wide reference EQ
uses a sourced WF-1000XM5 profile and limiter. New tracker identities start with
audio permission off. Optional modules fail independently.

The CLI is available as `budslink-spatial`; existing `spatialctl` commands still work:

```sh
budslink-spatial play /path/to/music.flac
budslink-spatial status
budslink-spatial recenter
budslink-spatial stop
spatial-verify
```

## Explore

| I want to… | Read |
|---|---|
| Install, upgrade or recover | [Installation](docs/install.md) · [Diagnostics](docs/automatic-diagnostics.md) |
| Understand the modules | [Architecture](docs/architecture.md) · [Documentation index](docs/README.md) |
| Tune tracking delay | [Timing snapshots and optional prediction](docs/head-tracking-timing.md) |
| Route games or other apps | [Application audio](docs/live-audio.md) |
| Develop or validate changes | [Contributing](CONTRIBUTING.md) · [Current evidence](STATUS.md) |
| Check reuse and licensing | [Licensing](docs/licensing.md) · [Upstream dependencies](docs/dependencies.md) |

Original project code is **AGPL-3.0-only**. Commercial use is allowed under its
terms. The derived Plasma widget remains GPL-3.0-or-later; third-party code and
assets retain their notices. Earlier MIT releases keep their original permissions.
