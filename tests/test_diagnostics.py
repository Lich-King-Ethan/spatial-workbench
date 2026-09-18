import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("diagnostics", Path(__file__).resolve().parents[1] / "tools/diagnostics.py")
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


class DiagnosticPrivacyTests(unittest.TestCase):
    def test_structured_credentials_and_identities_are_removed(self):
        redactor = diagnostics.Redactor()
        original = {"refresh_token": "VERY_SECRET", "device.serial": "unique-device-42",
                    "node.name": "bluez_output.AA_BB_CC_DD_EE_FF.1", "loopback": "127.0.0.1"}
        output = str(redactor.value(original))
        for secret in ("VERY_SECRET", "unique-device-42", "AA_BB_CC_DD_EE_FF"):
            self.assertNotIn(secret, output)
        self.assertIn("127.0.0.1", output)

    def test_text_credentials_urls_and_home_removed(self):
        redactor = diagnostics.Redactor()
        output = redactor.text('access_token="VERY_SECRET" Authorization: Bearer SIGNED_TOKEN '
                               'https://cdn.example/audio?token=PRIVATE ' + redactor.home + '/music/file.flac')
        for secret in ("VERY_SECRET", "SIGNED_TOKEN", "PRIVATE", "cdn.example", redactor.home):
            self.assertNotIn(secret, output)

    def test_redacted_identifiers_match_within_report_only(self):
        first, second = diagnostics.Redactor(), diagnostics.Redactor()
        self.assertEqual(first.identity("device"), first.identity("device"))
        self.assertNotEqual(first.identity("device"), second.identity("device"))


if __name__ == "__main__":
    unittest.main()
