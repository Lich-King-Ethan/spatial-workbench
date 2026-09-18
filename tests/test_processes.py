import asyncio
import sys
import unittest
from spatial.processes import supervise_process


class Isolation(unittest.IsolatedAsyncioTestCase):
    async def test_crashing_helper_does_not_stop_other_task(self):
        stop = asyncio.Event()
        failures = []
        ticks = []
        def unavailable(reason):
            failures.append(reason)
            if len(failures) == 2:
                stop.set()
        async def independent():
            while not stop.is_set():
                ticks.append(1)
                await asyncio.sleep(0.01)
        await asyncio.wait_for(asyncio.gather(
            supervise_process([sys.executable, "-c", "raise SystemExit(7)"], unavailable, stop),
            independent()), 3)
        self.assertEqual(failures, ["exit:7", "exit:7"])
        self.assertGreater(len(ticks), 2)

    async def test_stop_reaps_own_helper(self):
        stop = asyncio.Event()
        failures = []
        task = asyncio.create_task(supervise_process(
            [sys.executable, "-c", "import time; time.sleep(30)"], failures.append, stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, 3)
        self.assertEqual(failures, ["stopped"])
