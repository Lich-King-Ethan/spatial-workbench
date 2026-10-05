# TIDAL in the Companion widget

BudsLink Spatial Companion includes a native TIDAL mini-client. The full installer
includes `python-tidalapi`; you do not need another music application. The client
is unofficial and still requires your own account and service entitlement.

## Sign in and choose music

1. Open the **TIDAL** card and choose **Sign in**. Follow its authorization link
   in your browser and enter the displayed code on TIDAL's own page.
2. After authorizing, reopen **BudsLink Spatial Companion**. The popup may close
   when the browser opens; your pending sign-in remains available until its code
   expires. Keep the card open while it checks and confirms sign-in. Use **Cancel**
   to abandon a pending attempt, or start again if the code expires.
3. Use **Search** for tracks, albums, artists and playlists, or **Library** for
   your favorites and playlists. Artwork and a scrolling results area keep the
   panel compact; open a collection, browse its pages, and use **Back** to return.
4. The panel starts with **Lossless**. Choose **Dolby Atmos only** explicitly when
   you require Atmos, then play a track or collection. The headphone output must
   be ready. Quality applies to the next selection without changing saved settings.
5. The built-in **Now playing** area shows the current title, artist, progress,
   opening state and playback errors. Pause, skip or stop there; **Stop** remains
   available while music opens or catalogue requests finish. System media controls
   also work. **Sign out** removes this application's saved session.

The client uses the same spatial playback engine as local files, so there is no
second TIDAL application to manage. It offers search, favorites, collection
browsing and playback; personalized home feeds, downloads and queue editing are
not implemented. Account authorization still uses TIDAL's own browser page.

Opening the card reads local account state. Checking a saved session or starting
sign-in is explicit; the daemon never launches an authorization browser on its
own. Pending login polling is bounded, and its code/link are kept out of normal
broadcast status, diagnostics and persistent storage. Closing the popup pauses
polling; reopening restores the pending attempt through a local read and resumes
checks while visible. The attempt lives only in daemon memory, so a daemon restart
requires starting sign-in again. The browser handles account authorization;
passwords are never entered into the widget.

## What Atmos means here

| Choice | What the client asks for | What happens if unavailable |
|---|---|---|
| Lossless | FLAC or ALAC lossless audio | Rejects AAC/MP3 substitution and reports the format error |
| Atmos only | Original clear E-AC-3 media marked Dolby Atmos | Rejects stereo substitution, encrypted media and unsupported codecs |

If an Atmos selection fails, its error now stays visible inside the music panel.
Choose **Lossless** and press Play yourself to request a different rendition;
the client never silently retries Atmos as stereo. A successful catalogue search
or login does not establish that playback is available for that track.

A catalogue Atmos badge is only a hint. The returned manifest must satisfy the
source checks, and the actual decoder must report spatial objects before the
application confirms Atmos. Nothing turns a stereo TIDAL stream into object audio.

As checked October 5, 2026, [TIDAL's official support page](https://support.tidal.com/hc/en-us/articles/360004255778-Dolby-Atmos)
(updated March 12) excludes desktop clients from its supported Atmos playback.
This unofficial client's strict request path does **not** guarantee availability
or bypass account, platform, region or encryption restrictions. Real account
authorization and browsing were confirmed on October 5. The selected track's
Atmos request was rejected by the service; Atmos streaming remains unaccepted.

## Playback repair — October 5

Lossless playback exposed a separate local failure: FFmpeg rejected HTTPS segments
referenced by TIDAL's validated local DASH manifest. Core revision 4 supplies the
required protocol list only for a retained manifest prepared by the TIDAL provider.
Other local files and stream requests keep their existing behavior, and manifest,
codec and encryption checks remain enforced. The same real FLAC rendition failed
without this option and decoded successfully with it in a silent player probe.
After installation, a ten-second read-only observation confirmed real TIDAL FLAC
stereo through the binaural renderer, EQ and physical XM5 output with fresh head
tracking. All 12 independent graph observations passed without changing playback
or volume. The user confirmed that the music sounds good. Plasma's panel was
reloaded afterward. Interactive panel acceptance, physical Stop and Atmos
streaming remain pending; see [current status](../STATUS.md).

## CLI and settings

The existing CLI remains useful alongside the card:

```sh
budslink-spatial tidal login
budslink-spatial tidal status
budslink-spatial tidal search 'artist song'
budslink-spatial tidal inspect https://tidal.com/browse/track/TRACK_ID
budslink-spatial play https://tidal.com/browse/track/TRACK_ID
budslink-spatial tidal logout
```

Replace `TRACK_ID` with a real numeric ID. `inspect` checks the source response
without playing audio or printing a signed media URL. `--stereo` requests an
ordinary stream explicitly; this older CLI option allows the best available
non-Atmos format, whereas the widget's **Lossless** choice is strict. Direct `play` uses `tidal_require_atmos` in
`~/.config/spatiald/config.toml`; the widget's quality choice applies to that
play request without rewriting configuration. `spatialctl` is the compatible alias.

A manual core-only installation may omit the optional SDK. Install the official
`python-tidalapi` package, or use the `tidal` extra inside a development virtual
environment. Missing dependencies produce an unavailable state while local
playback, tracking and Sony controls continue independently.

## Local account storage

Tokens live in `~/.local/state/spatiald/tidal/session.json` (or the matching XDG
state directory), protected by a private directory and mode `0600` file. This is
filesystem protection, not encryption. Logout removes this client's local copy;
it does not cancel your subscription or sign out other applications. Concurrent
login/logout checks prevent an older request from resurrecting a deleted session.

[Implementation and stream contracts](tidal-reference.md) cover manifests,
credential races and source provenance. [Current validation](../STATUS.md)
separates synthetic tests from authenticated service and listening acceptance.
