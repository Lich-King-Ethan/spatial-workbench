"""Mini-client boundaries with fake providers, never account entitlement proof."""
import json
import logging
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from spatial.tidal import TidalError, TidalProvider, _private_provider_logs, _read_session, prepare_stream
from tests.test_tidal import MPD, bts, login, restored_provider, session, stream


UUID = "11111111-2222-3333-4444-555555555555"


def track(identifier=123):
    return SimpleNamespace(id=identifier, name="A song", artist=SimpleNamespace(name="An artist"),
                           album=SimpleNamespace(name="An album", cover=UUID), duration=180,
                           is_dolby_atmos=True, access_token="must-not-escape",
                           signed_url="https://cdn.example/?token=must-not-escape")


class DeviceAuthorization(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "session.json"
        self.now = [100.0]
        self.fake = session("new-device-token")
        self.fake.request_session = Mock()
        self.fake.get_link_login = Mock(return_value=SimpleNamespace(
            verification_uri_complete="link.tidal.com/ABCD", user_code="ABCD",
            device_code="private-device-code", expires_in=300, interval=5))
        self.fake.process_link_login = Mock(return_value=True)
        self.fake.check_login = Mock(return_value=True)
        self.provider = TidalProvider(self.path, login_session_factory=lambda: self.fake,
                                      clock=lambda: self.now[0])

    def start(self):
        result = self.provider.login_start()
        self.assertEqual(result["state"], "pending")
        self.now[0] += 5
        return result["login_id"]

    def test_device_flow_is_explicit_and_public_poll_never_exposes_credentials(self):
        with patch("spatial.tidal.webbrowser.open") as browser:
            initial = self.provider.session_status()
            self.assertEqual(initial["state"], "signed_out")
            self.fake.get_link_login.assert_not_called()
            started = self.provider.login_start()
            self.assertEqual(started["verification_url"], "https://link.tidal.com/ABCD")
            self.assertEqual(started["user_code"], "ABCD")
            self.assertFalse(self.path.exists())
            self.assertEqual(self.provider.login_poll(started["login_id"])["state"], "pending")
            self.fake.process_link_login.assert_not_called()
            self.now[0] += 5
            completed = self.provider.login_poll(started["login_id"])
            self.assertEqual(completed["state"], "signed_in")
            self.fake.process_link_login.assert_called_once_with(
                self.fake.get_link_login.return_value, until_expiry=False)
            self.assertEqual(_read_session(self.path)["access_token"], "new-device-token")
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("verification_url", completed)
            public = json.dumps([initial, started, completed, self.provider.session_status()])
            self.assertNotIn("private-device-code", public)
            self.assertNotIn("new-device-token", public)
            self.assertNotIn("refresh", public)
            browser.assert_not_called()
            self.fake.request_session.close.assert_called_once()

    def test_pending_single_poll_obeys_interval_then_expires_without_extra_request(self):
        identifier = self.start()
        self.fake.process_link_login.side_effect = TimeoutError("provider private response")
        self.assertEqual(self.provider.login_poll(identifier)["state"], "pending")
        self.assertEqual(self.provider.login_poll(identifier)["state"], "pending")
        self.assertEqual(self.fake.process_link_login.call_count, 1)
        self.now[0] = 401
        self.assertEqual(self.provider.login_poll(identifier)["state"], "expired")
        self.assertEqual(self.fake.process_link_login.call_count, 1)
        self.assertFalse(self.path.exists())

    def test_browser_handoff_recovers_the_same_pending_code_without_network_or_disk_secrets(self):
        self.assertEqual(self.provider.login_status()["state"], "idle")
        started = self.provider.login_start()
        self.now[0] += 20
        recovered = self.provider.login_status()
        self.assertEqual(recovered["login_id"], started["login_id"])
        self.assertEqual(recovered["verification_url"], started["verification_url"])
        self.assertEqual(recovered["user_code"], started["user_code"])
        self.assertEqual(recovered["expires_in"], 280)
        self.fake.get_link_login.assert_called_once()
        self.fake.process_link_login.assert_not_called()
        self.assertFalse(self.path.exists())
        self.assertNotIn("private-device-code", json.dumps(recovered))
        self.assertNotIn("new-device-token", json.dumps(recovered))
        self.assertNotIn("ABCD", json.dumps(self.provider.session_status()))
        self.assertEqual(self.provider.login_poll(recovered["login_id"])["state"], "signed_in")
        terminal = self.provider.login_status()
        self.assertEqual(terminal["state"], "signed_in")
        self.assertNotIn("verification_url", terminal)
        self.assertNotIn("user_code", terminal)
        self.assertIsNone(self.provider._login.link)

    def test_hidden_authorization_expires_locally_and_can_be_restarted_directly(self):
        for recover_before_restart in (False, True):
            with self.subTest(recover_before_restart=recover_before_restart):
                fake = session()
                fake.request_session = Mock()
                fake.get_link_login = self.fake.get_link_login
                provider = TidalProvider(self.path, login_session_factory=lambda: fake,
                                         clock=lambda: self.now[0])
                started = provider.login_start()
                self.now[0] += 301
                if recover_before_restart:
                    expired = provider.login_status()
                    self.assertEqual(expired["state"], "expired")
                    self.assertNotIn("user_code", expired)
                    self.assertNotIn("verification_url", expired)
                    self.assertIsNone(provider._login.link)
                restarted = provider.login_start()
                self.assertEqual(restarted["state"], "pending")
                self.assertNotEqual(restarted["login_id"], started["login_id"])
                fake.request_session.close.assert_called_once()
                self.assertFalse(self.path.exists())

    def test_expiring_recovery_during_poll_does_not_save_or_allow_another_worker(self):
        identifier = self.start()
        entered, release = threading.Event(), threading.Event()
        result = []

        def polling(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return True

        self.fake.process_link_login.side_effect = polling
        worker = threading.Thread(target=lambda: result.append(self.provider.login_poll(identifier)), daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.now[0] = 401
            expired = self.provider.login_status()
            self.assertEqual(expired["state"], "expired")
            self.assertNotIn("verification_url", expired)
            self.fake.request_session.close.assert_not_called()
            with self.assertRaisesRegex(TidalError, "still finishing"):
                self.provider.login_start()
            self.fake.get_link_login.assert_called_once()
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]["state"], "expired")
        self.assertIsNone(self.provider._login.link)
        self.fake.request_session.close.assert_called_once()
        self.assertFalse(self.path.exists())

    def test_duplicate_start_and_stale_cancel_cannot_replace_pending_login(self):
        identifier = self.start()
        with self.assertRaisesRegex(TidalError, "already pending"):
            self.provider.login_start()
        with self.assertRaises(TidalError):
            self.provider.login_cancel("old-request")
        self.assertEqual(self.provider.login_poll(identifier)["state"], "signed_in")

    def test_cancel_during_single_poll_cannot_persist_authorization(self):
        identifier = self.start()
        entered, release = threading.Event(), threading.Event()
        result = []

        def polling(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return True

        self.fake.process_link_login.side_effect = polling
        worker = threading.Thread(target=lambda: result.append(self.provider.login_poll(identifier)), daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.provider.login_cancel(identifier)["state"], "cancelled")
            self.fake.request_session.close.assert_not_called()
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]["state"], "cancelled")
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.fake.access_token)
        self.fake.request_session.close.assert_called_once()

    def test_cancel_during_start_does_not_expose_obsolete_verification_link(self):
        entered, release = threading.Event(), threading.Event()
        result = []
        link = self.fake.get_link_login.return_value

        def get_link():
            entered.set()
            self.assertTrue(release.wait(2))
            return link

        self.fake.get_link_login.side_effect = get_link
        worker = threading.Thread(target=lambda: result.append(self.provider.login_start()), daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            recovered = self.provider.login_status()
            self.assertEqual(recovered["state"], "starting")
            self.assertTrue(recovered["login_id"])
            self.assertNotIn("verification_url", recovered)
            self.assertEqual(self.provider.login_cancel(recovered["login_id"])["state"], "cancelled")
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]["state"], "cancelled")
        self.assertNotIn("verification_url", result[0])
        self.assertFalse(self.path.exists())

    def test_cancelled_auth_worker_must_finish_before_another_network_login_starts(self):
        entered, release = threading.Event(), threading.Event()
        result = []
        link = self.fake.get_link_login.return_value

        def get_link():
            entered.set()
            self.assertTrue(release.wait(2))
            return link

        self.fake.get_link_login.side_effect = get_link
        second = session("second-login-token")
        second.get_link_login = Mock(return_value=link)
        worker = threading.Thread(target=lambda: result.append(self.provider.login_start()), daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.provider.login_cancel()
            self.provider._login_session_factory = lambda: second
            with self.assertRaises(TidalError):
                self.provider.login_start()
            second.get_link_login.assert_not_called()
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]["state"], "cancelled")
        self.assertNotIn("verification_url", result[0])
        self.assertEqual(self.provider.login_start()["state"], "pending")

    def test_external_logout_even_without_existing_session_invalidates_pending_login(self):
        identifier = self.start()
        TidalProvider(self.path).logout()
        result = self.provider.login_poll(identifier)
        self.assertEqual(result["state"], "error")
        self.fake.process_link_login.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_external_login_during_network_poll_is_not_overwritten(self):
        identifier = self.start()

        def change_login(*args, **kwargs):
            login(self.path, "replacement-token")
            return True

        self.fake.process_link_login.side_effect = change_login
        self.assertEqual(self.provider.login_poll(identifier)["state"], "error")
        self.assertEqual(_read_session(self.path)["access_token"], "replacement-token")
        self.assertIsNone(self.fake.access_token)

    def test_external_logout_during_network_poll_cannot_restore_missing_session(self):
        identifier = self.start()

        def sign_out(*args, **kwargs):
            TidalProvider(self.path).logout()
            return True

        self.fake.process_link_login.side_effect = sign_out
        self.assertEqual(self.provider.login_poll(identifier)["state"], "error")
        self.assertFalse(self.path.exists())

    def test_expiry_while_successful_request_is_in_flight_does_not_commit_tokens(self):
        identifier = self.start()

        def finish_late(*args, **kwargs):
            self.now[0] = 401
            return True

        self.fake.process_link_login.side_effect = finish_late
        self.assertEqual(self.provider.login_poll(identifier)["state"], "expired")
        self.assertFalse(self.path.exists())

    def test_bad_service_link_and_timing_return_sanitized_errors(self):
        for field, value in (("verification_uri_complete", "https://evil.example/?token=secret"),
                             ("interval", float("nan")), ("expires_in", float("inf")),
                             ("user_code", "<script>")):
            with self.subTest(field=field):
                fake = session()
                fake.get_link_login = Mock(return_value=SimpleNamespace(
                    verification_uri_complete="link.tidal.com/ABCD", user_code="ABCD",
                    device_code="private", expires_in=300, interval=5))
                setattr(fake.get_link_login.return_value, field, value)
                result = TidalProvider(self.path, login_session_factory=lambda: fake).login_start()
                self.assertEqual(result["state"], "error")
                self.assertNotIn("secret", json.dumps(result))
                self.assertFalse(self.path.exists())

    def test_local_status_does_not_restore_or_contact_service_until_requested(self):
        provider, fake = restored_provider(self.path)
        fake.load_oauth_session.reset_mock()
        self.assertTrue(provider.session_status()["authenticated"])
        fake.load_oauth_session.assert_not_called()
        login(self.path)
        self.assertEqual(provider.session_status()["state"], "saved")
        fake.load_oauth_session.assert_not_called()
        self.assertTrue(provider.session_status(refresh=True)["authenticated"])
        fake.load_oauth_session.assert_called_once()
        TidalProvider(self.path).logout()
        self.assertEqual(provider.session_status()["state"], "signed_out")
        self.assertIsNone(fake.access_token)

    def test_upstream_response_bodies_do_not_reach_daemon_journal(self):
        logger = logging.getLogger("tidalapi")
        handlers, propagation = logger.handlers[:], logger.propagate
        try:
            _private_provider_logs()
            with self.assertNoLogs(level=logging.DEBUG):
                logging.getLogger("tidalapi.session").error("Login failed: access_token=private")
                logging.getLogger("tidalapi.request").warning("Request failed: signed_url=private")
        finally:
            logger.handlers[:] = handlers
            logger.propagate = propagation


class CatalogueNavigation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "session.json"
        self.provider, self.fake = restored_provider(self.path)
        self.album = SimpleNamespace(id=456, name="An album", artist=SimpleNamespace(name="An artist"),
                                     cover=UUID, num_tracks=12)
        self.artist = SimpleNamespace(id=789, name="An artist", picture=UUID)
        self.playlist = SimpleNamespace(id=UUID, name="My playlist", square_picture=UUID)

    def test_search_projects_four_types_and_uses_page_offsets(self):
        self.fake.search = Mock(return_value={"tracks": [track()], "albums": [self.album],
                                              "artists": [self.artist], "playlists": [self.playlist],
                                              "videos": [SimpleNamespace(secret="must-not-escape")]})
        result = self.provider.search_catalogue(" some music ", limit=1, offset=4)
        self.fake.search.assert_called_once_with("some music", limit=1, offset=4)
        self.assertEqual(result["tracks"][0]["reference"], "tidal:track:123")
        self.assertEqual(result["albums"][0]["reference"], "tidal:album:456")
        self.assertEqual(result["artists"][0]["reference"], "")
        self.assertEqual(result["playlists"][0]["reference"], "tidal:playlist:" + UUID)
        self.assertTrue(result["has_more"])
        self.assertTrue(result["tracks"][0]["catalogue_atmos"])
        self.assertNotIn("must-not-escape", json.dumps(result))
        self.assertNotIn("videos", result)
        self.assertTrue(result["tracks"][0]["artwork"].startswith("https://resources.tidal.com/images/"))

    def test_artwork_uses_only_public_image_identifiers(self):
        item = track()
        item.album.cover = "https://cdn.example/?token=must-not-escape"
        result = self.provider.catalogue_item(item, "track")
        self.assertEqual(result["artwork"], "")
        self.assertNotIn("must-not-escape", json.dumps(result))

    def test_album_playlist_and_artist_navigation_keep_pagination(self):
        for kind, item in (("album", self.album), ("playlist", self.playlist), ("artist", self.artist)):
            with self.subTest(kind=kind):
                item.tracks = Mock(return_value=[track()])
                item.get_top_tracks = Mock(return_value=[track()])
                item.get_albums = Mock(return_value=[self.album])
                setattr(self.fake, kind, Mock(return_value=item))
                result = self.provider.collection(kind, str(item.id), limit=10, offset=20)
                self.assertEqual(result["item"]["kind"], kind)
                self.assertEqual(result["tracks"][0]["id"], "123")
                self.assertFalse(result["has_more"])
                method = item.get_top_tracks if kind == "artist" else item.tracks
                method.assert_called_once_with(limit=10, offset=20)
                if kind == "artist":
                    item.get_albums.assert_called_once_with(limit=10, offset=20)
                    self.assertEqual(result["albums"][0]["id"], "456")

    def test_library_four_kinds_use_bounded_supported_favorites_calls(self):
        self.fake.user = SimpleNamespace(favorites=SimpleNamespace())
        for kind, item in (("tracks", track()), ("albums", self.album),
                           ("artists", self.artist), ("playlists", self.playlist)):
            with self.subTest(kind=kind):
                method = Mock(return_value=[item])
                setattr(self.fake.user.favorites, kind, method)
                result = self.provider.library(kind, limit=50, offset=50)
                method.assert_called_once_with(limit=50, offset=50)
                self.assertEqual(result["items"][0]["id"], str(item.id))
                self.assertFalse(result["has_more"])

    def test_invalid_inputs_do_not_contact_provider(self):
        self.fake.search = Mock()
        for kwargs in ({"query": ""}, {"query": "x", "limit": 51},
                       {"query": "x", "offset": -1}, {"query": "x", "limit": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(TidalError):
                self.provider.search_catalogue(**kwargs)
        self.fake.search.assert_not_called()
        with self.assertRaises(TidalError):
            self.provider.collection("album", "../private")
        with self.assertRaises(TidalError):
            self.provider.library("delete_account")

    def test_stale_catalogue_results_cannot_survive_external_logout(self):
        def logout_result(result):
            TidalProvider(self.path).logout()
            return result

        self.fake.search = Mock(side_effect=lambda *args, **kwargs: logout_result({"tracks": [track()]}))
        with self.assertRaises(TidalError):
            self.provider.search_catalogue("a song")
        self.assertFalse(self.path.exists())
        self.assertFalse(self.provider._authenticated)


class LosslessSelection(unittest.TestCase):
    def test_explicit_lossless_accepts_only_lossless_audio_codecs(self):
        for codec in ("FLAC", "ALAC"):
            with self.subTest(codec=codec), prepare_stream(bts(codec=codec, mode="STEREO"),
                    require_atmos=False, require_lossless=True) as prepared:
                self.assertEqual(prepared.metadata["codecs"], [codec])
        for codec in ("AAC", "MP4A", "MP3"):
            with self.subTest(codec=codec), self.assertRaisesRegex(TidalError, "no lossless"):
                prepare_stream(bts(codec=codec, mode="STEREO"), require_atmos=False, require_lossless=True)

    def test_lossless_rejects_mixed_dash_representations_and_atmos_fallback(self):
        mixed = MPD.replace('codecs="ec-3"', 'codecs="flac"').replace(
            '</AdaptationSet>', '<Representation id="2" codecs="mp4a"/></AdaptationSet>')
        with self.assertRaisesRegex(TidalError, "no lossless"):
            prepare_stream(stream(mixed, mode="STEREO"), require_atmos=False, require_lossless=True)
        with self.assertRaisesRegex(TidalError, "no lossless"):
            prepare_stream(bts(), require_atmos=False, require_lossless=True)
        with self.assertRaisesRegex(TidalError, "not both"):
            prepare_stream(bts(), require_atmos=True, require_lossless=True)

    def test_legacy_stereo_inspection_retains_explicit_best_available_behavior(self):
        with prepare_stream(bts(codec="AAC", mode="STEREO"), require_atmos=False) as prepared:
            self.assertEqual(prepared.metadata["codecs"], ["AAC"])
            self.assertFalse(prepared.metadata["atmos_manifest"])

    def test_provider_rejects_lossy_response_without_second_stream_request(self):
        with tempfile.TemporaryDirectory() as temp:
            provider, fake = restored_provider(Path(temp) / "session.json")
            item = track()
            item.get_stream = Mock(return_value=bts(codec="AAC", mode="STEREO"))
            fake.track = Mock(return_value=item)
            with self.assertRaisesRegex(TidalError, "no lossless"):
                provider.prepare("123", require_atmos=False, require_lossless=True)
            self.assertEqual(fake.config.quality, "HI_RES_LOSSLESS")
            item.get_stream.assert_called_once()


if __name__ == "__main__":
    unittest.main()
