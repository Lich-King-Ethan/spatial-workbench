# Documentation

Start with the [project overview](../README.md), then pick the task you need.

| Use the application | Develop and audit |
|---|---|
| [Install, upgrade and remove](install.md) | [Contribute and run tests](../CONTRIBUTING.md) |
| [TIDAL mini-client](tidal-runtime.md) | [Architecture and module map](architecture.md) |
| [Application audio and recovery](live-audio.md) | [Detailed architecture contracts](architecture-reference.md) |
| [Tracker permissions and recenter](tracker-runtime.md) | [Desktop/D-Bus API](desktop-runtime.md) |
| [Timing and optional prediction](head-tracking-timing.md) | [Dependency pins and provenance](dependencies.md) |
| [Reference EQ](equalizer.md) | [Packaging](../packaging/README.md) · [Publishing](publishing.md) |
| [Local diagnostics](automatic-diagnostics.md) | [Licensing](licensing.md) |

## Evidence

[Current status](../STATUS.md) is the entry point. Detailed records preserve the
scope and date of each result:

- [September 28 physical PC](local-pc-validation-20260928.md)
- [Software validation and withdrawn earlier claims](validation-environment.md)
- [Hosted release history](release-history.md)
- [Developer handoff](local-development-handoff.md)
- [Booted installer CI](full-installer-ci.md)

## Implementation references

[Playback](audio-runtime.md) · [Decoder evidence](decoder-validation.md) ·
[WirePlumber route guard](wireplumber-live-guard.md) ·
[Renderer patches](orender-loopback.md) · [Sony control modules](feature-modules.md) ·
[Prepared upstream compatibility report](upstream-report.md)
