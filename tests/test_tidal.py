import base64
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from spatial.tidal import (TidalError, TidalProvider, _read_session, _save_session,
                           main, parse_reference, prepare_stream)


def bts(*, codec="EAC3", mode="DOLBY_ATMOS", encryption="NONE", url="https://cdn.example/audio?token=secret"):
    value = {"codecs": codec, "encryptionType": encryption, "urls": [url]}
    return stream(json.dumps(value), "application/vnd.tidal.bts", mode)


def stream(text, mime="application/dash+xml", mode="DOLBY_ATMOS"):
    return SimpleNamespace(manifest=base64.b64encode(text.encode()).decode(),
                           manifest_mime_type=mime, audio_mode=mode, audio_quality="HIGH")


MPD = '''<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>
  <AdaptationSet codecs="ec-3" mimeType="audio/mp4">
    <Representation id="1"><BaseURL>https://cdn.example/music/</BaseURL>
      <SegmentTemplate initialization="init.mp4?token=secret" media="$Number$.m4s?token=secret"/>
    </Representation>
  </AdaptationSet>
</Period></MPD>'''


def session():
    return SimpleNamespace(token_type="Bearer", access_token="access-secret", refresh_token="refresh-secret",
                           expiry_time=datetime(2030, 1, 1, tzinfo=timezone.utc), is_pkce=False,
                           config=SimpleNamespace(quality="HIGH"))


class ManifestChecks(unittest.TestCase):
    def test_original_clear_eac3_url_is_preserved_without_secret_in_metadata(self):
        with prepare_stream(bts(), {"title": "Track"}) as prepared:
            self.assertEqual(prepared.media, "https://cdn.example/audio?token=secret")
            self.assertTrue(prepared.metadata["atmos_manifest"])
            self.assertFalse(prepared.metadata["renderer_confirmed_atmos"])
            self.assertNotIn("secret", repr(prepared))
            self.assertNotIn("secret", json.dumps(prepared.metadata))

    def test_dash_bytes_preserved_and_private_temporary_files_cleaned(self):
        with prepare_stream(stream(MPD)) as prepared:
            path = Path(prepared.media)
            self.assertEqual(path.read_text(), MPD)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertFalse(path.exists())

    def test_stereo_is_not_substituted_for_atmos(self):
        with self.assertRaisesRegex(TidalError, "no Atmos"):
            prepare_stream(bts(codec="FLAC", mode="STEREO"))
        with prepare_stream(bts(codec="FLAC", mode="STEREO"), require_atmos=False) as result:
            self.assertFalse(result.metadata["atmos_manifest"])

    def test_catalogue_atmos_cannot_upgrade_stereo(self):
        with self.assertRaises(TidalError):
            prepare_stream(bts(codec="AAC", mode="STEREO"), {"catalogue_atmos": True})

    def test_atmos_flag_alone_does_not_prove_audio_codec(self):
        for codec in ("AAC", "FLAC", "AC4"):
            with self.subTest(codec=codec), self.assertRaisesRegex(TidalError, "codec"):
                prepare_stream(bts(codec=codec))

    def test_codec_alone_does_not_prove_atmos_objects(self):
        with self.assertRaisesRegex(TidalError, "no Atmos"):
            prepare_stream(bts(codec="EAC3", mode="STEREO"))

    def test_bts_encryption_fails_closed(self):
        for encryption in ("AES", "OLD_AES", "WIDEVINE", None):
            with self.subTest(encryption=encryption), self.assertRaisesRegex(TidalError, "encrypted"):
                prepare_stream(bts(encryption=encryption))

    def test_dash_drm_is_never_passed_to_renderer(self):
        protected = MPD.replace('<Representation id="1">', '<Representation id="1"><ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011"/>')
        with self.assertRaisesRegex(TidalError, "protected audio"):
            prepare_stream(stream(protected))

    def test_unsafe_media_protocols_rejected(self):
        for url in ("http://cdn.example/x", "file:///etc/passwd", "https://user:pass@cdn.example/x",
                    "https://cdn.example:123/x", "https://cdn.example/x\n--bad"):
            with self.subTest(url=url), self.assertRaises(TidalError):
                prepare_stream(bts(url=url))
        with self.assertRaises(TidalError):
            prepare_stream(stream(MPD.replace('media="$Number$.m4s?token=secret"', 'media="file:///etc/passwd"')))

    def test_entity_and_remote_reference_rejected(self):
        with self.assertRaises(TidalError):
            prepare_stream(stream('<!DOCTYPE foo [<!ENTITY x "abc">]>' + MPD))
        with self.assertRaises(TidalError):
            prepare_stream(stream(MPD.replace('<Period>', '<Period xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="https://example.org/external.xml">')))

    def test_unknown_and_malformed_manifests_have_sanitized_errors(self):
        invalid = [stream("bad secret"), stream("{}", "application/vnd.tidal.bts"),
                   stream("signed secret", "application/unknown"),
                   SimpleNamespace(manifest="not base64: secret", manifest_mime_type="application/dash+xml")]
        for value in invalid:
            with self.subTest(value=value):
                try:
                    prepare_stream(value)
                except TidalError as exc:
                    self.assertNotIn("secret", str(exc))
                else:
                    self.fail("bad manifest accepted")

    def test_mixed_codec_representations_cannot_be_claimed_as_atmos(self):
        mixed = MPD.replace('</AdaptationSet>', '<Representation id="2" codecs="mp4a.40.2"><BaseURL>https://cdn.example/stereo.mp4</BaseURL></Representation></AdaptationSet>')
        with self.assertRaisesRegex(TidalError, "codec"):
            prepare_stream(stream(mixed))


class Authentication(unittest.TestCase):
    def test_atomic_session_permissions_and_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "private/session.json"
            _save_session(path, session())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            restored = _read_session(path)
            self.assertEqual(restored["access_token"], "access-secret")
            self.assertEqual(restored["expiry_time"], session().expiry_time)

    def test_public_or_symlink_token_files_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.json"
            _save_session(path, session())
            path.chmod(0o644)
            with self.assertRaisesRegex(TidalError, "600"):
                _read_session(path)
            link = Path(temp) / "link"
            link.symlink_to(path)
            with self.assertRaises(TidalError):
                _read_session(link)

    def test_logout_requires_no_optional_dependency(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.json"
            _save_session(path, session())
            with patch("spatial.tidal._new_session", side_effect=AssertionError("not required")):
                TidalProvider(path).logout()
            self.assertFalse(path.exists())

    def test_missing_login_does_not_start_a_browser_or_access_service(self):
        with tempfile.TemporaryDirectory() as temp, patch("spatial.tidal._new_session") as create:
            provider = TidalProvider(Path(temp) / "session.json")
            with self.assertRaisesRegex(TidalError, "tidal login"):
                provider.prepare("123")
            create.assert_not_called()

    def test_external_login_url_and_tokens_are_kept_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = session()
            fake.get_link_login = Mock(return_value=SimpleNamespace(verification_uri_complete="link.tidal.com/ABCD"))
            fake.process_link_login = Mock(return_value=True)
            fake.check_login = Mock(return_value=True)
            provider = TidalProvider(Path(temp) / "session.json", session=fake)
            shown = []
            with patch("webbrowser.open") as browser:
                provider.login(shown.append, open_browser=False)
            self.assertEqual(shown, ["https://link.tidal.com/ABCD"])
            browser.assert_not_called()
            self.assertNotIn("access-secret", str(shown))

    def test_login_service_redirect_is_validated_before_browser(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = session()
            fake.get_link_login = Mock(return_value=SimpleNamespace(verification_uri_complete="https://example.org/phish"))
            with patch("webbrowser.open") as browser, self.assertRaises(TidalError):
                TidalProvider(Path(temp) / "session.json", session=fake).login()
            browser.assert_not_called()


class SourceRequests(unittest.TestCase):
    def test_link_parsing_is_local_and_restricts_domains(self):
        for value in ("123", "tidal:track:123", "https://tidal.com/browse/track/123?u=ignored",
                      "https://listen.tidal.com/track/123"):
            self.assertEqual(parse_reference(value), ("track", "123"))
        self.assertEqual(parse_reference("https://tidal.com/album/456"), ("album", "456"))
        for value in ("0", "../track/123", "https://tidal.com.evil.test/track/123",
                      "https://user@tidal.com/track/123", "http://tidal.com/track/123",
                      "https://tidal.com:bad/track/123", "https://tidal.com:99999/track/123",
                      "https://[tidal.com/track/123"):
            with self.subTest(value=value), self.assertRaises(TidalError):
                parse_reference(value)

    def test_stream_request_uses_library_and_never_falls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = session()
            fake.load_oauth_session = Mock(return_value=True)
            track = SimpleNamespace(id=123, name="Song", is_dolby_atmos=True, get_stream=Mock(return_value=bts()))
            fake.track = Mock(return_value=track)
            path = Path(temp) / "session.json"
            _save_session(path, fake)
            provider = TidalProvider(path, session=fake)
            with provider.prepare("tidal:track:123") as prepared:
                self.assertTrue(prepared.metadata["atmos_manifest"])
            self.assertEqual(fake.config.quality, "DOLBY_ATMOS")
            fake.track.assert_called_once_with("123")
            fake.track.return_value.get_stream.return_value = bts(codec="FLAC", mode="STEREO")
            with self.assertRaisesRegex(TidalError, "no Atmos"):
                provider.prepare("123")
            self.assertEqual(track.get_stream.call_count, 2)

    def test_errors_never_include_response_body_or_tokenized_url(self):
        fake = session()
        fake.track = Mock(side_effect=RuntimeError("https://cdn.example?token=secret"))
        provider = TidalProvider(session=fake)
        provider._authenticated = True
        with self.assertRaises(TidalError) as failure:
            provider.prepare("123")
        self.assertNotIn("secret", str(failure.exception))

    def test_album_pagination(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = session()
            first = [SimpleNamespace(id=x) for x in range(100)]
            collection = SimpleNamespace(tracks=Mock(side_effect=[first, [SimpleNamespace(id=100)]]))
            fake.album = Mock(return_value=collection)
            provider = TidalProvider(Path(temp) / "session.json", session=fake)
            provider._authenticated = True
            self.assertEqual(len(provider.track_ids("tidal:album:123")), 101)
            collection.tracks.assert_any_call(limit=100, offset=100)

    def test_status_without_session_is_clean_failure(self):
        with tempfile.TemporaryDirectory() as temp, redirect_stderr(io.StringIO()) as errors, redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--state", str(Path(temp) / "missing"), "status"]), 1)
            self.assertIn("tidal login", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
