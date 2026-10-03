"""Real process/UDP and file-descriptor integration; wire fixtures aren't hardware."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from spatial.core import Engine
from spatial.trackers.common import EngineSource, Reporter
from spatial.trackers.hid import HidDevice
from spatial.trackers import slime, sony, run


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_timestamped_helper_uses_private_port_and_is_reaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "fixture-helper"
            identity = Path(tmp) / "selected HID identity.json"
            helper.write_text(f"#!{sys.executable}\n" + '''
import argparse,json,os,socket,struct,time
p=argparse.ArgumentParser();p.add_argument('--device');p.add_argument('--port',type=int)
p.add_argument('--absolute',action='store_true',required=True)
p.add_argument('--timestamped',action='store_true',required=True)
a=p.parse_args()
with open(a.device,'w') as f: json.dump({'pid':os.getpid(),'port':a.port,'absolute':a.absolute,'timestamped':a.timestamped},f)
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
sequence=0
while True:
    sequence+=1
    packet=b'SPT1'+struct.pack('<QQB6hB',time.monotonic_ns(),sequence,1,0,0,8192,0,0,0,7)
    s.sendto(packet,('127.0.0.1',a.port))
    time.sleep(.01)
''')
            helper.chmod(0o755)
            (Path(tmp) / "report_descriptor").write_bytes(b"")
            engine = Engine()
            engine.connect("AA:BB:CC:DD:EE:FF")
            available = asyncio.Event()
            received = []
            def changed():
                state = engine.snapshot()
                if state["earbud"]:
                    received.append(state["earbud"])
                    available.set()
            source = EngineSource(engine, changed)
            stop = asyncio.Event()
            device = HidDevice(str(identity), Path(tmp), 5, 0x54C, 0xDF1,
                               "WF-1000XM5", engine.device)
            task = asyncio.create_task(sony.session(
                engine, stop, device, source, Reporter("sony_tracker"), str(helper)))
            try:
                await asyncio.wait_for(available.wait(), 3)
                details = json.loads(identity.read_text())
                self.assertNotEqual(details["port"], 4242)
                self.assertTrue(details["absolute"])
                self.assertTrue(details["timestamped"])
                timing = engine.trackers[engine.device].timing
                self.assertEqual(timing["clock"], "host-monotonic")
                self.assertEqual(timing["discontinuity"], 7)
                self.assertEqual(len(timing["raw_report_hex"]), 28)
                self.assertLessEqual(timing["captured_ns"], timing["received_ns"])
                self.assertEqual(received[0]["id"], engine.device)
                # A new headphone epoch terminates this helper and rejects late packets.
                engine.disconnect()
                # Same MAC makes key-only validation insufficient: the old
                # helper's datagram is already pending in sock_recvfrom.
                engine.connect("AA:BB:CC:DD:EE:FF")
                await asyncio.wait_for(task, 3)
                self.assertIsNone(engine.snapshot()["earbud"])
                self.assertEqual(len(received), 1)
                with self.assertRaises(ProcessLookupError):
                    os.kill(details["pid"], 0)
            finally:
                stop.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_legacy_relative_helper_cannot_silently_supply_absolute_poses(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "legacy-helper"
            invocations = Path(tmp) / "invocations.json"
            helper.write_text(f"#!{sys.executable}\n" + '''
import argparse,json,sys
from pathlib import Path
path=Path(sys.argv[sys.argv.index('--device')+1])
previous=json.loads(path.read_text()) if path.exists() else []
path.write_text(json.dumps([*previous,sys.argv[1:]]))
p=argparse.ArgumentParser();p.add_argument('--device');p.add_argument('--port',type=int)
p.parse_args()  # Legacy protocol deliberately rejects the required --absolute.
''')
            helper.chmod(0o755)
            (Path(tmp) / "report_descriptor").write_bytes(b"")
            engine = Engine()
            engine.connect("AA:BB:CC:DD:EE:FF")
            source = EngineSource(engine)
            device = HidDevice(str(invocations), Path(tmp), 5, 0x54C, 0xDF1,
                               "WF-1000XM5", engine.device)
            with self.assertRaisesRegex(RuntimeError, "absolute"):
                await asyncio.wait_for(sony.session(
                    engine, asyncio.Event(), device, source,
                    Reporter("sony_tracker"), str(helper)), 3)
            calls = json.loads(invocations.read_text())
            self.assertEqual(len(calls), 1)
            self.assertIn("--absolute", calls[0])
            self.assertIn("--timestamped", calls[0])
            self.assertIsNone(engine.snapshot()["earbud"])

    async def test_passive_receiver_read_and_expiry_without_registration_heartbeat(self):
        read_fd, write_fd = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        engine = Engine(sample_timeout=0.05)
        available = asyncio.Event()
        stop = asyncio.Event()
        seen = []
        def changed():
            state = engine.snapshot()
            if state["optional_trackers"]:
                seen.append(state["optional_trackers"][0])
                available.set()
        device = HidDevice("/fixture/receiver", Path("/fixture"), 3, 0x1209, 0x7690, "", "")
        real_open = os.open
        opened = []
        def open_fixture(path, flags, *args):
            if path == device.path:
                opened.append(flags)
                return os.dup(read_fd)
            return real_open(path, flags, *args)
        task = None
        try:
            with patch("spatial.trackers.slime.os.open", side_effect=open_fixture):
                task = asyncio.create_task(slime.session(engine, stop, device, changed,
                                                         Reporter("optional_tracker")))
                await asyncio.sleep(0)
                import struct
                register = bytes((255, 2)) + bytes.fromhex("CDABF291C371") + bytes(8)
                rotation = struct.pack("<BB4h6x", 1, 2, 0, 0, 0, 32767)
                os.write(write_fd, register + rotation)
                await asyncio.wait_for(available.wait(), 2)
                self.assertEqual(seen[0]["name"], "71C391F2ABCD")
                self.assertEqual(opened[0] & os.O_ACCMODE, os.O_RDONLY)
                self.assertFalse(seen[0]["enabled"])
                os.write(write_fd, register * 4)
                await asyncio.sleep(.08)
                import time
                engine.advance(time.monotonic())
                self.assertEqual(engine.snapshot()["optional_trackers"], [])
                stop.set()
                await asyncio.wait_for(task, 1)
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            os.close(read_fd)
            os.close(write_fd)

    async def test_disabled_modules_open_nothing(self):
        engine = Engine()
        stop = asyncio.Event()
        stop.set()
        statuses = []
        with patch("spatial.trackers.sony.run") as sony_run, patch("spatial.trackers.slime.run") as slime_run:
            await run(engine, stop, status=lambda *args: statuses.append(args),
                      sony_enabled=False, optional_enabled=False)
        sony_run.assert_not_called()
        slime_run.assert_not_called()
        self.assertEqual([item[1] for item in statuses], ["disabled", "disabled"])

    async def test_provider_crash_restarts_without_stopping_other_module(self):
        from spatial.trackers import _supervise
        stop = asyncio.Event()
        attempts = []
        states = []
        async def factory():
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError("transient fixture error")
            stop.set()
        await asyncio.wait_for(_supervise(factory, stop, Reporter("test", lambda *a: states.append(a))), 2)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(states[0][1], "unavailable")
