"""Independent provider supervision, with cancellation and bounded backoff."""
import asyncio
from dataclasses import dataclass


@dataclass
class Backoff:
    attempt: int = 0

    def next_delay(self):
        delay = (0.25, 0.5, 1, 2, 4, 5)[min(self.attempt, 5)]
        self.attempt = min(5, self.attempt + 1)
        return delay

    def reset(self):
        self.attempt = 0


async def supervise(probe, ready, unavailable, stop, poll=1.0):
    """One task per provider. Callbacks are synchronous; probe must bound its I/O."""
    backoff = Backoff()
    while not stop.is_set():
        try:
            result = await probe()
            if result is None:
                unavailable("waiting")
                delay = backoff.next_delay()
            else:
                ready(result)
                backoff.reset()
                delay = poll
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            unavailable(type(exc).__name__)
            delay = backoff.next_delay()
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass
