import asyncio
import unittest
from spatial.retry import supervise


class Retry(unittest.IsolatedAsyncioTestCase):
    async def test_failed_provider_recovers_independently(self):
        stop = asyncio.Event()
        calls = []
        def ready(value):
            calls.append(value)
            stop.set()
        async def probe():
            if not calls:
                calls.append("attempt")
                raise OSError("not ready")
            return "ready"
        failures = []
        await asyncio.wait_for(supervise(probe, ready, failures.append, stop), 2)
        self.assertEqual(failures, ["OSError"])
        self.assertEqual(calls, ["attempt", "ready"])

    async def test_shutdown_interrupts_backoff(self):
        stop = asyncio.Event()
        async def probe():
            return None
        task = asyncio.create_task(supervise(probe, lambda v: None, lambda why: stop.set(), stop))
        await asyncio.wait_for(task, 0.1)
