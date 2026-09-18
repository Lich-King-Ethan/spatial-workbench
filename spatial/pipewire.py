"""Read-only pw-dump adapter. Hardware association is never inferred from aliases."""
import asyncio
import json
import re

from .core import Sink


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}", value):
        raise ValueError("expected a Bluetooth address AA:BB:CC:DD:EE:FF")
    return value.upper()


def parse_sinks(objects, selected_address):
    selected_address = address(selected_address)
    if not isinstance(objects, list):
        raise ValueError("pw-dump must be an array")
    devices = {}
    for obj in objects:
        if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Device":
            continue
        props = obj.get("info", {}).get("props", {})
        if props.get("device.api") == "bluez5":
            devices[str(obj.get("id"))] = props
    found = []
    for obj in objects:
        if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Node":
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        if props.get("media.class") != "Audio/Sink":
            continue
        parent = devices.get(str(props.get("device.id")), {})
        reported = props.get("api.bluez5.address", parent.get("api.bluez5.address"))
        if not isinstance(reported, str) or reported.upper() != selected_address:
            continue
        # Some versions put Bluetooth properties on the device; some on the node.
        profile = str(props.get("api.bluez5.profile", parent.get("api.bluez5.profile", "")))
        serial = props.get("object.serial")
        if serial is None:
            continue
        found.append(Sink(selected_address, str(props.get("node.name", "")), str(serial),
                          profile, str(info.get("state", "unknown")),
                          props.get("api.bluez5.codec", parent.get("api.bluez5.codec"))))
    return sorted(found, key=lambda s: (not s.usable, s.name, s.serial))


async def capture():
    proc = await asyncio.create_subprocess_exec(
        "pw-dump", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, error = await asyncio.wait_for(proc.communicate(), 4)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        raise RuntimeError("pw-dump could not read the user PipeWire session")
    return json.loads(output)
