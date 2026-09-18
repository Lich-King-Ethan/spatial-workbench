"""Native session D-Bus control and independent desktop discovery providers."""
import asyncio
import copy
import inspect
import json
import signal
import time

from dbus_next import DBusError, RequestNameReply, NameFlag
from dbus_next.aio import MessageBus
from dbus_next.constants import PropertyAccess
from dbus_next.service import ServiceInterface, dbus_property, method

from . import discovery, pipewire
from .retry import supervise
from .errors import public_message

BUS = "org.spatiald.Control1"
PATH = "/org/spatiald/Control1"


class Control(ServiceInterface):
    def __init__(self, engine, preference_path=None, *, on_change=None,
                 extra_state=None, on_play=None, on_stop=None,
                 on_live_start=None, on_live_stop=None, on_stop_if_process=None,
                 on_play_if_idle=None, on_begin_test_playback=None, on_stop_if_request=None):
        super().__init__(BUS)
        self.engine = engine
        self.preference_path = preference_path
        self.on_change = on_change
        self.extra_state = extra_state
        self.on_play = on_play
        self.on_stop = on_stop
        self.on_live_start = on_live_start
        self.on_live_stop = on_live_stop
        self.on_stop_if_process = on_stop_if_process
        self.on_play_if_idle = on_play_if_idle
        self.on_begin_test_playback = on_begin_test_playback
        self.on_stop_if_request = on_stop_if_request
        self.serialized = ""
        self.publish()

    def publish(self):
        snapshot = self.engine.snapshot()
        # UI gets capability state, not high-rate sensor traffic.
        snapshot.pop("orientation", None)
        if self.extra_state:
            snapshot["runtime"] = self.extra_state()
        current = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if current != self.serialized:
            self.serialized = current
            self.emit_properties_changed({"State": current})

    def changed(self):
        if self.on_change:
            self.on_change()
        self.publish()

    @dbus_property(access=PropertyAccess.READ)
    def State(self) -> 's':
        return self.serialized

    @method()
    def SetTrackerEnabled(self, tracker_id: 's', enabled: 'b'):
        old = copy.deepcopy(self.engine.preferences)
        try:
            self.engine.set_enabled(tracker_id, enabled)
            if self.preference_path is not None:
                self.engine.preferences.save(self.preference_path)
        except (ValueError, OSError) as exc:
            self.engine.preferences = old
            raise DBusError(BUS + ".Rejected", str(exc))
        self.changed()

    @method()
    def Recenter(self):
        try:
            self.engine.recenter_active()
        except ValueError as exc:
            raise DBusError(BUS + ".Unavailable", str(exc))
        self.changed()

    @method()
    async def Play(self, media: 's'):
        if not media or len(media) > 16384 or "\x00" in media:
            raise DBusError(BUS + ".Rejected", "A valid media path or URL is required")
        await self._command(self.on_play, media)

    @method()
    async def Stop(self):
        await self._command(self.on_stop)

    @method()
    async def BeginTestPlayback(self, media: 's', replace: 'b') -> 't':
        if not media or len(media) > 16384 or "\x00" in media or type(replace) is not bool:
            raise DBusError(BUS + ".Rejected", "A valid media path and replacement policy are required")
        result = await self._command(self.on_begin_test_playback, media, replace)
        if type(result) is not int or not 0 <= result <= 18446744073709551615:
            raise DBusError(BUS + ".Rejected", "The diagnostic playback request returned an invalid token")
        return result

    @method()
    async def StopIfRequest(self, token: 't') -> 'b':
        if type(token) is not int or not 0 < token <= 18446744073709551615:
            return False
        if self.on_stop_if_request is None:
            return False
        result = await self._command(self.on_stop_if_request, token)
        if type(result) is not bool:
            raise DBusError(BUS + ".Rejected", "The playback request ownership check returned an invalid result")
        return result

    @method()
    async def PlayIfIdle(self, media: 's') -> 'b':
        if not media or len(media) > 16384 or "\x00" in media:
            raise DBusError(BUS + ".Rejected", "A valid media path or URL is required")
        if self.on_play_if_idle is None:
            return False
        result = await self._command(self.on_play_if_idle, media)
        if type(result) is not bool:
            raise DBusError(BUS + ".Rejected", "The playback availability check returned an invalid result")
        return result

    @method()
    async def StopIfProcess(self, expected_pid: 'u') -> 'b':
        if type(expected_pid) is not int or not 0 < expected_pid <= 4294967295:
            return False
        if self.on_stop_if_process is None:
            return False
        result = await self._command(self.on_stop_if_process, expected_pid)
        if type(result) is not bool:
            raise DBusError(BUS + ".Rejected", "The playback ownership check returned an invalid result")
        return result

    @method()
    async def StartLive(self, stream_serial: 's'):
        if (not stream_serial or not stream_serial.isascii() or
                not stream_serial.isdecimal() or len(stream_serial) > 20 or
                not 0 < int(stream_serial) <= 18446744073709551615):
            raise DBusError(BUS + ".Rejected", "Select an available application audio stream")
        if self.on_live_start is None:
            raise DBusError(BUS + ".Unavailable", "Application audio is not configured in this service")
        await self._command(self.on_live_start, stream_serial)

    @method()
    async def StopLive(self):
        if self.on_live_stop is None:
            raise DBusError(BUS + ".Unavailable", "Application audio is not configured in this service")
        await self._command(self.on_live_stop)

    async def _command(self, callback, *args):
        if callback is None:
            raise DBusError(BUS + ".Unavailable", "Playback is not configured in this service")
        try:
            result = callback(*args)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            # Third-party exceptions can contain signed source URLs. Only
            # explicitly public provider errors may expose their message.
            raise DBusError(BUS + ".Rejected", public_message(exc)) from None
        self.changed()
        return result


async def export(engine, preferences=None, **callbacks):
    bus = await MessageBus().connect()
    try:
        reply = await bus.request_name(BUS, NameFlag.DO_NOT_QUEUE)
        if reply != RequestNameReply.PRIMARY_OWNER:
            raise RuntimeError("spatiald control service is already running")
        control = Control(engine, preferences, **callbacks)
        bus.export(PATH, control)
        return bus, control
    except BaseException:
        bus.disconnect()
        raise


class DesktopRuntime:
    """Reconcile connection, audio, and controls without coupling their lifetimes.

    BlueZ owns the headphone session. PipeWire may arrive later or restart without
    pretending Bluetooth disconnected. BudsLink controls attach whenever its
    manager reports the exact matching device. All discovery is read-only.
    """
    def __init__(self, engine, selected_address=None, preferences=None, *,
                 companion_path="", on_change=None, extra_state=None,
                 on_play=None, on_stop=None, on_live_start=None, on_live_stop=None,
                 on_stop_if_process=None, on_play_if_idle=None,
                 on_begin_test_playback=None, on_stop_if_request=None, clock=time.monotonic):
        self.engine = engine
        self.selected_address = pipewire.address(selected_address) if selected_address else None
        self.preferences = preferences
        self.requested_companion_path = companion_path
        self.on_change = on_change
        self.extra_state = extra_state
        self.on_play = on_play
        self.on_stop = on_stop
        self.on_live_start = on_live_start
        self.on_live_stop = on_live_stop
        self.on_stop_if_process = on_stop_if_process
        self.on_play_if_idle = on_play_if_idle
        self.on_begin_test_playback = on_begin_test_playback
        self.on_stop_if_request = on_stop_if_request
        self.clock = clock
        self.control = None
        self.bluetooth = None
        self.companion_paths = []
        self.pipewire_objects = []
        self.pipewire_probe_session = None
        self.discovery_state = {"bluez": "waiting", "pipewire": "waiting", "budslink": "waiting",
                                "selection": "waiting", "candidates": []}

    def state(self):
        extra = dict(self.extra_state() or {}) if self.extra_state else {}
        extra["discovery"] = dict(self.discovery_state)
        return extra

    def publish(self):
        self.engine.advance(self.clock())
        self.engine.select()
        if self.on_change:
            self.on_change()
        if self.control:
            self.control.publish()

    def _controls(self):
        path = discovery.companion_device_path(self.companion_paths, self.bluetooth)
        if self.requested_companion_path and path != self.requested_companion_path:
            path = ""
        self.engine.companion_path = path
        if self.engine.connected:
            self.engine.capability(self.engine.epoch, "controls", bool(path))

    def bluez_ready(self, objects):
        selected = discovery.select_bluetooth(objects, self.selected_address)
        self.discovery_state["bluez"] = "ready"
        self.discovery_state["selection"] = selected.reason
        self.discovery_state["candidates"] = [
            {"address": device.address, "name": device.name, "alias": device.alias,
             "connected": device.connected} for device in selected.candidates]
        previous_path = self.bluetooth.path if self.bluetooth else None
        self.bluetooth = selected.device
        if self.bluetooth is not None and self.bluetooth.connected:
            if (self.engine.connected and self.engine.device == self.bluetooth.address and
                    previous_path != self.bluetooth.path):
                self.engine.disconnect()
            is_new = not self.engine.connected or self.engine.device != self.bluetooth.address
            self.engine.connect(self.bluetooth.address)
            if is_new:
                # A previous pw-dump response cannot establish a new session's sink.
                self.pipewire_objects = []
                self.discovery_state["pipewire"] = "waiting"
        elif self.engine.connected:
            self.engine.disconnect()
            self.pipewire_objects = []
        self._controls()
        self.publish()

    def bluez_unavailable(self, reason):
        self.discovery_state["bluez"] = reason
        self.discovery_state["selection"] = "waiting"
        self.discovery_state["candidates"] = []
        self.bluetooth = None
        self.pipewire_objects = []
        if self.engine.connected:
            self.engine.disconnect()
        self._controls()
        self.publish()

    def budslink_ready(self, paths):
        self.companion_paths = list(paths)
        self.discovery_state["budslink"] = "ready"
        self._controls()
        self.publish()

    def budslink_unavailable(self, reason):
        self.companion_paths = []
        self.discovery_state["budslink"] = reason
        self._controls()
        self.publish()

    async def capture_pipewire(self):
        self.pipewire_probe_session = (self.engine.epoch, self.engine.device)
        return self.pipewire_probe_session, await pipewire.capture()

    def accept_pipewire(self, result):
        session, objects = result
        if session == (self.engine.epoch, self.engine.device):
            self.pipewire_ready(objects)

    def fail_pipewire(self, reason):
        if self.pipewire_probe_session == (self.engine.epoch, self.engine.device):
            self.pipewire_unavailable(reason)

    def pipewire_ready(self, objects):
        self.pipewire_objects = objects
        self.discovery_state["pipewire"] = "ready"
        if self.engine.connected:
            sinks = pipewire.parse_sinks(objects, self.engine.device)
            self.engine.observe_sink(self.engine.epoch, sinks[0] if sinks else None)
        self.publish()

    def pipewire_unavailable(self, reason):
        self.pipewire_objects = []
        self.discovery_state["pipewire"] = reason
        if self.engine.connected:
            self.engine.observe_sink(self.engine.epoch, None)
        self.publish()

    async def run(self, stop):
        bus, self.control = await export(
            self.engine, self.preferences, on_change=self.publish,
            extra_state=self.state, on_play=self.on_play, on_stop=self.on_stop,
            on_live_start=self.on_live_start, on_live_stop=self.on_live_stop,
            on_stop_if_process=self.on_stop_if_process, on_play_if_idle=self.on_play_if_idle,
            on_begin_test_playback=self.on_begin_test_playback, on_stop_if_request=self.on_stop_if_request)

        async def ticker():
            while not stop.is_set():
                self.publish()
                try:
                    await asyncio.wait_for(stop.wait(), 0.1)
                except TimeoutError:
                    pass

        tasks = [
            asyncio.create_task(discovery.watch_bluez(self.bluez_ready, self.bluez_unavailable, stop), name="bluez-discovery"),
            asyncio.create_task(discovery.watch_budslink(self.budslink_ready, self.budslink_unavailable, stop), name="budslink-discovery"),
            asyncio.create_task(supervise(self.capture_pipewire, self.accept_pipewire, self.fail_pipewire, stop), name="pipewire-discovery"),
            asyncio.create_task(ticker(), name="desktop-freshness")]
        disconnected = asyncio.create_task(bus.wait_for_disconnect())
        stopped = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait([disconnected, stopped, *tasks], return_when=asyncio.FIRST_COMPLETED)
            for task in [disconnected, *tasks]:
                if task.done() and not task.cancelled():
                    task.result()
            if disconnected.done() and not stop.is_set():
                raise ConnectionError("Session D-Bus disconnected")
        finally:
            for task in [*tasks, disconnected, stopped]:
                task.cancel()
            await asyncio.gather(*tasks, disconnected, stopped, return_exceptions=True)
            bus.disconnect()
            self.control = None


async def observe(engine, selected_address=None, companion_path="", preferences=None):
    """Compatibility entry point for read-only desktop observation."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)
    for sig in signals:
        loop.add_signal_handler(sig, stop.set)
    try:
        await DesktopRuntime(engine, selected_address, preferences,
                             companion_path=companion_path).run(stop)
    finally:
        for sig in signals:
            loop.remove_signal_handler(sig)
