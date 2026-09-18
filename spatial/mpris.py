"""Native desktop media controls for the owned mpv playback session."""
import asyncio
import math

from dbus_next import DBusError, Variant, NameFlag, RequestNameReply
from dbus_next.aio import MessageBus
from dbus_next.constants import PropertyAccess
from dbus_next.service import ServiceInterface, dbus_property, method
from .errors import public_message

SERVICE = "org.mpris.MediaPlayer2.spatiald"
PATH = "/org/mpris/MediaPlayer2"


class Application(ServiceInterface):
    def __init__(self, runtime):
        super().__init__("org.mpris.MediaPlayer2")
        self.runtime = runtime

    @method()
    def Raise(self):
        pass

    @method()
    async def Quit(self):
        await self.runtime.stop_playback()

    @dbus_property(access=PropertyAccess.READ)
    def CanQuit(self) -> 'b':
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanRaise(self) -> 'b':
        return False

    @dbus_property(access=PropertyAccess.READ)
    def HasTrackList(self) -> 'b':
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> 's':
        return "Spatial Audio"

    @dbus_property(access=PropertyAccess.READ)
    def SupportedUriSchemes(self) -> 'as':
        return ['file', 'http', 'https', 'tidal']

    @dbus_property(access=PropertyAccess.READ)
    def SupportedMimeTypes(self) -> 'as':
        return ['audio/flac', 'audio/x-matroska', 'video/x-matroska', 'audio/eac3',
                'audio/ac3', 'audio/wav', 'application/dash+xml']


class Player(ServiceInterface):
    def __init__(self, runtime):
        super().__init__("org.mpris.MediaPlayer2.Player")
        self.runtime = runtime
        self.last = None

    async def act(self, callback, *args):
        try:
            await callback(*args)
        except (OSError, ValueError, RuntimeError) as exc:
            raise DBusError("org.mpris.MediaPlayer2.Error.Failed", public_message(exc))

    @method()
    async def Play(self):
        await self.act(self.runtime.resume)

    @method()
    async def Pause(self):
        await self.act(self.runtime.pause, True)

    @method()
    async def PlayPause(self):
        if self.PlaybackStatus == "Stopped":
            await self.act(self.runtime.resume)
        else:
            await self.act(self.runtime.pause, not self.runtime.audio.status().get("paused", False))

    @method()
    async def Stop(self):
        await self.act(self.runtime.stop_playback)

    @method()
    async def Next(self):
        await self.act(self.runtime.next)

    @method()
    async def Previous(self):
        await self.act(self.runtime.previous)

    @method()
    async def OpenUri(self, uri: 's'):
        await self.act(self.runtime.request_play, uri)

    @method()
    async def Seek(self, offset: 'x'):
        await self.act(self.runtime.seek, offset / 1_000_000)

    @method()
    async def SetPosition(self, track_id: 'o', position: 'x'):
        if track_id == self.runtime.track_path and position >= 0:
            await self.act(self.runtime.seek, position / 1_000_000, True)

    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> 's':
        state = self.runtime.audio.status()
        if state.get("end_reason") or not state.get("running"):
            return "Stopped"
        return "Paused" if state.get("running") and state.get("paused") else (
            "Playing" if state.get("running") else "Stopped")

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> 'a{sv}':
        data = self.runtime.metadata
        if not data:
            return {}
        result = {"mpris:trackid": Variant('o', self.runtime.track_path),
                  "xesam:title": Variant('s', str(data.get("title", "Audio")))}
        artists = data.get("artists") or data.get("artist")
        if isinstance(artists, str):
            artists = [artists]
        if artists and isinstance(artists, list):
            result["xesam:artist"] = Variant('as', [str(a) for a in artists])
        duration = self.runtime.audio.status().get("duration")
        if type(duration) in (int, float) and math.isfinite(duration) and duration > 0:
            result["mpris:length"] = Variant('x', int(duration * 1_000_000))
        return result

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> 'x':
        value = self.runtime.audio.status().get("position", 0)
        if type(value) not in (int, float) or not math.isfinite(value):
            return 0
        return int(max(0, value or 0) * 1_000_000)

    @dbus_property(access=PropertyAccess.READ)
    def Rate(self) -> 'd':
        return 1.0

    @dbus_property()
    def Volume(self) -> 'd':
        value = self.runtime.audio.status().get("volume")
        return float(value / 100) if type(value) in (int, float) else 1.0

    @Volume.setter
    def Volume(self, value: 'd'):
        if not math.isfinite(value) or value < 0 or value > 1:
            raise DBusError("org.mpris.MediaPlayer2.Error.InvalidValue", "volume must be between 0 and 1")
        task = asyncio.create_task(self.runtime.set_volume(value))
        def finished(result):
            if result.cancelled():
                return
            error = result.exception()
            if error:
                self.runtime.provider_status("media_controls", "unavailable", type(error).__name__)
        task.add_done_callback(finished)

    @dbus_property(access=PropertyAccess.READ)
    def MinimumRate(self) -> 'd':
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def MaximumRate(self) -> 'd':
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> 'b':
        return self.runtime.queue_index + 1 < len(self.runtime.queue)

    @dbus_property(access=PropertyAccess.READ)
    def CanGoPrevious(self) -> 'b':
        return self.runtime.queue_index > 0

    @dbus_property(access=PropertyAccess.READ)
    def CanPlay(self) -> 'b':
        return bool(self.runtime.queue)

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> 'b':
        return self.PlaybackStatus != "Stopped"

    @dbus_property(access=PropertyAccess.READ)
    def CanSeek(self) -> 'b':
        return self.PlaybackStatus != "Stopped" and bool(self.runtime.audio.status().get("seekable", False))

    @dbus_property(access=PropertyAccess.READ)
    def CanControl(self) -> 'b':
        return True

    def publish(self):
        # Position is queried rather than emitting 100 updates a second.
        current = {name: getattr(self, name) for name in
                   ("PlaybackStatus", "Metadata", "Volume", "CanGoNext", "CanGoPrevious", "CanPlay", "CanPause", "CanSeek")}
        if current != self.last:
            self.emit_properties_changed(current)
            self.last = current


async def serve(runtime, stop):
    bus = await MessageBus().connect()
    reply = await bus.request_name(SERVICE, NameFlag.DO_NOT_QUEUE)
    if reply != RequestNameReply.PRIMARY_OWNER:
        bus.disconnect()
        raise RuntimeError("another Spatial Audio media service is already running")
    player = Player(runtime)
    bus.export(PATH, Application(runtime))
    bus.export(PATH, player)
    try:
        while not stop.is_set() and bus.connected:
            player.publish()
            try:
                await asyncio.wait_for(stop.wait(), 0.25)
            except TimeoutError:
                pass
    finally:
        bus.disconnect()
