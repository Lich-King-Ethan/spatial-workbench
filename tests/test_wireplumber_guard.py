"""Execute the shipped Lua policy against WirePlumber-shaped event fixtures.

This verifies real Lua parsing/branching without pretending to run a PipeWire
daemon. The target-PC acceptance test must still kill the actual renderer.
"""
import ctypes
import ctypes.util
import json
import os
from pathlib import Path
import unittest


LUA = os.environ.get("SPATIAL_TEST_LUA_LIBRARY") or next(
    (path for name in ("lua5.5", "lua5.4", "lua5.3", "lua")
     if (path := ctypes.util.find_library(name))), None)
SCRIPT = Path(__file__).resolve().parents[1] / "wireplumber/scripts/spatial-live-guard.lua"


@unittest.skipUnless(LUA, "Lua shared library required for WirePlumber policy execution")
class GuardTests(unittest.TestCase):
    def _run_policy(self, assertions):
        lib = ctypes.CDLL(LUA)
        lib.luaL_newstate.restype = ctypes.c_void_p
        # Lua 5.5 makes luaL_openlibs a header macro. ctypes must call the
        # exported function underlying it, with the same all-libraries mask.
        openlibs = getattr(lib, "luaL_openlibs", None)
        if openlibs is not None:
            openlibs.argtypes = [ctypes.c_void_p]
            openlibs.restype = None
        else:
            lib.luaL_openselectedlibs.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
            lib.luaL_openselectedlibs.restype = None
            openlibs = lambda state: lib.luaL_openselectedlibs(state, ~0, 0)
        lib.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.luaL_loadstring.restype = ctypes.c_int
        lib.lua_pcallk.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_ssize_t, ctypes.c_void_p]
        lib.lua_pcallk.restype = ctypes.c_int
        lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        lib.lua_tolstring.restype = ctypes.c_char_p
        lib.lua_close.argtypes = [ctypes.c_void_p]
        state = lib.luaL_newstate()
        self.assertTrue(state)
        try:
            openlibs(state)
            harness = r'''
hooks = {}
props = { ["media.class"] = "Stream/Output/Audio", ["node.id"] = "8", ["object.serial"] = "31" }
flags = {}
entries = {}
function new_metadata(values)
  local object = {values=values}
  function object:find(subject, key) return self.values[tostring(subject).."|"..key] end
  function object:set(subject, key, kind, value)
    self.values[tostring(subject).."|"..key] = value
    self.last_type = kind
  end
  return object
end
metadata = new_metadata(entries)
metadata_available = true
manager_available = true
metadata_manager = {}
function metadata_manager:lookup(interest)
  assert(#interest == 1 and #interest[1] == 3)
  assert(interest[1][1] == "metadata.name" and interest[1][2] == "=")
  assert(interest[1][3] == "default")
  return metadata_available and metadata or nil
end
source = {}
function source:call(action, kind)
  assert(action == "get-object-manager" and kind == "metadata")
  return manager_available and metadata_manager or nil
end
target = nil
can_link = true
compatible = true
om = {}
function om:lookup(interest)
  assert(interest.type == "SiLinkable")
  assert(interest[1][1] == "object.serial" and interest[1][2] == "=")
  assert(interest[1][3] == "47")
  return target
end
local lu = {}
function lu:unwrap_select_target_event(event)
  return event:get_source(), om, {}, props, flags, event.target
end
function lu.canLink(p, t) return can_link end
function lu.checkPassthroughCompatibility(s, t) return compatible, false end
package.preload["linking-utils"] = function() return lu end
-- Native WirePlumber can deliver initial metadata before the global plugin
-- lookup succeeds. Keep the old helper's failure mode visible to this fixture.
plugin_lookups = 0
Plugin = {find=function(name)
  assert(name == "standard-event-source")
  plugin_lookups = plugin_lookups + 1
  return nil
end}
package.preload["common-utils"] = function()
  return {get_default_metadata_object=function()
    return Plugin.find("standard-event-source"):call("get-object-manager", "metadata")
      :lookup {Constraint {"metadata.name", "=", "default"}}
  end}
end
function Constraint(value) return value end
function EventInterest(value) return value end
function SimpleEventHook(value)
  function value:register() hooks[self.name] = self end
  return value
end
function new_event()
  local event = {}
  function event:get_source() return source end
  function event:stop_processing() self.stopped = true end
  function event:set_data(key, value) self[key] = value end
  return event
end
'''
            code = harness + "\ndofile(" + json.dumps(str(SCRIPT)) + ")\n" + assertions
            result = lib.luaL_loadstring(state, code.encode())
            if result == 0:
                result = lib.lua_pcallk(state, 0, 0, 0, 0, None)
            message = lib.lua_tolstring(state, -1, None) if result else b""
            self.assertEqual(result, 0, (message or b"Lua execution failed").decode("utf-8", "replace"))
        finally:
            lib.lua_close(state)

    def test_metadata_event_precedes_global_plugin_discovery(self):
        self._run_policy(r'''
local announce = hooks["spatiald/announce-live-guard"]
-- Announce only the actual default metadata after all guard hooks registered.
assert(hooks["spatiald/protect-live-target"] and hooks["spatiald/verify-live-target"])
manager_available = false
announce.execute(new_event())
assert(not entries["0|spatiald.live-guard"])
manager_available = true
metadata_available = false
announce.execute(new_event())
assert(not entries["0|spatiald.live-guard"])
metadata_available = true
announce.execute(new_event())
assert(entries["0|spatiald.live-guard"] == "1" and metadata.last_type == "Spa:String")
-- Resolve new metadata on the next event instead of caching a removed proxy.
local previous = metadata
metadata = new_metadata({})
announce.execute(new_event())
assert(metadata ~= previous and metadata.values["0|spatiald.live-guard"] == "1")
-- Target selection also reads this event source's current metadata.
metadata.values["8|spatiald.live-target"] = "31:47"
metadata.values["8|target.object"] = "47"
local event = new_event()
hooks["spatiald/protect-live-target"].execute(event)
assert(event.stopped)
assert(plugin_lookups == 0)
''')

    def test_real_lua_policy_protects_only_current_explicit_session(self):
        self._run_policy(r'''
assert(hooks["spatiald/protect-live-target"].before == "linking/find-defined-target")
assert(hooks["spatiald/verify-live-target"].after == "linking/get-filter-from-target")
assert(hooks["spatiald/verify-live-target"].before == "linking/prepare-link")
hooks["spatiald/announce-live-guard"].execute(new_event())
assert(entries["0|spatiald.live-guard"] == "1" and metadata.last_type == "Spa:String")
function run()
  local event = new_event()
  hooks["spatiald/protect-live-target"].execute(event)
  return event
end
-- Ordinary streams retain their ordinary policy.
assert(not run().stopped)
entries["8|spatiald.live-target"] = "31:47"
entries["8|target.object"] = "47"
-- Lost renderer: selection is stopped before any fallback hook can run.
assert(run().stopped)
-- Exact target alive: normal subsequent linking operates on that one target.
target = {properties={ ["object.serial"]="47" }}
local event = run()
assert(not event.stopped and event.target == target)
assert(flags.has_defined_target and not flags.has_node_defined_target)
-- A stock smart-filter selection must not replace the protected input.
event.target = {properties={ ["object.serial"]="99" }}
hooks["spatiald/verify-live-target"].execute(event)
assert(not event.stopped and event.target == target)
-- The renderer can also disappear after the first selection hook.
target = nil
hooks["spatiald/verify-live-target"].execute(event)
assert(event.stopped)
target = {properties={ ["object.serial"]="47" }}
-- Incompatibility also cannot turn into default-device playback.
can_link = false
assert(run().stopped)
can_link = true
compatible = false
assert(run().stopped)
compatible = true
-- A later explicit user target and a reused node ID remain outside the guard.
entries["8|target.object"] = "99"
assert(not run().stopped and not run().target)
entries["8|target.object"] = "47"
props["object.serial"] = "new-session"
assert(not run().stopped and not run().target)
props["object.serial"] = "31"
entries["8|spatiald.live-target"] = "malformed"
assert(not run().stopped and not run().target)
assert(plugin_lookups == 0)
''')
