"""Shared adapter lifecycle; callbacks run on the daemon's asyncio thread."""
import asyncio
import time


async def pause(stop, delay):
    try:
        await asyncio.wait_for(stop.wait(), delay)
    except TimeoutError:
        pass


class Reporter:
    def __init__(self, name, callback=None):
        self.name = name
        self.callback = callback
        self.previous = None

    def __call__(self, state, detail=""):
        observation = (state, detail)
        if observation != self.previous:
            self.previous = observation
            if self.callback is not None:
                self.callback(self.name, state, detail)


class EngineSource:
    """Generation ownership keeps reconnect callbacks from removing a new session."""
    def __init__(self, engine, on_change=None):
        self.engine = engine
        self.on_change = on_change
        self.owned = {}
        self.sequence = 0

    def changed(self):
        if self.on_change is not None:
            self.on_change()

    def sample(self, key, reported_name, kind, pose):
        self.engine.advance(time.monotonic())
        if kind == "earbud" and (not self.engine.connected or self.engine.device != key):
            return False
        generation = self.owned.get(key)
        tracker = self.engine.trackers.get(key)
        new = (tracker is None or tracker.generation != generation or
               tracker.reported_name != reported_name)
        if new:
            generation = self.engine.announce(key, reported_name, kind)
            self.owned[key] = generation
        self.sequence += 1
        accepted = self.engine.sample(key, generation, self.sequence, pose.values())
        self.engine.select()
        if new and accepted:
            self.changed()
        return accepted

    def remove(self, key):
        generation = self.owned.pop(key, None)
        if generation is not None and self.engine.remove(key, generation):
            self.engine.select()
            self.changed()

    def clear(self):
        for key in list(self.owned):
            self.remove(key)
