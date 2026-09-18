# Dependencies and source contracts

Inspected September 17–18, 2026. Source revisions identify the contracts reviewed
for this release. They do not replace runtime capability checks or establish
hardware compatibility. The build uses distribution packages first, existing
reviewed AUR packages for upstream audio tools, and local packages for this
project's code and revision-specific patches.

## Installed components

| Component | Package/source | Use in 0.2 |
|---|---|---|
| Python 3.11+ | Official Arch | Daemon and CLI; system Python is never modified with pip |
| dbus-next | Official `python-dbus-next`; Python constraint `>=0.2.3,<0.3` | Session controls, BlueZ/BudsLink observation and MPRIS |
| PipeWire and audio modules | Official `pipewire`, `pipewire-audio` | Existing audio server, graph inspection and native filter-chain client |
| WirePlumber | Official `wireplumber`, version 0.5+ | Smart-filter EQ policy plus a scoped packaged hook preventing selected-app fallback |
| BlueZ | Existing official host stack | Bluetooth identity and connection events |
| BudsLink | User's existing upstream installation | Sole Sony control provider; not replaced by this installer |
| Companion | Local `plasma-budslink-companion-spatial` | Pinned upstream widget plus native playback/tracking/application cards |
| mpv-omniphony | Existing AUR package | Encoded source playback and embedded Omniphony decoder |
| Omniphony engine | Local `orender-spatial` from upstream 0.5.2 plus included patch | Matching CLI/library, verifiable loopback-only control |
| Decoder bridge | Existing AUR `harletty-bridge` | Upstream format decoding and spatial metadata |
| Sony helper | Existing AUR `sony-tracker` | Motion packets from a verified Sony HID sensor interface |
| SWH LADSPA plugins | Official `swh-plugins` | Actual stereo look-ahead limiter after headphone PEQ |
| tidalapi | Official `python-tidalapi`; Python extra `>=0.8.11,<0.9` | Device authorization, catalogue requests and clear stream manifests |

`bash install.sh` builds and installs the full selection. It installs missing
official build tools and uses existing `yay`/`paru` when available. Otherwise it
fetches the actual AUR recipes and builds them directly with makepkg; package
review prompts remain enabled. AUR metadata inspected for this release reported mpv-omniphony and
orender 0.5.2-1, harletty-bridge 0.7.3-1 and sony-tracker 1.0.0-1. AUR dependencies
resolve to their current reviewed package revisions when installed, so decoder
capability and ABI checks remain essential.

mpv-omniphony supplies/replaces mpv and libmpv; this is a package-manager
transaction, not a second unrelated executable. The local `orender-spatial`
package provides `orender=0.5.2`, conflicts with the unpatched `orender` package,
and installs its matching shared library. No script overwrites another package's
files. See [installation](install.md) and [packaging](../packaging/README.md).

The engine patch adds an explicit OSC bind policy and CLI/library capability
markers. The runtime requests IPv4 loopback, refuses an unpatched engine, and
pins the library path to avoid accidentally loading a separate Studio library.
Renderer DSP and the existing bridge ABI remain upstream. Source archives and
patches are checksummed; the package build runs the Rust bind-policy tests and
checks the resulting markers. Actual Rust/Arch compilation is pending on the
target environment. [Patch details](orender-loopback.md).

## Pinned and inspected source

| Repository/content | Revision | Purpose |
|---|---|---|
| maniacx/BudsLink-Companion, Plasma-Widget | `31c6b3802071a6efc6fbd5a821e9c97f97240fd7` | Packaged Companion base and native QML conventions |
| maniacx/BudsLink | `7405f6343f8390b5a78e358d3ad4c0870b2270bd` | D-Bus and WF-1000XM5 control contracts |
| mgth/Omniphony, v0.5.2 | `a8018cd78f813d5c760b40d49e60fb12afc22526` | Packaged engine source and release contracts |
| mgth/mpv-omniphony, v0.5.2 | `6b474e387d30fabec8927880a25075f288d675d3` | Player decoder, telemetry and release API |
| mgth/Omniphony, inspected development snapshot | `fc67346181f1c5908a1bc26968ff192eb09a531d` | Additional source audit; not the packaged engine pin |
| kdani3/SonyTrackerLinux | `f5326577c4ae1949c6cbce3d8a4905107a86452d` | HID discovery, binary packet and axis conventions |
| SlimeVR/SlimeVR-Server | `c11dc54ed1a745ffc4b15db7f820d8d0c1993990` | Receiver USB IDs, identity and pose decoding |
| SlimeVR/SlimeVR-Tracker-nRF-Receiver | `e3d66d5dbf70db47fe0e854fb0b00599bf2c20b3` | Repeated receiver identity reports |
| SlimeVR/SlimeVR-Tracker-nRF | `cbfff16ebed1659d4c0e9f361ded0030fb66855a` | Direct USB tracker identity reports |
| EbbLabs/python-tidal, 0.8.11 | `9c41fbe6b2f2cd9fa00dca11e83574fd929ec020` | Device authorization, sessions and manifests |
| jaakkopasanen/AutoEq | `7ae0f56d53074872b028649617a22bbb4232feb7` | Bundled DHRME WF-1000XM5 five-band profile and provenance |
| lullabyX/sone | `e6f85d5680881760aa2b6ac34177169db15f78ef` | Prior player audit; not a dependency |
| oliveiraethales/torrential | `f028ec587f698ee9448a2abc5cbf657e3dd7549b` | Atmos request/source audit; not a dependency |

## Why these playback components

[mpv-omniphony's integration](https://github.com/mgth/Omniphony/blob/v0.5.2/docs/mpv-omniphony.md)
uses its `orender` decoder to pass encoded access units into the engine and format
bridge. Stock mpv lacks this integration. The adapter checks installed decoder
options, the patched library marker, upstream's ABI handshake and real renderer
state. Nonzero decoded objects, rather than a filename or generic spatial
capability, establish object audio. Native-decoded PCM uses the separate live
renderer when enabled; it remains PCM.

The pinned 0.5.2 live route uses `render.input_mode: live`; a later inspected
development snapshot calls the mode `pipewire`. The runtime gates the release
contract. Upstream's `orender input-live` command exists in argument parsing but
its dispatcher is unimplemented; the application never invokes it. Per-application routing and
cleanup belong to this project's [live module](live-audio.md).

[SONE's inspected source](https://github.com/lullabyX/sone/blob/e6f85d5680881760aa2b6ac34177169db15f78ef/src-tauri/src/tidal_api.rs)
did not establish a metadata-preserving Omniphony handoff. It is no longer required.
[Torrential's player](https://github.com/oliveiraethales/torrential/blob/f028ec587f698ee9448a2abc5cbf657e3dd7549b/lib/services/audio_player.dart)
requests `DOLBY_ATMOS`, then hands the result to media_kit; that request alone
cannot prove object rendering. The shipped provider instead uses maintained
[tidalapi](https://github.com/EbbLabs/python-tidal) for explicit account login and
manifest retrieval, validates the actual response, and passes the original clear
source to our player. It does not use captured credentials or bypass DRM.

## Tracker and correction evidence

[SonyTrackerLinux](https://github.com/kdani3/SonyTrackerLinux/tree/f5326577c4ae1949c6cbce3d8a4905107a86452d)
documents testing on WH-1000XM5, with WF-1000XM5 expected but not established by
that evidence. This project's adapter checks exact Bluetooth identity, sensor
report layout and live packets before exposing a source. Companion compatibility
is a separate user confirmation. The Slime adapter implements the inspected nRF
HID protocol; arbitrary serial, Wi-Fi or custom receiver protocols are not claimed.

The bundled [AutoEQ reference](https://github.com/jaakkopasanen/AutoEq/blob/7ae0f56d53074872b028649617a22bbb4232feb7/results/DHRME/in-ear/Sony%20WF-1000XM5/README.md)
uses the documented first five filters and matching -2.6 dB preamp. Provenance,
source hash and license are included. PipeWire's native parametric EQ and SWH's
actual LADSPA limiter implement the graph; no unavailable FFmpeg filter plugin
is assumed. The official Arch limiter binary has been tested directly, while its
full PipeWire/WirePlumber path needs a real desktop check.

## Authoritative references

- [Arch PipeWire](https://archlinux.org/packages/extra/x86_64/pipewire/) and [pipewire-audio files](https://archlinux.org/packages/extra/x86_64/pipewire-audio/files/)
- [Arch python-dbus-next](https://archlinux.org/packages/extra/any/python-dbus-next/) and [python-tidalapi](https://archlinux.org/packages/extra/any/python-tidalapi/)
- [Arch swh-plugins](https://archlinux.org/packages/extra/x86_64/swh-plugins/) and [BlueZ](https://archlinux.org/packages/extra/x86_64/bluez/)
- [PipeWire stream properties](https://docs.pipewire.org/page_man_pipewire-props_7.html) and [filter-chain](https://docs.pipewire.org/page_module_filter_chain.html)
- [WirePlumber smart filters](https://pipewire.pages.freedesktop.org/wireplumber/policies/smart_filters.html)
- [Omniphony OSC contract](https://github.com/mgth/Omniphony/blob/v0.5.2/docs/osc-control-contract.md) and [binaural implementation](https://github.com/mgth/Omniphony/blob/v0.5.2/omniphony-renderer/BINAURAL.md)

Optional Omniphony Studio, EasyEffects and qpwgraph may be useful for inspection;
they are not required to run this release. Package/source evidence is distinct
from installed-host validation, which is tracked in [STATUS.md](../STATUS.md).
