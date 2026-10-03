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
3. Once signed in, search tracks, albums, artists or playlists, or open your saved
   favorites. Browse a collection and use its page/back controls as needed.
4. Choose **Lossless** or **Atmos only**, then play a track or supported collection.
   Playback requires the actual headphone route to be ready.
5. Use the ordinary playback card or system media controls to pause, seek and
   move through the queue. **Sign out** removes this application's saved session.

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

A catalogue Atmos badge is only a hint. The returned manifest must satisfy the
source checks, and the actual decoder must report spatial objects before the
application confirms Atmos. Nothing turns a stereo TIDAL stream into object audio.

As checked October 3, 2026, [TIDAL's official support page](https://support.tidal.com/hc/en-us/articles/360004255778-Dolby-Atmos)
(updated March 12) excludes desktop clients from its supported Atmos playback.
This unofficial client's strict request path does **not** guarantee availability
or bypass account, platform, region or encryption restrictions. Real account and
Atmos playback acceptance remain pending; the software tests use fixtures.

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
