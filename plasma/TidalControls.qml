// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.plasma.components 3.0 as PC
import org.kde.kirigami as Kirigami
import org.kde.plasma.workspace.dbus as DBus
import "TidalState.js" as TidalState

Item {
    id: root
    objectName: "tidalClient"
    required property int cardWidth
    readonly property string service: "org.spatiald.Control1"
    readonly property string objectPath: "/org/spatiald/Control1"
    property var snapshot: ({})
    property var account: ({})
    property var login: ({})
    property var page: ({})
    property var location: ({mode: "search", query: ""})
    property var history: []
    property int categoryIndex: 0
    property string quality: "atmos"
    property string errorText: ""
    property bool busy: false
    property int requestSerial: 0
    property DBus.DBusPendingReply pendingReply
    property var pendingFinished: null
    property string pendingAction: ""
    readonly property bool serviceAvailable: watcher.registered
    readonly property bool clientActive: visible && Window.window !== null && Window.window.visible
    readonly property bool authenticated: account.authenticated === true
    readonly property bool canPlay: serviceAvailable && snapshot.schema === 1 && snapshot.audio_ready === true
    readonly property string category: ["tracks", "albums", "artists", "playlists"][categoryIndex]
    readonly property var rows: TidalState.items(page, category)
    readonly property bool authorizing: login.state === "pending" || login.state === "starting"
    implicitWidth: cardWidth
    implicitHeight: card.implicitHeight

    DBus.DBusServiceWatcher {
        id: watcher
        busType: DBus.BusType.Session
        watchedService: root.service
        onRegisteredChanged: {
            root.invalidate();
            root.snapshot = ({}); root.account = ({}); root.login = ({});
            root.page = ({}); root.history = [];
            if (registered && root.clientActive) {
                props.updateAll(); root.refreshAccount(false, true);
            }
        }
    }
    DBus.Properties {
        id: props
        busType: DBus.BusType.Session
        service: root.service
        path: root.objectPath
        iface: root.service
        property string stateText: String(properties.State || "")
        onStateTextChanged: root.readState(stateText)
        onRefreshed: root.readState(stateText)
    }
    Timer {
        id: loginPoll
        interval: Math.max(1000, Number(root.login.interval || 5) * 1000)
        repeat: true
        running: root.clientActive && root.serviceAvailable && root.authorizing
        onTriggered: {
            if (root.busy) return;
            if (root.login.state === "starting") root.resumeLogin();
            else root.request("login_poll", {login_id: root.login.login_id}, result => root.acceptLogin(result, false));
        }
    }
    Component.onCompleted: { if (root.serviceAvailable && root.clientActive) root.refreshAccount(false, true); }
    Component.onDestruction: root.release()
    onClientActiveChanged: {
        if (!clientActive) root.release();
        else if (root.serviceAvailable) { props.updateAll(); root.refreshAccount(false, true); }
    }

    function readState(text) {
        try { root.snapshot = TidalState.decode(text); }
        catch (error) { root.snapshot = ({}); }
    }
    function invalidate(cancelPendingStart) {
        root.requestSerial += 1;
        if (root.pendingReply) {
            const reply = root.pendingReply;
            if (root.pendingFinished) reply.finished.disconnect(root.pendingFinished);
            if (cancelPendingStart === true && root.pendingAction === "login_start") {
                // A cancelled start has no ID yet. Retain only its reply so a
                // late result can cancel that exact attempt, never another client.
                const targetService = root.service;
                const targetPath = root.objectPath;
                const disposeStart = () => {
                    if (!reply.isError) {
                        try {
                            const result = TidalState.decode(reply.value);
                            if (result.login_id && result.state === "pending") {
                                const cancel = DBus.SessionBus.asyncCall({service: targetService,
                                    path: targetPath, iface: targetService, member: "TidalRequest",
                                    arguments: ["login_cancel", JSON.stringify({login_id: result.login_id})]});
                                const disposeCancel = () => cancel.destroy();
                                if (cancel.isFinished) disposeCancel(); else cancel.finished.connect(disposeCancel);
                            }
                        } catch (error) { /* Discard an unusable orphan response. */ }
                    }
                    reply.destroy();
                };
                if (reply.isFinished) disposeStart(); else reply.finished.connect(disposeStart);
            } else reply.destroy();
        }
        root.pendingReply = null; root.pendingFinished = null; root.pendingAction = ""; root.busy = false;
    }
    function release() {
        // Opening the browser hides Plasma's popup and can destroy this card.
        // The daemon retains the bounded attempt; only explicit Cancel/sign-out
        // abandon it. A recreated card recovers instructions by direct reply.
        root.invalidate(false); root.login = ({});
    }
    function request(action, payload, callback) {
        if (!root.serviceAvailable || !root.clientActive || root.busy) return;
        root.busy = true; root.errorText = "";
        const serial = ++root.requestSerial;
        const reply = DBus.SessionBus.asyncCall({service: root.service, path: root.objectPath,
            iface: root.service, member: "TidalRequest", arguments: [action, JSON.stringify(payload)]});
        root.pendingReply = reply;
        root.pendingAction = action;
        const finished = () => {
            if (serial === root.requestSerial) {
                root.pendingReply = null; root.pendingFinished = null; root.pendingAction = ""; root.busy = false;
                if (reply.isError) root.errorText = reply.error.message || i18n("TIDAL could not complete this request.");
                else {
                    try {
                        const result = TidalState.decode(reply.value);
                        if (result.error) root.errorText = String(result.error);
                        if (callback) callback(result);
                    } catch (error) { root.errorText = i18n("TIDAL returned an unreadable response."); }
                }
            }
            reply.destroy();
        };
        root.pendingFinished = finished;
        if (reply.isFinished) finished(); else reply.finished.connect(finished);
    }
    function refreshAccount(refresh, resume) {
        root.request("status", {refresh: refresh}, result => {
            root.account = result;
            if (resume && !root.authenticated) root.resumeLogin();
        });
    }
    function resumeLogin() {
        root.request("login_status", {}, result => root.acceptLogin(result, true));
    }
    function acceptLogin(result, recovered) {
        root.login = Object.assign({}, root.login, result);
        if (result.state === "pending" && !TidalState.loginUrl(root.login.verification_url)) {
            root.cancelLogin(); root.errorText = i18n("TIDAL returned an unsupported sign-in address.");
        } else if (result.state !== "pending" && result.state !== "starting") {
            root.login = ({state: result.state});
            if (result.state === "signed_in") {
                if (!recovered) root.refreshAccount(true);
                else if (!root.authenticated) root.request("status", {refresh: false}, current => {
                    // A detached poll can complete between the initial account
                    // read and login_status. Recheck local saved credentials;
                    // an old completed attempt must not undo external sign-out.
                    root.account = current;
                    if (current.session_saved === true) root.refreshAccount(true);
                });
            } else if (result.state === "expired" || result.state === "error")
                root.errorText = result.error || i18n("Sign-in ended. Start again to get a new code.");
        }
    }
    function signIn() {
        if (root.busy || !root.serviceAvailable) return;
        root.login = ({state: "starting"});
        root.request("login_start", {}, result => root.acceptLogin(result, false));
    }
    function cancelLogin() {
        const loginId = root.login.login_id || "";
        root.invalidate(true); root.login = ({});
        if (loginId) root.request("login_cancel", {login_id: loginId}, null);
    }
    function signOut() {
        root.invalidate(true); root.login = ({});
        root.request("logout", {}, result => {
            root.account = result; root.page = ({}); root.history = []; root.location = ({mode: "search", query: ""});
        });
    }
    function navigate(destination, remember) {
        if (root.busy) return;
        const old = {location: root.location, page: root.page, categoryIndex: root.categoryIndex};
        const payload = {limit: 20, offset: destination.offset || 0};
        const action = destination.mode;
        if (action === "search") payload.query = destination.query;
        else if (action === "collection") { payload.kind = destination.kind; payload.id = destination.id; }
        else payload.kind = root.category;
        root.request(action, payload, result => {
            if (result.error) return;
            if (remember) root.history = root.history.concat([old]);
            root.location = destination; root.page = result;
            if (action === "collection") root.categoryIndex = 0;
        });
    }
    function search() {
        const query = searchInput.text.trim();
        if (query.length) root.navigate({mode: "search", query: query}, root.location.mode !== "search");
    }
    function browse(item) {
        if (item.kind !== "track") root.navigate({mode: "collection", kind: item.kind, id: item.id}, true);
    }
    function back() {
        if (root.busy || !root.history.length) return;
        const previous = root.history[root.history.length - 1];
        root.history = root.history.slice(0, -1); root.location = previous.location;
        root.page = previous.page; root.categoryIndex = previous.categoryIndex;
    }
    function play(item) {
        if (!root.canPlay || !TidalState.playable(item)) return;
        root.request("play", {reference: item.reference, quality: root.quality}, () => props.updateAll());
    }
    function paginate(offset) { root.navigate(Object.assign({}, root.location, {offset: offset}), false); }

    WidgetCard {
        id: card
        cardWidth: root.cardWidth
        content: ColumnLayout {
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.smallSpacing * 2
            spacing: Kirigami.Units.smallSpacing * 2
            PC.Label {
                text: i18n("TIDAL")
                textFormat: Text.PlainText
                font.bold: true
                Layout.alignment: Qt.AlignHCenter
            }
            PC.Label {
                text: !root.serviceAvailable ? i18n("BudsLink Spatial Companion is unavailable")
                    : root.authenticated ? i18n("Signed in") : root.account.session_saved ? i18n("Saved session — check sign-in to browse") : i18n("Sign in to browse TIDAL")
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.fillWidth: true
                PC.Button {
                    objectName: "tidalSignIn"
                    text: root.account.session_saved ? i18n("Check sign-in") : i18n("Sign in")
                    visible: !root.authenticated && !root.authorizing
                    enabled: root.serviceAvailable && !root.busy
                    onClicked: root.account.session_saved ? root.refreshAccount(true) : root.signIn()
                }
                PC.Button {
                    objectName: "tidalSignOut"
                    text: i18n("Sign out")
                    visible: root.authenticated || root.account.session_saved === true
                    enabled: root.serviceAvailable
                    onClicked: root.signOut()
                }
            }
            ColumnLayout {
                visible: root.authorizing
                Layout.fillWidth: true
                PC.Label {
                    objectName: "tidalLoginCode"
                    text: root.login.state === "starting" ? i18n("Starting sign-in…") : i18n("Code: %1", root.login.user_code || "")
                    textFormat: Text.PlainText
                    Accessible.name: text
                    font.bold: true
                    wrapMode: Text.WrapAnywhere
                    Layout.fillWidth: true
                }
                PC.Label {
                    text: i18n("Enter this code on TIDAL’s sign-in page, then reopen this panel to finish. Sign-in checks pause while it is closed.")
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
                RowLayout {
                    PC.Button {
                        objectName: "tidalOpenLogin"
                        text: i18n("Open TIDAL sign-in")
                        enabled: !!TidalState.loginUrl(root.login.verification_url)
                        onClicked: Qt.openUrlExternally(TidalState.loginUrl(root.login.verification_url))
                    }
                    PC.Button {
                        objectName: "tidalCancelLogin"
                        text: i18n("Cancel")
                        onClicked: root.cancelLogin()
                    }
                }
            }
            ColumnLayout {
                visible: root.authenticated
                Layout.fillWidth: true
                RowLayout {
                    PC.TextField {
                        id: searchInput
                        objectName: "tidalSearchInput"
                        placeholderText: i18n("Search TIDAL")
                        Accessible.name: i18n("Search TIDAL")
                        Layout.fillWidth: true
                        selectByMouse: true
                        onAccepted: root.search()
                    }
                    PC.ToolButton {
                        objectName: "tidalSearch"
                        icon.name: "edit-find"
                        Accessible.name: i18n("Search")
                        enabled: !root.busy && !!searchInput.text.trim()
                        onClicked: root.search()
                    }
                }
                RowLayout {
                    PC.ComboBox {
                        objectName: "tidalCategory"
                        model: root.location.mode === "collection" ? [i18n("Tracks"), i18n("Albums")]
                            : [i18n("Tracks"), i18n("Albums"), i18n("Artists"), i18n("Playlists")]
                        Accessible.name: i18n("Browse category")
                        Layout.fillWidth: true
                        currentIndex: root.categoryIndex
                        enabled: !root.busy && (root.location.mode !== "collection" || (!!root.page.item && root.page.item.kind === "artist"))
                        onActivated: {
                            root.categoryIndex = currentIndex;
                            if (root.location.mode === "library") root.navigate({mode: "library"}, false);
                        }
                    }
                    PC.Button {
                        objectName: "tidalLibrary"
                        text: i18n("Favorites")
                        enabled: !root.busy
                        onClicked: root.navigate({mode: "library"}, true)
                    }
                }
                PC.ComboBox {
                    objectName: "tidalQuality"
                    model: [i18n("Dolby Atmos only"), i18n("Lossless")]
                    Accessible.name: i18n("TIDAL playback quality")
                    Layout.fillWidth: true
                    currentIndex: root.quality === "atmos" ? 0 : 1
                    onActivated: root.quality = currentIndex === 0 ? "atmos" : "lossless"
                }
                PC.Label {
                    text: root.quality === "atmos" ? i18n("Requires Dolby Atmos. Playback stops with an error if it is unavailable.") : i18n("Play the lossless stream using your audio settings.")
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                    font.pixelSize: Math.round(Kirigami.Theme.smallFont.pixelSize)
                }
                PC.Label {
                    visible: !root.canPlay
                    text: i18n("Connect your headphones to play. You can keep browsing.")
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
                RowLayout {
                    Layout.fillWidth: true
                    PC.ToolButton {
                        objectName: "tidalBack"
                        icon.name: "go-previous"
                        Accessible.name: i18n("Back")
                        enabled: !root.busy && root.history.length > 0
                        onClicked: root.back()
                    }
                    PC.Label {
                        text: root.page.item ? root.page.item.title : root.location.mode === "library" ? i18n("Favorites") : root.location.query || i18n("Search tracks, albums, artists and playlists")
                        textFormat: Text.PlainText
                        wrapMode: Text.WrapAnywhere
                        Layout.fillWidth: true
                    }
                    PC.ToolButton {
                        objectName: "tidalPlayCollection"
                        icon.name: "media-playback-start"
                        visible: TidalState.playable(root.page.item)
                        enabled: root.canPlay && !root.busy
                        Accessible.name: i18n("Play collection")
                        onClicked: root.play(root.page.item)
                    }
                }
                Repeater {
                    model: root.rows
                    delegate: RowLayout {
                        id: resultRow
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            PC.Label {
                                text: resultRow.modelData.title || ""
                                textFormat: Text.PlainText
                                wrapMode: Text.WrapAnywhere
                                Layout.fillWidth: true
                            }
                            PC.Label {
                                text: (resultRow.modelData.subtitle || "") + (resultRow.modelData.catalogue_atmos ? " · " + i18n("Atmos in catalog") : "")
                                textFormat: Text.PlainText
                                wrapMode: Text.WrapAnywhere
                                font.pixelSize: Math.round(Kirigami.Theme.smallFont.pixelSize)
                                Layout.fillWidth: true
                            }
                        }
                        PC.ToolButton {
                            objectName: "tidalBrowse" + resultRow.index
                            icon.name: "go-next"
                            visible: resultRow.modelData.kind !== "track"
                            enabled: !root.busy
                            Accessible.name: i18n("Browse %1", resultRow.modelData.title || "")
                            onClicked: root.browse(resultRow.modelData)
                        }
                        PC.ToolButton {
                            objectName: "tidalPlay" + resultRow.index
                            icon.name: "media-playback-start"
                            visible: TidalState.playable(resultRow.modelData)
                            enabled: root.canPlay && !root.busy
                            Accessible.name: i18n("Play %1", resultRow.modelData.title || "")
                            onClicked: root.play(resultRow.modelData)
                        }
                    }
                }
                PC.Label {
                    text: i18n("No results on this page.")
                    visible: !root.busy && root.page.offset !== undefined && root.rows.length === 0
                    textFormat: Text.PlainText
                    Layout.fillWidth: true
                }
                RowLayout {
                    Layout.alignment: Qt.AlignHCenter
                    PC.Button {
                        objectName: "tidalPreviousPage"
                        text: i18n("Previous page")
                        enabled: !root.busy && Number(root.page.offset || 0) > 0
                        onClicked: root.paginate(Math.max(0, Number(root.page.offset) - Number(root.page.limit || 20)))
                    }
                    PC.Button {
                        objectName: "tidalNextPage"
                        text: i18n("Next page")
                        enabled: !root.busy && root.page.has_more === true
                        onClicked: root.paginate(Number(root.page.offset || 0) + Number(root.page.limit || 20))
                    }
                }
            }
            PC.BusyIndicator {
                running: root.busy
                visible: running
                Accessible.name: i18n("Loading TIDAL")
                Layout.alignment: Qt.AlignHCenter
            }
            PC.Label {
                objectName: "tidalError"
                text: root.errorText
                visible: !!text
                textFormat: Text.PlainText
                wrapMode: Text.WrapAnywhere
                Layout.fillWidth: true
            }
        }
    }
}
