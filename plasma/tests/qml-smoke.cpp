// SPDX-License-Identifier: GPL-3.0-or-later
// Native UI smoke test. This private D-Bus fixture is not a hardware simulator.
#include <KLocalizedQmlContext>
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
#include <memory>

static const QString service = QStringLiteral("org.spatiald.Control1");
static const QString objectPath = QStringLiteral("/org/spatiald/Control1");
static const QString companionPath = QStringLiteral("/fixture/XM5");

class ControlFixture : public QDBusAbstractAdaptor
{
    Q_OBJECT
    Q_CLASSINFO("D-Bus Interface", "org.spatiald.Control1")
    Q_PROPERTY(QString State READ state)
public:
    explicit ControlFixture(QObject *parent) : QDBusAbstractAdaptor(parent) {}
    QJsonObject snapshot;
    QString state() const
    {
        return QString::fromUtf8(QJsonDocument(snapshot).toJson(QJsonDocument::Compact));
    }
    bool publish()
    {
        auto signal = QDBusMessage::createSignal(objectPath,
            QStringLiteral("org.freedesktop.DBus.Properties"), QStringLiteral("PropertiesChanged"));
        signal << service << QVariantMap{{QStringLiteral("State"), state()}} << QStringList{};
        return QDBusConnection::sessionBus().send(signal);
    }
};

class NativeQmlSmoke : public QObject
{
    Q_OBJECT
    QObject fixtureObject;
    ControlFixture fixture{&fixtureObject};

private slots:
    void initTestCase()
    {
        // The package check starts a new dbus-run-session, never the user's desktop bus.
        QVERIFY2(qEnvironmentVariable("SPATIAL_QML_PRIVATE_BUS") == QStringLiteral("1"),
                 "Run this test using the documented isolated dbus-run-session command.");
        QVERIFY(QDBusConnection::sessionBus().isConnected());
        QVERIFY(QDBusConnection::sessionBus().registerObject(
            objectPath, &fixtureObject, QDBusConnection::ExportAdaptors));
        QVERIFY(QDBusConnection::sessionBus().registerService(service));
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
        connect(&engine, &QQmlEngine::warnings, &engine, [&warnings](const QList<QQmlError> &errors) {
            for (const auto &error : errors)
                warnings.append(error.toString());
        });
        auto *translations = new KLocalizedQmlContext(&engine);
        translations->setTranslationDomain(QStringLiteral("spatial-companion-smoke"));
        engine.rootContext()->setContextObject(translations);
        QQmlEngine::setContextForObject(translations, engine.rootContext());

        QQuickWindow window;
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
            bool foundPlainName = false;
            for (auto *child : item->findChildren<QObject *>()) {
                if (child->property("text").toString() == QStringLiteral("<tracker & fixture>")) {
                    QCOMPARE(child->property("textFormat").toInt(), int(Qt::PlainText));
                    foundPlainName = true;
                }
            }
            QVERIFY(foundPlainName);
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
        QDBusConnection::sessionBus().unregisterService(service);
        QDBusConnection::sessionBus().unregisterObject(objectPath);
    }
};

int main(int argc, char **argv)
{
    QGuiApplication app(argc, argv);
    NativeQmlSmoke test;
    return QTest::qExec(&test, argc, argv);
}

#include "qml-smoke.moc"
