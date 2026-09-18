import tempfile
import unittest
from pathlib import Path
from spatial.core import Sink
from spatial.playback import mpv_plan


class Playback(unittest.TestCase):
    def test_plan_keeps_paths_as_arguments_and_targets_real_sink(self):
        with tempfile.TemporaryDirectory() as root:
            media = Path(root) / "name; with spaces.mkv"
            config = Path(root) / "config.yaml"
            media.touch()
            config.touch()
            sink = Sink("AA:BB:CC:DD:EE:FF", "bluez_output.test", "1", "a2dp", "idle")
            plan = mpv_plan(media, config, sink)
            self.assertEqual(plan["argv"][-2:], ["--", str(media)])
            self.assertIn("--audio-device=pipewire/bluez_output.test", plan["argv"])
            self.assertIn("node.dont-fallback = true", plan["environment"]["PIPEWIRE_PROPS"])
