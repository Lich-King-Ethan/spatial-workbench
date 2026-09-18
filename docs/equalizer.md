# WF-1000XM5 equalizer

`spatial.equalizer` runs headphone correction in its own PipeWire client process.
WirePlumber smart filters attach it to the discovered physical WF-1000XM5 output.
Applications continue selecting the normal XM5 device; the module never changes
the global default. The process is removed on disconnect and recreated with the
new PipeWire object serial after reconnection.

The processing order is:

1. Application output, including Omniphony's binaural stereo output.
2. PipeWire's native `param_eq`, applying the measured profile and its preamp.
3. SWH's stereo `fastLookaheadLimiter`, with zero input makeup gain and a
   `-1 dBFS` sample ceiling.
4. The existing physical XM5 sink and its ordinary volume controls.

The limiter adds approximately 5 ms of latency. Its ceiling is a digital sample
peak limit at that processing stage, not an acoustic loudness or true-peak
measurement of the earbuds. Later user volume boosts and Sony's own processing
are outside this module's measurement. Omniphony's clipping telemetry is measured
before this independent EQ stage and is not presented as its clipping meter.

## Native packages

The implementation uses official Arch packages:

```sh
sudo pacman -S --needed pipewire pipewire-audio wireplumber swh-plugins
```

The project's audio installation option includes these dependencies.
WirePlumber 0.5 or newer is required for smart filters. Missing components make
the EQ module unavailable; they do not disable ordinary audio, the renderer,
tracker discovery, or BudsLink controls.

## Reference profile and custom profiles

The bundled reference is AutoEQ's published Sony WF-1000XM5 result using DHRME
measurements, pinned to AutoEQ commit
`7ae0f56d53074872b028649617a22bbb4232feb7`.

The upstream README explicitly offers the first five filters with `-2.6 dB`
preamp. The bundled profile uses exactly that selection. It does not use all ten
filters with the five-filter preamp: upstream recommends `-2.7 dB` for that
expanded selection. The files include a provenance record, source hashes, and
the upstream MIT license.

| Type | Frequency | Gain | Q |
|---|---:|---:|---:|
| Low shelf | 105 Hz | -0.1 dB | 0.70 |
| Peaking | 157 Hz | -2.4 dB | 0.72 |
| Peaking | 3598 Hz | +2.5 dB | 1.86 |
| Peaking | 947 Hz | +1.1 dB | 1.37 |
| Peaking | 65 Hz | +0.8 dB | 1.19 |

This is a published measurement-based correction, not a personalized measurement
of an individual pair, ear fit, eartip, or listening preference. The module does
not silently change the hardware Sony EQ; avoid deliberately applying a second
correction curve unless that combination is what you want.

An alternative profile can be selected by its AutoEQ text filepath in the runtime
configuration. Supported filters are `PK`, `LSC`, and `HSC`. Profiles must include
a preamp from `-60` through `0 dB`, one to 32 active bands, frequencies from 10 to
20,000 Hz, gains from `-24` through `24 dB`, and Q from `0.1` through `20`.
Malformed or unsupported profiles produce a clear error rather than silently
loading an empty correction. Comments and disabled filters are normalized away
in a private copy; the original file is not edited.

The daemon configuration controls enablement and profile selection. Internally:

```python
equalizer = Equalizer(
    profile_path=None,  # bundled reference
    bluetooth_address=selected_bluetooth_address,
    enabled=True,
)
await equalizer.update_sink(discovered_sink)
```

`update_sink(None)` removes the filter process. `stop()` removes it during daemon
shutdown. Each module instance accepts only its selected Bluetooth identity and
a current, usable `bluez_output` sink. Its private configuration targets both the
sink's node name and its current object serial, so the profile cannot migrate to
speakers if the headphones disappear.

## Graph verification and failure handling

The main filter node has a low selection priority, `device.class=filter`, and
`filter.smart.targetable=false`. It is an internal processing object. PipeWire
graph tools can show it; the physical XM5 remains the final output. KDE's exact
presentation of smart filter objects still needs a local desktop check.

The module audits actual `pw-dump` observations before declaring its route active:

- Both nodes must belong to the launched process, with the expected session
  names and shared `node.link-group`.
- The filter's smart target must match the current physical sink session.
- The output stream must have `node.dont-fallback` and `node.dont-reconnect`.
- Every observed outgoing filter link must reach that physical sink.

Foreign output links immediately terminate the EQ process. Inputs that appear
before their output links are treated as waiting; a pending chain already in use
gets five seconds to finish connecting before the module is stopped and retried.
An idle filter does not need to manufacture audio merely to prove readiness.
Retries use bounded backoff and belong only to this module.

`verified_input_ids(snapshot)` exposes only complete verified intermediaries to
the player's routing auditor. `pending_input_ids(snapshot)` exposes only known
owned inputs still waiting for their output link. Those pending IDs can never
make the player report a verified route.

## Verification completed here

The tests cover profile validation, exact device targeting, graph ownership,
unexpected links, partial graph readiness, disconnect cleanup, reconnection with
a changed object serial, optional dependencies, and retry isolation.

The actual `fast_lookahead_limiter_1913.so` from Arch's official
`swh-plugins 0.4.17-7` package was also executed through its LADSPA ABI. The test
confirmed the exact port names used by the generated configuration, checked that
overloaded samples stay below the configured `-1 dBFS` ceiling, checked that a
quiet signal keeps its amplitude, and observed the 240-sample delay at 48 kHz.
The test uses the installed plugin automatically; a developer can provide a
separate trusted plugin file with `SPATIAL_TEST_LIMITER_PLUGIN`.

A real PipeWire/WirePlumber session could not run in this workspace because local
Unix sockets are restricted. The generated graph follows the inspected upstream
source and official smart-filter contract, but Bluetooth routing, KDE behavior,
and real reconnect behavior must still pass the included local host checks.

Sources:

- [AutoEQ pinned WF-1000XM5 result and preamp choices](https://github.com/jaakkopasanen/AutoEq/blob/7ae0f56d53074872b028649617a22bbb4232feb7/results/DHRME/in-ear/Sony%20WF-1000XM5/README.md)
- [PipeWire filter-chain and parametric EQ](https://docs.pipewire.org/page_module_filter_chain.html)
- [WirePlumber smart filters](https://pipewire.pages.freedesktop.org/wireplumber/policies/smart_filters.html)
- [SWH limiter implementation and ports](https://github.com/swh/ladspa/blob/master/fast_lookahead_limiter_1913.xml)
- [Arch swh-plugins package](https://archlinux.org/packages/extra/x86_64/swh-plugins/)
