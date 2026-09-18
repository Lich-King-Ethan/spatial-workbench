import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    from dbus_next import Message, MessageType, Variant
    from spatial import discovery
    from spatial.desktop import DesktopRuntime
    HAVE_DBUS = True
except ImportError:
    HAVE_DBUS = False

from spatial.core import Engine, Sink

ADDRESS = "AA:BB:CC:DD:EE:FF"
BLUEZ_PATH = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
COMPANION_PATH = "/io/github/maniacx/BudsLink/Devices/hci0/dev_AA_BB_CC_DD_EE_FF"


def objects(*, connected=True, alias="My earbuds", name="WF-1000XM5", adapter="hci0"):
    path = BLUEZ_PATH.replace("hci0", adapter)
    return {path: {"org.bluez.Device1": {
        "Address": ADDRESS, "Name": name, "Alias": alias, "Paired": True,
        "Connected": connected, "ServicesResolved": False}}}


@unittest.skipUnless(HAVE_DBUS, "requires dbus-next")
class Selection(unittest.TestCase):
    def test_model_selection_uses_name_and_unwraps_variants(self):
        data = objects()
        data[BLUEZ_PATH][discovery.DEVICE]["Address"] = Variant("s", ADDRESS)
        selected = discovery.select_bluetooth(data)
        self.assertEqual(selected.device.alias, "My earbuds")
        self.assertEqual(selected.device.address, ADDRESS)
        self.assertEqual(selected.reason, "connected")
        self.assertFalse(selected.device.services_resolved)

    def test_alias_does_not_turn_other_headphones_into_xm5(self):
        selected = discovery.select_bluetooth(objects(alias="WF-1000XM5", name="Other headset"))
        self.assertIsNone(selected.device)

    def test_explicit_address_matches_user_selected_device(self):
        selected = discovery.select_bluetooth(objects(name="Other headset"), ADDRESS.lower())
        self.assertEqual(selected.device.address, ADDRESS)

    def test_connected_pair_wins_over_disconnected_paired_identity(self):
        data = objects()
        data.update(objects(connected=False, adapter="hci1"))
        self.assertEqual(discovery.select_bluetooth(data).device.path, BLUEZ_PATH)

    def test_two_live_adapters_are_ambiguous_even_with_identical_address(self):
        data = objects()
        data.update(objects(adapter="hci1"))
        result = discovery.select_bluetooth(data)
        self.assertEqual(result.reason, "ambiguous")
        self.assertIsNone(result.device)

    def test_single_paired_but_disconnected_headset_waits(self):
        selected = discovery.select_bluetooth(objects(connected=False))
        self.assertEqual(selected.reason, "waiting")
        self.assertFalse(selected.device.connected)

    def test_only_manager_reported_exact_adapter_path_matches(self):
        device = discovery.select_bluetooth(objects()).device
        self.assertEqual(discovery.companion_device_path([], device), "")
        self.assertEqual(discovery.companion_device_path([COMPANION_PATH.replace("hci0", "hci1")], device), "")
        self.assertEqual(discovery.companion_device_path([COMPANION_PATH], device), COMPANION_PATH)


@unittest.skipUnless(HAVE_DBUS, "requires dbus-next")
class Reconciliation(unittest.TestCase):
    def setUp(self):
        self.engine = Engine()
        self.runtime = DesktopRuntime(self.engine, clock=lambda: 1)

    def test_bluetooth_starts_session_before_sink_and_controls(self):
        self.runtime.bluez_ready(objects())
        state = self.engine.snapshot()
        self.assertTrue(state["connected"])
        self.assertEqual(state["audio_state"], "waiting")
        self.assertFalse(state["controls_ready"])
        self.runtime.budslink_ready([COMPANION_PATH])
        self.assertTrue(self.engine.controls_ready)
        self.assertEqual(self.engine.companion_path, COMPANION_PATH)
        self.assertEqual(self.engine.snapshot()["audio_state"], "waiting")

    def test_audio_service_outage_does_not_disconnect_bluetooth_or_tracker(self):
        self.runtime.bluez_ready(objects())
        generation = self.engine.announce(ADDRESS, "WF-1000XM5", "earbud")
        self.engine.sample(ADDRESS, generation, 1, [1, 0, 0, 0])
        self.engine.observe_sink(self.engine.epoch, Sink(ADDRESS, "bluez_output.test", "123", "a2dp", "idle"))
        epoch = self.engine.epoch
        self.runtime.pipewire_unavailable("PipeWire restarting")
        self.assertTrue(self.engine.connected)
        self.assertEqual(self.engine.epoch, epoch)
        self.assertIn(ADDRESS, self.engine.trackers)
        self.assertIsNone(self.engine.sink)

    def test_budslink_late_arrival_and_restart_are_independent(self):
        self.runtime.budslink_ready([COMPANION_PATH])
        self.assertFalse(self.engine.controls_ready)
        self.runtime.bluez_ready(objects())
        self.assertTrue(self.engine.controls_ready)
        epoch = self.engine.epoch
        self.runtime.budslink_unavailable("service-restarting")
        self.assertFalse(self.engine.controls_ready)
        self.assertEqual(self.engine.companion_path, "")
        self.assertEqual(self.engine.epoch, epoch)
        self.assertTrue(self.engine.connected)
        self.runtime.budslink_ready([COMPANION_PATH])
        self.assertTrue(self.engine.controls_ready)

    def test_late_pipewire_cannot_resurrect_disconnected_headset(self):
        self.runtime.bluez_ready(objects())
        self.runtime.bluez_ready(objects(connected=False))
        self.runtime.pipewire_ready([])
        self.assertFalse(self.engine.connected)
        self.assertIsNone(self.engine.sink)

    def test_reconnect_does_not_reuse_previous_audio_observation(self):
        self.runtime.bluez_ready(objects())
        self.engine.observe_sink(self.engine.epoch, Sink(ADDRESS, "old", "123", "a2dp", "idle"))
        self.runtime.bluez_ready(objects(connected=False))
        self.runtime.bluez_ready(objects())
        self.assertTrue(self.engine.connected)
        self.assertIsNone(self.engine.sink)

    def test_same_address_changing_adapter_starts_new_session(self):
        self.runtime.bluez_ready(objects())
        old_epoch = self.engine.epoch
        self.engine.observe_sink(self.engine.epoch, Sink(ADDRESS, "old", "123", "a2dp", "idle"))
        self.runtime.bluez_ready(objects(adapter="hci1"))
        self.assertGreater(self.engine.epoch, old_epoch)
        self.assertIsNone(self.engine.sink)

    def test_ambiguity_drops_target_and_explains_candidate_identities(self):
        data = objects()
        data.update(objects(adapter="hci1"))
        self.runtime.bluez_ready(data)
        self.assertFalse(self.engine.connected)
        self.assertEqual(self.runtime.state()["discovery"]["selection"], "ambiguous")
        self.assertEqual(len(self.runtime.state()["discovery"]["candidates"]), 2)


class FakeBus:
    def __init__(self):
        self.handlers = []
        self.owner = ":1.20"
        self.value = objects()
        self.calls = []
        self.disconnected = asyncio.Event()
        self.snapshot_started = asyncio.Event()
        self.gate = None

    async def connect(self):
        return self

    def add_message_handler(self, handler):
        self.handlers.append(handler)

    def remove_message_handler(self, handler):
        self.handlers.remove(handler)

    def disconnect(self):
        self.disconnected.set()

    async def wait_for_disconnect(self):
        await self.disconnected.wait()

    async def call(self, message):
        self.calls.append(message.member)
        result = []
        if message.member == "GetNameOwner":
            if not self.owner:
                return SimpleNamespace(message_type=MessageType.ERROR,
                                       error_name="org.freedesktop.DBus.Error.NameHasNoOwner", body=[])
            result = [self.owner]
        if message.member == "GetManagedObjects":
            value = self.value
            self.snapshot_started.set()
            if self.gate:
                await self.gate.wait()
            result = [value]
        return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=result)

    def signal(self, sender, interface, member, body, signature):
        for handler in self.handlers[:]:
            handler(Message(message_type=MessageType.SIGNAL, path="/", sender=sender,
                            interface=interface, member=member, body=body, signature=signature))


@unittest.skipUnless(HAVE_DBUS, "requires dbus-next")
class WatchLifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bus = FakeBus()
        self.stop = asyncio.Event()
        self.changed = []
        self.failures = []
        self.ready = asyncio.Event()
        self.failed = asyncio.Event()
        def changed(value):
            self.changed.append(value)
            self.ready.set()
        def failed(reason):
            self.failures.append(reason)
            self.failed.set()
        self.task = asyncio.create_task(discovery.watch_bluez(changed, failed, self.stop,
                                                            bus_factory=lambda: self.bus))

    async def asyncTearDown(self):
        self.stop.set()
        await asyncio.wait_for(self.task, 1)
        self.assertTrue(self.bus.disconnected.is_set())
        self.assertEqual(self.bus.handlers, [])

    async def test_subscriptions_precede_initial_snapshot_and_properties_trigger_refresh(self):
        await asyncio.wait_for(self.ready.wait(), 1)
        self.assertLess(self.bus.calls.index("AddMatch"), self.bus.calls.index("GetManagedObjects"))
        self.ready.clear()
        self.bus.value = objects(connected=False)
        self.bus.signal(":1.20", discovery.PROPERTIES, "PropertiesChanged",
                        [discovery.DEVICE, {"Connected": Variant("b", False)}, []], "sa{sv}as")
        await asyncio.wait_for(self.ready.wait(), 1)
        self.assertFalse(self.changed[-1][BLUEZ_PATH][discovery.DEVICE]["Connected"])

    async def test_wrong_sender_cannot_inject_state_changes(self):
        await asyncio.wait_for(self.ready.wait(), 1)
        self.ready.clear()
        count = self.bus.calls.count("GetManagedObjects")
        self.bus.signal(":1.999", discovery.PROPERTIES, "PropertiesChanged",
                        [discovery.DEVICE, {"Connected": Variant("b", False)}, []], "sa{sv}as")
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertEqual(self.bus.calls.count("GetManagedObjects"), count)

    async def test_service_loss_invalidates_inflight_snapshot(self):
        await asyncio.wait_for(self.ready.wait(), 1)
        self.ready.clear()
        self.bus.snapshot_started.clear()
        self.bus.gate = asyncio.Event()
        self.bus.value = {"stale": {}}
        self.bus.signal(":1.20", discovery.OBJECT_MANAGER, "InterfacesRemoved",
                        [BLUEZ_PATH, [discovery.DEVICE]], "oas")
        await asyncio.wait_for(self.bus.snapshot_started.wait(), 1)
        self.bus.owner = ":1.21"
        self.bus.signal("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                        [discovery.BLUEZ, ":1.20", ":1.21"], "sss")
        self.bus.value = objects(connected=False)
        self.bus.gate.set()
        await asyncio.wait_for(self.ready.wait(), 1)
        self.assertIn("service-restarting", self.failures)
        self.assertNotIn({"stale": {}}, self.changed)
        self.assertEqual(self.changed[-1], objects(connected=False))

    async def test_disconnect_during_snapshot_never_publishes_stale_connection(self):
        await asyncio.wait_for(self.ready.wait(), 1)
        self.ready.clear()
        self.bus.snapshot_started.clear()
        self.bus.gate = asyncio.Event()
        self.bus.value = {"stale-connected-snapshot": {}}
        self.bus.signal(":1.20", discovery.PROPERTIES, "PropertiesChanged",
                        [discovery.DEVICE, {"Alias": Variant("s", "Updated alias")}, []], "sa{sv}as")
        await asyncio.wait_for(self.bus.snapshot_started.wait(), 1)
        self.bus.value = objects(connected=False)
        self.bus.signal(":1.20", discovery.PROPERTIES, "PropertiesChanged",
                        [discovery.DEVICE, {"Connected": Variant("b", False)}, []], "sa{sv}as")
        self.bus.gate.set()
        await asyncio.wait_for(self.ready.wait(), 1)
        self.assertNotIn({"stale-connected-snapshot": {}}, self.changed)
        self.assertEqual(self.changed[-1], objects(connected=False))

    async def test_absent_service_recovers_on_name_owner_event(self):
        # Start running without an owner, then have a real name-owner signal wake
        # the capped-backoff loop immediately instead of waiting for a sleep.
        await asyncio.wait_for(self.ready.wait(), 1)
        self.ready.clear()
        self.bus.owner = None
        self.bus.signal("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                        [discovery.BLUEZ, ":1.20", ""], "sss")
        await asyncio.wait_for(self.failed.wait(), 1)
        self.bus.owner = ":1.30"
        self.bus.signal("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                        [discovery.BLUEZ, "", ":1.30"], "sss")
        await asyncio.wait_for(self.ready.wait(), 1)
        self.assertEqual(self.changed[-1], objects())


@unittest.skipUnless(HAVE_DBUS, "requires dbus-next")
class PipeWireProbeSession(unittest.IsolatedAsyncioTestCase):
    async def test_inflight_probe_cannot_attach_to_reconnected_session(self):
        engine = Engine()
        runtime = DesktopRuntime(engine, clock=lambda: 1)
        runtime.bluez_ready(objects())
        started, release = asyncio.Event(), asyncio.Event()
        async def capture():
            started.set()
            await release.wait()
            return []
        with patch("spatial.desktop.pipewire.capture", capture):
            pending = asyncio.create_task(runtime.capture_pipewire())
            await started.wait()
            runtime.bluez_ready(objects(connected=False))
            runtime.bluez_ready(objects())
            release.set()
            result = await pending
        with patch.object(runtime, "pipewire_ready") as ready:
            runtime.accept_pipewire(result)
            ready.assert_not_called()
        with patch.object(runtime, "pipewire_unavailable") as unavailable:
            runtime.fail_pipewire("old-session-failure")
            unavailable.assert_not_called()
