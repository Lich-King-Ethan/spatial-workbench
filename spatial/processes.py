"""Supervise an optional helper in its own process; never treat spawn as readiness."""
import asyncio
import os
import signal
import time

from .retry import Backoff


async def stop_child(child):
    if child.returncode is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(child.wait(), 2)
    except TimeoutError:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await child.wait()


async def supervise_process(argv, unavailable, stop):
    """argv is trusted local configuration; each helper publishes readiness separately.

    All children run as the desktop user. This is crash isolation, not a security
    sandbox. No package downloads, shell interpretation, or privilege escalation.
    """
    if not argv or not all(isinstance(value, str) for value in argv):
        raise ValueError("provider argv must be a nonempty list of strings")
    backoff = Backoff()
    while not stop.is_set():
        child = None
        waiter = stopper = None
        start = time.monotonic()
        try:
            child = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True)
            waiter = asyncio.create_task(child.wait())
            stopper = asyncio.create_task(stop.wait())
            await asyncio.wait([waiter, stopper], return_when=asyncio.FIRST_COMPLETED)
        except OSError as exc:
            unavailable(type(exc).__name__)
        finally:
            if child is not None:
                await stop_child(child)
                unavailable("stopped" if stop.is_set() else f"exit:{child.returncode}")
            for task in (waiter, stopper):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(t for t in (waiter, stopper) if t is not None),
                                 return_exceptions=True)
        if stop.is_set():
            break
        if time.monotonic() - start > 30:
            backoff.reset()
        try:
            await asyncio.wait_for(stop.wait(), backoff.next_delay())
        except TimeoutError:
            pass
