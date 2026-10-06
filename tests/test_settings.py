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

    def test_prediction_is_explicit_bounded_and_preserves_old_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            original = 'schema=1\nsony_enabled=true\n'
            path.write_text(original)
            self.assertFalse(Settings.load(path).head_prediction_enabled)
            self.assertEqual(path.read_text(), original)
            path.write_text(original + 'head_prediction_enabled=true\nhead_prediction_max_ms=60\n')
            settings = Settings.load(path)
            self.assertTrue(settings.head_prediction_enabled)
            self.assertEqual(settings.head_prediction_max_ms, 60)
            for field in ('head_prediction_enabled=1', 'head_prediction_max_ms=151',
                          'head_prediction_max_ms=0', 'head_prediction_max_ms=nan',
                          'head_prediction_max_ms=inf', 'head_prediction_max_ms=true'):
                path.write_text(original + field + '\n')
                with self.subTest(field=field), self.assertRaises(ValueError):
                    Settings.load(path)
