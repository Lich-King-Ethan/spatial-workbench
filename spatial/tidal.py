"""Optional TIDAL catalogue, account authorization, and compressed audio source.

The source passes the original compressed stream to the renderer. It does not
decode audio, remove encryption, or infer Atmos from a catalogue badge.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import fcntl
import json
import logging
import math
import os
import re
import stat
import sys
import tempfile
import threading
import time
import uuid
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


def _save_session(path: Path, session, *, expected=_UNCONDITIONAL,
                  expected_revision=_UNCONDITIONAL) -> tuple:
    expiry = session.expiry_time
    data = {"schema": 1, "token_type": session.token_type,
            "access_token": session.access_token, "refresh_token": session.refresh_token,
            "expiry_time": expiry.isoformat() if expiry else None,
            "is_pkce": bool(getattr(session, "is_pkce", False))}
    if not data["access_token"] or not data["token_type"]:
        raise TidalError("TIDAL did not return a usable session")
    with _session_lock(path):
        if expected_revision is not _UNCONDITIONAL and _session_revision(path) != expected_revision:
            raise TidalError("TIDAL sign-in changed during this request; retry")
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


def _read_session_record(path: Path, *, allow_missing=False) -> tuple[dict, tuple | None]:
    """Read credentials and their exact file generation under _session_lock."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        if allow_missing:
            return {}, None
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


def _session_revision(path: Path):
    """Credential generation plus logout tombstone; caller holds _session_lock.

    The lock's timestamp also changes on logout when no session exists, so an
    already pending device authorization cannot resurrect a signed-out account.
    """
    _, generation = _read_session_record(path, allow_missing=True)
    info = path.with_name(path.name + ".lock").stat(follow_symlinks=False)
    return generation, info.st_mtime_ns


def _login_url(link) -> str:
    url = str(link.verification_uri_complete)
    if not url.startswith("https://"):
        url = "https://" + url
    _https(url)
    if urlsplit(url).hostname not in ("link.tidal.com", "login.tidal.com", "tidal.com"):
        raise TidalError("TIDAL returned an unexpected sign-in website")
    return url


@dataclass
class _DeviceLogin:
    identifier: str
    session: object = field(repr=False)
    revision: tuple = field(repr=False)
    link: object | None = field(default=None, repr=False)
    expires: float = 0
    interval: float = 5
    next_poll: float = 0
    busy: bool = True
    state: str = "starting"
    error: str = ""


def _new_session():
    try:
        import requests
        import tidalapi
    except ImportError:
        raise TidalError("TIDAL support is not installed; install python-tidalapi or the project's tidal extra") from None

    _private_provider_logs()

    class BoundedSession(requests.Session):
        def request(self, method, url, **kwargs):
            kwargs.setdefault("timeout", (10, 30))
            return super().request(method, url, **kwargs)

    session = tidalapi.Session(tidalapi.Config(quality="DOLBY_ATMOS"))
    session.request_session.close()
    session.request_session = BoundedSession()
    return session


def _private_provider_logs():
    # Upstream logs raw HTTP bodies for some authorization failures. Our public
    # errors already report the actionable failure; keep provider internals out
    # of the daemon journal and diagnostic bundles.
    logger = logging.getLogger("tidalapi")
    if not any(isinstance(handler, logging.NullHandler) for handler in logger.handlers):
        logger.addHandler(logging.NullHandler())
    logger.propagate = False


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

    @property
    def validated_dash(self) -> bool:
        """Only our retained, validated DASH file needs remote segment access."""
        return (self._directory is not None
                and self.media == str(Path(self._directory.name) / "stream.mpd"))

    def cleanup(self):
        if self._directory is not None:
            self._directory.cleanup()
            self._directory = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cleanup()


def prepare_stream(stream, metadata=None, *, require_atmos=True, require_lossless=False) -> PreparedTrack:
    """Validate a service response and preserve its compressed stream unchanged."""
    if require_atmos and require_lossless:
        raise TidalError("Choose Atmos or lossless playback, not both")
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
    if require_lossless and not codecs <= {"FLAC", "ALAC"}:
        raise TidalError("TIDAL returned no lossless FLAC or ALAC stream for this account and track; lossy audio was not substituted")
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
    def __init__(self, state_path=None, *, session=None, login_session_factory=None,
                 clock=time.monotonic):
        self.state_path = Path(state_path) if state_path else default_state_path()
        self._session = session
        self._authenticated = False
        self._generation = None
        self._operation_lock = threading.RLock()
        self._login_lock = threading.RLock()
        self._login = None
        self._login_session_factory = login_session_factory or _new_session
        self._clock = clock

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
            url = _login_url(link)
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
        self.login_cancel()
        try:
            with _session_lock(self.state_path):
                self.state_path.unlink(missing_ok=True)
                lock = self.state_path.with_name(self.state_path.name + ".lock")
                stamp = max(time.time_ns(), lock.stat().st_mtime_ns + 1)
                os.utime(lock, ns=(stamp, stamp), follow_symlinks=False)
        finally:
            self._invalidate()

    def close(self):
        self.login_cancel()
        if self._session is not None:
            transport = getattr(self._session, "request_session", None)
            if transport:
                transport.close()

    @staticmethod
    def _close_login(attempt):
        transport = getattr(attempt.session, "request_session", None)
        if transport is not None:
            transport.close()
        attempt.session.access_token = None
        attempt.session.refresh_token = None
        attempt.link = None

    def _login_result(self, attempt):
        return {"login_id": attempt.identifier, "state": attempt.state,
                "expires_in": max(0, math.ceil(attempt.expires - self._clock())),
                "interval": attempt.interval, "error": attempt.error}

    def login_status(self) -> dict:
        """Resume the single in-memory authorization without contacting TIDAL.

        A taskbar popup can close while the user authorizes in their browser.
        Its next explicit private reply may recover the same code/link; these
        fields must never enter broadcast State, logs, or persistent storage.
        """
        with self._login_lock:
            attempt = self._login
            if attempt is None:
                return {"state": "idle", "login_id": "", "expires_in": 0, "interval": 5, "error": ""}
            if attempt.state == "pending" and self._clock() >= attempt.expires:
                attempt.state = "expired"
                if not attempt.busy:
                    self._close_login(attempt)
            result = self._login_result(attempt)
            if attempt.state == "pending":
                # The link was validated before the attempt became pending.
                result.update(verification_url=_login_url(attempt.link),
                              user_code=str(attempt.link.user_code))
            return result

    def login_start(self) -> dict:
        """Start device authorization in a worker; never opens a browser.

        Only this direct, ephemeral response contains the user's verification
        link/code. Do not include it in daemon snapshots or diagnostic reports.
        The OAuth device code and tokens never leave this provider.
        """
        with self._login_lock:
            # A popup may remain closed until its previous code has expired.
            # Reap that code before deciding whether a new start is allowed.
            self.login_status()
            if self._login and self._login.busy and self._login.state not in ("starting", "pending"):
                raise TidalError("The previous TIDAL sign-in request is still finishing; try again shortly")
            if self._login and self._login.state in ("starting", "pending"):
                raise TidalError("TIDAL sign-in is already pending; finish or cancel it first")
            with _session_lock(self.state_path):
                revision = _session_revision(self.state_path)
            attempt = _DeviceLogin(uuid.uuid4().hex, self._login_session_factory(), revision)
            self._login = attempt
        try:
            link = attempt.session.get_link_login()
            url = _login_url(link)
            expiry, interval = float(link.expires_in), float(link.interval)
            code = str(link.user_code)
            if (not math.isfinite(expiry) or not 1 <= expiry <= 3600
                    or not math.isfinite(interval) or not 1 <= interval <= 60
                    or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", code)):
                raise TidalError("TIDAL returned invalid sign-in instructions")
            with self._login_lock:
                if self._login is not attempt or attempt.state == "cancelled":
                    return self._login_result(attempt)
                attempt.link = link
                attempt.expires = self._clock() + expiry
                attempt.interval = interval
                attempt.next_poll = self._clock() + interval
                attempt.state = "pending"
                return dict(self._login_result(attempt), verification_url=url, user_code=code)
        except Exception as exc:
            with self._login_lock:
                if attempt.state != "cancelled":
                    attempt.state = "error"
                    attempt.error = str(self._failure("sign-in", exc))
                return self._login_result(attempt)
        finally:
            with self._login_lock:
                attempt.busy = False
                if attempt.state != "pending":
                    self._close_login(attempt)

    def login_poll(self, login_id: str) -> dict:
        """Perform at most one supported library poll in a bounded worker.

        tidalapi 0.8.11 raises TimeoutError for a still-pending single poll as
        well as an expired code. Our monotonic deadline owns local expiry.
        """
        with self._login_lock:
            attempt = self._login
            if attempt is None or attempt.identifier != login_id:
                raise TidalError("TIDAL sign-in request is no longer active")
            if attempt.state != "pending":
                return self._login_result(attempt)
            if self._clock() >= attempt.expires:
                attempt.state = "expired"
                if not attempt.busy:
                    self._close_login(attempt)
                return self._login_result(attempt)
            if attempt.busy or self._clock() < attempt.next_poll:
                return self._login_result(attempt)
            attempt.busy = True
        try:
            with _session_lock(self.state_path):
                if _session_revision(self.state_path) != attempt.revision:
                    raise TidalError("TIDAL sign-in changed during this request; retry")
            try:
                completed = attempt.session.process_link_login(attempt.link, until_expiry=False)
            except TimeoutError:
                completed = False
            if completed and not attempt.session.check_login():
                raise TidalError("TIDAL sign-in was not accepted; try signing in again")
            with self._login_lock:
                if self._login is not attempt or attempt.state != "pending":
                    return self._login_result(attempt)
                if self._clock() >= attempt.expires:
                    attempt.state = "expired"
                elif completed:
                    _save_session(self.state_path, attempt.session, expected_revision=attempt.revision)
                    attempt.state = "signed_in"
                return self._login_result(attempt)
        except Exception as exc:
            with self._login_lock:
                if attempt.state == "pending":
                    attempt.state = "error"
                    attempt.error = str(self._failure("sign-in", exc))
                return self._login_result(attempt)
        finally:
            with self._login_lock:
                attempt.busy = False
                attempt.next_poll = self._clock() + attempt.interval
                if attempt.state != "pending":
                    self._close_login(attempt)

    def login_cancel(self, login_id: str = "") -> dict:
        """Cancel immediately, including a currently blocked network worker."""
        with self._login_lock:
            attempt = self._login
            if attempt is None:
                return {"state": "cancelled", "login_id": "", "expires_in": 0, "interval": 5, "error": ""}
            if login_id and attempt.identifier != login_id:
                raise TidalError("TIDAL sign-in request is no longer active")
            if attempt.state not in ("starting", "pending"):
                return self._login_result(attempt)
            attempt.state = "cancelled"
            attempt.error = ""
            if not attempt.busy:
                self._close_login(attempt)
            return self._login_result(attempt)

    @_serialized
    def session_status(self, refresh=False) -> dict:
        """Safe account state; local-only unless an explicit refresh is requested."""
        result = {"authenticated": False, "session_saved": False, "state": "signed_out",
                  "atmos_access": "requires per-track manifest check", "error": ""}
        try:
            with _session_lock(self.state_path):
                _, generation = _read_session_record(self.state_path, allow_missing=True)
            if generation is not None:
                result["session_saved"] = True
                if refresh:
                    self.restore()
                result["authenticated"] = bool(self._authenticated and generation == self._generation) if not refresh else self._authenticated
                result["state"] = "signed_in" if result["authenticated"] else "saved"
            elif self._authenticated:
                self._invalidate()
        except Exception as exc:
            self._invalidate()
            result.update(state="error", error=str(self._failure("session restore", exc)))
        return result

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

    @staticmethod
    def _page(limit, offset):
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 50 or not 0 <= offset <= 10000:
            raise TidalError("Choose a page size between 1 and 50 and an offset between 0 and 10,000")

    @staticmethod
    def _catalogue_id(kind, identifier):
        value = str(identifier)
        pattern = (r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
                   if kind == "playlist" else r"[1-9][0-9]{0,18}")
        if kind not in ("track", "album", "artist", "playlist") or not re.fullmatch(pattern, value):
            raise TidalError("Invalid TIDAL catalogue selection")
        return value

    @staticmethod
    def _label(value):
        return "".join(char for char in str(value or "") if ord(char) >= 32)[:1024]

    @classmethod
    def catalogue_item(cls, item, kind):
        """Project an allowlist of fields; never serialize a library object."""
        identifier = cls._catalogue_id(kind, item.id)
        artist = cls._label(getattr(getattr(item, "artist", None), "name", ""))
        album = getattr(item, "album", None)
        artwork_id = (getattr(album, "cover", "") if kind == "track" else
                      getattr(item, "cover", "") if kind == "album" else
                      getattr(item, "square_picture", "") or getattr(item, "picture", ""))
        artwork = ""
        if isinstance(artwork_id, str) and re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", artwork_id):
            artwork = "https://resources.tidal.com/images/" + artwork_id.replace("-", "/") + "/320x320.jpg"
        duration = getattr(item, "duration", 0)
        count = getattr(item, "num_tracks", 0)
        result = {"kind": kind, "id": identifier, "title": cls._label(getattr(item, "name", "")),
                  "subtitle": artist, "artist": artist,
                  "album": cls._label(getattr(album, "name", "")),
                  "reference": "" if kind == "artist" else f"tidal:{kind}:{identifier}",
                  "artwork": artwork,
                  "duration": max(0, duration) if type(duration) is int else 0,
                  "track_count": max(0, count) if type(count) is int else 0,
                  "catalogue_atmos": bool(getattr(item, "is_dolby_atmos", False)
                                           or "DOLBY_ATMOS" in (getattr(item, "audio_modes", None) or [])),
                  "explicit": bool(getattr(item, "explicit", False))}
        if kind == "playlist":
            result["subtitle"] = cls._label(getattr(getattr(item, "creator", None), "name", ""))
        return result

    @_serialized
    def search_catalogue(self, query: str, limit=20, offset=0) -> dict:
        self._page(limit, offset)
        if not isinstance(query, str) or not query.strip() or len(query) > 512:
            raise TidalError("Search requires a nonempty query of up to 512 characters")
        self._ready()
        try:
            response = self.session.search(query.strip(), limit=limit, offset=offset)
            result = {plural: [self.catalogue_item(item, kind) for item in response.get(plural, [])[:limit]]
                      for kind, plural in (("track", "tracks"), ("album", "albums"),
                                           ("artist", "artists"), ("playlist", "playlists"))}
            result.update(offset=offset, limit=limit,
                          has_more=any(len(items) == limit for items in result.values()))
            self._save_authenticated()
            return result
        except Exception as exc:
            raise self._failure("search", exc) from None

    @_serialized
    def collection(self, kind: str, identifier: str, limit=50, offset=0) -> dict:
        self._page(limit, offset)
        identifier = self._catalogue_id(kind, identifier)
        if kind not in ("album", "playlist", "artist"):
            raise TidalError("Choose a TIDAL album, playlist, or artist")
        self._ready()
        try:
            item = getattr(self.session, kind)(identifier)
            if kind == "artist":
                tracks = item.get_top_tracks(limit=limit, offset=offset)
                albums = item.get_albums(limit=limit, offset=offset)
            else:
                tracks, albums = item.tracks(limit=limit, offset=offset), []
            result = {"item": self.catalogue_item(item, kind),
                      "tracks": [self.catalogue_item(track, "track") for track in tracks[:limit]],
                      "albums": [self.catalogue_item(album, "album") for album in albums[:limit]],
                      "offset": offset, "limit": limit,
                      "has_more": len(tracks) >= limit or len(albums) >= limit}
            self._save_authenticated()
            return result
        except Exception as exc:
            raise self._failure("collection lookup", exc) from None

    @_serialized
    def library(self, kind: str, limit=50, offset=0) -> dict:
        self._page(limit, offset)
        kinds = {"tracks": "track", "albums": "album", "artists": "artist", "playlists": "playlist"}
        if kind not in kinds:
            raise TidalError("Choose library tracks, albums, artists, or playlists")
        self._ready()
        try:
            # Favorites.playlists() uses the maintained root collection API,
            # including the user's own and saved playlists (50 items maximum).
            items = getattr(self.session.user.favorites, kind)(limit=limit, offset=offset)
            result = {"kind": kind, "items": [self.catalogue_item(item, kinds[kind]) for item in items[:limit]],
                      "offset": offset, "limit": limit, "has_more": len(items) >= limit}
            self._save_authenticated()
            return result
        except Exception as exc:
            raise self._failure("library lookup", exc) from None

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
    def prepare(self, track_id: str, require_atmos=True, *, require_lossless=False) -> PreparedTrack:
        if require_atmos and require_lossless:
            raise TidalError("Choose Atmos or lossless playback, not both")
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
            prepared = prepare_stream(stream, self.track_metadata(track), require_atmos=require_atmos,
                                      require_lossless=require_lossless)
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
