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
import "PlaybackState.js" as PlaybackState

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
    property string quality: "lossless"
    property string errorText: ""
    property string transportError: ""
    property bool transportBusy: false
    property int transportSerial: 0
    property DBus.DBusPendingReply transportReply
    property var transportFinished: null
    readonly property var runtime: snapshot.runtime || ({})
    readonly property var audio: runtime.audio || ({})
    readonly property var track: runtime.track || ({})
    readonly property string playbackFailure: audio.error || runtime.playback_error || ""
    readonly property string failure: errorText || transportError || playbackFailure
    readonly property string mediaService: "org.mpris.MediaPlayer2.spatiald"
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
            root.invalidate(); root.releaseTransport();
            root.snapshot = ({}); root.account = ({}); root.login = ({});
            root.page = ({}); root.history = [];
            if (registered && root.clientActive) {
                props.updateAll(); root.refreshAccount(false, true);
            }
        }
    }
    DBus.DBusServiceWatcher {
        id: mediaWatcher
        busType: DBus.BusType.Session
        watchedService: root.mediaService
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
        root.invalidate(false); root.releaseTransport(); root.login = ({});
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
            if (remember) root.history = root.history.concat([old]).slice(-20);
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
        if (!root.canPlay || root.runtime.loading || !TidalState.playable(item)) return;
        root.request("play", {reference: item.reference, quality: root.quality}, () => props.updateAll());
    }
    function paginate(offset) { root.navigate(Object.assign({}, root.location, {offset: offset}), false); }

    function showSearch() {
        if (root.busy) return;
        if (searchInput.text.trim()) root.search();
        else {
            root.history = root.history.concat([{location: root.location, page: root.page,
                categoryIndex: root.categoryIndex}]).slice(-20);
            root.location = ({mode: "search", query: ""}); root.page = ({});
        }
    }
    function releaseTransport() {
        root.transportSerial += 1;
        if (root.transportReply) {
            if (root.transportFinished) root.transportReply.finished.disconnect(root.transportFinished);
            root.transportReply.destroy();
        }
        root.transportReply = null; root.transportFinished = null; root.transportBusy = false;
    }
    function transport(member) {
        if (!root.serviceAvailable || !root.clientActive || (root.transportBusy && member !== "Stop")) return;
        if (member !== "Stop" && !mediaWatcher.registered) return;
        root.releaseTransport(); root.transportBusy = true; root.transportError = "";
        const serial = root.transportSerial;
        const stop = member === "Stop";
        const reply = DBus.SessionBus.asyncCall({service: stop ? root.service : root.mediaService,
            path: stop ? root.objectPath : "/org/mpris/MediaPlayer2",
            iface: stop ? root.service : "org.mpris.MediaPlayer2.Player", member: member, arguments: []});
        root.transportReply = reply;
        const finished = () => {
            if (serial === root.transportSerial) {
                root.transportReply = null; root.transportFinished = null; root.transportBusy = false;
                if (reply.isError) root.transportError = reply.error.message || i18n("Playback could not be changed.");
                props.updateAll();
            }
            reply.destroy();
        };
        root.transportFinished = finished;
        if (reply.isFinished) finished(); else reply.finished.connect(finished);
    }
    function playbackText() {
        if (root.playbackFailure) return i18n("Playback unavailable");
        if (root.runtime.loading) return i18n("Opening music…");
        if (root.audio.running && !root.audio.loaded) return i18n("Loading audio…");
        if (root.audio.running) {
            const state = root.audio.paused ? i18n("Paused") : i18n("Playing");
            // Only actual decoded objects establish Atmos; catalog badges cannot.
            return PlaybackState.sourceKind(root.audio) === "atmos" ? state + " · " + i18n("Dolby Atmos") : state;
        }
        return i18n("Choose music to play");
    }

    component Cover: Item {
        property string artwork: ""
        implicitWidth: 44
        implicitHeight: 44
        Kirigami.Icon {
            anchors.fill: parent
            source: "media-optical-audio"
            visible: cover.status !== Image.Ready
        }
        Image {
            id: cover
            anchors.fill: parent
            source: root.clientActive ? TidalState.artworkUrl(parent.artwork) : ""
            asynchronous: true
            fillMode: Image.PreserveAspectFit
            sourceSize.width: 160
            sourceSize.height: 160
        }
    }

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
                spacing: Kirigami.Units.smallSpacing * 2
                RowLayout {
                    Layout.fillWidth: true
                    PC.Button {
                        objectName: "tidalSearchTab"
                        text: i18n("Search")
                        icon.name: "edit-find"
                        checkable: true
                        checked: root.location.mode === "search"
                        enabled: !root.busy
                        Layout.fillWidth: true
                        onClicked: root.showSearch()
                    }
                    PC.Button {
                        objectName: "tidalLibrary"
                        text: i18n("Library")
                        icon.name: "folder-favorites"
                        checkable: true
                        checked: root.location.mode === "library"
                        enabled: !root.busy
                        Layout.fillWidth: true
                        onClicked: root.navigate({mode: "library"}, true)
                    }
                }
                RowLayout {
                    visible: root.location.mode === "search"
                    Layout.fillWidth: true
                    PC.TextField {
                        id: searchInput
                        objectName: "tidalSearchInput"
                        placeholderText: i18n("Artists, albums, songs…")
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
                RowLayout {
                    Layout.fillWidth: true
                    PC.ToolButton {
                        objectName: "tidalBack"
                        icon.name: "go-previous"
                        Accessible.name: i18n("Back")
                        enabled: !root.busy && root.history.length > 0
                        onClicked: root.back()
                    }
                    Cover {
                        artwork: root.page.item ? root.page.item.artwork || "" : ""
                        visible: !!root.page.item
                        Layout.preferredWidth: 48
                        Layout.preferredHeight: 48
                    }
                    PC.Label {
                        text: root.page.item ? root.page.item.title : root.location.mode === "library" ? i18n("Your favorites") : root.location.query || i18n("Find your next listen")
                        textFormat: Text.PlainText
                        wrapMode: Text.WrapAnywhere
                        maximumLineCount: 2
                        elide: Text.ElideRight
                        font.bold: true
                        Layout.fillWidth: true
                    }
                    PC.ToolButton {
                        objectName: "tidalPlayCollection"
                        icon.name: "media-playback-start"
                        visible: TidalState.playable(root.page.item)
                        enabled: root.canPlay && !root.busy && !root.runtime.loading
                        Accessible.name: i18n("Play collection")
                        onClicked: root.play(root.page.item)
                    }
                }
                QQC2.ScrollView {
                    id: resultsView
                    objectName: "tidalResults"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 240
                    clip: true
                    contentWidth: availableWidth
                    QQC2.ScrollBar.horizontal.policy: QQC2.ScrollBar.AlwaysOff
                    ColumnLayout {
                        width: resultsView.availableWidth
                        spacing: Kirigami.Units.smallSpacing * 2
                        Repeater {
                            model: root.rows
                            delegate: RowLayout {
                                id: resultRow
                                required property var modelData
                                required property int index
                                Layout.fillWidth: true
                                Cover {
                                    objectName: "tidalArtwork" + resultRow.index
                                    artwork: resultRow.modelData.artwork || ""
                                    Layout.preferredWidth: 44
                                    Layout.preferredHeight: 44
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 0
                                    PC.Label {
                                        text: resultRow.modelData.title || ""
                                        textFormat: Text.PlainText
                                        elide: Text.ElideRight
                                        maximumLineCount: 2
                                        wrapMode: Text.WrapAnywhere
                                        Layout.fillWidth: true
                                    }
                                    PC.Label {
                                        text: (resultRow.modelData.subtitle || "") + (resultRow.modelData.catalogue_atmos ? " · " + i18n("Atmos in catalog") : "")
                                        textFormat: Text.PlainText
                                        elide: Text.ElideRight
                                        Layout.fillWidth: true
                                        font.pixelSize: Math.round(Kirigami.Theme.smallFont.pixelSize)
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
                                    enabled: root.canPlay && !root.busy && !root.runtime.loading
                                    Accessible.name: i18n("Play %1", resultRow.modelData.title || "")
                                    onClicked: root.play(resultRow.modelData)
                                }
                            }
                        }
                        PC.Label {
                            text: root.page.offset !== undefined ? i18n("No results on this page.") : i18n("Search TIDAL or open your library.")
                            visible: !root.busy && root.rows.length === 0
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    PC.Button {
                        objectName: "tidalPreviousPage"
                        text: i18n("Previous page")
                        enabled: !root.busy && Number(root.page.offset || 0) > 0
                        onClicked: root.paginate(Math.max(0, Number(root.page.offset) - Number(root.page.limit || 20)))
                    }
                    Item { Layout.fillWidth: true }
                    PC.Button {
                        objectName: "tidalNextPage"
                        text: i18n("Next page")
                        enabled: !root.busy && root.page.has_more === true
                        onClicked: root.paginate(Number(root.page.offset || 0) + Number(root.page.limit || 20))
                    }
                }
                PC.ComboBox {
                    objectName: "tidalQuality"
                    model: [i18n("Lossless"), i18n("Dolby Atmos only")]
                    Accessible.name: i18n("TIDAL playback quality")
                    Layout.fillWidth: true
                    currentIndex: root.quality === "lossless" ? 0 : 1
                    onActivated: root.quality = currentIndex === 0 ? "lossless" : "atmos"
                }
                PC.Label {
                    text: root.quality === "atmos" ? i18n("Atmos must be available for this track and account. No stereo fallback.") : i18n("Lossless music. Choose Atmos only when you want to require spatial audio.")
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
            }
            Kirigami.Separator { Layout.fillWidth: true }
            ColumnLayout {
                objectName: "tidalNowPlaying"
                Layout.fillWidth: true
                PC.Label {
                    text: root.track.title || i18n("Now playing")
                    textFormat: Text.PlainText
                    font.bold: true
                    maximumLineCount: 2
                    elide: Text.ElideRight
                    wrapMode: Text.WrapAnywhere
                    Layout.fillWidth: true
                }
                PC.Label {
                    text: root.track.artist || ""
                    visible: !!text
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                PC.Label {
                    objectName: "tidalPlaybackState"
                    text: root.playbackText()
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
                PC.ProgressBar {
                    from: 0; to: 1
                    value: PlaybackState.progress(root.audio)
                    visible: !!root.audio.running || !!root.runtime.loading
                    indeterminate: !!root.runtime.loading || (!!root.audio.running && !root.audio.loaded)
                    Accessible.name: i18n("Playback progress")
                    Layout.fillWidth: true
                }
                PC.Label {
                    text: PlaybackState.durationText(root.audio.position) + " / " + PlaybackState.durationText(root.audio.duration)
                    visible: !!root.audio.loaded
                    textFormat: Text.PlainText
                    Layout.alignment: Qt.AlignHCenter
                }
                RowLayout {
                    Layout.alignment: Qt.AlignHCenter
                    PC.ToolButton {
                        objectName: "tidalPrevious"
                        icon.name: "media-skip-backward"
                        Accessible.name: i18n("Previous track")
                        enabled: root.canPlay && !root.transportBusy && !root.runtime.loading && mediaWatcher.registered && root.runtime.queue_index > 0
                        onClicked: root.transport("Previous")
                    }
                    PC.ToolButton {
                        objectName: "tidalPlayPause"
                        icon.name: root.audio.running && !root.audio.paused ? "media-playback-pause" : "media-playback-start"
                        Accessible.name: root.audio.running && !root.audio.paused ? i18n("Pause") : i18n("Play")
                        enabled: root.canPlay && !root.transportBusy && !root.runtime.loading && mediaWatcher.registered && root.runtime.queue_length > 0
                        onClicked: root.transport("PlayPause")
                    }
                    PC.ToolButton {
                        objectName: "tidalNext"
                        icon.name: "media-skip-forward"
                        Accessible.name: i18n("Next track")
                        enabled: root.canPlay && !root.transportBusy && !root.runtime.loading && mediaWatcher.registered && root.runtime.queue_index + 1 < root.runtime.queue_length
                        onClicked: root.transport("Next")
                    }
                    PC.ToolButton {
                        objectName: "tidalStop"
                        icon.name: "media-playback-stop"
                        Accessible.name: i18n("Stop")
                        enabled: root.serviceAvailable && (!!root.audio.running || !!root.runtime.loading)
                        onClicked: root.transport("Stop")
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
                text: root.failure
                visible: !!text
                textFormat: Text.PlainText
                wrapMode: Text.WrapAnywhere
                Layout.fillWidth: true
            }
        }
    }
}
