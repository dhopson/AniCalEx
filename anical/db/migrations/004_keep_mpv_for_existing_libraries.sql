-- AniCal used to play only in mpv. It can now use other players, and picks the
-- first one installed when no choice is saved. Save mpv as the choice for
-- libraries from before that, so an update doesn't move anyone to another
-- player. A new library has nothing in it yet, and so gets no choice.
INSERT OR IGNORE INTO settings (key, value_json)
SELECT 'playback.player_choice', '"mpv"'
WHERE EXISTS (SELECT 1 FROM settings) OR EXISTS (SELECT 1 FROM anime);
