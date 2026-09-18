// SPDX-License-Identifier: GPL-3.0-or-later
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
    property string selectedSerial: ""
    property string errorText: ""
    property bool busy: false
    property string pendingMember: ""
    property int requestSerial: 0
    property DBus.DBusPendingReply pendingReply
    readonly property var runtime: snapshot.runtime || ({})
    readonly property var live: runtime.live || ({})
    readonly property var streams: Array.isArray(live.available_streams) ? live.available_streams : []
    readonly property bool selectedAvailable: streams.some(stream => String(stream.serial) === selectedSerial)
    readonly property bool matches: watcher.registered && snapshot.schema === 1
        && snapshot.connected && device && snapshot.companion_path === device.path
    readonly property string failure: errorText || live.error || ""
    visible: matches && runtime.live !== undefined
    implicitWidth: cardWidth
    implicitHeight: visible ? card.implicitHeight : 0
    onStreamsChanged: Qt.callLater(root.restoreSelection)

    DBus.DBusServiceWatcher {
        id: watcher
        busType: DBus.BusType.Session
        watchedService: root.service
        onRegisteredChanged: {
            if (registered) {
                controlProps.updateAll();
            } else {
                root.snapshot = ({});
                root.selectedSerial = "";
                root.errorText = "";
                root.busy = false;
                root.pendingMember = "";
                root.requestSerial += 1;
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
    function restoreSelection() {
        const index = root.streams.findIndex(stream => String(stream.serial) === root.selectedSerial);
        applicationBox.currentIndex = index;
        if (index < 0) root.selectedSerial = "";
    }
    function stateText() {
        if (root.busy && root.pendingMember === "StartLive") return i18n("Starting…");
        if (root.busy && root.pendingMember === "StopLive") return i18n("Stopping…");
        if (root.live.state === "starting") return i18n("Starting…");
        if (root.live.state === "stopping") return i18n("Stopping…");
        if (root.live.state === "unavailable" || root.live.state === "error" || root.live.state === "failed")
            return i18n("Unavailable");
        if (root.live.state === "disabled") return i18n("Disabled");
        if (root.live.running && root.live.renderer_ready) return i18n("Binaural PCM audio");
        if (root.live.running) return i18n("Starting…");
        if (!root.snapshot.audio_ready) return i18n("Waiting for headphone output…");
        return i18n("Ready");
    }
    function invoke(member, args) {
        if (!watcher.registered || (root.busy && member !== "StopLive")) return;
        root.busy = true;
        root.pendingMember = member;
        root.errorText = "";
        const serial = ++root.requestSerial;
        root.pendingReply = DBus.SessionBus.asyncCall({service: root.service,
            path: root.objectPath, iface: root.service, member: member, arguments: args});
        const reply = root.pendingReply;
        const finished = () => {
            if (serial === root.requestSerial) {
                root.busy = false;
                root.pendingMember = "";
                root.pendingReply = null;
                if (reply.isError)
                    root.errorText = reply.error.message || i18n("Application audio could not be changed.");
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
                PlasmaComponents3.Label {
                    text: i18n("Application Audio")
                    horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: Math.round(Kirigami.Theme.smallFont.pixelSize)
                    Layout.fillWidth: true
                }
                PlasmaComponents3.ComboBox {
                    id: applicationBox
                    model: root.streams
                    textRole: "name"
                    valueRole: "serial"
                    currentIndex: -1
                    displayText: currentIndex >= 0 ? currentText : i18n("Choose an application")
                    enabled: !root.busy && root.streams.length > 0
                    Accessible.name: i18n("Application")
                    Layout.fillWidth: true
                    onActivated: index => root.selectedSerial = String(root.streams[index].serial)
                }
                PlasmaComponents3.Label {
                    text: root.streams.length ? i18n("Applies to the selected application's audio.")
                        : i18n("Play audio in an application to make it available here.")
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: Math.round(Kirigami.Theme.smallFont.pixelSize)
                }
                RowLayout {
                    Layout.alignment: Qt.AlignHCenter
                    PlasmaComponents3.Button {
                        text: i18n("Start")
                        icon.name: "media-playback-start"
                        enabled: !root.busy && !root.live.running && root.selectedAvailable
                            && !!root.snapshot.audio_ready
                        onClicked: root.invoke("StartLive", [root.selectedSerial])
                    }
                    PlasmaComponents3.Button {
                        text: i18n("Stop")
                        icon.name: "media-playback-stop"
                        enabled: !!root.live.running || root.live.state === "starting" || root.busy
                        onClicked: root.invoke("StopLive", [])
                    }
                }
                PlasmaComponents3.Label {
                    text: root.live.stream_name || ""
                    visible: !!root.live.running && text.length > 0
                    textFormat: Text.PlainText
                    wrapMode: Text.WrapAnywhere
                    horizontalAlignment: Text.AlignHCenter
                    Layout.fillWidth: true
                }
                PlasmaComponents3.Label {
                    text: root.stateText()
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    horizontalAlignment: Text.AlignHCenter
                    Layout.fillWidth: true
                }
                PlasmaComponents3.Label {
                    text: root.failure
                    visible: text.length > 0
                    textFormat: Text.PlainText
                    wrapMode: Text.WrapAnywhere
                    Layout.fillWidth: true
                }
            }
        ]
    }
}
