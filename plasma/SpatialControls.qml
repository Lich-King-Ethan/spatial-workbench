// SPDX-License-Identifier: GPL-3.0-or-later
// Native tracking controls for BudsLink Companion's Plasma-Widget branch.
pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.plasma.components 3.0 as PlasmaComponents3
import org.kde.kirigami as Kirigami
import org.kde.plasma.workspace.dbus as DBus

Item {
    id: root
    required property var device
    required property int cardWidth
    readonly property string service: "org.spatiald.Control1"
    readonly property string objectPath: "/org/spatiald/Control1"
    property var snapshot: ({})
    property string errorText: ""
    property DBus.DBusPendingReply pendingReply
    property bool busy: false
    property int requestSerial: 0
    readonly property bool matches: watcher.registered && snapshot.schema === 1
        && snapshot.connected && device && snapshot.companion_path === device.path
    readonly property var rows: {
        if (!matches) return [];
        let result = [];
        if (snapshot.earbud) {
            result.push({ id: snapshot.earbud.id, name: "",
                title: i18n("Use Earbud Head Tracking"), enabled: snapshot.earbud.enabled });
        }
        for (let tracker of (snapshot.optional_trackers || [])) {
            result.push({ id: tracker.id, name: tracker.name,
                title: i18n("Use Optional Motion Tracker:"), enabled: tracker.enabled });
        }
        return result;
    }
    visible: matches && rows.length > 0
    implicitHeight: visible ? card.implicitHeight : 0
    implicitWidth: cardWidth

    DBus.DBusServiceWatcher {
        id: watcher
        busType: DBus.BusType.Session
        watchedService: root.service
        onRegisteredChanged: {
            if (!registered) {
                root.snapshot = ({});
                root.busy = false;
                root.requestSerial += 1;
                root.errorText = "";
            } else {
                // Properties does not re-fetch when a service acquires its name.
                controlProps.updateAll();
            }
        }
    }
    DBus.Properties {
        id: controlProps
        busType: DBus.BusType.Session
        service: root.service
        path: root.objectPath
        iface: root.service
        property string stateText: String(properties.State || "")
        onStateTextChanged: root.readState(stateText)
        onRefreshed: root.readState(stateText)
    }
    function readState(text) {
        try { root.snapshot = text ? JSON.parse(text) : ({}); }
        catch (error) { root.snapshot = ({}); }
    }
    function invoke(member, args) {
        if (root.busy || !watcher.registered) return;
        root.busy = true;
        root.errorText = "";
        const serial = ++root.requestSerial;
        root.pendingReply = DBus.SessionBus.asyncCall({service: root.service,
            path: root.objectPath, iface: root.service, member: member, arguments: args});
        const reply = root.pendingReply;
        const finished = () => {
            if (serial === root.requestSerial) {
                root.busy = false;
                root.pendingReply = null;
                if (reply.isError)
                    root.errorText = i18n("The tracking setting could not be changed.");
                controlProps.updateAll();
            }
            reply.destroy();
        };
        if (reply.isFinished) finished();
        else reply.finished.connect(finished);
    }

    WidgetCard {
        id: card
        cardWidth: root.cardWidth
        content: [
            ColumnLayout {
                Layout.fillWidth: true
                Layout.margins: Kirigami.Units.smallSpacing * 2
                spacing: Kirigami.Units.smallSpacing * 2
                Repeater {
                    model: root.rows
                    delegate: ColumnLayout {
                        id: row
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: Kirigami.Units.smallSpacing
                        PlasmaComponents3.Label {
                            text: row.modelData.title
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            horizontalAlignment: Text.AlignHCenter
                            Layout.fillWidth: true
                            font.pixelSize: Math.round(Kirigami.Theme.smallFont.pixelSize)
                        }
                        PlasmaComponents3.Label {
                            text: row.modelData.name
                            textFormat: Text.PlainText
                            visible: text.length > 0
                            wrapMode: Text.WrapAnywhere
                            horizontalAlignment: Text.AlignHCenter
                            Layout.fillWidth: true
                        }
                        QQC2.CheckBox {
                            id: checkbox
                            checked: row.modelData.enabled
                            enabled: !root.busy
                            Layout.alignment: Qt.AlignHCenter
                            Accessible.name: row.modelData.title + " " + row.modelData.name
                            contentItem: Item { implicitWidth: 0; implicitHeight: 0 }
                            onToggled: {
                                const requested = checked;
                                // Keep the displayed value authoritative until D-Bus confirms it.
                                checked = Qt.binding(() => row.modelData.enabled);
                                root.invoke("SetTrackerEnabled", [row.modelData.id, requested]);
                            }
                        }
                    }
                }
                PlasmaComponents3.Label {
                    text: i18n("Active Motion Tracker: %1", root.snapshot.active_name || i18n("None"))
                    Accessible.name: text
                    textFormat: Text.PlainText
                    wrapMode: Text.WrapAnywhere
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                }
                PlasmaComponents3.Button {
                    text: i18n("Recenter")
                    enabled: !root.busy && !!root.snapshot.active_id
                    Layout.alignment: Qt.AlignHCenter
                    onClicked: root.invoke("Recenter", [])
                }
                PlasmaComponents3.Label {
                    text: root.errorText
                    textFormat: Text.PlainText
                    visible: text.length > 0
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
            }
        ]
    }
}
