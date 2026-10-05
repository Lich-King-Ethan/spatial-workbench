// SPDX-License-Identifier: GPL-3.0-or-later
// Native UI smoke test. This private D-Bus fixture is not a hardware simulator.
#include <KLocalizedQmlContext>
#include <Kirigami/Platform/PlatformTheme>
#include <Plasma/Plasma>
#include <Plasma/Theme>
#include <QCoreApplication>
#include <QDBusAbstractAdaptor>
#include <QDBusConnection>
#include <QDBusMessage>
#include <QEvent>
#include <QGuiApplication>
#include <QJSValue>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QQmlComponent>
#include <QQmlContext>
#include <QQmlEngine>
#include <QQmlError>
#include <QQuickItem>
#include <QQuickWindow>
#include <QTest>
#include <QTimer>
#include <memory>
#include <cmath>

static const QString service = QStringLiteral("org.spatiald.Control1");
static const QString objectPath = QStringLiteral("/org/spatiald/Control1");
static const QString companionPath = QStringLiteral("/fixture/XM5");

static QDBusConnection controlBus()
{
    // Separate connections preserve real asynchronous D-Bus reply semantics.
    static auto bus = QDBusConnection::connectToBus(QDBusConnection::SessionBus,
                                                   QStringLiteral("qml-control-fixture"));
    return bus;
}

static QQuickItem *findVisualLabel(QQuickItem *item, const QString &text)
{
    if (item->property("text").toString() == text)
        return item;
    // Repeater delegates are visual children; QObject ownership is a separate tree.
    for (auto *child : item->childItems()) {
        if (auto *found = findVisualLabel(child, text))
            return found;
    }
    return nullptr;
}

static QQuickItem *findVisualObject(QQuickItem *item, const QString &name)
{
    if (item->objectName() == name) return item;
    for (auto *child : item->childItems()) {
        if (auto *found = findVisualObject(child, name)) return found;
    }
    return nullptr;
}

class ControlFixture : public QDBusAbstractAdaptor
{
    Q_OBJECT
    Q_CLASSINFO("D-Bus Interface", "org.spatiald.Control1")
    Q_PROPERTY(QString State READ state)
public:
    explicit ControlFixture(QObject *parent) : QDBusAbstractAdaptor(parent) {}
    QJsonObject snapshot;
    bool signedIn = false;
    bool savedSession = false;
    bool delaySearch = false;
    bool failSearch = false;
    bool delayLogin = false;
    bool completeLogin = true;
    bool completeOnLoginStatus = false;
    QJsonObject loginAttempt;
    QDBusMessage delayedSearch;
    QDBusMessage delayedLogin;
    QJsonArray requests;
    QStringList transportRequests;
    bool manyTracks = false;
    QJsonObject track = {{"kind", "track"}, {"id", "42"}, {"title", "<Track & fixture>"},
        {"subtitle", "Fixture artist"}, {"reference", "tidal:track:42"}, {"catalogue_atmos", true}};
    static QString json(const QJsonObject &value) {
        return QString::fromUtf8(QJsonDocument(value).toJson(QJsonDocument::Compact));
    }
    QString state() const
    {
        return QString::fromUtf8(QJsonDocument(snapshot).toJson(QJsonDocument::Compact));
    }
    bool publish()
    {
        auto signal = QDBusMessage::createSignal(objectPath,
            QStringLiteral("org.freedesktop.DBus.Properties"), QStringLiteral("PropertiesChanged"));
        signal << service << QVariantMap{{QStringLiteral("State"), state()}} << QStringList{};
        return controlBus().send(signal);
    }
public slots:
    void Stop() { transportRequests.append(QStringLiteral("Stop")); }
    QString TidalRequest(const QString &action, const QString &payload, const QDBusMessage &message)
    {
        auto args = QJsonDocument::fromJson(payload.toUtf8()).object();
        requests.append(QJsonObject{{"action", action}, {"payload", args}});
        if (action == "status") {
            if (savedSession && args["refresh"].toBool()) signedIn = true;
            return json({{"authenticated", signedIn}, {"session_saved", savedSession}});
        }
        if (action == "login_start") {
            loginAttempt = {{"login_id", "fixture-login"}, {"state", delayLogin ? "starting" : "pending"}, {"interval", 1}};
            if (delayLogin) {
                message.setDelayedReply(true); delayedLogin = message; return {};
            }
            loginAttempt["user_code"] = "ABCD-1234";
            loginAttempt["verification_url"] = "https://link.tidal.com/fixture";
            return json(loginAttempt);
        }
        if (action == "login_status") {
            if (completeOnLoginStatus) {
                completeOnLoginStatus = false; savedSession = true;
                loginAttempt = {{"state", "signed_in"}};
            }
            return json(loginAttempt.isEmpty() ? QJsonObject{{"state", "idle"}} : loginAttempt);
        }
        if (action == "login_poll") {
            if (!completeLogin) return json({{"state", "pending"}});
            savedSession = true; loginAttempt = {{"state", "signed_in"}};
            return json(loginAttempt);
        }
        if (action == "login_cancel") { loginAttempt = {{"state", "cancelled"}}; return json(loginAttempt); }
        if (action == "logout") {
            signedIn = savedSession = false; loginAttempt = {};
            return json({{"authenticated", false}, {"session_saved", false}});
        }
        if (action == "play") return json({{"accepted", true}});
        if (action == "search" && delaySearch) {
            message.setDelayedReply(true); delayedSearch = message; return {};
        }
        if (action == "search" && failSearch) {
            message.setDelayedReply(true);
            controlBus().send(message.createErrorReply(service + ".Failed", "Fixture search unavailable"));
            return {};
        }
        QJsonObject album{{"kind", "album"}, {"id", "12"}, {"title", "Fixture album"},
                          {"reference", "tidal:album:12"}};
        QJsonObject artist{{"kind", "artist"}, {"id", "7"}, {"title", "Fixture artist"}, {"reference", ""}};
        QJsonObject playlist{{"kind", "playlist"}, {"id", "list-1"}, {"title", "Fixture playlist"},
                             {"reference", "tidal:playlist:list-1"}};
        QJsonObject result{{"offset", args["offset"].toInt()}, {"limit", 20}, {"has_more", args["offset"].toInt() == 0}};
        QJsonArray tracks{track};
        if (manyTracks) for (int i = 1; i < 20; ++i) {
            auto row = track; row["title"] = QStringLiteral("Track with a long fixture title %1").arg(i);
            tracks.append(row);
        }
        result["tracks"] = tracks; result["albums"] = QJsonArray{album};
        result["artists"] = QJsonArray{artist}; result["playlists"] = QJsonArray{playlist};
        if (args["offset"].toInt() > 0)
            for (const auto &key : {"tracks", "albums", "artists", "playlists"}) result[key] = QJsonArray{};
        if (action == "collection") result["item"] = args["kind"] == "artist" ? artist : album;
        if (action == "library") result["items"] = result[args["kind"].toString()];
        return json(result);
    }
};

class MediaFixture : public QDBusAbstractAdaptor
{
    Q_OBJECT
    Q_CLASSINFO("D-Bus Interface", "org.mpris.MediaPlayer2.Player")
public:
    explicit MediaFixture(QObject *parent) : QDBusAbstractAdaptor(parent) {}
    QStringList calls;
public slots:
    void PlayPause() { calls.append(QStringLiteral("PlayPause")); }
    void Previous() { calls.append(QStringLiteral("Previous")); }
    void Next() { calls.append(QStringLiteral("Next")); }
};

class NativeQmlSmoke : public QObject
{
    Q_OBJECT
    QObject fixtureObject;
    ControlFixture fixture{&fixtureObject};
    QObject mediaObject;
    MediaFixture mediaFixture{&mediaObject};

private slots:
    void initTestCase()
    {
        // The package runner starts a private bus with desktop activation disabled.
        QVERIFY2(qEnvironmentVariable("SPATIAL_QML_PRIVATE_BUS") == QStringLiteral("1"),
                 "Run this test through plasma/tests/run-private.sh and private-session.conf.");
        QVERIFY(controlBus().isConnected());
        QVERIFY(controlBus().registerObject(
            objectPath, &fixtureObject, QDBusConnection::ExportAdaptors));
        QVERIFY(controlBus().registerService(service));
        QVERIFY(controlBus().registerObject(QStringLiteral("/org/mpris/MediaPlayer2"), &mediaObject,
            QDBusConnection::ExportAdaptors));
        QVERIFY(controlBus().registerService(QStringLiteral("org.mpris.MediaPlayer2.spatiald")));
    }

    void cards_data()
    {
        QTest::addColumn<QString>("filename");
        QTest::newRow("tracking") << QStringLiteral("SpatialControls.qml");
        QTest::newRow("playback") << QStringLiteral("PlaybackControls.qml");
        QTest::newRow("application-audio") << QStringLiteral("LiveAudioControls.qml");
    }

    void cards()
    {
        QFETCH(QString, filename);
        // Deliberately includes markup and a serial above JavaScript's exact integer range.
        fixture.snapshot = QJsonDocument::fromJson(R"JSON({
            "schema":1,"connected":true,"audio_ready":true,"companion_path":"/fixture/XM5",
            "earbud":{"id":"earbud","enabled":false},
            "optional_trackers":[{"id":"fixture","name":"<tracker & fixture>","enabled":true}],
            "active_id":"fixture","active_name":"<tracker & fixture>",
            "runtime":{
                "loading":false,"queue_index":0,"queue_length":1,"track":{"title":"Fixture audio"},
                "audio":{"running":true,"loaded":true,"paused":false,"renderer_ready":true,
                    "position":2,"duration":10,"source_mode":"spatial","object_count":0},
                "live":{"state":"idle","running":false,"source_mode":"pcm",
                    "available_streams":[{"serial":"18446744073709551614","name":"Fixture application"}]}
            }
        })JSON").object();
        QVERIFY(!fixture.snapshot.isEmpty());

        QStringList warnings;
        QQmlEngine engine;
        // Match the actual Plasma engine. Without this, Kirigami BasicTheme
        // text colors can disagree with the current Plasma SVG controls.
        Plasma::setupPlasmaStyle(&engine);
        connect(&engine, &QQmlEngine::warnings, &engine, [&warnings](const QList<QQmlError> &errors) {
            for (const auto &error : errors)
                warnings.append(error.toString());
        });
        auto *translations = new KLocalizedQmlContext(&engine);
        translations->setTranslationDomain(QStringLiteral("spatial-companion-smoke"));
        engine.rootContext()->setContextObject(translations);
        QQmlEngine::setContextForObject(translations, engine.rootContext());

        QQuickWindow window;
        Plasma::Theme plasmaTheme; window.setColor(plasmaTheme.color(Plasma::Theme::BackgroundColor));
        window.resize(520, 1000);
        window.show();
        QQmlComponent component(&engine,
            QUrl::fromLocalFile(QStringLiteral(SPATIAL_COMPANION_QML_DIR) + '/' + filename));
        QTRY_VERIFY_WITH_TIMEOUT(component.status() != QQmlComponent::Loading, 5000);
        QVERIFY2(component.isReady(), qPrintable(component.errorString()));
        std::unique_ptr<QObject> object(component.createWithInitialProperties({
            {QStringLiteral("device"), QVariantMap{{QStringLiteral("path"), companionPath}}},
            {QStringLiteral("cardWidth"), 480}
        }));
        QVERIFY2(object != nullptr, qPrintable(component.errorString()));
        auto *item = qobject_cast<QQuickItem *>(object.get());
        QVERIFY(item);
        item->setParentItem(window.contentItem());
        QTRY_VERIFY_WITH_TIMEOUT(item->property("matches").toBool(), 5000);
        QTRY_VERIFY_WITH_TIMEOUT(item->isVisible() && item->implicitHeight() > 0, 5000);

        if (filename == QStringLiteral("SpatialControls.qml")) {
            QTRY_COMPARE(item->property("rows").value<QJSValue>().property("length").toInt(), 2);
            QQuickItem *plainName = nullptr;
            QTRY_VERIFY_WITH_TIMEOUT(
                (plainName = findVisualLabel(item, QStringLiteral("<tracker & fixture>"))) != nullptr,
                5000);
            QCOMPARE(plainName->property("textFormat").toInt(), int(Qt::PlainText));
        } else if (filename == QStringLiteral("PlaybackControls.qml")) {
            QCOMPARE(item->property("sourceKind").toString(), QStringLiteral("binaural"));
            auto runtime = fixture.snapshot["runtime"].toObject();
            auto audio = runtime["audio"].toObject();
            audio["object_count"] = 4;
            audio["content_format"] = QStringLiteral("Dolby Atmos");
            runtime["audio"] = audio;
            fixture.snapshot["runtime"] = runtime;
            QVERIFY(fixture.publish());
            QTRY_COMPARE(item->property("sourceKind").toString(), QStringLiteral("atmos"));
        } else {
            QVERIFY(item->setProperty("selectedSerial", QStringLiteral("18446744073709551614")));
            QTRY_VERIFY(item->property("selectedAvailable").toBool());
            auto runtime = fixture.snapshot["runtime"].toObject();
            auto live = runtime["live"].toObject();
            live["available_streams"] = QJsonArray{};
            runtime["live"] = live;
            fixture.snapshot["runtime"] = runtime;
            QVERIFY(fixture.publish());
            QTRY_VERIFY(item->property("selectedSerial").toString().isEmpty());
        }
        // Allow queued bindings, delegate creation, and layout polish to run.
        QTest::qWait(150);
        object.reset();
        QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);
        QVERIFY2(warnings.isEmpty(), qPrintable(warnings.join('\n')));
    }

    void cleanupTestCase()
    {
        controlBus().unregisterService(service);
        controlBus().unregisterObject(objectPath);
    }

    void disconnectedFullRepresentation()
    {
        fixture.snapshot = QJsonDocument::fromJson(R"JSON({
            "schema":1,"connected":false,"audio_ready":false,"runtime":{
                "live":{"state":"disabled"},"equalizer":{"state":"waiting","enabled":true}}
        })JSON").object();
        QVERIFY(fixture.publish());
        QStringList warnings;
        QQmlEngine engine;
        // Match the actual Plasma engine. Without this, Kirigami BasicTheme
        // text colors can disagree with the current Plasma SVG controls.
        Plasma::setupPlasmaStyle(&engine);
        connect(&engine, &QQmlEngine::warnings, &engine, [&warnings](const QList<QQmlError> &errors) {
            for (const auto &error : errors)
                warnings.append(error.toString());
        });
        auto *translations = new KLocalizedQmlContext(&engine);
        translations->setTranslationDomain(QStringLiteral("spatial-companion-smoke"));
        engine.rootContext()->setContextObject(translations);
        QQmlEngine::setContextForObject(translations, engine.rootContext());
        QQuickWindow window;
        Plasma::Theme plasmaTheme; window.setColor(plasmaTheme.color(Plasma::Theme::BackgroundColor));
        window.resize(500, 500);
        window.show();
        QQmlComponent component(&engine, QUrl::fromLocalFile(
            QStringLiteral(SPATIAL_COMPANION_QML_DIR) + QStringLiteral("/FullRepresentation.qml")));
        QTRY_VERIFY_WITH_TIMEOUT(component.status() != QQmlComponent::Loading, 5000);
        QVERIFY2(component.isReady(), qPrintable(component.errorString()));
        std::unique_ptr<QObject> object(component.createWithInitialProperties({
            {QStringLiteral("isEditMode"), false}, {QStringLiteral("isDesktopMode"), true},
            {QStringLiteral("budsLinkRunning"), false}
        }));
        QVERIFY2(object != nullptr, qPrintable(component.errorString()));
        auto *item = qobject_cast<QQuickItem *>(object.get());
        QVERIFY(item);
        item->setParentItem(window.contentItem());
        item->setSize(QSizeF(500, 500));
        QVERIFY(!item->property("hasDevice").toBool());
        for (const auto &text : {"No compatible headphones connected", "BudsLink Spatial Companion is running",
                                "BudsLink is not running", "Headphone output: Waiting",
                                "Application audio: Disabled", "Equalizer: Waiting"}) {
            QQuickItem *label = nullptr;
            QTRY_VERIFY_WITH_TIMEOUT((label = findVisualLabel(item, QString::fromUtf8(text))) != nullptr, 5000);
            QTRY_VERIFY(label->isVisible() && label->width() > 0 && label->height() > 0);
        }
        auto *refresh = item->findChild<QQuickItem *>(QStringLiteral("spatialStatusRefresh"));
        QVERIFY(refresh);
        QTRY_VERIFY(refresh->isEnabled());
        // Change the service's property without a PropertiesChanged signal.
        // The old value must remain until the real button handler requests it.
        auto changedRuntime = fixture.snapshot["runtime"].toObject();
        changedRuntime["live"] = QJsonObject{{QStringLiteral("state"), QStringLiteral("waiting")}};
        fixture.snapshot["runtime"] = changedRuntime;
        QTest::qWait(150);
        QVERIFY(findVisualLabel(item, QStringLiteral("Application audio: Disabled")) != nullptr);
        QVERIFY(findVisualLabel(item, QStringLiteral("Application audio: Waiting")) == nullptr);
        QVERIFY(QMetaObject::invokeMethod(refresh, "clicked"));
        QTRY_VERIFY(findVisualLabel(item, QStringLiteral("Application audio: Waiting")) != nullptr);
        auto *status = item->findChild<QQuickItem *>(QStringLiteral("spatialStatus"));
        QVERIFY(status);
        // Invalid state must clear readiness rather than throwing binding errors
        // or leaving the previous running-service state on screen.
        for (const auto &invalid : {"null", "[]", "\"text\"", "1", "true", "{"}) {
            QVERIFY(QMetaObject::invokeMethod(status, "readState",
                    Q_ARG(QVariant, QVariant(QString::fromUtf8(invalid)))));
            QTRY_VERIFY(!refresh->isEnabled());
            QTRY_VERIFY(findVisualLabel(item, QStringLiteral("Waiting for BudsLink Spatial Companion status")) != nullptr);
        }
        QVERIFY(QMetaObject::invokeMethod(status, "readState", Q_ARG(QVariant, QVariant(fixture.state()))));
        QTRY_VERIFY(refresh->isEnabled());
        // Losing the actual service must clear previous readiness and disable
        // refresh; neither a fake device nor a null DevicePage is constructed.
        QVERIFY(controlBus().unregisterService(service));
        QTRY_VERIFY(!refresh->isEnabled());
        QTRY_VERIFY(findVisualLabel(item, QStringLiteral("BudsLink Spatial Companion is unavailable")) != nullptr);
        QVERIFY(controlBus().registerService(service));
        QTRY_VERIFY(refresh->isEnabled());
        object.reset();
        QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);
        QVERIFY2(warnings.isEmpty(), qPrintable(warnings.join('\n')));
    }

    void tidalClient()
    {
        fixture.signedIn = fixture.savedSession = false; fixture.requests = {};
        fixture.transportRequests.clear(); mediaFixture.calls.clear(); fixture.manyTracks = true;
        fixture.delaySearch = fixture.failSearch = fixture.delayLogin = false;
        fixture.completeLogin = true; fixture.completeOnLoginStatus = false; fixture.loginAttempt = {};
        fixture.snapshot = QJsonObject{{"schema", 1}, {"connected", false}, {"audio_ready", false}};
        QVERIFY(fixture.publish());
        QStringList warnings;
        QQmlEngine engine;
        // Match the actual Plasma engine. Without this, Kirigami BasicTheme
        // text colors can disagree with the current Plasma SVG controls.
        Plasma::setupPlasmaStyle(&engine);
        connect(&engine, &QQmlEngine::warnings, &engine, [&warnings](const QList<QQmlError> &errors) {
            for (const auto &error : errors) warnings.append(error.toString());
        });
        auto *translations = new KLocalizedQmlContext(&engine);
        translations->setTranslationDomain(QStringLiteral("spatial-companion-smoke"));
        engine.rootContext()->setContextObject(translations);
        QQmlEngine::setContextForObject(translations, engine.rootContext());
        QQuickWindow window;
        Plasma::Theme plasmaTheme; window.setColor(plasmaTheme.color(Plasma::Theme::BackgroundColor)); window.resize(380, 1000); window.show();
        QQmlComponent component(&engine, QUrl::fromLocalFile(
            QStringLiteral(SPATIAL_COMPANION_QML_DIR) + QStringLiteral("/TidalControls.qml")));
        QTRY_VERIFY_WITH_TIMEOUT(component.status() != QQmlComponent::Loading, 5000);
        QVERIFY2(component.isReady(), qPrintable(component.errorString()));
        std::unique_ptr<QObject> object(component.createWithInitialProperties({{"cardWidth", 360}}));
        QVERIFY2(object != nullptr, qPrintable(component.errorString()));
        auto *item = qobject_cast<QQuickItem *>(object.get()); QVERIFY(item);
        item->setParentItem(window.contentItem());
        auto control = [&](const char *name) { return findVisualObject(item, QString::fromUtf8(name)); };
        auto click = [&](const char *name) {
            auto *button = control(name);
            return button && button->isEnabled() && QMetaObject::invokeMethod(button, "clicked");
        };
        QTRY_VERIFY(control("tidalSignIn") && control("tidalSignIn")->isEnabled());
        QVERIFY(click("tidalSignIn"));
        QTRY_VERIFY(item->property("authorizing").toBool());
        QTRY_VERIFY(findVisualLabel(item, QStringLiteral("Code: ABCD-1234")) != nullptr);
        QVERIFY(click("tidalCancelLogin"));
        QTRY_VERIFY(!item->property("authorizing").toBool());
        QTRY_VERIFY(!item->property("busy").toBool());
        // Cancel before the start response: its exact eventual ID is cancelled
        // without reviving the code, account state or browser action.
        fixture.delayLogin = true;
        QVERIFY(click("tidalSignIn"));
        QTRY_VERIFY(fixture.delayedLogin.type() == QDBusMessage::MethodCallMessage);
        QVERIFY(click("tidalCancelLogin"));
        QVERIFY(controlBus().send(fixture.delayedLogin.createReply(QVariantList{
            ControlFixture::json({{"state", "pending"}, {"login_id", "late-exact-login"},
                {"verification_url", "https://link.tidal.com/late"}, {"user_code", "LATE"}})})));
        QTRY_COMPARE(fixture.requests.last().toObject()["action"].toString(), QStringLiteral("login_cancel"));
        QCOMPARE(fixture.requests.last().toObject()["payload"].toObject()["login_id"].toString(), QStringLiteral("late-exact-login"));
        QVERIFY(!item->property("authorizing").toBool());
        fixture.delayLogin = false;
        QVERIFY(click("tidalSignIn"));
        QTRY_VERIFY_WITH_TIMEOUT(item->property("authenticated").toBool(), 5000);
        QVERIFY(!item->property("canPlay").toBool());
        QVERIFY(control("tidalSearchInput")->setProperty("text", "fixture"));
        QVERIFY(click("tidalSearch"));
        QTRY_VERIFY(findVisualLabel(item, QStringLiteral("<Track & fixture>")) != nullptr);
        QCOMPARE(findVisualLabel(item, QStringLiteral("<Track & fixture>"))->property("textFormat").toInt(), int(Qt::PlainText));
        // Catalog browsing works disconnected, but its real Play control cannot.
        QQuickItem *play = control("tidalPlay0"); QVERIFY(play);
        QVERIFY(!play->isEnabled());
        fixture.snapshot["connected"] = true; fixture.snapshot["audio_ready"] = true;
        QVERIFY(fixture.publish());
        QTRY_VERIFY(play->isEnabled());
        QVERIFY(QMetaObject::invokeMethod(play, "clicked"));
        QTRY_VERIFY(!item->property("busy").toBool());
        QCOMPARE(fixture.requests.last().toObject()["action"].toString(), QStringLiteral("play"));
        QCOMPARE(fixture.requests.last().toObject()["payload"].toObject()["quality"].toString(), QStringLiteral("lossless"));
        QVERIFY(control("tidalQuality")->setProperty("currentIndex", 1));
        QVERIFY(QMetaObject::invokeMethod(control("tidalQuality"), "activated", Q_ARG(int, 1)));
        QVERIFY(QMetaObject::invokeMethod(play, "clicked"));
        QTRY_VERIFY(!item->property("busy").toBool());
        QCOMPARE(fixture.requests.last().toObject()["payload"].toObject()["quality"].toString(), QStringLiteral("atmos"));
        // Require the native Plasma palette on real controls, not BasicTheme
        // black text accidentally paired with dark Plasma SVG backgrounds.
        auto luminance = [](const QColor &color) {
            auto linear = [](double value) { return value <= 0.04045 ? value / 12.92 : std::pow((value + 0.055) / 1.055, 2.4); };
            return 0.2126 * linear(color.redF()) + 0.7152 * linear(color.greenF()) + 0.0722 * linear(color.blueF());
        };
        for (const char *name : {"tidalSignOut", "tidalSearchTab", "tidalLibrary", "tidalQuality", "tidalSearchInput"}) {
            auto *theme = qobject_cast<Kirigami::Platform::PlatformTheme *>(
                qmlAttachedPropertiesObject<Kirigami::Platform::PlatformTheme>(control(name)));
            QVERIFY(theme);
            QCOMPARE(QString::fromLatin1(theme->metaObject()->className()), QStringLiteral("PlasmaTheme"));
            const double foreground = luminance(theme->textColor()), background = luminance(theme->backgroundColor());
            QVERIFY2((std::max(foreground, background) + 0.05) / (std::min(foreground, background) + 0.05) >= 4.5, name);
        }
        // An accepted request can fail later; the music panel must show the
        // actual asynchronous runtime error instead of appearing to do nothing.
        fixture.snapshot["runtime"] = QJsonObject{{"loading", true}, {"queue_length", 1}};
        QVERIFY(fixture.publish());
        QTRY_COMPARE(control("tidalPlaybackState")->property("text").toString(), QStringLiteral("Opening music…"));
        QTRY_VERIFY(control("tidalStop")->isEnabled());
        QVERIFY(!play->isEnabled());
        fixture.snapshot["runtime"] = QJsonObject{{"playback_error", "Fixture Atmos stream unavailable"}};
        QVERIFY(fixture.publish());
        QTRY_COMPARE(control("tidalError")->property("text").toString(), QStringLiteral("Fixture Atmos stream unavailable"));
        QCOMPARE(item->property("quality").toString(), QStringLiteral("atmos")); // No automatic fallback.
        const int failedRequests = fixture.requests.size(); QTest::qWait(50);
        QCOMPARE(fixture.requests.size(), failedRequests);
        QJsonObject playback{{"running", true}, {"loaded", true}, {"paused", false},
            {"position", 12}, {"duration", 180}, {"renderer_ready", true}, {"source_mode", "pcm"}};
        QJsonObject playing{{"audio", playback}, {"queue_length", 3}, {"queue_index", 1},
            {"track", QJsonObject{{"title", "Playing fixture"}, {"artist", "Fixture artist"}}}};
        fixture.snapshot["runtime"] = playing; QVERIFY(fixture.publish());
        QTRY_COMPARE(control("tidalPlaybackState")->property("text").toString(), QStringLiteral("Playing"));
        QTRY_VERIFY(control("tidalPlayPause")->isEnabled());
        QVERIFY(click("tidalPlayPause")); QTRY_VERIFY(!mediaFixture.calls.isEmpty()); QCOMPARE(mediaFixture.calls.last(), QStringLiteral("PlayPause"));
        QTRY_VERIFY(!item->property("transportBusy").toBool());
        QVERIFY(click("tidalNext")); QTRY_COMPARE(mediaFixture.calls.last(), QStringLiteral("Next"));
        QTRY_VERIFY(!item->property("transportBusy").toBool());
        QVERIFY(click("tidalPrevious")); QTRY_COMPARE(mediaFixture.calls.last(), QStringLiteral("Previous"));
        QTRY_VERIFY(!item->property("transportBusy").toBool());
        playback["paused"] = true; playing["audio"] = playback;
        fixture.snapshot["runtime"] = playing; QVERIFY(fixture.publish());
        QTRY_COMPARE(control("tidalPlaybackState")->property("text").toString(), QStringLiteral("Paused"));
        playback["error"] = "Fixture decoder refused the media";
        playback["state"] = "finished"; playback["end_reason"] = "error";
        playback["loaded"] = false; playing["audio"] = playback;
        fixture.snapshot["runtime"] = playing; QVERIFY(fixture.publish());
        QTRY_COMPARE(control("tidalError")->property("text").toString(), QStringLiteral("Fixture decoder refused the media"));
        QCOMPARE(control("tidalPlaybackState")->property("text").toString(), QStringLiteral("Playback unavailable"));
        playback["loaded"] = true; playback.remove("state"); playback.remove("end_reason");
        playback.remove("error"); playing["audio"] = playback;
        fixture.snapshot["runtime"] = playing; fixture.snapshot["audio_ready"] = false;
        QVERIFY(fixture.publish()); QTRY_VERIFY(!control("tidalPlayPause")->isEnabled());
        QVERIFY(control("tidalStop")->isEnabled());
        fixture.snapshot["audio_ready"] = true; QVERIFY(fixture.publish());
        QTRY_VERIFY(control("tidalPlayPause")->isEnabled());
        // Stop is independent from a pending catalog lookup.
        fixture.delaySearch = true; QVERIFY(click("tidalSearch"));
        QTRY_VERIFY(item->property("busy").toBool());
        QVERIFY(click("tidalStop")); QTRY_VERIFY(!fixture.transportRequests.isEmpty()); QCOMPARE(fixture.transportRequests.last(), QStringLiteral("Stop"));
        QVERIFY(controlBus().send(fixture.delayedSearch.createReply(QVariantList{
            ControlFixture::json({{"tracks", QJsonArray{fixture.track}}, {"offset", 0}})})));
        QTRY_VERIFY(!item->property("busy").toBool()); fixture.delaySearch = false;
        // A bounded list retains space for transport even with twenty results.
        QVERIFY(click("tidalSearch")); QTRY_VERIFY(!item->property("busy").toBool());
        QTRY_COMPARE(control("tidalResults")->height(), 240.0);
        QTRY_VERIFY(control("tidalResults")->property("contentHeight").toDouble() > 240);
        QVERIFY(control("tidalArtwork0"));
        if (!qEnvironmentVariable("SPATIAL_QML_SCREENSHOT").isEmpty()) {
            QTest::qWait(100);
            QVERIFY(window.grabWindow().save(qEnvironmentVariable("SPATIAL_QML_SCREENSHOT")));
        }
        item->setProperty("cardWidth", 280); window.resize(300, 1000);
        QTest::qWait(100);
        QVERIFY(control("tidalResults")->width() <= 280);
        QVERIFY(control("tidalNowPlaying")->width() <= 280);
        if (!qEnvironmentVariable("SPATIAL_QML_SCREENSHOT").isEmpty())
            QVERIFY(window.grabWindow().save(qEnvironmentVariable("SPATIAL_QML_SCREENSHOT") + ".narrow.png"));
        item->setProperty("cardWidth", 360); window.resize(380, 1000);
        // Native category and browse controls request collections, then restore
        // the previous category and cached page through Back.
        QVERIFY(control("tidalCategory")->setProperty("currentIndex", 1));
        QVERIFY(QMetaObject::invokeMethod(control("tidalCategory"), "activated", Q_ARG(int, 1)));
        QTRY_VERIFY(control("tidalBrowse0") && control("tidalBrowse0")->isVisible());
        QVERIFY(click("tidalBrowse0")); QTRY_VERIFY(!item->property("busy").toBool());
        QCOMPARE(fixture.requests.last().toObject()["action"].toString(), QStringLiteral("collection"));
        QCOMPARE(fixture.requests.last().toObject()["payload"].toObject()["kind"].toString(), QStringLiteral("album"));
        QVERIFY(click("tidalPlayCollection")); QTRY_VERIFY(!item->property("busy").toBool());
        QCOMPARE(fixture.requests.last().toObject()["payload"].toObject()["reference"].toString(), QStringLiteral("tidal:album:12"));
        QVERIFY(click("tidalBack"));
        QCOMPARE(item->property("categoryIndex").toInt(), 1);
        QVERIFY(control("tidalCategory")->setProperty("currentIndex", 2));
        QVERIFY(QMetaObject::invokeMethod(control("tidalCategory"), "activated", Q_ARG(int, 2)));
        QVERIFY(click("tidalBrowse0")); QTRY_VERIFY(!item->property("busy").toBool());
        QCOMPARE(fixture.requests.last().toObject()["payload"].toObject()["kind"].toString(), QStringLiteral("artist"));
        QVERIFY(!control("tidalPlayCollection")->isVisible());
        QVERIFY(click("tidalBack"));
        QVERIFY(control("tidalCategory")->setProperty("currentIndex", 0));
        QVERIFY(QMetaObject::invokeMethod(control("tidalCategory"), "activated", Q_ARG(int, 0)));
        QVERIFY(click("tidalLibrary")); QTRY_VERIFY(!item->property("busy").toBool());
        QCOMPARE(fixture.requests.last().toObject()["action"].toString(), QStringLiteral("library"));
        QVERIFY(click("tidalNextPage")); QTRY_VERIFY(!item->property("busy").toBool());
        QCOMPARE(fixture.requests.last().toObject()["payload"].toObject()["offset"].toInt(), 20);
        QTRY_VERIFY(findVisualLabel(item, QStringLiteral("No results on this page."))->isVisible());
        QVERIFY(click("tidalBack"));
        fixture.failSearch = true;
        QVERIFY(click("tidalSearch"));
        QTRY_COMPARE(item->property("errorText").toString(), QStringLiteral("Fixture search unavailable"));
        fixture.failSearch = false; fixture.delaySearch = true;
        QVERIFY(click("tidalSearch"));
        QTRY_VERIFY(fixture.delayedSearch.type() == QDBusMessage::MethodCallMessage);
        QVERIFY(controlBus().unregisterService(service));
        QTRY_VERIFY(!item->property("serviceAvailable").toBool());
        QTRY_VERIFY(!item->property("busy").toBool());
        QVERIFY(controlBus().send(fixture.delayedSearch.createReply(
            QVariantList{ControlFixture::json({{"tracks", QJsonArray{fixture.track}}, {"offset", 0}})})));
        QTest::qWait(100);
        QCOMPARE(item->property("rows").value<QJSValue>().property("length").toInt(), 0);
        QVERIFY(controlBus().registerService(service));
        QTRY_VERIFY(item->property("authenticated").toBool());
        QVERIFY(click("tidalSignOut")); QTRY_VERIFY(!item->property("busy").toBool());
        QTRY_VERIFY(!item->property("authenticated").toBool());
        const int count = fixture.requests.size();
        item->setVisible(false); QTest::qWait(1200);
        QCOMPARE(fixture.requests.size(), count); // No permanent account or catalog polling.
        item->setVisible(true); QTRY_VERIFY(!item->property("busy").toBool());
        // Browser activation hides the popup window even if Item.visible stays
        // true. Keep the daemon attempt, stop polling, then recover its code.
        fixture.completeLogin = false;
        QVERIFY(click("tidalSignIn"));
        QTRY_VERIFY(findVisualLabel(item, QStringLiteral("Code: ABCD-1234")) != nullptr);
        QTRY_VERIFY(!item->property("busy").toBool());
        const int beforeWindowHide = fixture.requests.size();
        window.hide(); QTest::qWait(1200);
        QCOMPARE(fixture.requests.size(), beforeWindowHide);
        QCOMPARE(fixture.loginAttempt["state"].toString(), QStringLiteral("pending"));
        QVERIFY(item->isVisible()); // Window visibility is independently gated.
        window.show();
        QTRY_VERIFY(findVisualLabel(item, QStringLiteral("Code: ABCD-1234")) != nullptr);
        QTRY_VERIFY(!item->property("busy").toBool());
        QCOMPARE(fixture.requests.last().toObject()["action"].toString(), QStringLiteral("login_status"));
        // A page Loader can also destroy the item completely. Recreate the
        // actual QML card and recover the same pending attempt without start.
        object.reset(); QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);
        const int afterDestruction = fixture.requests.size(); QTest::qWait(1200);
        QCOMPARE(fixture.requests.size(), afterDestruction);
        object.reset(component.createWithInitialProperties({{"cardWidth", 360}}));
        QVERIFY2(object != nullptr, qPrintable(component.errorString()));
        item = qobject_cast<QQuickItem *>(object.get()); QVERIFY(item);
        item->setParentItem(window.contentItem());
        QTRY_VERIFY(findVisualLabel(item, QStringLiteral("Code: ABCD-1234")) != nullptr);
        QCOMPARE(item->property("login").value<QJSValue>().property("login_id").toString(), QStringLiteral("fixture-login"));
        QVERIFY(click("tidalCancelLogin")); QTRY_VERIFY(!item->property("busy").toBool());
        QCOMPARE(fixture.requests.last().toObject()["payload"].toObject()["login_id"].toString(), QStringLiteral("fixture-login"));
        // Reopen while the original login_start worker is still blocked. The
        // new card observes starting, then obtains instructions by local status.
        fixture.delayLogin = true; fixture.delayedLogin = QDBusMessage();
        QVERIFY(click("tidalSignIn"));
        QTRY_VERIFY(fixture.delayedLogin.type() == QDBusMessage::MethodCallMessage);
        object.reset(); QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);
        const int destroyedStarting = fixture.requests.size();
        object.reset(component.createWithInitialProperties({{"cardWidth", 360}}));
        QVERIFY2(object != nullptr, qPrintable(component.errorString()));
        item = qobject_cast<QQuickItem *>(object.get()); QVERIFY(item);
        item->setParentItem(window.contentItem());
        QTRY_COMPARE(item->property("login").value<QJSValue>().property("state").toString(), QStringLiteral("starting"));
        fixture.loginAttempt = {{"state", "pending"}, {"login_id", "fixture-login"}, {"interval", 1},
            {"verification_url", "https://link.tidal.com/fixture"}, {"user_code", "RESTORED"}};
        QVERIFY(controlBus().send(fixture.delayedLogin.createReply(QVariantList{
            ControlFixture::json(fixture.loginAttempt)})));
        QTRY_VERIFY_WITH_TIMEOUT(findVisualLabel(item, QStringLiteral("Code: RESTORED")) != nullptr, 3000);
        for (int i = destroyedStarting; i < fixture.requests.size(); ++i) {
            const auto action = fixture.requests[i].toObject()["action"].toString();
            QVERIFY(action != QStringLiteral("login_cancel") && action != QStringLiteral("login_start"));
        }
        fixture.completeLogin = true;
        QTRY_VERIFY_WITH_TIMEOUT(item->property("authenticated").toBool(), 5000);
        QVERIFY(click("tidalSignOut")); QTRY_VERIFY(!item->property("busy").toBool());
        // A detached login worker can save credentials between the initial
        // account read and recovery. Recheck local state before one refresh.
        item->setVisible(false);
        fixture.completeOnLoginStatus = true;
        item->setVisible(true);
        QTRY_VERIFY_WITH_TIMEOUT(item->property("authenticated").toBool(), 3000);
        QCOMPARE(fixture.requests.last().toObject()["action"].toString(), QStringLiteral("status"));
        QVERIFY(fixture.requests.last().toObject()["payload"].toObject()["refresh"].toBool());
        // Conversely, a terminal attempt retained after external logout must
        // not perform a network refresh or resurrect the signed-out account.
        item->setVisible(false); fixture.signedIn = fixture.savedSession = false;
        const int beforeExternalLogout = fixture.requests.size();
        item->setVisible(true);
        QTRY_VERIFY(fixture.requests.size() >= beforeExternalLogout + 3);
        QTRY_VERIFY(!item->property("busy").toBool());
        QVERIFY(!item->property("authenticated").toBool());
        for (int i = beforeExternalLogout; i < fixture.requests.size(); ++i)
            QVERIFY(!fixture.requests[i].toObject()["payload"].toObject()["refresh"].toBool());
        object.reset(); QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);
        fixture.delayLogin = false; fixture.manyTracks = false;
        QVERIFY2(warnings.isEmpty(), qPrintable(warnings.join('\n')));
    }
};

int main(int argc, char **argv)
{
    QGuiApplication app(argc, argv);
    NativeQmlSmoke test;
    return QTest::qExec(&test, argc, argv);
}

#include "qml-smoke.moc"
