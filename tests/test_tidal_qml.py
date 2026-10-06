"""Execute native-client presentation helpers; real controls have a Qt gate."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "needs Node for QML JavaScript helper")
class TidalPresentation(unittest.TestCase):
    def evaluate(self, expression):
        script = (ROOT / "plasma/TidalState.js").read_text().replace(".pragma library", "")
        result = subprocess.run(["node", "-e", script + "\nconsole.log(JSON.stringify(" + expression + "));"],
                                check=True, capture_output=True, text=True, timeout=5)
        return json.loads(result.stdout)

    def test_signin_url_is_only_tidals_https_device_auth_site(self):
        accepted = ["https://link.tidal.com/ABCD", "https://login.tidal.com/?code=ABCD", "https://tidal.com"]
        rejected = ["http://link.tidal.com/code", "https://link.tidal.com.evil/code",
                    "https://link.tidal.com@evil/code", "https://evil@link.tidal.com/code",
                    "https://link.tidal.com:443/code", "javascript:alert(1)",
                    "https://link.tidal.com\\@evil/code", "https://link.tidal.com/\ncode", None]
        for value in accepted:
            self.assertEqual(self.evaluate("loginUrl(" + json.dumps(value) + ")"), value)
        for value in rejected:
            self.assertEqual(self.evaluate("loginUrl(" + json.dumps(value) + ")"), "")

    def test_artwork_accepts_only_public_identifier_paths(self):
        url = "https://resources.tidal.com/images/12345678/1234/1234/1234/123456789abc/320x320.jpg"
        self.assertEqual(self.evaluate("artworkUrl(" + json.dumps(url) + ")"), url)
        for value in (url + "?token=secret", url.replace("resources.tidal.com", "evil.example"),
                      "file:///home/user/private.png", "https://resources.tidal.com/private.jpg", None):
            self.assertEqual(self.evaluate("artworkUrl(" + json.dumps(value) + ")"), "")

    def test_catalog_badge_does_not_make_an_artist_playable(self):
        self.assertFalse(self.evaluate("playable({kind:'artist',reference:'',catalogue_atmos:true})"))
        self.assertFalse(self.evaluate("playable({kind:'folder',reference:'',id:'a-folder'})"))
        self.assertTrue(self.evaluate("playable({kind:'album',reference:'tidal:album:12'})"))
        self.assertFalse(self.evaluate("playable({kind:'track',reference:'https://example.com'})"))

    def test_pages_and_malformed_root_values_remain_bounded_arrays_or_objects(self):
        self.assertEqual(self.evaluate("items({tracks:[{id:'42'}]},'tracks')"), [{"id": "42"}])
        self.assertEqual(self.evaluate("items({items:[{id:'12'}]},'albums')"), [{"id": "12"}])
        self.assertEqual(self.evaluate("items({tracks:'text'},'tracks')"), [])
        for value in (None, [], True, "text", 1):
            self.assertEqual(self.evaluate("object(" + json.dumps(value) + ")"), {})

    def test_decoded_format_uses_observed_audio_not_requested_or_catalog_quality(self):
        audio = {"running": True, "loaded": True, "codec": "aac", "sample_rate": 44100,
                 "channels": 2, "bitrate": 96340, "requested_quality": "max", "catalogue_atmos": True}
        self.assertEqual(self.evaluate("decodedFormat(" + json.dumps(audio) + ")"),
                         "AAC · 44.1 kHz · 2 ch · 96 kbps")
        audio = {"running": True, "loaded": True, "codec": "flac",
                 "audio_parameters": {"samplerate": 192000, "channel-count": 2, "format": "floatp"}}
        # Decoded float samples do not establish source bit depth or bitrate.
        self.assertEqual(self.evaluate("decodedFormat(" + json.dumps(audio) + ")"), "FLAC · 192 kHz · 2 ch")

    def test_decoded_format_hides_stale_or_unavailable_decoder_properties(self):
        for audio in ({"running": False, "loaded": True, "codec": "flac"},
                      {"running": True, "loaded": False, "codec": "aac"}, None):
            self.assertEqual(self.evaluate("decodedFormat(" + json.dumps(audio) + ")"), "")
        audio = {"running": True, "loaded": True, "codec": "aac", "sample_rate": "48000",
                 "bitrate": -1, "channels": None, "audio_parameters": []}
        self.assertEqual(self.evaluate("decodedFormat(" + json.dumps(audio) + ")"), "AAC")

    def test_pagination_uses_raw_server_offset_for_filtered_playlist_pages(self):
        self.assertEqual(self.evaluate("nextOffset({offset:50,limit:20,next_offset:57})"), 57)
        self.assertEqual(self.evaluate("nextOffset({offset:50,limit:20})"), 70)
        for value in (50, 0, "57", None, -1):
            self.assertEqual(self.evaluate("nextOffset({offset:50,limit:20,next_offset:" + json.dumps(value) + "})"), 70)


if __name__ == "__main__":
    unittest.main()
