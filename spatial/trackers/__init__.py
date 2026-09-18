"""Live tracker providers. No replay data enters this runtime."""
import asyncio

from . import slime, sony
from .common import Reporter
from .common import pause
from ..retry import Backoff


async def _supervise(factory, stop, reporter):
    backoff = Backoff()
    while not stop.is_set():
        try:
            await factory()
            if not stop.is_set():
                raise RuntimeError("provider exited unexpectedly")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reporter("unavailable", f"Provider restarting: {type(exc).__name__}: {exc}")
        if not stop.is_set():
            await pause(stop, backoff.next_delay())


async def run(engine, stop, on_change=None, status=None, *, sony_executable="sony-tracker",
              sony_enabled=True, optional_enabled=True,
              sysfs="/sys/class/hidraw", devdir="/dev"):
    tasks = []
    if sony_enabled:
        factory = lambda: sony.run(
            engine, stop, on_change, status, executable=sony_executable,
            sysfs=sysfs, devdir=devdir)
        tasks.append(asyncio.create_task(_supervise(factory, stop, Reporter("sony_tracker", status))))
    else:
        Reporter("sony_tracker", status)("disabled", "Earbud provider disabled in configuration")
    if optional_enabled:
        factory = lambda: slime.run(engine, stop, on_change, status, sysfs=sysfs, devdir=devdir)
        tasks.append(asyncio.create_task(_supervise(factory, stop, Reporter("optional_tracker", status))))
    else:
        Reporter("optional_tracker", status)("disabled", "Optional provider disabled in configuration")
    try:
        await stop.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
