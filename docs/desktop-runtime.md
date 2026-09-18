# Desktop runtime

`spatial.desktop.DesktopRuntime` exports the session service
`org.spatiald.Control1` at `/org/spatiald/Control1`. It owns three independent
read-only discovery tasks and a 100 ms freshness check. Playback and tracker
providers are supplied by `spatial.runtime`; the UI does not launch programs,
open tracker ports, select audio outputs, or decode media.

## Connection and readiness

BlueZ `org.bluez.Device1.Connected` establishes the headphone session. Automatic
selection uses the actual `Name` property `WF-1000XM5`, not the editable alias.
A unique connected match wins; otherwise a unique paired match is remembered
while waiting. Ambiguous matches stay unavailable and expose their identities in
`State.runtime.discovery.candidates`. A configured Bluetooth address selects a
specific device explicitly. Pairing, scanning, and connecting remain under the
existing desktop's control.

BlueZ subscriptions are installed before `GetManagedObjects`. Relevant device
property changes, interface additions/removals, and service-owner changes trigger
new observations. Replies from an obsolete service owner are discarded. Failed
calls retry independently at 250 ms, 500 ms, 1 s, 2 s, 4 s, then 5 s; each D-Bus
operation has a 5 s timeout. A 30 s reconciliation read recovers a missed signal.

PipeWire is observed with bounded `pw-dump` reads. A usable A2DP sink may be idle
or suspended; requiring running audio before starting audio would deadlock.
Every read retains the Bluetooth session epoch and device address from before
the subprocess started. A response or failure from an older session cannot
attach to or clear the new session's sink. A PipeWire outage removes only audio
readiness. It does not claim Bluetooth disconnected or remove tracker discovery.
No global default device, fake sink, or Bluetooth policy setting is written.

BudsLink is observed through its real `DeviceManager.ListDevices` method and
`DeviceAdded`/`DeviceRemoved` signals. The matching path must appear in the
manager's results and match the BlueZ adapter plus device identity. An optional
manual path never overrides that identity check. Late BudsLink startup changes
controls readiness without blocking audio or tracker providers. BudsLink is not
auto-activated, held open, or made a dependency of the audio service.

## Control API

| Member | D-Bus signature | Purpose |
|---|---|---|
| `State` | read-only `s` | JSON capability and runtime snapshot; excludes sensor-rate orientation |
| `SetTrackerEnabled` | `sb` | Set and atomically persist permission for that physical tracker |
| `Recenter` | no arguments | Recenter the current eligible tracking source |
| `Play` | `s` | Pass a media path or supported URL to the configured playback provider |
| `Stop` | no arguments | Stop the playback provider |
| `StartLive` | `s` | Start processing the specifically selected application stream serial |
| `StopLive` | no arguments | Stop application processing and restore its previous routing |
| `StopIfProcess` | `u` input, `b` output | Diagnostic cleanup only when the active owned process still matches |
| `PlayIfIdle` | `s` input, `b` output | Compatibility API for an idle-state playback request |
| `BeginTestPlayback` | `sb` input, `t` output | Atomic diagnostic start; explicit replace flag; returns accepted request token or zero when busy |
| `StopIfRequest` | `t` input, `b` output | Cancel/stop only that current diagnostic playback request, including pending startup |

Failed preference writes restore the old preference state. Playback commands
await their provider and return an explicit D-Bus error when unavailable or
rejected. The service does not silently claim playback succeeded. Only one
instance may own the control service name.

The runtime extension point is:

```python
DesktopRuntime(engine, selected_address=None, preferences=None,
               on_change=changed, extra_state=status,
               on_play=play, on_stop=stop,
               on_live_start=start_live, on_live_stop=stop_live,
               on_stop_if_process=stop_if_process, on_play_if_idle=play_if_idle,
               on_begin_test_playback=begin_test_playback, on_stop_if_request=stop_if_request)
```

`run(stop)` runs until cancellation or session-bus loss. `publish()` reevaluates
freshness and publishes changed state. The containing runtime owns the lifetime
of tracker and audio providers. `extra_state()` appears under the JSON `runtime`
key; discovery adds its own `runtime.discovery` subkey.

## Native Companion integration

`plasma/companion.patch` targets BudsLink Companion's inspected Plasma revision
`31c6b3802071a6efc6fbd5a821e9c97f97240fd7`. It adds separate playback and tracking `WidgetCard` sections using
the existing checkbox, Plasma label/button controls, Kirigami theme spacing,
and KDE session D-Bus module. The playback file chooser uses the standard
`QtQuick.Dialogs` module. No new icons or rendering framework are added.

The exact visible label is `Use Optional Motion Tracker:`. The receiver-reported
identifier is plain text below it. Optional rows exist only while the policy core
has fresh, valid packets for that identity. Earbud permission controls only the
earbud fallback. The widget's current device path must exactly match the
manager-verified Companion path before these controls appear.

The widget refreshes D-Bus properties after the daemon acquires its service name,
clears cached state after loss, and ignores pending replies from a prior daemon
session. Checkboxes remain bound to the accepted backend value. Rejected calls
show a short error instead of pretending the preference was saved. Pending QML
D-Bus reply objects are destroyed after completion, as KDE requires.

## Verification boundaries

Discovery selection, independent provider failure, snapshot ordering, service
restart races, sender filtering, delayed PipeWire reads across reconnects,
preference persistence, and async playback control are covered by automated
unit tests. These tests exercise production parsing/reconciliation and watcher
code with controlled observations; they do not establish hardware compatibility.

Real D-Bus integration tests are available with:

```sh
SPATIAL_TEST_DBUS=1 dbus-run-session -- python -m unittest discover -s tests -p test_desktop.py -v
```

This development runner refuses D-Bus socket creation with `Operation not
permitted`, so those tests could not run here. Qt/Plasma QML tooling is absent;
installing it was also blocked by the runner's user/group permission restrictions.
The patch is checked against upstream source, but native Plasma rendering,
keyboard interaction, light/dark themes, and real hardware reconnect behavior
still require the actual Arch/Plasma environment. These limitations are not
recorded as successful runtime tests.

Source contracts inspected: BudsLink's `data/dbus-interfaces` XML and
`src/appLibs/dbusService.js` at the audited revision, upstream Companion's
`CheckButtonsSet.qml` and `WidgetCard.qml`, and KDE Plasma workspace
`components/dbus/dbusproperties.h` / `dbuspendingreply.h`.


## Playback card

Playback is independent of tracker availability. The matching Companion device
shows a file/TIDAL URL field and a native file chooser. Open and Stop call the
control API; Play/Pause, Previous, and Next call the owned MPRIS player. Stop can
cancel a pending Open. Source format, running/paused state, track title, elapsed
time, duration, and errors come from `State.runtime`, not UI guesses. Playback
controls reuse system media icons, Plasma controls, and the existing card style.

The shared JavaScript presentation code has executable tests. Its Atmos label
requires a running, loaded renderer with decoded objects and the matching
observed format. Capabilities, filenames, and a TIDAL Atmos badge alone cannot
produce that label. Native QML construction and rendering remain unverified in
this runner for the permission/tooling reasons described above.


## Application audio card

`LiveAudioControls.qml` is a separate native card. Its selector uses the actual
application names and string-valued PipeWire stream serials from
`State.runtime.live.available_streams`. Selection is retained by identity across
list updates; a disappeared stream clears the selection instead of silently
choosing a different application. Start requires an explicit selection and a
ready headphone output. Stop is available during an outstanding start request.

The card shows the live provider's running/readiness/error state and identifies
its output as binaural PCM audio. It never labels captured application PCM as
Atmos. The UI does not implement PipeWire routing; StartLive and StopLive
forward to the configured provider. Discovery remains read-only.

Control callbacks surface only explicitly public provider errors. Arbitrary
third-party exception messages are reduced to their exception class so that a
signed media URL cannot leak into the popup. StopIfProcess is intended for
host-verification cleanup: the owning runtime checks current process identity
and pending playback before performing a stop, returning false when they differ.


The host verifier uses BeginTestPlayback/StopIfRequest rather than inferring
ownership from a process that might not exist yet. BeginTestPlayback passes the
explicit replacement flag to the owning runtime and returns a 64-bit request
token. A busy refusal is zero. Cleanup must use the accepted token; a newer user
playback request invalidates it, so late diagnostic cleanup cannot stop that
new request. The runtime owns the atomic generation and locking checks.
