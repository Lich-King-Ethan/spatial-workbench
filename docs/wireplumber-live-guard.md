# Live application routing guard

An application redirected to a temporary renderer input must not suddenly play
through speakers when the renderer crashes. A Python polling loop cannot close
that race: destroying the input can cause WirePlumber to select another output
before the daemon observes the failure.

The included WirePlumber policy participates directly in `select-target`, before
the stock `linking/find-defined-target` hook. Its metadata contract applies only
to the exact selected application stream and temporary target:

| Metadata subject | Key | Type | Value |
|---|---|---|---|
| `0` | `spatiald.live-guard` | `Spa:String` | `1`, written by the loaded policy |
| Current application node ID | `spatiald.live-target` | `Spa:String` | `<application object.serial>:<renderer-input object.serial>` |

The live module requires the capability marker, writes the guard before setting
`target.object`, and verifies the resulting route. The policy checks the stream
serial as well as its current target metadata. For an active matching guard it
selects only the recorded compatible target; if that target has disappeared or
is incompatible it stops event processing before default/best-target selection.
A newer explicit user destination takes precedence. Ordinary streams and stale
guards for reused node IDs are ignored. On deliberate cleanup the previous
target is restored first, then the owned guard is cleared.

This uses WirePlumber's supported custom event-hook API. Remote `WpNode` objects
do not expose `update_properties`; setting a metadata key called
`node.dont-fallback` would not change the node's policy properties. The project
does not rely on such an unrecognized setter.

The package installs `spatial-live-guard.lua` under the normal WirePlumber script
directory and its component fragment under `wireplumber.conf.d`. The component's
`hooks.*` feature name registers it before the standard event source starts.
It advertises readiness on the initial `metadata-added` event. Restarting the
user's WirePlumber service once after installation activates it; live routing
fails closed until its capability marker appears.

The shipped Lua was executed through a real Lua 5.4 runtime against event
fixtures. Tests cover disappeared/incompatible targets, normal exact-target
selection, explicit newer user choices, stale object identities and unaffected
ordinary streams. A target-PC test must still kill the actual renderer process
and inspect its application's links. This guard is specifically a protection
against renderer/input loss while WirePlumber is operating; it is not a promise
to preserve a session through replacement of the entire audio/session manager.

Primary contracts inspected:

* [WirePlumber custom target-selection hooks](https://pipewire.pages.freedesktop.org/wireplumber/scripting/custom_scripts.html)
* [Stock explicit target selection](https://gitlab.freedesktop.org/pipewire/wireplumber/-/blob/3f0598d73f012a1a883f96bc14a0a7b486f60e55/src/scripts/linking/find-defined-target.lua)
* [Stock link preparation and reconnect policy](https://gitlab.freedesktop.org/pipewire/wireplumber/-/blob/3f0598d73f012a1a883f96bc14a0a7b486f60e55/src/scripts/linking/prepare-link.lua)
