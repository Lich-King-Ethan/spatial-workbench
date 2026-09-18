"""Optional TIDAL source. Authentication never belongs to the audio daemon.

The source passes the original compressed stream to the renderer. It does not
decode audio, remove encryption, or infer Atmos from a catalogue badge.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import fcntl
import json
import os
import re
import stat
import sys
import tempfile
import threading
import webbrowser
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps
from pathlib import Path
from urllib.parse import urljoin, urlsplit


from .errors import PublicError


class TidalError(PublicError):
    """An actionable error without tokens, signed URLs, or response bodies."""


def default_state_path() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "spatiald/tidal/session.json"


def _private_directory(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise TidalError("TIDAL state directory must be a directory owned by this user")
    path.chmod(0o700)
    return path


@contextmanager
def _session_lock(path: Path):
    """Serialize local credential transactions, never HTTP or browser work.

    Keep the lock inode after logout so every process uses the same flock.
    """
    _private_directory(path.parent)
    try:
        fd = os.open(path.with_name(path.name + ".lock"),
                     os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                     0o600)
    except OSError:
        raise TidalError("Cannot safely lock TIDAL session") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise TidalError("TIDAL session lock must be owned by this user with permissions 600")
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


_UNCONDITIONAL = object()


def _save_session(path: Path, session, *, expected=_UNCONDITIONAL) -> tuple:
    expiry = session.expiry_time
    data = {"schema": 1, "token_type": session.token_type,
            "access_token": session.access_token, "refresh_token": session.refresh_token,
            "expiry_time": expiry.isoformat() if expiry else None,
            "is_pkce": bool(getattr(session, "is_pkce", False))}
    if not data["access_token"] or not data["token_type"]:
        raise TidalError("TIDAL did not return a usable session")
    with _session_lock(path):
        if expected is not _UNCONDITIONAL:
            _, current = _read_session_record(path)
            if current != expected:
                raise TidalError("TIDAL sign-in changed during this request; retry")
        fd, name = tempfile.mkstemp(prefix=".session-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(data, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
            # Capture the committed inode while locked; rename can change ctime.
            return _read_session_record(path)[1]
        finally:
            if os.path.exists(name):
                os.unlink(name)


def _read_session(path: Path) -> dict:
    with _session_lock(path):
        return _read_session_record(path)[0]


def _read_session_record(path: Path) -> tuple[dict, tuple]:
    """Read credentials and their exact file generation under _session_lock."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        raise TidalError("TIDAL is not signed in; run spatialctl tidal login") from None
    except OSError:
        raise TidalError("Cannot safely read TIDAL session; run spatialctl tidal login") from None
    with os.fdopen(fd, "r") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise TidalError("TIDAL session must be owned by this user with permissions 600")
        try:
            text = handle.read(65537)
            if len(text) > 65536:
                raise ValueError()
            data = json.loads(text)
            if data.get("schema") != 1:
                raise ValueError()
            for key in ("token_type", "access_token"):
                if not isinstance(data.get(key), str) or not data[key]:
                    raise ValueError()
            if data.get("refresh_token") is not None and not isinstance(data["refresh_token"], str):
                raise ValueError()
            if type(data.get("is_pkce")) is not bool:
                raise ValueError()
            if data.get("expiry_time"):
                data["expiry_time"] = datetime.fromisoformat(data["expiry_time"])
            credentials = {key: data.get(key) for key in (
                "token_type", "access_token", "refresh_token", "expiry_time", "is_pkce")}
            identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            return credentials, identity
        except (ValueError, TypeError, AttributeError):
            raise TidalError("Invalid TIDAL session; run spatialctl tidal login") from None


def _new_session():
    try:
        import requests
        import tidalapi
    except ImportError:
        raise TidalError("TIDAL support is not installed; install python-tidalapi or the project's tidal extra") from None

    class BoundedSession(requests.Session):
        def request(self, method, url, **kwargs):
            kwargs.setdefault("timeout", (10, 30))
            return super().request(method, url, **kwargs)

    session = tidalapi.Session(tidalapi.Config(quality="DOLBY_ATMOS"))
    session.request_session.close()
    session.request_session = BoundedSession()
    return session


def parse_reference(reference: str) -> tuple[str, str]:
    """Accept track IDs and canonical TIDAL links without fetching a URL."""
    value = str(reference).strip()
    if re.fullmatch(r"[1-9][0-9]{0,18}", value):
        return "track", value
    if value.startswith("tidal:"):
        parts = value.split(":")
        if len(parts) != 3:
            raise TidalError("Use a TIDAL track, album, or playlist link")
        kind, identifier = parts[1:]
    else:
        try:
            parsed = urlsplit(value)
            valid = (parsed.scheme == "https" and parsed.hostname in (
                "tidal.com", "www.tidal.com", "listen.tidal.com")
                and not parsed.username and not parsed.password and parsed.port in (None, 443))
        except ValueError:
            valid = False
        if not valid:
            raise TidalError("Use an HTTPS link from tidal.com or listen.tidal.com")
        parts = parsed.path.strip("/").split("/")
        if parts and parts[0] == "browse":
            parts = parts[1:]
        if len(parts) != 2:
            raise TidalError("Use a TIDAL track, album, or playlist link")
        kind, identifier = parts
    if kind in ("track", "album") and re.fullmatch(r"[1-9][0-9]{0,18}", identifier):
        return kind, identifier
    if kind == "playlist" and re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", identifier):
        return kind, identifier
    raise TidalError("Invalid TIDAL track, album, or playlist identifier")


def _https(value: str) -> str:
    if not isinstance(value, str) or any(ord(c) <= 32 for c in value):
        raise TidalError("TIDAL returned an invalid media URL")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.port not in (None, 443)):
            raise ValueError()
    except ValueError:
        raise TidalError("TIDAL returned a media URL without safe HTTPS transport") from None
    return value


def _codec(value: str) -> str:
    return str(value).upper().replace("-", "").split(".", 1)[0]


def _dash_codecs(text: str) -> set[str]:
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.IGNORECASE):
        raise TidalError("TIDAL returned an unsupported DASH document")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise TidalError("TIDAL returned an invalid DASH manifest") from None
    local = lambda tag: tag.rsplit("}", 1)[-1]
    if local(root.tag) != "MPD":
        raise TidalError("TIDAL did not return a DASH MPD")
    codecs = set()
    resources = 0

    def visit(element, base="", inherited_codec=""):
        nonlocal resources
        tag = local(element.tag)
        if tag == "ContentProtection":
            raise TidalError("TIDAL returned protected audio; this player does not handle DRM")
        if any(local(key) == "href" for key in element.attrib):
            raise TidalError("TIDAL returned an unsupported external DASH reference")
        current_codec = element.attrib.get("codecs", inherited_codec)
        base_urls = [child for child in element if local(child.tag) == "BaseURL"]
        if len(base_urls) > 1:
            raise TidalError("Multiple DASH base URLs are not supported")
        if base_urls:
            base = _https(urljoin(base, (base_urls[0].text or "").strip()))
            resources += 1
        for key in ("media", "initialization", "sourceURL"):
            if element.attrib.get(key):
                _https(urljoin(base, element.attrib[key]))
                resources += 1
        if tag == "Representation":
            if not current_codec:
                raise TidalError("TIDAL DASH representation has no codec")
            codecs.update(_codec(item.strip()) for item in current_codec.split(","))
        for child in element:
            if local(child.tag) != "BaseURL":
                visit(child, base, current_codec)

    visit(root)
    if not codecs or not resources:
        raise TidalError("TIDAL DASH manifest contains no usable audio representation")
    return codecs


@dataclass
class PreparedTrack:
    media: str = field(repr=False)
    metadata: dict
    _directory: tempfile.TemporaryDirectory | None = field(default=None, repr=False)

    def cleanup(self):
        if self._directory is not None:
            self._directory.cleanup()
            self._directory = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cleanup()


def prepare_stream(stream, metadata=None, *, require_atmos=True) -> PreparedTrack:
    """Validate a service response and preserve its compressed stream unchanged."""
    encoded = getattr(stream, "manifest", None)
    if not isinstance(encoded, str) or len(encoded) > 1_500_000:
        raise TidalError("TIDAL returned an invalid or oversized manifest")
    try:
        text = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise TidalError("TIDAL returned a malformed stream manifest") from None
    mime = str(getattr(stream, "manifest_mime_type", "")).split(";", 1)[0].lower()
    mode = str(getattr(stream, "audio_mode", ""))
    directory = None
    media = None
    if mime == "application/vnd.tidal.bts":
        try:
            content = json.loads(text)
            if content.get("encryptionType") != "NONE" or content.get("keyId"):
                raise TidalError("TIDAL returned encrypted audio; this player does not handle DRM")
            codecs = {_codec(content["codecs"])}
            urls = content["urls"]
            if not isinstance(urls, list) or not urls:
                raise ValueError()
            media = _https(urls[0])
        except (ValueError, KeyError, TypeError, AttributeError):
            raise TidalError("TIDAL returned an invalid BTS manifest") from None
    elif mime == "application/dash+xml":
        codecs = _dash_codecs(text)
    else:
        raise TidalError("TIDAL returned an unsupported manifest format")
    atmos = mode == "DOLBY_ATMOS" and codecs <= {"EAC3", "EC3"}
    if mode == "DOLBY_ATMOS" and not atmos:
        raise TidalError("TIDAL Atmos requires an E-AC-3 stream; this stream's codec is unsupported")
    if require_atmos and not atmos:
        raise TidalError("TIDAL returned no Atmos stream for this account and track; stereo was not substituted")
    if not atmos and not codecs <= {"FLAC", "AAC", "MP4A", "ALAC", "MP3"}:
        raise TidalError("TIDAL returned an unsupported audio codec")
    if media is None:
        directory = tempfile.TemporaryDirectory(prefix="spatial-tidal-")
        path = Path(directory.name) / "stream.mpd"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        media = str(path)
    details = dict(metadata or {})
    details.update({"source": "TIDAL", "audio_mode": mode, "codecs": sorted(codecs),
                    "quality": str(getattr(stream, "audio_quality", "")),
                    "atmos_manifest": atmos, "renderer_confirmed_atmos": False})
    return PreparedTrack(media, details, directory)


def _serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        # A cancelled asyncio.to_thread worker can outlive its caller. Keep a
        # second worker from replacing this provider's session mid-request.
        with self._operation_lock:
            return method(self, *args, **kwargs)
    return call


class TidalProvider:
    def __init__(self, state_path=None, *, session=None):
        self.state_path = Path(state_path) if state_path else default_state_path()
        self._session = session
        self._authenticated = False
        self._generation = None
        self._operation_lock = threading.RLock()

    @property
    def session(self):
        if self._session is None:
            self._session = _new_session()
        return self._session

    def _failure(self, action: str, exc: Exception) -> TidalError:
        if isinstance(exc, TidalError):
            return exc
        name = type(exc).__name__
        if action == "sign-in" and name == "TimeoutError":
            return TidalError("TIDAL sign-in code expired; run spatialctl tidal login again")
        if "Auth" in name or "Unauthorized" in name:
            return TidalError("TIDAL authorization failed; run spatialctl tidal login")
        if "TooManyRequests" in name:
            return TidalError("TIDAL rate limited this request; try again later")
        if "Timeout" in name or "Connection" in name:
            return TidalError(f"TIDAL {action} could not reach the service; check the connection and retry")
        return TidalError(f"TIDAL {action} failed ({name}); check subscription, track availability, and authorization")

    def _invalidate(self):
        self._authenticated = False
        self._generation = None
        if self._session is not None:
            self._session.access_token = None
            self._session.refresh_token = None

    def _restore(self, credentials, generation):
        self._invalidate()
        try:
            if not self.session.load_oauth_session(**credentials):
                raise TidalError("TIDAL session expired; run spatialctl tidal login")
            self._generation = _save_session(self.state_path, self.session, expected=generation)
            self._authenticated = True
            return True
        except Exception as exc:
            self._invalidate()
            raise self._failure("session restore", exc) from None

    @_serialized
    def restore(self) -> bool:
        try:
            with _session_lock(self.state_path):
                credentials, generation = _read_session_record(self.state_path)
            return self._restore(credentials, generation)
        except Exception as exc:
            self._invalidate()
            raise self._failure("session restore", exc) from None

    @_serialized
    def login(self, show_url=print, *, open_browser=True) -> bool:
        try:
            session = self.session
            # Public library API, synchronously polled so Ctrl-C does not leave a
            # background executor holding the command open until code expiry.
            link = session.get_link_login()
            url = str(link.verification_uri_complete)
            if not url.startswith("https://"):
                url = "https://" + url
            _https(url)
            if urlsplit(url).hostname not in ("link.tidal.com", "login.tidal.com", "tidal.com"):
                raise TidalError("TIDAL returned an unexpected sign-in website")
            show_url(url)
            if open_browser:
                webbrowser.open(url)
            if not session.process_link_login(link) or not session.check_login():
                raise TidalError("TIDAL sign-in did not finish; run spatialctl tidal login again")
            # An explicit completed login is the only unconditional replacement.
            self._generation = _save_session(self.state_path, session)
            self._authenticated = True
            return True
        except Exception as exc:
            self._invalidate()
            raise self._failure("sign-in", exc) from None

    @_serialized
    def logout(self):
        try:
            with _session_lock(self.state_path):
                self.state_path.unlink(missing_ok=True)
        finally:
            self._invalidate()

    def close(self):
        if self._session is not None:
            transport = getattr(self._session, "request_session", None)
            if transport:
                transport.close()

    def _ready(self):
        try:
            with _session_lock(self.state_path):
                credentials, generation = _read_session_record(self.state_path)
            if not self._authenticated or generation != self._generation:
                self._restore(credentials, generation)
        except Exception as exc:
            self._invalidate()
            raise self._failure("session restore", exc) from None

    def _save_authenticated(self):
        try:
            self._generation = _save_session(self.state_path, self.session, expected=self._generation)
        except Exception:
            self._invalidate()
            raise

    @staticmethod
    def track_metadata(track):
        return {"id": str(track.id), "title": str(track.name),
                "artist": str(getattr(getattr(track, "artist", None), "name", "")),
                "album": str(getattr(getattr(track, "album", None), "name", "")),
                "catalogue_atmos": bool(getattr(track, "is_dolby_atmos", False))}

    @_serialized
    def search(self, query: str, limit=20) -> list[dict]:
        if not query.strip() or len(query) > 512 or not 1 <= limit <= 100:
            raise TidalError("Search requires a nonempty query and limit between 1 and 100")
        self._ready()
        try:
            results = self.session.search(query, limit=limit)
            tracks = [self.track_metadata(track) for track in results.get("tracks", [])]
            self._save_authenticated()
            return tracks
        except Exception as exc:
            raise self._failure("search", exc) from None

    @_serialized
    def track_ids(self, reference: str) -> list[str]:
        kind, identifier = parse_reference(reference)
        if kind == "track":
            return [identifier]
        self._ready()
        try:
            collection = getattr(self.session, kind)(identifier)
            tracks = []
            offset = 0
            while True:
                batch = collection.tracks(limit=100, offset=offset)
                tracks.extend(str(track.id) for track in batch)
                if len(batch) < 100:
                    break
                offset += len(batch)
                if offset >= 10000:
                    raise TidalError("This collection exceeds the supported 10,000-track queue")
            self._save_authenticated()
            return tracks
        except Exception as exc:
            raise self._failure("collection lookup", exc) from None

    @_serialized
    def prepare(self, track_id: str, require_atmos=True) -> PreparedTrack:
        kind, identifier = parse_reference(track_id)
        if kind != "track":
            raise TidalError("Choose a track for playback, or expand the album/playlist into a queue")
        self._ready()
        prepared = None
        try:
            session = self.session
            session.config.quality = "DOLBY_ATMOS" if require_atmos else "HI_RES_LOSSLESS"
            track = session.track(identifier)
            stream = track.get_stream()
            prepared = prepare_stream(stream, self.track_metadata(track), require_atmos=require_atmos)
            self._save_authenticated()
            return prepared
        except Exception as exc:
            if prepared is not None:
                prepared.cleanup()
            raise self._failure("stream request", exc) from None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="spatialctl tidal", description="Optional TIDAL authentication and source")
    parser.add_argument("--state", type=Path, help="private session file (normally selected automatically)")
    sub = parser.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login", help="sign in through TIDAL in the external browser")
    login.add_argument("--no-browser", action="store_true", help="print the login link without launching a browser")
    sub.add_parser("logout", help="remove this application's saved login")
    sub.add_parser("status", help="verify the saved session without printing credentials")
    search = sub.add_parser("search", help="search tracks; catalogue Atmos badges are not playback proof")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)
    tracks = sub.add_parser("tracks", help="list track IDs in an album or playlist")
    tracks.add_argument("reference")
    inspect = sub.add_parser("inspect", help="check the actual stream manifest without starting playback")
    inspect.add_argument("track")
    inspect.add_argument("--stereo", action="store_true", help="explicitly request ordinary stereo instead of Atmos")
    args = parser.parse_args(argv)
    provider = TidalProvider(args.state)
    try:
        if args.command == "login":
            provider.login(lambda url: print("Complete TIDAL sign-in at " + url, flush=True),
                           open_browser=not args.no_browser)
            print("TIDAL sign-in complete.")
        elif args.command == "logout":
            provider.logout()
            print("Saved TIDAL login removed.")
        elif args.command == "status":
            provider.restore()
            print(json.dumps({"authenticated": True, "atmos_access": "requires per-track manifest check"}))
        elif args.command == "search":
            print(json.dumps(provider.search(args.query, args.limit), ensure_ascii=False, indent=2))
        elif args.command == "tracks":
            print(json.dumps(provider.track_ids(args.reference), indent=2))
        elif args.command == "inspect":
            with provider.prepare(args.track, require_atmos=not args.stereo) as prepared:
                print(json.dumps(prepared.metadata, ensure_ascii=False, indent=2))
        return 0
    except (TidalError, OSError) as exc:
        print("spatialctl tidal: " + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("TIDAL sign-in or request cancelled.", file=sys.stderr)
        return 130
    finally:
        provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
