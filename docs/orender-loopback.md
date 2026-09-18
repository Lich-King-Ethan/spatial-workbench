# The downstream Omniphony bind-address patch

The owned renderer accepts head pose, volume and other controls over OSC. Its
control socket must remain on localhost. In upstream 0.5.2, the embedded host's
`osc_bind` field and mpv's `--ad-orender-osc-bind` option do not reach the shared
engine's actual binding code. The standalone CLI uses that same code. Both
therefore bind their control listener to `0.0.0.0`, even when a caller requested
localhost. This was established by inspecting the tagged source, not by relying
on the option's name.

`packaging/orender-loopback.patch` fixes that boundary without changing DSP,
decoder support, tracker math, or existing library signatures. It changes four
upstream files:

* `orender_engine/src/osc.rs`: centralize production socket binding through
  `OMNIPHONY_OSC_BIND`. All listener/retry/resume-probe and sender socket paths
  use it. Invalid/non-UTF-8 values fail rather than falling back to a wildcard.
  When the environment variable is absent, upstream behavior remains intact.
* `src/cli/command.rs`: include the pinned `0.5.2+spatial-loopback1` in
  `orender --version` so the live PCM provider can verify the feature before
  starting the process. Source archives have no Git metadata, so the upstream
  version stamp alone would otherwise be `unknown`.
* `src/main.rs`: let Clap handle console help/version requests, including their
  successful exit status. Upstream propagated these requests as errors, causing
  the provider's real `--version` and `render --help` probes to fail.
* `orender_ffi/src/lib.rs`: export the additive function
  `orender_spatial_loopback_supported()`, which returns `1`. The existing ABI
  major/minor, frozen configuration structure and exported functions remain
  unchanged. mpv remains compatible with the matching engine package.

Every owned launch sets `OMNIPHONY_OSC_BIND=127.0.0.1`. The mpv adapter loads the
exact verified system library, rather than allowing an older Studio-installed
user library to override it. If the capability is missing, the feature reports
the required package and does not start an exposed listener. Upstream's
permanently local yield/resume notification sockets remain on `127.0.0.1`.

`orender-spatial` provides `orender=0.5.2` and `liborender.so=0-64`, and conflicts
with the original `orender` package because it owns the same CLI/library files.
This is a normal package-manager replacement. It does not put unmanaged
binaries under `/usr`. The engine and CLI are built together from the official
0.5.2 release archive. The archive, local patch and supplied
`packaging/orender-Cargo.lock` have enforced SHA-256 checksums. The tagged archive
omits the renderer workspace lockfile; `prepare()` installs our reviewed dependency
resolution so build and test can use `--locked`. The player and decoder bridge
stay separate packages.

On September 18, 2026, both native release artifacts were compiled successfully
with Rust/Cargo 1.98.1 on Ubuntu 24.04 x86_64, using privately extracted official
PipeWire 1.0.5 and libclang 18 development packages. The official archive's
SHA-256 matched the package recipe, and the final patch applied cleanly.

The package's actual `check()` passed: both added Rust address/binding tests,
successful CLI version/help requests, the required live-render options, and the
exported library marker. The real `AudioRuntime` child-process library probe
accepted the compiled Rust library. The package's `package()` also staged the
CLI, library, header, layouts and license successfully; the shared library's
SONAME was checked as `liborender.so.0`.

These checks establish native compilation and packaging-function behavior. They
do not establish an Arch `makepkg` installation or live PipeWire/Bluetooth/audio
operation: the development environment still has no headset, decoder bridge,
running audio server or usable Unix-domain audio/session socket. The normal
local `makepkg` build/check stage and desktop acceptance checks remain required.

Source references:

* [v0.5.2 OSC binding implementation](https://github.com/mgth/Omniphony/blob/v0.5.2/omniphony-renderer/orender_engine/src/osc.rs)
* [v0.5.2 embedded engine OSC startup](https://github.com/mgth/Omniphony/blob/v0.5.2/omniphony-renderer/orender_engine/src/engine.rs)
* [v0.5.2 standalone listener startup](https://github.com/mgth/Omniphony/blob/v0.5.2/omniphony-renderer/src/cli/decode/bootstrap.rs)
