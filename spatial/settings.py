"""Validated, per-user configuration for the real runtime."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import tomllib
from dataclasses import dataclass

from .pipewire import address


def config_directory():
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "spatiald"


@dataclass(frozen=True)
class Settings:
    bluetooth_address: str | None = None
    mpv_binary: str = "mpv"
    bridge_path: str | None = None
    library_path: str | None = None
    sony_enabled: bool = True
    sony_binary: str = "sony-tracker"
    slime_enabled: bool = True
    equalizer_enabled: bool = True
    equalizer_profile: str | None = None
    live_enabled: bool = True
    stereo_spatialization: bool = True
    orender_binary: str = "orender"
    tidal_require_atmos: bool = True
    automatic_diagnostics: bool = True
    sample_timeout: float = 0.5
    readiness_timeout: float = 30.0

    @classmethod
    def load(cls, path=None):
        path = Path(path) if path is not None else config_directory() / "config.toml"
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text())
        if data.get("schema") != 1:
            raise ValueError("configuration schema must be 1")
        fields = {field for field in cls.__dataclass_fields__}
        unknown = set(data) - fields - {"schema"}
        if unknown:
            raise ValueError("unknown configuration keys: " + ", ".join(sorted(unknown)))
        values = {key: value for key, value in data.items() if key in fields}
        for key in ("bluetooth_address", "bridge_path", "library_path", "equalizer_profile"):
            if key in values:
                if not isinstance(values[key], str):
                    raise ValueError(f"{key} must be text")
                values[key] = values[key] or None
        if values.get("bluetooth_address"):
            values["bluetooth_address"] = address(values["bluetooth_address"])
        for key in ("sony_enabled", "slime_enabled", "equalizer_enabled", "live_enabled", "stereo_spatialization", "tidal_require_atmos", "automatic_diagnostics"):
            if key in values and type(values[key]) is not bool:
                raise ValueError(f"{key} must be a boolean")
        for key in ("sample_timeout", "readiness_timeout"):
            if key in values and (type(values[key]) not in (int, float) or not 0 < values[key] <= 120):
                raise ValueError(f"{key} must be positive and at most 120 seconds")
        for key in ("mpv_binary", "sony_binary", "orender_binary"):
            if key in values and (not isinstance(values[key], str) or not values[key].strip()):
                raise ValueError(f"{key} must be nonempty text")
        return cls(**values)

    def resolve_bridge(self):
        if self.bridge_path:
            return str(Path(self.bridge_path).expanduser())
        candidates = sorted(set(Path("/usr/lib/orender").glob("*harletty*bridge*.so*")))
        resolved = sorted({str(p.resolve()) for p in candidates if p.is_file()})
        if len(resolved) == 1:
            return resolved[0]
        # The renderer reports a missing bridge explicitly, never substitutes a plain decoder.
        return "/usr/lib/orender/libharletty_bridge.so"


DEFAULT_CONFIG = '''# Spatial audio companion. Empty address selects the unique WF-1000XM5.
schema = 1
bluetooth_address = ""
mpv_binary = "mpv"
bridge_path = ""
library_path = ""
sony_enabled = true
sony_binary = "sony-tracker"
slime_enabled = true
# DHRME/AutoEQ WF-1000XM5 five-band reference correction on this device only.
# Empty profile uses the bundled measured profile; Sony hardware EQ is independent.
equalizer_enabled = true
equalizer_profile = ""
# Creates a temporary application input only after an explicit Start request.
live_enabled = true
# Route this player's ordinary decoded audio through binaural PCM rendering.
stereo_spatialization = true
orender_binary = "orender"
# Set false to explicitly request ordinary lossless TIDAL audio.
tidal_require_atmos = true
# Save bounded, redacted local reports after persistent module errors.
automatic_diagnostics = true
sample_timeout = 0.5
readiness_timeout = 30.0
'''


def setup(path=None):
    """Create only missing user configuration. Never replace a user's choices."""
    path = Path(path) if path is not None else config_directory() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        Settings.load(path)
        return path, False
    fd, temp = tempfile.mkstemp(prefix=".config-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(DEFAULT_CONFIG)
            handle.flush()
            os.fsync(handle.fileno())
        # Hard link provides exclusive creation without overwriting a racing setup.
        try:
            os.link(temp, path)
        except FileExistsError:
            Settings.load(path)
            return path, False
        return path, True
    finally:
        os.unlink(temp)
