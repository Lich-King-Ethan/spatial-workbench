# Tracking timing and optional prediction

`spatialctl timing-snapshot` explicitly reads one bounded snapshot from the running
installed service and prints JSON. It does not enable prediction, change playback,
or upload anything. Recent timing records are kept in bounded memory; this
command copies them once without starting a continuous file recording.

To save a snapshot privately:

```sh
spatialctl timing-snapshot --output timing-snapshot.json
```

The output file is created exclusively with mode `0600`. Existing files and
symlinks are refused instead of overwritten. Without `--output`, no file is
created. Review evidence before sharing it.

The snapshot contains accepted reports from the selected tracking provider and
bounded software observations. It is not a lossless Bluetooth transport capture:
malformed/reordered reports may be rejected, older history is evicted, and these
records do not include every radio/controller event.

The session D-Bus method is `org.spatiald.Control1.TimingSnapshot` at
`/org/spatiald/Control1`, returning a JSON string. The runtime supplies the schema,
host-monotonic capture timestamp, sensor samples, renderer application records,
latency evidence, and prediction status. When available, `renderer_observations`
contains the renderer's separately received head-pose reports. Each history
contains at most its last 512 records; the complete response is limited to 2 MiB.
Unsupported providers or invalid snapshots return explicit D-Bus errors.

Snapshots are pulled only on request. Timing samples are not included in the
ordinary `State` property and a snapshot request does not emit property updates.

## What the timestamps establish

The Sony helper timestamps successful HID reads using the host's monotonic clock
and attaches a helper sequence number to the raw report. The daemon records its
own packet receipt time on the same host clock. This measures the local handoff
delay and preserves the raw report for inspection. It does not manufacture a
headset acquisition timestamp or a Bluetooth controller packet timestamp when
the sensor report does not provide one. Wall-clock changes do not alter these
intervals, and timestamps from a different host or boot are not comparable.

Renderer send timestamps and received renderer pose reports show which
orientations the software sent and subsequently observed. A renderer report is
not an acknowledgement that a particular audio sample reached the listener.
These observations alone do not measure the earbuds' acoustic output delay.

The output latency estimate comes only from an unambiguous nanosecond latency
value on the exact selected physical PipeWire sink. The graph's monotonic
observation timestamp is refreshed only by a successful read in the current
Bluetooth session. A service outage, disconnect, or session replacement clears
it; old in-flight reads cannot refresh the replacement session. Runtime
compensation must reject missing or stale graph observations. Reported latency
remains an estimate, not an acoustic measurement or proof of full Bluetooth
clock synchronization.

## Optional prediction

Prediction is **disabled by default**. Timestamp collection works without it.
The experimental predictor
can be enabled in the existing service configuration with:

```toml
head_prediction_enabled = true
head_prediction_max_ms = 80.0
```

Existing settings are preserved; adding these keys does not require replacing
the rest of the configuration. Settings are loaded at service startup. Once your
owned playback is stopped and you are ready to apply the change, restart the
service:

```sh
systemctl --user restart spatiald.service
```

The restart interrupts this application's current work; do it at a convenient
time. No restart is performed by merely saving a timing snapshot.

The configured horizon must be positive and at most 150 ms. This is a cap on orientation extrapolation, not an additional audio
delay. The predictor also limits angular correction to 12 degrees and uses a
shorter 40 ms cap when deriving velocity from quaternion changes instead of a
validated gyro. The observed WF-1000XM5 reports contained zero gyro values;
these are preserved, so prediction can only use the guarded quaternion-motion
fallback on that evidence. It requires consistent recent motion and backs off on stops,
braking, reversals, stale samples, and reference or session changes. Unknown
output latency leaves the observed orientation unchanged.

The helper continues to read the existing HID stream at its existing report
rate. These features add host-side timestamps, bounded history, and prediction
work; they do not request a higher radio reporting rate. Earbud battery impact
has not been measured. Physical listening checks remain necessary to assess
whether the bounded prediction reduces lag without objectionable overshoot.
