import tempfile
import unittest
from pathlib import Path
from spatial.settings import Settings, setup


class SettingsTests(unittest.TestCase):
    def test_setup_preserves_user_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            self.assertTrue(setup(path)[1])
            content = path.read_text().replace('sony_enabled = true', 'sony_enabled = false')
            path.write_text(content)
            self.assertFalse(setup(path)[1])
            self.assertEqual(path.read_text(), content)
            self.assertFalse(Settings.load(path).sony_enabled)

    def test_invalid_configuration_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            for content in ('schema=1\nsonny_enabled=true', 'schema=1\nsony_enabled=1',
                            'schema=1\nsample_timeout=nan', 'schema=2',
                            'schema=1\nbluetooth_address="WF-1000XM5"'):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    Settings.load(path)

    def test_default_needs_no_mac_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(Settings.load(Path(directory) / 'missing').bluetooth_address)
