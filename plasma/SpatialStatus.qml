// SPDX-License-Identifier: GPL-3.0-or-later
// Disconnected-state view. Device controls still require a real BudsLink device.
pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.plasma.components as PlasmaComponents3
import org.kde.kirigami as Kirigami
import org.kde.plasma.workspace.dbus as DBus

QQC2.ScrollView {
    id: root
    objectName: "spatialStatus"
    property bool budsLinkRunning: false
    property var snapshot: ({})
    readonly property bool serviceAvailable: watcher.registered && snapshot.schema === 1
    readonly property bool connected: serviceAvailable && snapshot.connected === true
    readonly property var runtime: serviceAvailable ? (snapshot.runtime || ({})) : ({})
    contentWidth: availableWidth

    DBus.DBusServiceWatcher {
        id: watcher
        busType: DBus.BusType.Session
        watchedService: "org.spatiald.Control1"
        onRegisteredChanged: {
            root.snapshot = ({});
            if (registered) controlProps.updateAll();
        }
    }
    DBus.Properties {
        id: controlProps
        busType: DBus.BusType.Session
        service: "org.spatiald.Control1"
        path: "/org/spatiald/Control1"
        iface: "org.spatiald.Control1"
        property string stateText: String(properties.State || "")
        onStateTextChanged: root.readState(stateText)
        onRefreshed: root.readState(stateText)
    }
    function readState(text) {
        try {
            const parsed = text ? JSON.parse(text) : ({});
            root.snapshot = parsed !== null && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : ({});
        }
        catch (error) { root.snapshot = ({}); }
    }
    function moduleState(module) {
        if (!root.serviceAvailable) return i18n("Unavailable");
        if (!module) return i18n("Waiting");
        if (module.state === "disabled" || module.enabled === false) return i18n("Disabled");
        if (module.error || ["error", "unavailable", "failed"].includes(module.state)) return i18n("Unavailable");
        if (["active", "playing", "running", "ready"].includes(module.state)) return i18n("Active");
        return i18n("Waiting");
    }

    ColumnLayout {
        width: root.availableWidth
        spacing: Kirigami.Units.largeSpacing

        Kirigami.Icon {
            source: "audio-headphones"
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: Kirigami.Units.iconSizes.large
            Layout.preferredHeight: Kirigami.Units.iconSizes.large
            Layout.topMargin: Kirigami.Units.largeSpacing * 2
        }
        Kirigami.Heading {
            text: !root.serviceAvailable ? i18n("Headphone controls unavailable")
                : root.connected ? i18n("Waiting for headphone controls") : i18n("No compatible headphones connected")
            level: 2
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            textFormat: Text.PlainText
            Layout.fillWidth: true
            Layout.leftMargin: Kirigami.Units.largeSpacing
            Layout.rightMargin: Kirigami.Units.largeSpacing
        }
        PlasmaComponents3.Label {
            text: i18n("Connect your headphones through KDE Bluetooth and open BudsLink to show their controls.")
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
            horizontalAlignment: Text.AlignHCenter
            Layout.fillWidth: true
            Layout.leftMargin: Kirigami.Units.largeSpacing
            Layout.rightMargin: Kirigami.Units.largeSpacing
        }
        PlasmaComponents3.Label {
            text: root.serviceAvailable ? i18n("BudsLink Spatial Companion is running")
                : watcher.registered ? i18n("Waiting for BudsLink Spatial Companion status") : i18n("BudsLink Spatial Companion is unavailable")
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            horizontalAlignment: Text.AlignHCenter
            Layout.fillWidth: true
        }
        PlasmaComponents3.Label {
            text: root.budsLinkRunning ? i18n("BudsLink is running") : i18n("BudsLink is not running")
            textFormat: Text.PlainText
            Layout.alignment: Qt.AlignHCenter
        }
        PlasmaComponents3.Label {
            text: root.connected && root.snapshot.audio_ready === true
                ? i18n("Headphone output: Ready") : i18n("Headphone output: Waiting")
            textFormat: Text.PlainText
            Layout.alignment: Qt.AlignHCenter
        }
        PlasmaComponents3.Label {
            text: i18n("Application audio: %1", root.moduleState(root.runtime.live))
            textFormat: Text.PlainText
            Layout.alignment: Qt.AlignHCenter
        }
        PlasmaComponents3.Label {
            text: i18n("Equalizer: %1", root.moduleState(root.runtime.equalizer))
            textFormat: Text.PlainText
            Layout.alignment: Qt.AlignHCenter
        }
        PlasmaComponents3.Button {
            objectName: "spatialStatusRefresh"
            text: i18n("Refresh status")
            enabled: root.serviceAvailable
            onClicked: controlProps.updateAll()
            Layout.alignment: Qt.AlignHCenter
            Layout.bottomMargin: Kirigami.Units.largeSpacing * 2
        }
        Loader {
            active: root.visible
            Layout.alignment: Qt.AlignHCenter
            sourceComponent: TidalControls {
                cardWidth: Math.max(240, root.availableWidth - Kirigami.Units.largeSpacing * 2)
            }
        }
    }
}
