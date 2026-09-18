"""Shared report-local privacy filtering for diagnostic and acceptance tools."""
import getpass
import hashlib
import hmac
from pathlib import Path
import re
import secrets
import socket

SECRET_KEYS = re.compile(r"token|password|passwd|secret|authorization|cookie|credential", re.I)
IDENTITY_KEYS = re.compile(
    r"(^|[_.-])(address|serial|unique|uniq|hostname|username|user.name|hardware.identifier)([_.-]|$)", re.I)
MAC = re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{2}[:_-]){5}[0-9a-f]{2}(?![0-9a-f])", re.I)
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
URL = re.compile(r"\b(?:https?|rtsp|ftp)://[^\s\"'<>]+", re.I)
SECRET_TEXT = re.compile(
    r"(?i)((?:access[_-]?token|refresh[_-]?token|password|authorization|cookie|secret)"
    r"[\"']?\s*[:=]\s*)(?:Bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)")


class Redactor:
    def __init__(self):
        self.key = secrets.token_bytes(32)
        self.home = str(Path.home())
        self.user = getpass.getuser()
        self.hostname = socket.gethostname()

    def identity(self, text):
        # Report-local salt keeps cross-device relationships useful without
        # permitting a dictionary attack against a saved stable Bluetooth hash.
        value = hmac.new(self.key, str(text).encode(), hashlib.sha256).hexdigest()[:10]
        return f"<id:{value}>"

    def text(self, value):
        value = str(value)
        value = SECRET_TEXT.sub(lambda m: m.group(1) + "<redacted>", value)
        value = URL.sub("<url:redacted>", value)
        value = MAC.sub(lambda m: self.identity(m.group()), value)
        value = IPV4.sub(lambda m: m.group() if m.group().startswith("127.") else self.identity(m.group()), value)
        if self.home != "/":
            value = value.replace(self.home, "<home>")
        for known in (self.user, self.hostname):
            if known and len(known) >= 3:
                value = re.sub(r"(?<![\w-])" + re.escape(known) + r"(?![\w-])", "<local>", value)
        return value

    def value(self, value, key=""):
        if SECRET_KEYS.search(key):
            return "<redacted>"
        if IDENTITY_KEYS.search(key) and value is not None:
            return self.identity(value)
        if isinstance(value, dict):
            return {self.text(k): self.value(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [self.value(v) for v in value]
        return self.text(value) if isinstance(value, str) else value

