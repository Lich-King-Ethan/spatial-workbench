# Live tracker runtime

`spatial.trackers.run` discovers physical Linux HID devices and feeds actual
orientation observations into the policy engine. The daemon does not use replay
data or create trackers from a saved name. Each adapter retries independently.
The Sony reader is an upstream helper process; Slime readers use independent
nonblocking file descriptors and tasks. These are fault boundaries, not security
containers.

```python
await trackers.run(
    engine, stop,
    on_change=mark_state_changed,
    status=record_module_state,  # (module, state, detail)
    sony_executable="sony-tracker",
    sony_enabled=True,
    optional_enabled=True,
)
```

Module names are `sony_tracker` and `optional_tracker`. `engine.device` is the
selected Bluetooth address. Changing its connection epoch stops the previous
Sony helper. Optional discovery continues independently. Samples use local
monotonic arrival time; UI publication and pose delivery remain owned by the
daemon runtime. Dependencies are Python's standard library and `sony-tracker`.

## Sony WF-1000XM5

Discovery requires all of the following:

1. A Bluetooth HID (`HID_ID` bus `0005`) with Sony vendor `054c`.
2. Its sysfs `HID_UNIQ` Bluetooth address matches the selected audio device.
3. The report descriptor declares Sensor/Custom (`05 20 09 e1`).
4. Report 1 has the rotation-vector layout and scale hard-coded by the installed
   upstream decoder: three initial signed 16-bit fields, logical ±32767,
   physical −314159264 to 314159265, exponent −8, and at least 14 report bytes.
5. Feature report 2 contains the `#AndroidHeadTracker#` marker.
6. The helper actually delivers finite, correctly sized orientation packets.

The daemon starts `sony-tracker --device <that exact hidraw path> --port <port>`.
It binds an ephemeral IPv4 loopback port before launch and never captures the
default OpenTrack port 4242. Late reports cannot attach to a different headphone
connection. Helper exit or three seconds without valid packets triggers restart
with bounded retry; source freshness expires earlier through the engine's normal
sample timeout. Audio does not wait for tracking.

SonyTrackerLinux's upstream documentation says its report decoding and axis map
were tested on **WH-1000XM5**. A working WF-1000XM5 BudsLink Companion is evidence
for Sony controls, not proof of an exposed Android HID sensor or its physical
axis directions. This runtime checks capability and layout, and exposes the
earbud row only after receiving orientation. On hardware which exposes no such
HID, the optional motion tracker and static rendering continue to work. A
physical nod/turn/tilt check on the user's WF firmware remains necessary.

Sources checked:

- [SonyTrackerLinux README](https://github.com/kdani3/SonyTrackerLinux/blob/f5326577c4ae1949c6cbce3d8a4905107a86452d/README.md)
- [HID discovery and Android feature validation](https://github.com/kdani3/SonyTrackerLinux/blob/f5326577c4ae1949c6cbce3d8a4905107a86452d/hidraw.h)
- [Wire output and report decode](https://github.com/kdani3/SonyTrackerLinux/blob/f5326577c4ae1949c6cbce3d8a4905107a86452d/main.c)
- [Quaternion and axis math](https://github.com/kdani3/SonyTrackerLinux/blob/f5326577c4ae1949c6cbce3d8a4905107a86452d/quat.h)
- [Android head-tracker rotation direction and axes](https://source.android.com/docs/core/interaction/sensors/head-tracker-hid-protocol#data-field-custom-value-1-0x0544)

## Optional SlimeVR nRF tracker

The module passively reads the established SlimeVR USB HID protocol. Supported
USB identifiers match current upstream server product rules:

| Vendor | Product | Device |
|---|---|---|
| `1209` | `7690` | nRF receiver |
| `1209` | `7692` | Tracker connected directly by USB |
| `4e76` | `d2d0`–`d2df` | Receiver family supported by upstream |

Every device is opened **read-only**. The adapter sends no receiver commands,
changes no radio settings, performs no pairing or sensor reset, and claims no
libusb interface. Linux hidraw allocates a report queue per open file and copies
incoming reports to every reader, so a running SlimeVR server can read the same
receiver. Turning the UI toggle off removes audio permission only; it does not
disable or take ownership of the hardware.

The 16-byte registration frame (`0xff`) supplies a receiver slot and the actual
48-bit tracker hardware address. The label is the same uppercase, 12-digit
hexadecimal representation used by SlimeVR's receiver adapter, without a product
nickname, role guess, shortened ID, or user-created alias. Persistent preferences
use `slime:<hardware address>`; the transient slot is never persisted. If the
receiver reuses a slot, the old identity is invalidated first.

The optional row needs both a registration and a valid rotation frame:

- Types 1/4: little-endian Q15 quaternion, `x,y,z,w`.
- Types 2/7: the upstream 10/11/11-bit exponential-map encoding.
- Type 3: server status; disconnect/error/timeout immediately removes the row.
- Registration, status, battery, and other packets never refresh pose freshness.

The receiver firmware rotates registration addresses into spare USB report
slots, and sends registrations when idle every 100 ms. A daemon launched after
pairing can therefore discover identities without writing to the receiver.
Until the registration arrives, unknown slot data is ignored. When USB batches
remain fully occupied, identity delivery may be delayed; diagnostics report
that condition rather than inventing an ID. A tracker which merely remains
stored in a receiver but sends no orientation never appears as available.

This adapter covers the verified HID protocol above. Older serial-only receivers,
arbitrary custom Neon firmware, Wi-Fi trackers, and synthetic SlimeVR skeleton
outputs are not decoded by it. They require their own adapters; they are not
silently represented as supported physical hardware.

Sources checked:

- [Upstream Linux/desktop HID receiver parsing](https://github.com/SlimeVR/SlimeVR-Server/blob/c11dc54ed1a745ffc4b15db7f820d8d0c1993990/server/desktop/src/main/java/dev/slimevr/desktop/tracking/trackers/hid/DesktopHIDManager.kt)
- [Product IDs, quaternion decoding and axes](https://github.com/SlimeVR/SlimeVR-Server/blob/c11dc54ed1a745ffc4b15db7f820d8d0c1993990/server/core/src/main/java/dev/slimevr/tracking/trackers/hid/HIDCommon.kt)
- [Receiver registration repetition](https://github.com/SlimeVR/SlimeVR-Tracker-nRF-Receiver/blob/e3d66d5dbf70db47fe0e854fb0b00599bf2c20b3/src/hid.c)
- [Direct tracker registration repetition](https://github.com/SlimeVR/SlimeVR-Tracker-nRF/blob/cbfff16ebed1659d4c0e9f361ded0030fb66855a/src/hid.c)
- [Linux hidraw API](https://docs.kernel.org/hid/hidraw.html)
- [Linux per-reader report queues (`hidraw_open`, `hidraw_report_event`)](https://github.com/torvalds/linux/blob/master/drivers/hid/hidraw.c)

## Coordinates and recentering

The canonical orientation is a normalized `w,x,y,z` **head-to-world** quaternion
with right-handed axes **X right, Y up, Z back**. Consumer adapters must explicitly
convert this frame to their own coordinate system and direction.

Sony's UDP packet contains six native-endian doubles. The last three are the
Euler output of upstream's Z-Y-X extraction, so we reconstruct
`qz(yaw) * qy(pitch) * qx(roll)`. Android reports a **reference-to-head**
rotation vector in X-right/Y-forward/Z-up axes; the helper first remaps that
vector to `(-ry, rx, -rz)`. Accounting for this remap, the transform direction,
and the canonical basis gives **`(w,x,y,z) → (w,-y,z,-x)`** for the reconstructed
helper quaternion. This final map is a proper basis rotation, so the helper's
`inverse(reference) * current` remains the correct canonical relative pose even
when its startup reference is not identity. Treating helper output as unmodified
Android axes would swap physical nod and tilt. Tests compare packets generated
by the pinned upstream C functions against independent Android rotation matrices,
including combined rotations and nonidentity startup references.

The Slime decoder applies the same world-axis correction as the upstream server:
−90° about X, multiplied on the left of the decoded device quaternion. It does
not silently apply SlimeVR skeleton mounting or drift corrections. The headband
must be worn in the intended mounting orientation; check all three motions during
local setup. Recenter affects only this application's active source; no reset
command is sent to SlimeVR or to hardware.

## Validation and limits

`python -m unittest discover -s tests -p 'test_trackers*.py' -v` covers physical
identity filtering, wrong-report rejection, known rotations, corrupt quaternion
rejection, slot reuse, stale data, late epochs, independent restart, disabled
modules, and subprocess cleanup. The transport tests launch a real subprocess
which uses the upstream UDP packet format and pass verified wire fixtures
through real nonblocking file descriptors. Those fixtures test the adapters;
they are not a claim that physical hardware was attached during development.

No Sony headset or Slime receiver is attached to the development environment.
Actual HID permission, firmware compatibility, physical axes, and simultaneous
receiver use with SlimeVR remain local hardware acceptance checks. In particular,
this code does not claim that successful Companion controls establish WF motion
sensor support.
