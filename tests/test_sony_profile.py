"""Missing-profile recovery uses BlueZ fixtures, never a physical connection."""
import asyncio
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch

from spatial.core import Engine
from spatial.discovery import BluetoothDevice
from spatial.trackers import sony
from spatial.trackers.hid import HidDevice


ADDRESS = "AA:BB:CC:DD:EE:FF"
PATH = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"


class SonyProfileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = Engine()
        self.engine.connect(ADDRESS)
        self.selected = BluetoothDevice(ADDRESS, PATH, "WF-1000XM5", "Earbuds", True, True, True)
        self.properties = dict(Address=ADDRESS, Connected=True, Paired=True,
                               ServicesResolved=True, UUIDs=[sony.HID_PROFILE])
        self.bus = Mock(connect=AsyncMock())
        self.calls = []

    async def call(self, bus, destination, path, interface, member, *args):
        self.calls.append((path, interface, member, args))
        return [self.properties] if member == "GetAll" else []

    async def activate(self, call=None):
        with patch.object(sony, "MessageBus", return_value=self.bus), \
                patch.object(sony, "call", side_effect=call or self.call):
            return await sony.connect_missing_hid(self.engine, lambda: self.selected)

    async def test_only_selected_advertised_hid_is_connected(self):
        self.assertTrue(await self.activate())
        self.assertEqual(self.calls, [
            (PATH, sony.PROPERTIES, "GetAll", ("s", [sony.DEVICE])),
            (PATH, sony.DEVICE, "ConnectProfile", ("s", [sony.HID_PROFILE]))])
        self.bus.disconnect.assert_called_once()

    async def test_unready_or_unrelated_properties_never_connect(self):
        for key, value in [("Address", "11:22:33:44:55:66"), ("Connected", False),
                           ("Paired", False), ("ServicesResolved", False), ("UUIDs", [])]:
            with self.subTest(key=key):
                original = self.properties[key]
                self.properties[key] = value
                self.calls.clear()
                self.assertFalse(await self.activate())
                self.assertEqual([item[2] for item in self.calls], ["GetAll"])
                self.properties[key] = original

    async def test_invalid_selected_identity_does_not_contact_bluez(self):
        original = self.selected
        for selection in [None, replace(original, connected=False),
                          replace(original, address="11:22:33:44:55:66"),
                          replace(original, path="/org/bluez/hci0/dev_11_22_33_44_55_66"),
                          replace(original, path="/unrelated")]:
            self.selected = selection
            self.assertFalse(await self.activate())
        self.assertEqual(self.calls, [])
        self.bus.connect.assert_not_called()

    async def test_selection_changes_during_read_prevent_connection(self):
        original = self.selected
        for change in (lambda: setattr(self, "selected", replace(original, path=PATH.replace("hci0", "hci1"))),
                       lambda: self.engine.disconnect()):
            self.selected = original
            self.engine.connect(ADDRESS)
            self.calls.clear()

            async def changing(*args):
                result = await self.call(*args)
                change()
                return result

            self.assertFalse(await self.activate(changing))
            self.assertEqual([item[2] for item in self.calls], ["GetAll"])

    async def run_polling(self, inventories, activation):
        stop = asyncio.Event()
        count = 0

        async def pause(*args):
            nonlocal count
            count += 1
            if count >= len(inventories):
                stop.set()

        statuses = []
        with patch.object(sony, "inventory", side_effect=inventories), \
                patch.object(sony, "connect_missing_hid", activation), \
                patch.object(sony, "pause", side_effect=pause):
            await sony.run(self.engine, stop, status=lambda *args: statuses.append(args),
                           selected_bluetooth=lambda: self.selected)
        return statuses

    async def test_successful_activation_waits_for_hid_without_repeating(self):
        activation = AsyncMock(return_value=True)
        await self.run_polling([[], [], []], activation)
        activation.assert_awaited_once()

    async def test_existing_unsupported_hid_does_not_reconnect_profile(self):
        device = HidDevice("/dev/test", Path("/nonexistent"), 5, 0x054C, 1, "Sony", ADDRESS)
        activation = AsyncMock()
        await self.run_polling([[device]], activation)
        activation.assert_not_called()

    async def test_activation_failure_stays_local_and_retries(self):
        activation = AsyncMock(side_effect=[RuntimeError("BlueZ unavailable"), True])
        statuses = await self.run_polling([[], [], []], activation)
        self.assertEqual(activation.await_count, 2)
        self.assertTrue(self.engine.connected)
        self.assertTrue(any("BlueZ unavailable" in detail for _, _, detail in statuses))

    async def test_bus_failure_closes_connection(self):
        self.bus.connect.side_effect = RuntimeError("No system bus")
        with self.assertRaisesRegex(RuntimeError, "No system bus"):
            await self.activate()
        self.bus.disconnect.assert_called_once()
