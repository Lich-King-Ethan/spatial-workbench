import base64
import fcntl
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
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


def session(token="access-secret"):
    value = SimpleNamespace(token_type="Bearer", access_token=token, refresh_token="refresh-" + token,
                            expiry_time=datetime(2030, 1, 1, tzinfo=timezone.utc), is_pkce=False,
                            config=SimpleNamespace(quality="HIGH"))

    def restore(**credentials):
        for key, item in credentials.items():
            setattr(value, key, item)
        return True

    value.load_oauth_session = Mock(side_effect=restore)
    return value


def login(path, token="new-access-secret"):
    fake = session(token)
    fake.get_link_login = Mock(return_value=SimpleNamespace(verification_uri_complete="link.tidal.com/ABCD"))
    fake.process_link_login = Mock(return_value=True)
    fake.check_login = Mock(return_value=True)
    TidalProvider(path, session=fake).login(lambda _: None, open_browser=False)


def restored_provider(path):
    fake = session()
    _save_session(path, fake)
    provider = TidalProvider(path, session=fake)
    provider.restore()
    return provider, fake


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
            lock = path.with_name(path.name + ".lock")
            self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
            inode = lock.stat().st_ino
            _save_session(path, session())
            self.assertEqual(lock.stat().st_ino, inode)

    def test_unsafe_lock_files_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.json"
            lock = path.with_name(path.name + ".lock")
            target = Path(temp) / "unrelated"
            target.write_text("keep")
            lock.symlink_to(target)
            with self.assertRaisesRegex(TidalError, "lock"):
                _save_session(path, session())
            self.assertEqual(target.read_text(), "keep")
            lock.unlink()
            lock.write_text("")
            lock.chmod(0o644)
            with self.assertRaisesRegex(TidalError, "600"):
                TidalProvider(path).logout()

    def test_logout_invalidates_warm_provider_before_another_service_request(self):
        for operation in ("search", "prepare", "album"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "session.json"
                provider, fake = restored_provider(path)
                fake.search, fake.track, fake.album = Mock(), Mock(), Mock()
                TidalProvider(path).logout()
                with self.assertRaisesRegex(TidalError, "tidal login"):
                    if operation == "search":
                        provider.search("song")
                    elif operation == "prepare":
                        provider.prepare("123")
                    else:
                        provider.track_ids("tidal:album:123")
                fake.search.assert_not_called()
                fake.track.assert_not_called()
                fake.album.assert_not_called()
                self.assertFalse(path.exists())
                self.assertFalse(provider._authenticated)
                self.assertIsNone(fake.access_token)
                self.assertIsNone(fake.refresh_token)

    def test_new_login_reloads_warm_provider_instead_of_overwriting_new_tokens(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.json"
            provider, fake = restored_provider(path)
            fake.search = Mock(return_value={"tracks": []})
            login(path)
            provider.search("song")
            self.assertEqual(fake.load_oauth_session.call_count, 2)
            self.assertEqual(fake.access_token, "new-access-secret")
            self.assertEqual(_read_session(path)["access_token"], "new-access-secret")
            provider.search("song")
            self.assertEqual(fake.load_oauth_session.call_count, 2)

    def test_changed_permissions_invalidate_warm_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.json"
            provider, fake = restored_provider(path)
            fake.search = Mock()
            path.chmod(0o644)
            with self.assertRaisesRegex(TidalError, "600"):
                provider.search("song")
            fake.search.assert_not_called()
            self.assertFalse(provider._authenticated)

    def test_external_login_or_logout_during_service_work_rejects_stale_results(self):
        for operation in ("restore", "search", "prepare", "album"):
            for change in ("logout", "login"):
                with self.subTest(operation=operation, change=change), tempfile.TemporaryDirectory() as temp:
                    path = Path(temp) / "session.json"
                    provider, fake = restored_provider(path)

                    def change_login(result):
                        if change == "logout":
                            TidalProvider(path).logout()
                        else:
                            login(path)
                        return result

                    if operation == "restore":
                        load = fake.load_oauth_session.side_effect
                        fake.load_oauth_session.side_effect = lambda **kw: change_login(load(**kw))
                        request = provider.restore
                    elif operation == "search":
                        fake.search = Mock(side_effect=lambda *a, **kw: change_login({"tracks": []}))
                        request = lambda: provider.search("song")
                    elif operation == "prepare":
                        track = SimpleNamespace(id=123, name="Song", get_stream=Mock(
                            side_effect=lambda: change_login(bts())))
                        fake.track = Mock(return_value=track)
                        request = lambda: provider.prepare("123")
                    else:
                        collection = SimpleNamespace(tracks=Mock(
                            side_effect=lambda **kw: change_login([SimpleNamespace(id=123)])))
                        fake.album = Mock(return_value=collection)
                        request = lambda: provider.track_ids("tidal:album:123")
                    with self.assertRaises(TidalError):
                        request()
                    self.assertFalse(provider._authenticated)
                    self.assertIsNone(fake.access_token)
                    if change == "logout":
                        self.assertFalse(path.exists())
                    else:
                        self.assertEqual(_read_session(path)["access_token"], "new-access-secret")
                    self.assertEqual(list(path.parent.glob(".session-*")), [])

    def test_logout_during_dash_preparation_removes_stale_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.json"
            provider, fake = restored_provider(path)
            fake.track = Mock(return_value=SimpleNamespace(
                id=123, name="Song", get_stream=Mock(return_value=stream(MPD))))
            artifacts = []

            def prepare_then_logout(*args, **kwargs):
                prepared = prepare_stream(*args, **kwargs)
                artifacts.append(Path(prepared.media))
                TidalProvider(path).logout()
                return prepared

            with patch("spatial.tidal.prepare_stream", side_effect=prepare_then_logout):
                with self.assertRaisesRegex(TidalError, "tidal login"):
                    provider.prepare("123")
            self.assertEqual(len(artifacts), 1)
            self.assertFalse(artifacts[0].exists())
            self.assertFalse(artifacts[0].parent.exists())
            self.assertFalse(path.exists())

    def test_logout_cannot_slip_between_generation_check_and_atomic_replace(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.json"
            provider, fake = restored_provider(path)
            fake.search = Mock(return_value={"tracks": []})
            attempting = threading.Event()
            finished = threading.Event()
            failures = []
            original_flock, original_replace = fcntl.flock, os.replace

            def logout():
                try:
                    TidalProvider(path).logout()
                except Exception as exc:
                    failures.append(exc)
                finally:
                    finished.set()

            worker = threading.Thread(target=logout, name="test-tidal-logout")

            def flock(fd, operation):
                if threading.current_thread() is worker and operation == fcntl.LOCK_EX:
                    attempting.set()
                return original_flock(fd, operation)

            def replace(source, destination):
                worker.start()
                self.assertTrue(attempting.wait(2), "logout did not attempt the shared lock")
                self.assertFalse(finished.is_set(), "logout bypassed the credential transaction")
                return original_replace(source, destination)

            try:
                with patch("spatial.tidal.fcntl.flock", side_effect=flock), \
                        patch("spatial.tidal.os.replace", side_effect=replace):
                    provider.search("song")
            finally:
                if worker.ident is not None:
                    worker.join(2)
            self.assertTrue(finished.is_set())
            self.assertEqual(failures, [])
            self.assertFalse(path.exists())

    def test_overlapping_workers_do_not_replace_an_inflight_requests_session(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.json"
            provider, fake = restored_provider(path)
            started, release, second_entered = (threading.Event() for _ in range(3))
            results = {}
            observed_tokens = []
            operation_lock = provider._operation_lock

            @contextmanager
            def observed_lock():
                if threading.current_thread() is second:
                    second_entered.set()
                with operation_lock:
                    yield

            # Observe arrival without scheduler sleeps; each thread's context
            # still acquires the real provider mutex.
            contexts = threading.local()

            class WorkerLock:
                def __enter__(self):
                    contexts.lock = observed_lock()
                    return contexts.lock.__enter__()

                def __exit__(self, *args):
                    return contexts.lock.__exit__(*args)

            provider._operation_lock = WorkerLock()

            def search(query, **kwargs):
                observed_tokens.append(fake.access_token)
                if query == "old request":
                    started.set()
                    self.assertTrue(release.wait(2))
                return {"tracks": []}

            def request(query):
                try:
                    results[query] = provider.search(query)
                except Exception as exc:
                    results[query] = exc

            fake.search = Mock(side_effect=search)
            first = threading.Thread(target=request, args=("old request",), daemon=True)
            second = threading.Thread(target=request, args=("new request",), daemon=True)
            try:
                first.start()
                self.assertTrue(started.wait(2))
                login(path)
                second.start()
                self.assertTrue(second_entered.wait(2))
                self.assertEqual(observed_tokens, ["access-secret"])
            finally:
                release.set()
                first.join(2)
                if second.ident is not None:
                    second.join(2)
            self.assertIsInstance(results["old request"], TidalError)
            self.assertEqual(results["new request"], [])
            self.assertEqual(observed_tokens, ["access-secret", "new-access-secret"])
            self.assertEqual(_read_session(path)["access_token"], "new-access-secret")

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
        with tempfile.TemporaryDirectory() as temp:
            provider, fake = restored_provider(Path(temp) / "session.json")
            fake.track = Mock(side_effect=RuntimeError("https://cdn.example?token=secret"))
            with self.assertRaises(TidalError) as failure:
                provider.prepare("123")
            fake.track.assert_called_once()
            self.assertNotIn("secret", str(failure.exception))

    def test_album_pagination(self):
        with tempfile.TemporaryDirectory() as temp:
            provider, fake = restored_provider(Path(temp) / "session.json")
            first = [SimpleNamespace(id=x) for x in range(100)]
            collection = SimpleNamespace(tracks=Mock(side_effect=[first, [SimpleNamespace(id=100)]]))
            fake.album = Mock(return_value=collection)
            self.assertEqual(len(provider.track_ids("tidal:album:123")), 101)
            collection.tracks.assert_any_call(limit=100, offset=100)

    def test_status_without_session_is_clean_failure(self):
        with tempfile.TemporaryDirectory() as temp, redirect_stderr(io.StringIO()) as errors, redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--state", str(Path(temp) / "missing"), "status"]), 1)
            self.assertIn("tidal login", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
