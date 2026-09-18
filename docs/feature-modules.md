# Additional WF-1000XM5 modules

These are supported upstream Sony controls and proposals for additional helpers,
not claims that new control implementations have been added here. Existing
BudsLink controls remain the entry point. Release 0.2 adds separate playback,
tracking, application-audio and host-EQ modules; the inventory below concerns
additional device features. Its native Companion cards reuse existing spacing,
labels, theme controls and accessibility conventions.

Evidence: BudsLink model configuration and implementation inspected at
`7405f6343f8390b5a78e358d3ad4c0870b2270bd`:
[model capabilities](https://github.com/maniacx/BudsLink/blob/7405f6343f8390b5a78e358d3ad4c0870b2270bd/src/lib/devices/sony/deviceConfigs/WF-1000XM5.js),
[Sony settings implementation](https://github.com/maniacx/BudsLink/blob/7405f6343f8390b5a78e358d3ad4c0870b2270bd/src/lib/devices/sony/sonyDevice.js).
The declared feature is evidence of upstream support, not a per-feature hardware test.

| Module | Capability and purpose | Integration rule |
|---|---|---|
| Multipoint | Dual connection, routing indicator/control, active-device control | Show real peer state; delegate to BudsLink |
| Battery alerts | Left/right/case battery monitoring with optional threshold alerts | Debounce and rate-limit notifications; unavailable case reading stays unknown |
| Speak-to-Chat | Enable and exposed configuration | Use upstream settings; no microphone analysis in our daemon |
| Wearing behavior | Pause-on-removal and automatic power-off settings | Let earbuds/upstream implement it; avoid a second competing pause engine |
| Sound settings | Six-band Sony EQ and DSEE upsampling control | Separate from measured host PEQ; coordinate profile ownership explicitly |
| Touch assignment | Supported left/right button modes | Show only reported choices; no promise of arbitrary shortcuts |
| Voice notifications | Upstream-supported voice notification controls | Do not invent volume controls absent from this model's capability table |

Priority after tracker/routing validation: multipoint, battery alerts, then a
coordinated sound-profile module. Speak-to-Chat and wear/touch controls should
be reused where BudsLink already exposes them rather than duplicated in the popup.

LE Audio/Auracast, firmware updating, ear-tip fit testing, and mobile-service features
are not part of this native module commitment. A hardware feature needs a usable
Linux interface and coexistence checks before it becomes a module. Prefer the
official Sony path for firmware while that integration is unverified.

## Isolation rules

- Explicit ownership, health state, timeout, backoff, versioned APIs and tests.
  Renderers, Sony helpers and EQ use separate processes; Python adapters also use
  independently supervised tasks inside one daemon. This is not a promise of a
  separate process or security sandbox for every module.
- No module may restart the entire audio stack to repair itself.
- Core preferences survive missing providers; UI availability does not.
- Starting a module does not prove that its device/API is ready.
- Provider failures remove the affected capabilities and retry locally. A whole
  daemon failure can affect all of its tasks; the user service handles its restart.
- BudsLink is the sole Sony control owner; feature helpers are API clients.
- Registry entries in `config/modules.toml` describe ownership and maturity.

User confirmation recorded separately: the Companion works on the user's
WF-1000XM5 under Arch Linux, September 17, 2026, Midwest USA. Exact software
versions and a per-feature checklist were not supplied.
