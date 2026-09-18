"""Create a reviewable mpv invocation. This module never launches a player."""
from pathlib import Path

from .core import Sink


def mpv_plan(media, config, sink: Sink):
    if not sink.usable:
        raise ValueError("a usable A2DP output is required")
    # Local content only in this milestone; no stream-token/auth ownership here.
    media = Path(media).expanduser().resolve(strict=True)
    config = Path(config).expanduser().resolve(strict=True)
    if not media.is_file() or not config.is_file():
        raise ValueError("media and config must be files")
    return {
        "status": "unvalidated_on_hardware",
        "argv": ["mpv", "--no-config", "--ao=pipewire", "--ad=orender",
                 "--ad-orender-config=" + str(config), "--ad-orender-osc",
                 "--ad-orender-osc-bind=127.0.0.1",
                 "--audio-device=pipewire/" + sink.name,
                 "--audio-channels=stereo", "--", str(media)],
        "environment": {"PIPEWIRE_PROPS":
                        '{ node.dont-fallback = true node.dont-reconnect = true }'},
        "expected_target": sink.name,
        "requirements": ["mpv built with ad_orender", "compatible liborender and decoder bridge",
                         "binaural configuration", "decoded spatial metadata confirmed",
                         "actual PipeWire stream properties and disconnect behavior verified"],
    }
