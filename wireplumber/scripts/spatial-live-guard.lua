-- SPDX-License-Identifier: MIT
-- Protect only an explicitly redirected application during a live PCM session.
-- Runs in WirePlumber's target-selection transaction, so renderer death cannot
-- race a Python poll loop and send the stream to the default speakers.
local lutils = require ("linking-utils")
local cutils = require ("common-utils")

local function announce (metadata)
  if metadata then
    metadata:set (0, "spatiald.live-guard", "Spa:String", "1")
  end
end

local function protect_target (event)
  local source, om, si, props, flags =
      lutils:unwrap_select_target_event (event)
  if props ["media.class"] ~= "Stream/Output/Audio" then
    return
  end
  local metadata = cutils.get_default_metadata_object ()
  if not metadata then
    return
  end
  local guard = metadata:find (props ["node.id"], "spatiald.live-target")
  if type (guard) ~= "string" then
    return
  end
  local source_serial, target_serial = guard:match ("^(%d+):(%d+)$")
  if source_serial ~= tostring (props ["object.serial"]) then
    return
  end
  -- A later explicit application/user routing choice supersedes our session.
  local requested = metadata:find (props ["node.id"], "target.object")
  if tostring (requested) ~= target_serial then
    return
  end
  local target = om:lookup {
    type = "SiLinkable",
    Constraint { "object.serial", "=", target_serial },
  }
  if not target or not lutils.canLink (props, target) then
    event:stop_processing ()
    return
  end
  local compatible, passthrough = lutils.checkPassthroughCompatibility (si, target)
  if not compatible then
    event:stop_processing ()
    return
  end
  -- Set the same flags as the stock defined-target hook. Subsequent stock
  -- target selectors bypass their search once this exact target is present.
  flags.has_defined_target = true
  flags.has_node_defined_target = false
  flags.can_passthrough = passthrough
  event:set_data ("target", target)
end

SimpleEventHook {
  name = "spatiald/protect-live-target",
  before = "linking/find-defined-target",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "select-target" },
    },
  },
  execute = protect_target,
}:register ()

-- Stock smart-filter selection can replace even an explicit target. Reassert
-- this session's exact input after that selection, before prepare-link checks
-- target availability and exclusive access. Do not run after prepare-link:
-- that would undo its decision to leave an unavailable target unlinked.
SimpleEventHook {
  name = "spatiald/verify-live-target",
  after = "linking/get-filter-from-target",
  before = "linking/prepare-link",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "select-target" },
    },
  },
  execute = protect_target,
}:register ()

SimpleEventHook {
  name = "spatiald/announce-live-guard",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "metadata-added" },
      Constraint { "metadata.name", "=", "default" },
    },
  },
  execute = function (event)
    announce (cutils.get_default_metadata_object ())
  end,
}:register ()

-- The hooks.* component loads before the standard event source. Its initial
-- enumeration then emits metadata-added, announcing only after registration.
