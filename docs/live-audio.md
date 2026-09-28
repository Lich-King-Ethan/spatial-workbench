# Application and game PCM spatial audio

The independent `spatial.live_audio` module implements explicit per-application
capture through the real Omniphony renderer. Select an actual playing application
stream, identified by its PipeWire object serial. The module starts its own
renderer and temporary input sink, moves only that selected application to the
input, and sends the rendered stereo to the selected physical WF-1000XM5 output.
It never changes PipeWire's global default. Stop restores that application's
previous routing metadata unless the user has chosen a newer destination.

The temporary input is an internal processing endpoint. The final output remains
the ordinary physical headphones, including the separately verified headphone EQ
when enabled. Capturing the headphone monitor is not used. The graph audit rejects
feedback, unselected applications entering the capture input, renderer links to
other outputs, replaced device serials, missing routing protections, and failed
links. Readiness requires the renderer's negotiated native format to contain the
fixed eight named channels, the source's native speakers to survive in its actual
graph ports, and each application channel to reach its matching renderer input.
Every renderer output channel must have an established path. A declared node
property or a process alone cannot establish channel preservation.

## Engine and content contract

The supported standalone engine is **Omniphony v0.5.2 with the packaged
`+spatial-loopback1` patch and positioned-input patch (`orender-spatial` pkgrel 2)**.
The runtime probes the version and necessary
CLI options before launch. It creates a private configuration and FIFO, starts
`orender render --continuous`, and uses v0.5.2's implemented
`render.input_mode: live` path. The separate `orender input-live` command is
unimplemented upstream and is not used. Later upstream source renamed this
configuration mode to `pipewire`; accepting an unverified version here would
silently change the contract.

The renderer input accepts eight-channel 48 kHz floating-point PCM in the fixed
FL, FR, FC, LFE, SL, SR, RL, RR layout. PipeWire negotiates the selected
application's channels into that input. Its renderer configuration enables
spatial channel rendering, binaural output and head-pose control. Stereo remains
channel-based stereo positioned in that layout. A game's already-binaural mix
cannot be decomposed into its original speakers or objects; use the game's
speaker/surround output for this path where available.

**This is PCM spatial audio, not recovered Atmos metadata.** Compressed Atmos
playback uses the separate mpv-Omniphony decoder path. Sending already-rendered
binaural mpv output through this module would apply a second binaural render;
callers must exclude that path. Renderer telemetry must independently confirm
the standalone CLI host, exact private configuration, PipeWire input node,
eight-channel F32 format, and binaural output. Controls bind only loopback;
see [the engine control patch](orender-loopback.md).

## Scoped routing and failure recovery

The packaged WirePlumber policy hook advertises the `spatiald.live-guard=1`
capability on subject 0 of the default metadata store. Installation must be
followed by restarting WirePlumber for that hook to become active. The runtime
refuses to move applications without the marker. Before changing a selected
stream's `target.object`, it records the source and capture object serials in
`spatiald.live-target`. The hook runs before normal target selection and blocks
fallback when that protected target is missing or incompatible. Node ID reuse
cannot transfer the protection to a different source serial, and an explicit
new destination chosen by the user supersedes the session.

Normal Stop restores the old `target.object` value and type, or removes only
that key if it was originally absent, then removes the owned guard marker. It
never clears an entire metadata subject or overwrites a later user choice.
Other applications and the global default are not modified.

On headphone loss, renderer failure or an unsafe graph, the supervisor first
mutes the selected application and verifies the mute against the same stream
serial. It then restores the previous route and terminates its owned renderer.
The application stays muted, with an explanatory status, until explicit Stop
restores its prior unmuted state or the user changes mute in KDE Sound. If mute
cannot be verified while the source still exists, the capture and route are
retained for safe recovery. The WirePlumber guard also handles the interval in
which the renderer disappears before the supervisor observes its exit; polling
alone would be insufficient to prevent the selected application falling back to
speakers. If the daemon is forcibly killed, a protected source can remain
unlinked until the user selects a different output. Global recovery or default
changes are intentionally not performed.

Module APIs are `await start(sink, stream_serial, objects)`, `await stop()`,
`await update_sink(sink)`, `cancel_start()`, `set_pose(pose)`, `audit(objects)`,
`status()`, and `available_streams(objects)`. Start schedules a cancellable owned
launch and returns immediately; slow renderer readiness does not block D-Bus.
`pending_input_ids` and `verified_input_ids` let an owned source's auditor
recognize this exact capture path while retaining separate readiness checks.
Verified EQ input IDs are accepted only after the EQ module proves its physical
output; pending EQ IDs keep the live path waiting, never ready.

## Verification boundary

Automated tests exercise exact stream identity, full channel-link readiness,
unsafe graph rejection, source-route transaction ordering, user route changes,
WirePlumber capability gating, version gating, prompt startup cancellation,
verified failure muting, and safe capture retention when muting fails. The
WirePlumber hook has a separate executable Lua policy test. These fixtures test
policy and process lifecycle, not an actual desktop graph.

The development host has no running PipeWire desktop, physical XM5, or installed
Omniphony renderer. Actual channel negotiation, game behavior, local latency,
physical head-motion directions, output privacy on process/device failures, and
KDE presentation still require the supported Arch desktop's integration checks.
The user's September 17, 2026 Companion confirmation does not independently
validate those new audio features.

Primary references:

- [Pinned v0.5.2 renderer source](https://github.com/mgth/Omniphony/tree/v0.5.2/omniphony-renderer)
- [PipeWire input implementation](https://github.com/mgth/Omniphony/blob/v0.5.2/omniphony-renderer/audio_input/src/pipewire_pods.rs)
- [WirePlumber target and linking policy](https://pipewire.pages.freedesktop.org/wireplumber/policies/linking.html)
- [PipeWire scoped metadata operations](https://docs.pipewire.org/page_man_pw-metadata_1.html)

## Correct placement of headphone EQ

The inspected Omniphony renderer includes HRTF processing, crossover filters,
gain control, and clipping telemetry. Its public configuration does not expose
a general headphone parametric-EQ profile loader. HRTF diffuse-field equalization
is a different operation and must not be presented as correction for the XM5.

Headphone correction belongs after the binaural stage and before the physical
output. For device-wide correction, ordinary stereo applications need to pass
through the same device-specific correction stage. A filter embedded only in
mpv would cover only media played through that instance.

There is a maintained native mechanism for that device-wide stage:

- PipeWire's `libpipewire-module-filter-chain` offers the builtin `param_eq`
  processor. It reads AutoEQ/Squiglink text profiles, including the preamp value.
- WirePlumber smart filters can attach a processing chain to a specific physical
  device. Applications continue targeting that physical sink; WirePlumber
  inserts the processing chain internally.
- `filter.smart.target` must match the discovered XM5 node, never the unrestricted
  default sink. Otherwise headphone correction could apply to speakers.
- A separate filter process can own its graph objects, allowing them to disappear
  when the process ends. The module must be removed or bypassed when the XM5
  disconnects, then recreated against the newly discovered endpoint.

This preserves the physical device as the final output and requires no global
`set-default`. Smart filters still create internal nodes; they are not a promise
that no processing objects exist in PipeWire. KDE's display/selection behavior,
existing-stream relinking, filter crash recovery, and Bluetooth reconnect behavior
must be verified before shipping a transparent device-wide feature.

This mechanism is now implemented as the independent [equalizer module](equalizer.md),
with a sourced AutoEQ DHRME five-band profile and a downstream SWH limiter. It does
not implement arbitrary-application head tracking. The module's real limiter DSP
has been tested; its full live desktop graph still needs a local runtime check.
Omniphony's upstream clipping meter cannot prove that a later EQ stage is safe,
and EQ headroom is not unconditionally added back as makeup gain.

Authoritative references:

- [PipeWire filter-chain, parametric EQ, and filter graph](https://docs.pipewire.org/page_module_filter_chain.html)
- [WirePlumber smart filters and physical-device targeting](https://pipewire.pages.freedesktop.org/wireplumber/policies/smart_filters.html)
