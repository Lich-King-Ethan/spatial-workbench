"""Atomic user preferences. Never persist discovery, timestamps, or PW node IDs."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


def identity(value):
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("device identity must be a nonempty string (max 256 characters)")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("device identity contains control characters")
    return value


@dataclass
class Preferences:
    earbuds: dict[str, bool] = field(default_factory=dict)
    optional: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path):
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise ValueError("unsupported preferences schema")
        maps = []
        for key in ("earbuds", "optional"):
            entries = data.get(key, {})
            if not isinstance(entries, dict):
                raise ValueError(f"{key} preferences must be an object")
            for name, value in entries.items():
                identity(name)
                if type(value) is not bool:
                    raise ValueError("preference must be a boolean")
            maps.append(entries)
        if sum(maps[1].values()) > 1:
            raise ValueError("only one optional tracker may be selected")
        return cls(*maps)

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".preferences-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump({"schema": 1, "earbuds": self.earbuds,
                           "optional": self.optional}, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
