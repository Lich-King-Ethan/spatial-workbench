# SonyTrackerLinux absolute-orientation and timing extensions

Upstream: <https://github.com/kdani3/SonyTrackerLinux>, tag `v1.0.0`, MIT.
The complete upstream source archive has SHA-256
`27752dd19b4e6a6da8eb816b9beb22adcfe94ac68d756d9489dd613bbd5d9b26`.
The maintained AUR recipe was inspected at commit
`4d4b558a43ded4a1033e6e528a5543195bbb2ab2`; this downstream package independently
pins the actual source bytes instead of its unchecked archive download.

The production patch adds two opt-in modes. Without either flag, the helper
still discards the first valid sample, captures its startup reference, and sends
the same reference-relative OpenTrack Euler packets as upstream. `--absolute`
sends each current orientation immediately, without capturing or applying a
reference. It retains upstream rotation-vector scaling, axis map `(-ry, rx,
-rz)`, ZYX Euler conversion, and native-endian six-double UDP format.

`--timestamped` sends raw reports instead of Euler packets. Each 34-byte
little-endian packet is `SPT1`, uint64 capture nanoseconds, uint64 sequence,
then the 14 raw HID bytes. Capture time is host `CLOCK_MONOTONIC`, sampled
immediately after the HID read. It is **not a sensor timestamp** and does not
measure Bluetooth transport before delivery or headphone acoustic output.
Sequence starts at one for each helper/device session and counts valid reports.
The raw report already contains absolute orientation, so combining this flag
with `--absolute` gives the same timestamped format. Neither flag changes the
upstream loopback destination, exact-device selection, or feature activation;
there are no added report-interval writes or higher requested sensor rates.

The daemon requires both flags and converts the raw Android rotation vector
directly to canonical right/up/back coordinates, preserving the reference until
its own recenter. It records received time and rejects invalid, stale, future,
or reordered packets. Raw angular velocity is scaled only after checking its
HID usage, offsets and physical scale. Android Custom Value 2 specifies body
angular velocity in rad/s even if the device inherited a different HID Unit:
<https://source.android.com/docs/core/interaction/sensors/head-tracker-hid-protocol#data-field-custom-value-2>.
Zero velocity readings remain zero; the helper does not invent missing gyro data.

The source archive contains an executable. `build()` explicitly removes it and
compiles the patched C source. Native package checks compile that actual source
again with only HID operations replaced by test fixtures. They exercise its
real argument parser, scaling, remapping, quaternion operations and UDP sender;
independent Rodrigues matrices verify compound and nonidentity-startup poses.
Timestamped checks verify exact byte order and length, sequence, untouched raw
report bytes, capture time bounded by actual HID-read calls, and unchanged
feature-write count for both timestamped argument combinations.
These checks do not claim Bluetooth connectivity or physical head motion.

The package provides and conflicts with `sony-tracker`, preserving the existing
executable path and legacy behavior for other clients. Package replacement uses
ordinary pacman conflict prompts. No user configuration is rewritten.
