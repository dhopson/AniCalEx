-- Initial schema.
-- Timestamps are INTEGER UTC epoch seconds.

CREATE TABLE anime (
    id                 INTEGER PRIMARY KEY,
    title              TEXT NOT NULL CHECK (length(trim(title)) > 0),
    title_en           TEXT,
    title_ja           TEXT,
    synonyms_json      TEXT NOT NULL DEFAULT '[]',
    synopsis           TEXT,
    media_type         TEXT NOT NULL DEFAULT 'unknown'
                       CHECK (media_type IN ('tv', 'movie', 'ova', 'ona', 'special', 'music', 'unknown')),
    airing_status      TEXT NOT NULL DEFAULT 'unknown'
                       CHECK (airing_status IN ('upcoming', 'airing', 'completed', 'cancelled', 'unknown')),
    total_episodes     INTEGER CHECK (total_episodes IS NULL OR total_episodes >= 0),
    episode_duration_s INTEGER CHECK (episode_duration_s IS NULL OR episode_duration_s > 0),
    start_date         TEXT,  -- ISO date, may be partial: '2026', '2026-10', '2026-10-04'
    end_date           TEXT,
    broadcast_day      INTEGER CHECK (broadcast_day IS NULL OR broadcast_day BETWEEN 0 AND 6),  -- 0 = Monday
    broadcast_time     TEXT,  -- 'HH:MM' in broadcast_tz
    broadcast_tz       TEXT,  -- IANA zone, e.g. 'Asia/Tokyo'
    cover_url          TEXT,
    cover_path         TEXT,
    in_library         INTEGER NOT NULL DEFAULT 1,
    added_manually     INTEGER NOT NULL DEFAULT 0,
    notes              TEXT,
    created_at         INTEGER NOT NULL,
    updated_at         INTEGER NOT NULL
);

-- Stable cross-provider identity; the dedup key for imports.
CREATE TABLE external_id (
    anime_id INTEGER NOT NULL REFERENCES anime(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('mal', 'anilist', 'animeschedule', 'anicli')),
    ext_id   TEXT NOT NULL,
    PRIMARY KEY (provider, ext_id),
    UNIQUE (anime_id, provider)
);

-- User-entered values that win over any external source.
CREATE TABLE field_override (
    anime_id   INTEGER NOT NULL REFERENCES anime(id) ON DELETE CASCADE,
    field      TEXT NOT NULL,
    value_json TEXT,
    set_at     INTEGER NOT NULL,
    PRIMARY KEY (anime_id, field)
);

CREATE TABLE tag (
    anime_id INTEGER NOT NULL REFERENCES anime(id) ON DELETE CASCADE,
    tag      TEXT NOT NULL CHECK (length(trim(tag)) > 0),
    PRIMARY KEY (anime_id, tag)
);

-- MAL's own view of the list entry, stored verbatim.
CREATE TABLE mal_list_entry (
    anime_id         INTEGER PRIMARY KEY REFERENCES anime(id) ON DELETE CASCADE,
    list_status      TEXT NOT NULL,
    episodes_watched INTEGER NOT NULL DEFAULT 0,
    is_rewatching    INTEGER NOT NULL DEFAULT 0,
    mal_updated_at   INTEGER,
    imported_at      INTEGER NOT NULL
);

CREATE TABLE episode (
    id           INTEGER PRIMARY KEY,
    anime_id     INTEGER NOT NULL REFERENCES anime(id) ON DELETE CASCADE,
    number       INTEGER NOT NULL CHECK (number >= 0),
    kind         TEXT NOT NULL DEFAULT 'regular' CHECK (kind IN ('regular', 'special', 'recap')),
    title        TEXT,
    title_source TEXT,
    UNIQUE (anime_id, kind, number)
);

CREATE TABLE episode_airing (
    episode_id  INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
    source      TEXT NOT NULL CHECK (source IN ('user', 'anilist', 'animeschedule', 'mal_broadcast')),
    airs_at     INTEGER,
    precision   TEXT NOT NULL CHECK (precision IN ('exact', 'day', 'estimated')),
    sub_airs_at INTEGER,
    dub_airs_at INTEGER,
    fetched_at  INTEGER NOT NULL,
    PRIMARY KEY (episode_id, source)
);

CREATE TABLE airing_change (
    id          INTEGER PRIMARY KEY,
    episode_id  INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
    source      TEXT NOT NULL,
    old_airs_at INTEGER,
    new_airs_at INTEGER,
    detected_at INTEGER NOT NULL
);

CREATE TABLE import_batch (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,
    started_at   INTEGER NOT NULL,
    summary_json TEXT
);

-- Append-only. Effective state is resolved by the episode_watch_state view.
CREATE TABLE watch_event (
    id           INTEGER PRIMARY KEY,
    episode_id   INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
    state        TEXT NOT NULL CHECK (state IN ('watched', 'unwatched')),
    source       TEXT NOT NULL CHECK (source IN ('user', 'playback', 'mal_import')),
    progress_pct REAL,
    batch_id     INTEGER REFERENCES import_batch(id) ON DELETE CASCADE,
    occurred_at  INTEGER NOT NULL
);
CREATE INDEX watch_event_episode ON watch_event(episode_id, occurred_at, id);

CREATE TABLE playback_mapping (
    anime_id       INTEGER PRIMARY KEY REFERENCES anime(id) ON DELETE CASCADE,
    query          TEXT NOT NULL,
    result_title   TEXT,
    anicli_id      TEXT,
    episode_offset INTEGER NOT NULL DEFAULT 0,
    mode           TEXT CHECK (mode IS NULL OR mode IN ('sub', 'dub')),
    anicli_version TEXT,
    verified_at    INTEGER
);

CREATE TABLE playback_session (
    id         INTEGER PRIMARY KEY,
    episode_id INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
    started_at INTEGER NOT NULL,
    ended_at   INTEGER,
    exit_code  INTEGER,
    end_reason TEXT,
    percent    REAL,
    error      TEXT
);

CREATE TABLE refresh_state (
    anime_id     INTEGER NOT NULL REFERENCES anime(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('metadata', 'schedule', 'mal_list')),
    last_attempt INTEGER,
    last_success INTEGER,
    next_due     INTEGER,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    PRIMARY KEY (anime_id, kind)
);

CREATE TABLE sync_state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at INTEGER NOT NULL
);

CREATE TABLE notification_sent (
    episode_id INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    sent_at    INTEGER NOT NULL,
    PRIMARY KEY (episode_id, kind)
);

CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

-- Watched resolution: the latest locally
-- confirmed event (user or playback) wins; otherwise the latest MAL import;
-- otherwise unwatched.
CREATE VIEW episode_watch_state AS
SELECT
    e.id       AS episode_id,
    e.anime_id AS anime_id,
    COALESCE(
        (SELECT w.state FROM watch_event w
          WHERE w.episode_id = e.id AND w.source IN ('user', 'playback')
          ORDER BY w.occurred_at DESC, w.id DESC LIMIT 1),
        (SELECT w.state FROM watch_event w
          WHERE w.episode_id = e.id AND w.source = 'mal_import'
          ORDER BY w.occurred_at DESC, w.id DESC LIMIT 1),
        'unwatched'
    ) AS state
FROM episode e;
