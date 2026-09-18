# Decoder and simulated headphone validation

`tools/ci/decoder-smoke.py` checks the installed `liborender` and the genuine
`harletty-bridge` shared library with a real E-AC-3 JOC Atmos elementary stream.
It requires actual decoded object counts, finite non-silent PCM, twelve speaker
channels and two binaural channels. It uses the production headphone config and
`renderer_pose` coordinate mapping. The renderer must acknowledge the mapped
quaternion over private loopback OSC; turning the head must measurably change
the decoded binaural waveform compared with two identical neutral runs.

After installing `spatial-workbench`, `orender-spatial` and `harletty-bridge`:

```sh
python tools/ci/decoder-smoke.py --download-fixture --report decoder-smoke.json
```

Use `--library PATH` and `--bridge PATH` to check specific binaries, or
`--sample PATH` instead of downloading to check another audible E-AC-3 JOC
elementary stream. The script uses Python's standard library and the installed
production `spatial` package; it does not inject the source checkout into the
module path. A missing dependency, decoder failure, silent output, missing
objects, missing OSC acknowledgement or absent acoustic response fails the gate.

The upstream renderer initially reports its default speaker count before its
first decoded frame commits the configured output mode. The check records this
initial count and verifies the channel count of every actual output packet.
The mpv decoder negotiates the output map after the first decoded frame.

## Full media route

The stricter integration invocation is:

```sh
dbus-run-session -- python tools/ci/audio-stack-smoke.py \
  --require-media-audio --report-dir audio-stack-report
```

This requires the real installed `mpv-omniphony` and bridge in addition to the
normal audio gate dependencies. It first runs the PCM position, orientation and
recenter checks. It then keeps the verified private PipeWire session, production
EQ and simulated earbud sink active and invokes `media-spatial-smoke.py`.
That helper uses the production `AudioRuntime` to launch the actual mpv decoder,
requires its decoded object profile and binaural renderer handshake, and audits
both stereo links through the EQ into the selected sink. The graph monitor
retains any forbidden links observed during playback.

The same public fixture is repeated by concatenating complete encoded frames;
it is never replaced with synthetic object metadata or re-encoded. Synthetic
Sony helper UDP reports pass through the installed Sony adapter, selection
engine, coordinate mapping and renderer OSC controls. Sink-monitor captures
measure the post-EQ left and right PCM. Complete fixture periods make energy
measurements independent of capture start phase. Repeated neutral measurements
must agree, and a 90-degree turn must move this front-left channel-check signal
to the opposite simulated ear. JSON reports and float32 captures are retained
with the workflow evidence.

These are software tests. They do not measure Bluetooth transport, a physical
tracker, DAC output, earbud fit or perceived localization. The live PCM test
moves virtual source positions; the supported tracker supplies orientation
(3DOF), not physical listener translation.

## Fixture provenance

The 144,384-byte test asset is fetched directly from the upstream decoder's
committed regression fixtures, rather than redistributed in this repository:

- Repository: [`harletty/harletty-bridge`](https://github.com/harletty/harletty-bridge).
- Release: `v0.7.3`; resolved commit: `ff98334f00bef130fa93e2590fcd5bd50961e0d2`.
- [Encoded fixture](https://github.com/harletty/harletty-bridge/blob/ff98334f00bef130fa93e2590fcd5bd50961e0d2/harletty/tests/fixtures/joc_atmos_1s.eac3).
- SHA-256: `3f4d165fa732a4d3ac8e914572f32b0d3c631f77b935fc714ad15d8d72534280`.
- Upstream's [golden test](https://github.com/harletty/harletty-bridge/blob/ff98334f00bef130fa93e2590fcd5bd50961e0d2/harletty/tests/golden.rs)
  describes it as 1.5 seconds of a 7.1.4 Atmos channel-check clip at 768 kbit/s.
  It contains 47 complete 1,536-sample frames: 72,192 samples at 48 kHz.
- The upstream tree includes an [Apache-2.0 license](https://github.com/harletty/harletty-bridge/blob/ff98334f00bef130fa93e2590fcd5bd50961e0d2/LICENSE).
  This repository does not add a separate license claim for the media asset.

The downloader pins both the immutable source commit and the checksum, and
refuses changed bytes. The reports include the source URL and digest alongside
the actual renderer build, library path and bridge path.
