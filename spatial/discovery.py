"""Read-only, independently reconnecting BlueZ and BudsLink discovery.

Subscriptions are installed before the initial snapshot. A revision guard prevents
an old reply from resurrecting state after a service ownership change. No method
here pairs devices, starts discovery, activates BudsLink, or changes audio policy.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from dbus_next import BusType, Message, MessageType, Variant
from dbus_next.aio import MessageBus

from .pipewire import address
from .retry import Backoff

LOG = logging.getLogger(__name__)
BLUEZ = "org.bluez"
DEVICE = "org.bluez.Device1"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
BUDSLINK = "io.github.maniacx.BudsLink"
BUDSLINK_PATH = "/io/github/maniacx/BudsLink"
BUDSLINK_MANAGER = BUDSLINK + ".DeviceManager"
BLUEZ_PATH = re.compile(r"^/org/bluez/(hci[0-9]+)/(dev_(?:[0-9A-Fa-f]{2}_){5}[0-9A-Fa-f]{2})$")


def unwrap(value):
    if isinstance(value, Variant):
        return unwrap(value.value)
    if isinstance(value, dict):
        return {key: unwrap(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [unwrap(item) for item in value]
    return value


@dataclass(frozen=True)
class BluetoothDevice:
    address: str
    path: str
    name: str
    alias: str
    connected: bool
    paired: bool
    services_resolved: bool


@dataclass(frozen=True)
class BluetoothSelection:
    device: BluetoothDevice | None
    candidates: tuple[BluetoothDevice, ...]
    reason: str


def select_bluetooth(objects, selected_address=None):
    """Select the configured identity or an unambiguous actual XM5 model.

    Alias is display-only. An unrelated headset renamed WF-1000XM5 is never an
    automatic candidate. A single connected XM5 takes priority over paired ones.
    """
    wanted = address(selected_address) if selected_address else None
    found = []
    for path, interfaces in unwrap(objects).items():
        properties = interfaces.get(DEVICE, {})
        if not properties or not BLUEZ_PATH.fullmatch(path):
            continue
        try:
            device_address = address(properties.get("Address", ""))
        except ValueError:
            continue
        name = properties.get("Name", "")
        if wanted and device_address != wanted:
            continue
        if not wanted and name != "WF-1000XM5":
            continue
        found.append(BluetoothDevice(
            device_address, path, name, properties.get("Alias", name),
            properties.get("Connected") is True, properties.get("Paired") is True,
            properties.get("ServicesResolved") is True))
    found.sort(key=lambda item: item.path)
    connected = [item for item in found if item.connected]
    candidates = connected or [item for item in found if item.paired] or (found if wanted else [])
    if len(candidates) == 1:
        return BluetoothSelection(candidates[0], tuple(found), "connected" if connected else "waiting")
    return BluetoothSelection(None, tuple(found), "ambiguous" if len(candidates) > 1 else "not-found")


def companion_device_path(paths, device):
    """Resolve a manager-reported object using its upstream adapter/device mapping.

    The expected path is compared to ListDevices results. Constructing a path is
    not evidence that BudsLink has created that device or is ready.
    """
    if device is None or not device.connected:
        return ""
    match = BLUEZ_PATH.fullmatch(device.path)
    if not match:
        return ""
    expected = f"{BUDSLINK_PATH}/Devices/{match.group(1)}/{match.group(2)}"
    return expected if expected in paths else ""


async def call(bus, destination, path, interface, member, signature="", body=None, timeout=5):
    reply = await asyncio.wait_for(bus.call(Message(
        destination=destination, path=path, interface=interface,
        member=member, signature=signature, body=body or [])), timeout)
    if reply is None or reply.message_type == MessageType.ERROR:
        detail = reply.error_name if reply is not None else "NoReply"
        raise RuntimeError(f"{interface}.{member}: {detail}")
    return reply.body


async def _wait_event(wake, stop, disconnected, delay):
    ready = asyncio.create_task(wake.wait())
    stopped = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait((ready, stopped, disconnected), timeout=delay,
                           return_when=asyncio.FIRST_COMPLETED)
        if disconnected.done():
            # Propagate the concrete transport error, if one was supplied.
            disconnected.result()
            raise ConnectionError("D-Bus disconnected")
    finally:
        ready.cancel()
        stopped.cancel()
        await asyncio.gather(ready, stopped, return_exceptions=True)


async def watch_service(service, bus_type, rules, relevant, snapshot, changed,
                        unavailable, stop, *, bus_factory=None):
    """Reconnect one read-only provider without affecting another provider.

    Rules scope signal delivery; sender checks scope processing. Refresh is
    event-driven, with a 30-second reconciliation check as a recovery measure.
    Each failed operation has a bounded timeout and capped retry backoff.
    """
    factory = bus_factory or (lambda: MessageBus(bus_type=bus_type))
    backoff = Backoff()
    while not stop.is_set():
        bus = None
        disconnected = None
        handler = None
        try:
            bus = await asyncio.wait_for(factory().connect(), 5)
            wake = asyncio.Event()
            revision = 0
            owner = None

            def handler(message):
                nonlocal revision, owner
                if message.message_type != MessageType.SIGNAL:
                    return
                if (message.sender == "org.freedesktop.DBus" and
                        message.interface == "org.freedesktop.DBus" and
                        message.member == "NameOwnerChanged" and
                        len(message.body) == 3 and message.body[0] == service):
                    revision += 1
                    owner = None
                    unavailable("service-restarting" if message.body[2] else "service-unavailable")
                    wake.set()
                elif owner is not None and message.sender == owner and relevant(message):
                    # A disconnect/removal arriving during the snapshot makes
                    # that reply obsolete just as a service-owner change does.
                    revision += 1
                    wake.set()

            bus.add_message_handler(handler)
            matches = [f"type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='{service}'"]
            matches.extend(f"type='signal',sender='{service}',{rule}" for rule in rules)
            for match in matches:
                await call(bus, "org.freedesktop.DBus", "/org/freedesktop/DBus",
                           "org.freedesktop.DBus", "AddMatch", "s", [match])
            disconnected = asyncio.create_task(bus.wait_for_disconnect())
            while not stop.is_set():
                wake.clear()
                before = revision
                try:
                    if owner is None:
                        result = await call(bus, "org.freedesktop.DBus", "/org/freedesktop/DBus",
                                            "org.freedesktop.DBus", "GetNameOwner", "s", [service])
                        if before != revision:
                            continue
                        owner = result[0]
                    result = await snapshot(bus, owner)
                    if before != revision:
                        continue
                    changed(result)
                    backoff.reset()
                    delay = 30.0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    unavailable(str(exc))
                    owner = None
                    delay = backoff.next_delay()
                await _wait_event(wake, stop, disconnected, delay)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            unavailable(str(exc))
        finally:
            if bus is not None:
                if handler is not None:
                    bus.remove_message_handler(handler)
                bus.disconnect()
            if disconnected is not None:
                disconnected.cancel()
                await asyncio.gather(disconnected, return_exceptions=True)
        if not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), backoff.next_delay())
            except TimeoutError:
                pass


def _bluez_relevant(message):
    if message.interface == OBJECT_MANAGER:
        return message.member in ("InterfacesAdded", "InterfacesRemoved")
    if message.interface == PROPERTIES and message.member == "PropertiesChanged":
        if len(message.body) != 3 or message.body[0] != DEVICE:
            return False
        properties = set(message.body[1]) | set(message.body[2])
        return bool(properties & {"Address", "Name", "Alias", "Connected", "Paired", "ServicesResolved"})
    return False


async def watch_bluez(changed, unavailable, stop, *, bus_factory=None):
    async def snapshot(bus, owner):
        return (await call(bus, owner, "/", OBJECT_MANAGER, "GetManagedObjects"))[0]
    await watch_service(BLUEZ, BusType.SYSTEM,
                        [f"interface='{OBJECT_MANAGER}'", f"interface='{PROPERTIES}'"],
                        _bluez_relevant, snapshot, changed, unavailable, stop,
                        bus_factory=bus_factory)


async def watch_budslink(changed, unavailable, stop, *, bus_factory=None):
    async def snapshot(bus, owner):
        return (await call(bus, owner, BUDSLINK_PATH, BUDSLINK_MANAGER, "ListDevices"))[0]
    await watch_service(BUDSLINK, BusType.SESSION,
                        [f"interface='{BUDSLINK_MANAGER}',path='{BUDSLINK_PATH}'"],
                        lambda msg: msg.interface == BUDSLINK_MANAGER and
                        msg.member in ("DeviceAdded", "DeviceRemoved"),
                        snapshot, changed, unavailable, stop, bus_factory=bus_factory)
