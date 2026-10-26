-- Where the base metadata columns of an anime row come from:
-- NULL = typed in by the user (edited in place); 'mal' = imported (user edits go to field_override).
ALTER TABLE anime ADD COLUMN metadata_source TEXT;

-- Base values with user overrides applied. An override row always wins,
-- including an explicit null (e.g. "episode count unknown").
CREATE VIEW anime_effective AS
SELECT
    a.id, a.added_manually, a.metadata_source, a.in_library, a.notes,
    a.cover_url, a.cover_path, a.episode_duration_s,
    a.broadcast_day, a.broadcast_time, a.broadcast_tz,
    a.created_at, a.updated_at,
    CASE WHEN o_title.anime_id    IS NULL THEN a.title          ELSE json_extract(o_title.value_json, '$')    END AS title,
    CASE WHEN o_title_en.anime_id IS NULL THEN a.title_en       ELSE json_extract(o_title_en.value_json, '$') END AS title_en,
    CASE WHEN o_title_ja.anime_id IS NULL THEN a.title_ja       ELSE json_extract(o_title_ja.value_json, '$') END AS title_ja,
    CASE WHEN o_synonyms.anime_id IS NULL THEN a.synonyms_json  ELSE o_synonyms.value_json                    END AS synonyms_json,
    CASE WHEN o_synopsis.anime_id IS NULL THEN a.synopsis       ELSE json_extract(o_synopsis.value_json, '$') END AS synopsis,
    CASE WHEN o_media.anime_id    IS NULL THEN a.media_type     ELSE json_extract(o_media.value_json, '$')    END AS media_type,
    CASE WHEN o_status.anime_id   IS NULL THEN a.airing_status  ELSE json_extract(o_status.value_json, '$')   END AS airing_status,
    CASE WHEN o_total.anime_id    IS NULL THEN a.total_episodes ELSE json_extract(o_total.value_json, '$')    END AS total_episodes,
    CASE WHEN o_start.anime_id    IS NULL THEN a.start_date     ELSE json_extract(o_start.value_json, '$')    END AS start_date,
    CASE WHEN o_end.anime_id      IS NULL THEN a.end_date       ELSE json_extract(o_end.value_json, '$')      END AS end_date
FROM anime a
LEFT JOIN field_override o_title    ON o_title.anime_id    = a.id AND o_title.field    = 'title'
LEFT JOIN field_override o_title_en ON o_title_en.anime_id = a.id AND o_title_en.field = 'title_en'
LEFT JOIN field_override o_title_ja ON o_title_ja.anime_id = a.id AND o_title_ja.field = 'title_ja'
LEFT JOIN field_override o_synonyms ON o_synonyms.anime_id = a.id AND o_synonyms.field = 'synonyms'
LEFT JOIN field_override o_synopsis ON o_synopsis.anime_id = a.id AND o_synopsis.field = 'synopsis'
LEFT JOIN field_override o_media    ON o_media.anime_id    = a.id AND o_media.field    = 'media_type'
LEFT JOIN field_override o_status   ON o_status.anime_id   = a.id AND o_status.field   = 'airing_status'
LEFT JOIN field_override o_total    ON o_total.anime_id    = a.id AND o_total.field    = 'total_episodes'
LEFT JOIN field_override o_start    ON o_start.anime_id    = a.id AND o_start.field    = 'start_date'
LEFT JOIN field_override o_end      ON o_end.anime_id      = a.id AND o_end.field      = 'end_date';
