# Native Companion integration

Target: BudsLink Companion `Plasma-Widget` commit
`31c6b3802071a6efc6fbd5a821e9c97f97240fd7` (metadata version 0.2.0).

`companion.patch` adds `SpatialControls.qml`, `PlaybackControls.qml`, and the
shared playback presentation helper, plus `LiveAudioControls.qml`, then inserts
all three native cards into `DevicePage.qml`.
It reuses upstream's `WidgetCard`, Qt checkbox/label layout, Kirigami spacing,
Plasma controls, session D-Bus module, and service watcher. No icon assets.
The project packaging applies the patch to a pinned downstream Companion package;
do not edit a package-owned plasmoid directory manually.

The visible label is exactly `Use Optional Motion Tracker:`. The hardware or
receiver-reported identifier beneath it is rendered as plain text. The row
requires fresh provider data and disappears on loss. The earbud switch permits
only the earbud fallback. The integration matches the current BudsLink device's
exact manager-reported D-Bus path; automatic discovery replaces manual path setup.

The patch passes `git apply --check` against that revision. On September 18, 2026,
the [Arch package check](https://github.com/Lich-King-Ethan/spatial-workbench/actions/runs/35338214187/job/105577902132)
compiled and ran the native smoke harness with Qt 6.11.2: all three card cases
passed, with five total passes including setup/cleanup and no failures or skips.
It instantiated the patched cards and upstream `WidgetCard` with real Plasma,
Kirigami, KI18n, and D-Bus modules, checked state bindings, and reported no QML
engine warnings. The Companion package then built successfully.

The harness runs offscreen with explicitly declared state fixtures on a private
D-Bus session. Container platform/icon/portal diagnostics remain visible; this
does not establish visual appearance, full Plasma-shell interaction, or headset
compatibility. See [tests/README.md](tests/README.md) for its exact scope.

Daemon registration refreshes properties, loss clears cached state, stale pending
replies are ignored, and reply objects are released after use. Checkboxes show
backend-accepted state. Errors do not silently change the saved preference.

Remaining interactive UI checks: light/dark theme, narrow popup, long device identifier,
keyboard navigation, accessible checkbox name, daemon loss/restart, D-Bus errors,
late tracker arrival, and no controls appearing on another headphone's tab.
See `docs/desktop-runtime.md` for the live API and verification boundaries.


The separate playback card is available for the matching XM5 even when no tracker
exists. It opens a local file through the native Qt file chooser or accepts a
local path/TIDAL URL. Open/Stop use the control service; Play/Pause and queue
navigation use the existing MPRIS service. Stop remains available while a source
is opening. Track title, elapsed time, duration, errors, and playback state come
from actual runtime observations. Dolby Atmos is shown only with decoded object
telemetry and a matching format; a source badge or filename is insufficient.

The file chooser adds the standard `QtQuick.Dialogs` import from Qt Declarative.
Five JavaScript presentation tests cover format evidence, progress bounds, and
time labels; these do not replace native Plasma rendering checks.


The separate Application Audio card lists actual available playback applications.
Selection is retained by the observed stream serial; stream disappearance clears
it. Start and Stop call the live provider explicitly, show provider readiness and
errors, and label captured content as binaural PCM audio. No Atmos label is
inferred from an application's PCM output.
