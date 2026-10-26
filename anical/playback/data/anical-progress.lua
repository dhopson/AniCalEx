-- AniCal progress reporter for mpv.
-- Loaded through ani-cli's ANI_CLI_PLAYER_FLAGS; writes how far playback got and
-- why it ended to the file named by --script-opts=anical-out=PATH.
local options = { out = "" }
require("mp.options").read_options(options, "anical")

local last = {}
local written = false

for _, name in ipairs({ "percent-pos", "time-pos", "duration" }) do
    mp.observe_property(name, "number", function(_, value)
        if value ~= nil then last[name] = value end
    end)
end

local function number(value)
    if value == nil then return "null" end
    return string.format("%.3f", value)
end

local function write(reason)
    if written or options.out == "" then return end
    written = true
    local partial = options.out .. ".part"
    local file = io.open(partial, "w")
    if not file then return end
    file:write(string.format(
        '{"reason":"%s","percent":%s,"position":%s,"duration":%s}\n',
        reason, number(last["percent-pos"]), number(last["time-pos"]), number(last["duration"])
    ))
    file:close()
    os.rename(partial, options.out)
end

-- end-file carries the reason: "eof" (played to the end), "quit", "stop", "error", ...
mp.register_event("end-file", function(event) write(event.reason or "unknown") end)
mp.register_event("shutdown", function() write("shutdown") end)
