"""Explicit development event format, not a Sony/Slime wire protocol."""
import json
from .core import Sink


def apply(engine, event):
    engine.advance(event["at"])
    action = event["type"]
    if action == "connect":
        engine.connect(event["device"], event.get("companion_path", ""))
    elif action == "disconnect":
        engine.disconnect()
    elif action == "sink":
        sink = Sink(**event["sink"]) if event.get("sink") else None
        engine.observe_sink(event.get("epoch", engine.epoch), sink)
    elif action == "capability":
        engine.capability(event.get("epoch", engine.epoch), event["name"], event["ready"])
    elif action == "source":
        if event["mode"] not in ("none", "spatial", "pcm"):
            raise ValueError("unknown source mode")
        engine.source_mode = event["mode"]
    elif action == "announce":
        engine.announce(event["id"], event["name"], event["kind"])
    elif action == "sample":
        generation = event.get("generation", engine.generations.get(event["id"], 0))
        engine.sample(event["id"], generation, event["sequence"], event["orientation"])
    elif action == "remove":
        engine.remove(event["id"], event.get("generation", engine.generations[event["id"]]))
    elif action == "enable":
        engine.set_enabled(event["id"], event["enabled"])
    elif action == "recenter":
        engine.recenter_active()
    elif action != "tick":
        raise ValueError("unknown event type")
    result = engine.snapshot()
    for key, expected in event.get("expect", {}).items():
        if result.get(key) != expected:
            raise AssertionError(f"{action} at {engine.now}: {key}: {result.get(key)!r} != {expected!r}")
    return result


def run(engine, path):
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        try:
            event = json.loads(line)
            state = apply(engine, event)
        except (ValueError, KeyError, TypeError, AssertionError) as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
        yield event, state
