# TIDAL source

The optional `spatial.tidal` module handles account authorization and compressed
stream discovery. It does not depend on SONE, Torrential, WebKit, or an embedded
login window. The renderer receives the original E-AC-3 media or DASH manifest;
the provider does not turn a stereo application stream into Atmos.

The implementation uses the maintained `tidalapi` Python package, constrained to
`>=0.8.11,<0.9`. This is an unofficial service client. TIDAL can change its internal
API or deny playback for a particular client, subscription, region, or track.
The integration has no mechanism to bypass those restrictions.

## On your computer

Install the optional TIDAL dependency through the project's package/installer.
For development in an isolated virtual environment, `pip install '.[tidal]'`
installs it. Do not use `sudo pip` in the system Python environment.

```sh
spatialctl tidal login
spatialctl tidal status
spatialctl tidal search 'artist song'
spatialctl tidal inspect https://tidal.com/browse/track/TRACK_ID
spatialctl play https://tidal.com/browse/track/TRACK_ID
```

Replace `TRACK_ID` with a real numeric catalogue track ID. The login command opens
the official TIDAL device authorization page in your external browser and waits
for you to authorize. `spatialctl tidal login --no-browser` prints the same link
for manual use. The daemon never opens a sign-in window and never asks for a
password. Cancel the login command with Ctrl-C if necessary.

`inspect` contacts TIDAL, checks the actual playback response, and prints only
safe metadata. It does not play audio or print a signed media URL. A successful
`inspect` result proves that the service returned a usable clear E-AC-3 manifest
marked `DOLBY_ATMOS`; the decoder must still confirm that object metadata was
actually decoded. `renderer_confirmed_atmos` therefore remains false in the
provider's result.

To check ordinary stereo explicitly, use `spatialctl tidal inspect TRACK_ID
--stereo`. An Atmos request never silently retries as stereo.

Other commands:

```sh
spatialctl tidal tracks https://tidal.com/browse/album/ALBUM_ID
spatialctl tidal tracks https://tidal.com/browse/playlist/PLAYLIST_UUID
spatialctl tidal logout
```

`tracks` enumerates collection track IDs; it does not start playback or claim
that every track has an Atmos rendition. Search's `catalogue_atmos` field is a
catalogue hint, not a playback capability result. Album/playlist queue behavior
belongs to the player, not to this source module.

The module is also directly callable as `python -m spatial.tidal` with the same
arguments, which is useful while developing before installation.

## Capability checks

| Response | Behavior |
|---|---|
| `DOLBY_ATMOS`, clear E-AC-3 BTS manifest | Original HTTPS media URL passed through |
| `DOLBY_ATMOS`, clear E-AC-3 DASH manifest | Original manifest passed through in a temporary private file |
| Stereo returned for an Atmos request | Request fails with an explicit message |
| Catalogue says Atmos but stream says stereo | Request fails with an explicit message |
| AC-4 or another unsupported Atmos codec | Request fails with an explicit message |
| DASH `ContentProtection` or BTS encryption | Request fails; no key handling or decryption |
| Missing login | Audio daemon reports sign-in needed; other modules continue |
| Expired/revoked login, network failure, or service rejection | Source reports the error without exposing credentials |

The Atmos request uses `audioquality=DOLBY_ATMOS`, as implemented by Torrential's
current source. `tidalapi` supports string quality values and its normal
`Track.get_stream()` sends that field. This request choice is based on inspected
upstream code, not an authenticated playback observation on the user's account.
Service acceptance is checked at runtime; the code never assumes the request
field makes a returned stream Atmos.

## Isolation and credential storage

Account state lives in
`$XDG_STATE_HOME/spatiald/tidal/session.json`, defaulting to
`~/.local/state/spatiald/tidal/session.json`. The containing directory is private
(`0700`); the session file is private (`0600`) and replaced atomically. Tokens are
never placed in UI state, source metadata, or normal diagnostic output. These
tokens are stored locally, protected by filesystem permissions rather than
encrypted at rest. `logout` deletes this application's local copy; it does not
revoke other TIDAL clients or cancel a subscription.

A running daemon checks the saved session before each source request. A new
login is loaded on the next request; logout invalidates cached authorization
without restarting the daemon. Credential reads, atomic replacements, and
logout share a private, persistent `session.json.lock` file. A request may save
refreshed credentials only if the session file still has the identity it read.
If another process logs out or replaces the login during a service request, the
old result is rejected and any prepared DASH file is removed. An HTTP request
already in progress can finish, but cannot restore the deleted login or overwrite
the new one. The filesystem lock is released during all network/browser work.

Every service HTTP request has connect/read timeouts. Account restoration and
stream resolution should run in the source worker rather than the desktop
event loop. An unavailable TIDAL service does not own or stop tracker discovery,
BudsLink controls, PipeWire device discovery, or other media sources.

Signed media URLs are short lived. A player must request a fresh `PreparedTrack`
before playback/retry and must not persist a resolved URL as a bookmark. Player
diagnostics must redact source URLs. The `PreparedTrack` object owns temporary
DASH files and must be cleaned up when playback ends.

```python
from spatial.tidal import TidalProvider

provider = TidalProvider()
try:
    with provider.prepare("tidal:track:123456789") as source:
        # Give source.media to the player and keep this context alive until stop.
        # Only source.metadata is suitable for normal status reporting.
        run_player(source.media)
finally:
    provider.close()
```

## Verification

Automated tests cover real manifest parsing and the provider's library boundary:
clear E-AC-3 handoff, preserved DASH text, explicit stereo rejection, mixed codec
rejection, encrypted/protected stream rejection, URL validation, session
permissions, source failure isolation, login separation, and collection
pagination. Concurrent-session tests cover daemon logout, new-login reload,
credential changes during restore/service requests, atomic save/logout ordering,
overlapping source workers, and stale DASH cleanup. Fixtures describe protocol
responses; they are not recorded proof
that an account received Atmos.

The `tidalapi 0.8.11` wheel was installed and its public auth/session interfaces
were checked without importing or copying a user's credentials. A live
unauthenticated request to TIDAL's device authorization endpoint also succeeded:
the service returned a device authorization with a 300-second lifetime. No browser
was opened, no account was authorized, and the unused code was discarded. Full
TIDAL authorization and track playback require the user's own account and local
audio stack; they have not been validated in this workspace.

Inspected upstream sources:

- [python-tidal 9c41fbe — session/authentication](https://github.com/EbbLabs/python-tidal/blob/9c41fbe6b2f2cd9fa00dca11e83574fd929ec020/tidalapi/session.py)
- [python-tidal 9c41fbe — stream and manifest model](https://github.com/EbbLabs/python-tidal/blob/9c41fbe6b2f2cd9fa00dca11e83574fd929ec020/tidalapi/media.py)
- [Torrential f028ec5 — playback request](https://github.com/oliveiraethales/torrential/blob/f028ec587f698ee9448a2abc5cbf657e3dd7549b/lib/core/tidal_api.dart)
- [tidalapi login documentation](https://tidalapi.netlify.app/login)
