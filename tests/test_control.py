"""Explicit timing evidence over a private D-Bus, never the desktop bus."""
import asyncio
import io
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from dbus_next import DBusError
from dbus_next.aio import MessageBus
from spatial import cli
from spatial.core import Engine
from spatial.desktop import BUS, PATH, Control, DesktopRuntime, export


def snapshot():
    return {'schema': 1, 'clock': 'host-monotonic', 'captured_ns': 123456789,
            'samples': [{'sequence': i, 'received_ns': i * 10000000} for i in range(520)],
            'applications': [{'sequence': i} for i in range(514)],
            'latency_evidence': {'output_latency_known': False},
            'prediction_status': {'enabled': False}}


def bluetooth_objects(*, connected=True, adapter='hci0'):
    return {f'/org/bluez/{adapter}/dev_AA_BB_CC_DD_EE_FF': {'org.bluez.Device1': {
        'Address': 'AA:BB:CC:DD:EE:FF', 'Name': 'WF-1000XM5', 'Paired': True,
        'Connected': connected, 'ServicesResolved': True}}}


class PipeWireObservationTime(unittest.TestCase):
    def setUp(self):
        self.runtime = DesktopRuntime(Engine(), clock=lambda: 1)
        self.monotonic = patch('spatial.desktop.time.monotonic_ns', return_value=1000000000)
        self.now = self.monotonic.start()
        self.addCleanup(self.monotonic.stop)

    def accept(self):
        runtime = self.runtime
        runtime.accept_pipewire(((runtime.engine.epoch, runtime.engine.device), []))

    def test_only_successful_session_observation_refreshes_timestamp(self):
        runtime = self.runtime
        self.assertIsNone(runtime.pipewire_observed_ns)
        runtime.bluez_ready(bluetooth_objects())
        self.assertIsNone(runtime.pipewire_observed_ns)
        self.accept()
        self.assertEqual(runtime.pipewire_observed_ns, 1000000000)
        self.now.return_value = 9000000000
        runtime.publish()
        runtime.state()
        runtime.bluez_ready(bluetooth_objects())
        self.assertEqual(runtime.pipewire_observed_ns, 1000000000)
        self.accept()
        self.assertEqual(runtime.pipewire_observed_ns, 9000000000)

    def test_pipewire_failure_and_bluetooth_loss_clear_timestamp(self):
        runtime = self.runtime
        for invalidate in (lambda: runtime.pipewire_unavailable('restarting'),
                           lambda: runtime.bluez_unavailable('disconnected'),
                           lambda: runtime.bluez_ready(bluetooth_objects(connected=False))):
            with self.subTest(invalidate=invalidate):
                runtime.bluez_ready(bluetooth_objects())
                self.accept()
                self.assertIsNotNone(runtime.pipewire_observed_ns)
                invalidate()
                self.assertIsNone(runtime.pipewire_observed_ns)

    def test_adapter_change_and_reconnect_cannot_reuse_observation(self):
        runtime = self.runtime
        runtime.bluez_ready(bluetooth_objects())
        self.accept()
        old_session = (runtime.engine.epoch, runtime.engine.device)
        runtime.pipewire_probe_session = old_session
        runtime.bluez_ready(bluetooth_objects(adapter='hci1'))
        self.assertIsNone(runtime.pipewire_observed_ns)
        runtime.accept_pipewire((old_session, []))
        self.assertIsNone(runtime.pipewire_observed_ns)
        self.accept()
        self.now.return_value = 9000000000
        runtime.accept_pipewire((old_session, []))
        runtime.fail_pipewire('old probe failed')
        self.assertEqual(runtime.pipewire_observed_ns, 1000000000)
        runtime.bluez_ready(bluetooth_objects(connected=False, adapter='hci1'))
        runtime.pipewire_ready([])
        self.assertIsNone(runtime.pipewire_observed_ns)
        runtime.bluez_ready(bluetooth_objects(adapter='hci1'))
        self.assertIsNone(runtime.pipewire_observed_ns)
        self.accept()
        self.assertEqual(runtime.pipewire_observed_ns, 9000000000)


class TimingControl(unittest.TestCase):
    def test_callback_is_explicit_bounded_and_does_not_publish(self):
        data = snapshot()
        callback = Mock(return_value=data)
        control = Control(Engine(), on_timing_snapshot=callback)
        callback.assert_not_called()
        previous = control.State
        with patch.object(control, 'emit_properties_changed') as changed:
            result = json.loads(Control.TimingSnapshot.__wrapped__(control))
            control.publish()
            changed.assert_not_called()
        self.assertEqual(control.State, previous)
        self.assertEqual(result['samples'], data['samples'][-512:])
        self.assertEqual(result['applications'], data['applications'][-512:])
        self.assertEqual(len(data['samples']), 520)
        self.assertEqual(result['captured_ns'], 123456789)
        self.assertNotIn('samples', json.loads(control.State))
        callback.assert_called_once_with()

    def test_unavailable_callback_is_explicit(self):
        with self.assertRaises(DBusError) as caught:
            Control.TimingSnapshot.__wrapped__(Control(Engine()))
        self.assertEqual(caught.exception.type, BUS + '.Unavailable')

    def test_optional_renderer_observations_are_bounded_without_mutation(self):
        data = {**snapshot(), 'renderer_observations': [{'sequence': i} for i in range(530)]}
        control = Control(Engine(), on_timing_snapshot=lambda: data)
        result = json.loads(Control.TimingSnapshot.__wrapped__(control))
        self.assertEqual(result['renderer_observations'], data['renderer_observations'][-512:])
        self.assertEqual(len(data['renderer_observations']), 530)

    def test_malformed_or_failed_callbacks_are_sanitized(self):
        values = [None, [], {}, {**snapshot(), 'samples': 'not-a-list'},
                  {**snapshot(), 'renderer_observations': 'not-a-list'},
                  {**snapshot(), 'captured_ns': math.nan},
                  {**snapshot(), 'latency_evidence': {'bad': object()}},
                  {**snapshot(), 'excess': 'x' * (2 * 1024 * 1024)},
                  {**snapshot(), 'excess': '\N{SNOWMAN}' * (1024 * 1024)}]
        for value in values:
            with self.subTest(kind=type(value).__name__):
                control = Control(Engine(), on_timing_snapshot=lambda: value)
                with self.assertRaises(DBusError) as caught:
                    Control.TimingSnapshot.__wrapped__(control)
                self.assertEqual(caught.exception.type, BUS + '.Failed')
        control = Control(Engine(), on_timing_snapshot=Mock(side_effect=ValueError('private-token')))
        with self.assertRaises(DBusError) as caught:
            Control.TimingSnapshot.__wrapped__(control)
        self.assertNotIn('private-token', str(caught.exception))

    def test_async_callback_is_rejected_without_leaking_coroutine(self):
        async def callback():
            return snapshot()
        control = Control(Engine(), on_timing_snapshot=callback)
        with self.assertRaises(DBusError):
            Control.TimingSnapshot.__wrapped__(control)

    def test_desktop_stores_callback_without_polling(self):
        callback = Mock(return_value=snapshot())
        runtime = DesktopRuntime(Engine(), on_timing_snapshot=callback)
        self.assertIs(runtime.on_timing_snapshot, callback)
        runtime.state()
        callback.assert_not_called()


class TimingCli(unittest.TestCase):
    def invoke(self, args, value=None):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(cli, 'client', AsyncMock(return_value=value or snapshot())) as client, \
                patch('sys.stdout', output), patch('sys.stderr', errors):
            result = cli.main(['timing-snapshot', *args])
        client.assert_awaited_once_with('timing-snapshot')
        return result, output.getvalue(), errors.getvalue()

    def test_default_output_is_json_only(self):
        result, output, errors = self.invoke([])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output), snapshot())
        self.assertEqual(errors, '')

    def test_explicit_file_is_exclusive_and_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'timing.json'
            result, output, errors = self.invoke(['--output', str(path)])
            self.assertEqual((result, output, errors), (0, '', ''))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text()), snapshot())
            original = path.read_bytes()
            self.assertEqual(self.invoke(['--output', str(path)])[0], 1)
            self.assertEqual(path.read_bytes(), original)

    def test_symlinks_existing_and_dangling_are_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'target'
            target.write_text('keep user data')
            for name, destination in (('existing', target), ('dangling', Path(tmp) / 'missing')):
                link = Path(tmp) / name
                link.symlink_to(destination)
                self.assertEqual(self.invoke(['--output', str(link)])[0], 1)
                self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text(), 'keep user data')
            self.assertFalse((Path(tmp) / 'missing').exists())

    def test_nonfinite_data_never_creates_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'timing.json'
            value = {**snapshot(), 'captured_ns': math.inf}
            self.assertEqual(self.invoke(['--output', str(path)], value)[0], 1)
            self.assertFalse(path.exists())


@unittest.skipUnless(os.environ.get('SPATIAL_TEST_DBUS') == '1'
                     and os.environ.get('DBUS_SESSION_BUS_ADDRESS'),
                     'requires explicitly private D-Bus session')
class TimingPrivateBus(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.data = snapshot()
        self.server, self.control = await export(Engine(), on_timing_snapshot=lambda: self.data)
        self.bus = await MessageBus().connect()
        proxy = self.bus.get_proxy_object(BUS, PATH, await self.bus.introspect(BUS, PATH))
        self.api = proxy.get_interface(BUS)
        self.changes = []
        proxy.get_interface('org.freedesktop.DBus.Properties').on_properties_changed(self.changed)

    def changed(self, interface, values, invalidated):
        self.changes.append(values)

    async def asyncTearDown(self):
        self.bus.disconnect(); self.server.disconnect()
        await asyncio.gather(self.bus.wait_for_disconnect(), self.server.wait_for_disconnect())

    async def test_actual_method_and_cli_client_do_not_emit_state_changes(self):
        previous = await self.api.get_state()
        for captured in range(5):
            self.data['captured_ns'] = captured
            result = await cli.client('timing-snapshot')
            self.assertEqual(result['captured_ns'], captured)
            self.assertEqual(len(result['samples']), 512)
        await asyncio.sleep(.05)
        self.assertEqual(self.changes, [])
        self.assertEqual(await self.api.get_state(), previous)

    async def test_unavailable_and_invalid_data_cross_bus_as_errors(self):
        self.control.on_timing_snapshot = None
        with self.assertRaises(DBusError) as caught:
            await self.api.call_timing_snapshot()
        self.assertEqual(caught.exception.type, BUS + '.Unavailable')
        self.control.on_timing_snapshot = lambda: {'samples': [], 'applications': [], 'bad': math.nan}
        with self.assertRaises(DBusError) as caught:
            await self.api.call_timing_snapshot()
        self.assertEqual(caught.exception.type, BUS + '.Failed')
