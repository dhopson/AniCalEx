CREATE INDEX episode_airing_time ON episode_airing(airs_at);
CREATE INDEX airing_change_episode ON airing_change(episode_id);

-- One airing time per episode: the highest-precedence source that has one
-- (user > anilist > animeschedule > mal_broadcast).
CREATE VIEW episode_effective_airing AS
SELECT ea.episode_id, ea.source, ea.airs_at, ea.precision
FROM episode_airing ea
WHERE ea.airs_at IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM episode_airing better
      WHERE better.episode_id = ea.episode_id
        AND better.airs_at IS NOT NULL
        AND (CASE better.source WHEN 'user' THEN 0 WHEN 'anilist' THEN 1 WHEN 'animeschedule' THEN 2 ELSE 3 END)
          < (CASE ea.source     WHEN 'user' THEN 0 WHEN 'anilist' THEN 1 WHEN 'animeschedule' THEN 2 ELSE 3 END)
  );

-- Airing schedules are a new kind of data: force a refresh on the first start
-- after this upgrade instead of waiting out the 24-hour gate.
DELETE FROM sync_state WHERE key = 'refresh.last_run';
