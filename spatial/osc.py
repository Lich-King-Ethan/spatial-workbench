"""Bounded OSC 1.0 messages/bundles used by the local renderer protocol.

No networking policy lives here. The caller binds only to loopback. Unknown
argument types, malformed padding, and scheduled bundles fail closed.
"""
from __future__ import annotations

import math
import struct


MAX_PACKET = 65507
MAX_MESSAGES = 512


def _string(value):
    if not isinstance(value, str) or "\0" in value:
        raise ValueError("OSC strings must be text without NUL")
    value = value.encode("utf-8") + b"\0"
    return value + b"\0" * (-len(value) % 4)


def encode(address, *arguments):
    if not isinstance(address, str) or not address.startswith("/"):
        raise ValueError("OSC address must begin with /")
    tags, data = ",", []
    for value in arguments:
        if isinstance(value, bool):
            tags += "T" if value else "F"
        elif isinstance(value, int):
            tags += "i" if -(2**31) <= value < 2**31 else "h"
            try:
                data.append(struct.pack(">i" if tags[-1] == "i" else ">q", value))
            except struct.error as exc:
                raise ValueError("OSC integer is outside int64") from exc
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("OSC floats must be finite")
            tags += "f"
            try:
                data.append(struct.pack(">f", value))
            except (struct.error, OverflowError) as exc:
                raise ValueError("OSC float is outside float32") from exc
        elif isinstance(value, str):
            tags += "s"
            data.append(_string(value))
        elif value is None:
            tags += "N"
        else:
            raise ValueError("unsupported OSC argument type")
    result = _string(address) + _string(tags) + b"".join(data)
    if len(result) > MAX_PACKET:
        raise ValueError("OSC message exceeds one UDP datagram")
    return result


def decode(packet):
    """Return ``[(address, arguments), ...]``; flatten immediate OSC bundles."""
    if not isinstance(packet, bytes) or not 4 <= len(packet) <= MAX_PACKET:
        raise ValueError("invalid OSC packet size")
    messages = []

    def parse(data, depth=0):
        if depth > 8:
            raise ValueError("OSC bundle nesting is too deep")
        if data.startswith(b"#bundle\0"):
            if len(data) < 16 or data[8:16] not in (b"\0" * 8, b"\0" * 7 + b"\1"):
                raise ValueError("only immediate OSC bundles are accepted")
            offset = 16
            while offset < len(data):
                if offset + 4 > len(data):
                    raise ValueError("truncated OSC bundle size")
                size = struct.unpack_from(">I", data, offset)[0]
                offset += 4
                if size < 4 or size % 4 or offset + size > len(data):
                    raise ValueError("invalid OSC bundle element")
                parse(data[offset:offset + size], depth + 1)
                offset += size
            return
        offset = 0

        def string():
            nonlocal offset
            end = data.find(b"\0", offset)
            if end < 0:
                raise ValueError("unterminated OSC string")
            padded = (end + 4) & ~3
            if padded > len(data) or any(data[end:padded]):
                raise ValueError("invalid OSC string padding")
            try:
                result = data[offset:end].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("invalid OSC UTF-8") from exc
            offset = padded
            return result

        address, tags = string(), string()
        if not address.startswith("/") or not tags.startswith(","):
            raise ValueError("invalid OSC address or type tags")
        args = []
        for tag in tags[1:]:
            if tag == "s":
                args.append(string())
            elif tag in "ifhd":
                size = 8 if tag in "hd" else 4
                if offset + size > len(data):
                    raise ValueError("truncated OSC number")
                value = struct.unpack_from(">" + {"h": "q"}.get(tag, tag), data, offset)[0]
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError("non-finite OSC number")
                args.append(value)
                offset += size
            elif tag in "TFN":
                args.append({"T": True, "F": False, "N": None}[tag])
            elif tag == "b":
                if offset + 4 > len(data):
                    raise ValueError("truncated OSC blob length")
                size = struct.unpack_from(">I", data, offset)[0]
                offset += 4
                end = offset + size
                padded = (end + 3) & ~3
                if padded > len(data) or any(data[end:padded]):
                    raise ValueError("invalid OSC blob")
                args.append(data[offset:end])
                offset = padded
            else:
                raise ValueError("unsupported OSC type tag")
        if offset != len(data):
            raise ValueError("trailing OSC packet data")
        messages.append((address, args))
        if len(messages) > MAX_MESSAGES:
            raise ValueError("too many OSC bundle messages")

    parse(packet)
    return messages
