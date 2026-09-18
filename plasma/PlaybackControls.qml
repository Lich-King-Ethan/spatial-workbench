// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import QtQuick.Dialogs as Dialogs
import org.kde.plasma.components 3.0 as PlasmaComponents3
import org.kde.kirigami as Kirigami
import org.kde.plasma.workspace.dbus as DBus
import "PlaybackState.js" as PlaybackState

Item {
    id: root
    required property var device
    required property int cardWidth
    readonly property string service: "org.spatiald.Control1"
    readonly property string objectPath: "/org/spatiald/Control1"
    readonly property string mediaService: "org.mpris.MediaPlayer2.spatiald"
    property var snapshot: ({})
    property string errorText: ""
    property bool busy: false
    property string pendingMember: ""
    property int requestSerial: 0
    property DBus.DBusPendingReply pendingReply
    readonly property var runtime: snapshot.runtime || ({})
    readonly property var audio: runtime.audio || ({})
    readonly property var track: runtime.track || ({})
    readonly property string sourceKind: PlaybackState.sourceKind(audio)
    readonly property bool matches: watcher.registered && snapshot.schema === 1
        && snapshot.connected && device && snapshot.companion_path === device.path
    readonly property string failure: errorText || audio.error || runtime.playback_error || ""
    visible: matches
    implicitWidth: cardWidth
    implicitHeight: visible ? card.implicitHeight : 0

    DBus.DBusServiceWatcher {
        id: watcher
        busType: DBus.BusType.Session
        watchedService: root.service
        onRegisteredChanged: {
            if (registered) {
                controlProps.updateAll();
            } else {
                root.snapshot = ({});
                root.busy = false;
                root.pendingMember = "";
                root.requestSerial += 1;
                root.errorText = "";
            }
        }
    }
    DBus.DBusServiceWatcher {
        id: mediaWatcher
        busType: DBus.BusType.Session
        watchedService: root.mediaService
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
    Dialogs.FileDialog {
        id: fileDialog
        title: i18n("Open Audio or Video")
        fileMode: Dialogs.FileDialog.OpenFile
        nameFilters: [i18n("Audio and video (*.flac *.m4a *.mkv *.mka *.mp4 *.wav *.mp3 *.ogg *.opus *.aac *.eac3 *.ac3 *.thd *.truehd *.dts)"),
                      i18n("All files (*)")]
        onAccepted: mediaInput.text = selectedFile.toString()
    }

    function readState(text) {
        try { root.snapshot = text ? JSON.parse(text) : ({}); }
        catch (error) { root.snapshot = ({}); }
    }
    function sourceText() {
        switch (root.sourceKind) {
        case "atmos": return i18n("Dolby Atmos");
        case "dtsx": return i18n("DTS:X");
        case "objects": return i18n("Object audio");
        case "binaural": return i18n("Binaural audio");
        case "stereo": return i18n("Stereo audio");
        default: return "";
        }
    }
    function playbackText() {
        if (root.runtime.loading || (root.busy && root.pendingMember === "Play")) return i18n("Opening…");
        if (root.audio.running && !root.audio.loaded) return i18n("Loading…");
        if (root.audio.running) return root.audio.paused ? i18n("Paused") : i18n("Playing");
        if (!root.snapshot.audio_ready) return i18n("Waiting for headphone output…");
        return i18n("Ready");
    }
    function invoke(member, args, useMediaService) {
        // Stop remains available while a source is opening or waiting for audio.
        if (!watcher.registered || (root.busy && member !== "Stop")) return;
        if (useMediaService && !mediaWatcher.registered) return;
        root.busy = true;
        root.pendingMember = member;
        root.errorText = "";
        const serial = ++root.requestSerial;
        root.pendingReply = DBus.SessionBus.asyncCall({
            service: useMediaService ? root.mediaService : root.service,
            path: useMediaService ? "/org/mpris/MediaPlayer2" : root.objectPath,
            iface: useMediaService ? "org.mpris.MediaPlayer2.Player" : root.service,
            member: member, arguments: args
        });
        const reply = root.pendingReply;
        const finished = () => {
            if (serial === root.requestSerial) {
                root.busy = false;
                root.pendingMember = "";
                root.pendingReply = null;
                if (reply.isError)
                    root.errorText = reply.error.message || i18n("Playback could not be changed.");
                controlProps.updateAll();
            }
            reply.destroy();
        };
        if (reply.isFinished) finished();
        else reply.finished.connect(finished);
    }
    function openMedia() {
        const value = mediaInput.text.trim();
        if (value.length) root.invoke("Play", [value], false);
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
                    text: i18n("Playback")
                    font.pixelSize: Math.round(Kirigami.Theme.smallFont.pixelSize)
                    horizontalAlignment: Text.AlignHCenter
                    Layout.fillWidth: true
                }
                RowLayout {
                    Layout.fillWidth: true
                    PlasmaComponents3.TextField {
                        id: mediaInput
                        Layout.fillWidth: true
                        placeholderText: i18n("File path or TIDAL URL")
                        Accessible.name: i18n("File path or TIDAL URL")
                        selectByMouse: true
                        onAccepted: root.openMedia()
                    }
                    PlasmaComponents3.ToolButton {
                        icon.name: "document-open"
                        Accessible.name: i18n("Browse for a file")
                        QQC2.ToolTip.text: i18n("Browse for a file")
                        QQC2.ToolTip.visible: hovered
                        onClicked: fileDialog.open()
                    }
                }
                PlasmaComponents3.Button {
                    text: i18n("Open")
                    icon.name: "media-playback-start"
                    enabled: !root.busy && !root.runtime.loading && mediaInput.text.trim().length > 0
                    Layout.alignment: Qt.AlignHCenter
                    onClicked: root.openMedia()
                }
                PlasmaComponents3.Label {
                    text: root.track.title || ""
                    visible: text.length > 0
                    textFormat: Text.PlainText
                    wrapMode: Text.WrapAnywhere
                    horizontalAlignment: Text.AlignHCenter
                    Layout.fillWidth: true
                }
                PlasmaComponents3.Label {
                    text: root.playbackText() + (root.sourceText() ? " · " + root.sourceText() : "")
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    horizontalAlignment: Text.AlignHCenter
                    Layout.fillWidth: true
                }
                PlasmaComponents3.ProgressBar {
                    from: 0
                    to: 1
                    value: PlaybackState.progress(root.audio)
                    visible: !!root.audio.running
                    indeterminate: !!root.audio.running && !root.audio.loaded
                    Accessible.name: i18n("Playback progress")
                    Layout.fillWidth: true
                }
                PlasmaComponents3.Label {
                    text: PlaybackState.durationText(root.audio.position)
                          + (PlaybackState.seconds(root.audio.duration) > 0
                             ? " / " + PlaybackState.durationText(root.audio.duration) : "")
                    visible: !!root.audio.loaded
                    Accessible.name: i18n("Playback time")
                    horizontalAlignment: Text.AlignHCenter
                    Layout.fillWidth: true
                    font.pixelSize: Math.round(Kirigami.Theme.smallFont.pixelSize)
                }
                RowLayout {
                    Layout.alignment: Qt.AlignHCenter
                    PlasmaComponents3.ToolButton {
                        icon.name: "media-skip-backward"
                        enabled: !root.busy && !root.runtime.loading && mediaWatcher.registered && root.runtime.queue_index > 0
                        Accessible.name: i18n("Previous track")
                        QQC2.ToolTip.text: i18n("Previous track")
                        QQC2.ToolTip.visible: hovered
                        onClicked: root.invoke("Previous", [], true)
                    }
                    PlasmaComponents3.ToolButton {
                        icon.name: root.audio.running && !root.audio.paused ? "media-playback-pause" : "media-playback-start"
                        enabled: !root.busy && !root.runtime.loading && mediaWatcher.registered && root.runtime.queue_length > 0
                        Accessible.name: root.audio.running && !root.audio.paused ? i18n("Pause") : i18n("Play")
                        QQC2.ToolTip.text: Accessible.name
                        QQC2.ToolTip.visible: hovered
                        onClicked: root.invoke("PlayPause", [], true)
                    }
                    PlasmaComponents3.ToolButton {
                        icon.name: "media-playback-stop"
                        enabled: !!root.audio.running || !!root.runtime.loading || root.busy
                        Accessible.name: i18n("Stop")
                        QQC2.ToolTip.text: i18n("Stop")
                        QQC2.ToolTip.visible: hovered
                        onClicked: root.invoke("Stop", [], false)
                    }
                    PlasmaComponents3.ToolButton {
                        icon.name: "media-skip-forward"
                        enabled: !root.busy && !root.runtime.loading && mediaWatcher.registered
                            && root.runtime.queue_index + 1 < root.runtime.queue_length
                        Accessible.name: i18n("Next track")
                        QQC2.ToolTip.text: i18n("Next track")
                        QQC2.ToolTip.visible: hovered
                        onClicked: root.invoke("Next", [], true)
                    }
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
