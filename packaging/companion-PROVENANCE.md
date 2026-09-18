# BudsLink Companion downstream integration

Upstream: https://github.com/maniacx/BudsLink-Companion

Source branch: Plasma-Widget

Pinned commit: `31c6b3802071a6efc6fbd5a821e9c97f97240fd7`

Upstream version: 0.2.0

License: GNU GPL version 3 or later, as stated by upstream metadata and QML notices.

The `companion.patch` distributed beside this notice adds the SpatialControls
and PlaybackControls components, a playback-state helper, and inserts the
controls into the existing device page. This is a downstream
integration with `org.spatiald.Control1`; it is not an upstream BudsLink release
or endorsement. The original author attribution and UI resources remain intact.

The package retains upstream's plasmoid ID to preserve panel placement. Its
files are owned by pacman. The install script backs up a user-local installation
outside Plasma's search path because a local copy otherwise shadows the package.
It never edits another package's files in place or uses pacman `--overwrite`.
