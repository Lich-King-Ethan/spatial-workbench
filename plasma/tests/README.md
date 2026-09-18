# Native Companion smoke gate

The Companion package's `check()` compiles this Qt Test executable and runs it
offscreen on a new private D-Bus session. It loads the **patched upstream** cards,
including the original `WidgetCard.qml`, actual Plasma/Kirigami/DBus QML plugins,
Qt's file-dialog type, and a real KI18n translation context. It does not substitute
QML components or replace imports with test doubles.

The explicitly named `ControlFixture` provides only a `State` property and
`PropertiesChanged` signals on that private bus. No headset, audio renderer,
desktop daemon, or playback methods are started. The test covers component
creation, visible tracker delegates, plain tracker names, playback format updates,
and exact application-stream identifiers. Component errors and **every warning
reported by the QML engine** fail the test. General platform diagnostic messages
remain visible; there is no warning suppression list.

Arch dependencies come from the Companion recipe, plus `cmake`, `ninja`, `dbus`,
`ki18n`, and `ttf-dejavu`. To run against an already patched checkout:

```sh
cmake -S plasma/tests -B /tmp/spatial-qml-build -G Ninja \
  -DSPATIAL_COMPANION_QML_DIR=/absolute/path/to/companion/contents/ui
cmake --build /tmp/spatial-qml-build
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \
  SPATIAL_QML_PRIVATE_BUS=1 dbus-run-session -- /tmp/spatial-qml-build/spatial-qml-smoke
```

This is a native import/instantiation and binding smoke test, not a screenshot
review, interactive Plasma shell test, file-picker interaction test, or validation
of physical audio/head tracking. Adding the gate does not establish that it has
passed; its result is the Companion package check log from an actual run.

The harness uses Qt's documented [component creation](https://doc.qt.io/qt-6/qqmlcomponent.html),
[engine warning signal](https://doc.qt.io/qt-6/qqmlengine.html#warnings), and KDE's
[localized QML context](https://api.kde.org/klocalizedqmlcontext.html).
