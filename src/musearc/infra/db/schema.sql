PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS library_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_batches (
  import_batch_id TEXT PRIMARY KEY,
  source_path TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  scanned_files INTEGER NOT NULL DEFAULT 0,
  imported_tracks INTEGER NOT NULL DEFAULT 0,
  duplicate_tracks INTEGER NOT NULL DEFAULT 0,
  imported_lyrics INTEGER NOT NULL DEFAULT 0,
  matched_lyrics INTEGER NOT NULL DEFAULT 0,
  review_items INTEGER NOT NULL DEFAULT 0,
  errors_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS tracks (
  track_id TEXT PRIMARY KEY,
  file_name TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  artist TEXT NOT NULL,
  album TEXT NOT NULL,
  language_kind TEXT NOT NULL DEFAULT '',
  preference_level INTEGER NOT NULL DEFAULT 5,
  storage_format TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  duration_sec REAL NOT NULL,
  sample_rate INTEGER,
  channels INTEGER,
  bit_rate INTEGER,
  quality_score REAL NOT NULL,
  storage_relpath TEXT NOT NULL,
  source_relpath TEXT NOT NULL,
  source_fullpath TEXT NOT NULL DEFAULT '',
  source_sha256 TEXT NOT NULL,
  source_ext TEXT NOT NULL,
  probe_codec TEXT,
  file_health TEXT NOT NULL,
  fingerprint_version INTEGER NOT NULL,
  fingerprint_digest TEXT NOT NULL,
  fingerprint_hash32 INTEGER,
  fingerprint_payload TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT,
  ext_json TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_sha ON tracks(source_sha256);
CREATE INDEX IF NOT EXISTS idx_tracks_duration ON tracks(duration_sec);
CREATE INDEX IF NOT EXISTS idx_tracks_fp_digest ON tracks(fingerprint_digest);
CREATE INDEX IF NOT EXISTS idx_tracks_fp_hash32 ON tracks(fingerprint_hash32);
CREATE INDEX IF NOT EXISTS idx_tracks_search ON tracks(title, artist, album);
CREATE INDEX IF NOT EXISTS idx_tracks_source_fullpath ON tracks(source_fullpath);

CREATE TABLE IF NOT EXISTS track_variants (
  variant_id TEXT PRIMARY KEY,
  primary_track_id TEXT NOT NULL REFERENCES tracks(track_id) ON DELETE CASCADE,
  variant_track_id TEXT NOT NULL REFERENCES tracks(track_id) ON DELETE CASCADE,
  relation_type TEXT NOT NULL,
  similarity_score REAL NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  ext_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(primary_track_id, variant_track_id)
);

CREATE TABLE IF NOT EXISTS lyrics (
  lyrics_id TEXT PRIMARY KEY,
  source_relpath TEXT NOT NULL,
  storage_relpath TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  raw_encoding TEXT NOT NULL,
  lyrics_title TEXT NOT NULL DEFAULT '',
  lyrics_artist TEXT NOT NULL DEFAULT '',
  lyrics_album TEXT NOT NULL DEFAULT '',
  lyrics_author TEXT NOT NULL DEFAULT '',
  line_count INTEGER NOT NULL DEFAULT 0,
  imported_at TEXT NOT NULL,
  deleted_at TEXT,
  ext_json TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_lyrics_hash ON lyrics(text_hash);

CREATE TABLE IF NOT EXISTS track_lyrics (
  track_id TEXT NOT NULL REFERENCES tracks(track_id) ON DELETE CASCADE,
  lyrics_id TEXT NOT NULL REFERENCES lyrics(lyrics_id) ON DELETE CASCADE,
  confidence REAL NOT NULL,
  match_method TEXT NOT NULL,
  is_primary INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  ext_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(track_id, lyrics_id)
);

CREATE INDEX IF NOT EXISTS idx_track_lyrics_track ON track_lyrics(track_id, is_primary);

CREATE TABLE IF NOT EXISTS playlists (
  playlist_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  ext_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS playlist_items (
  playlist_id TEXT NOT NULL REFERENCES playlists(playlist_id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  entry INTEGER NOT NULL DEFAULT 0,
  track_id TEXT NOT NULL REFERENCES tracks(track_id) ON DELETE CASCADE,
  added_at TEXT NOT NULL,
  PRIMARY KEY(playlist_id, position)
);

CREATE INDEX IF NOT EXISTS idx_playlist_items_track ON playlist_items(track_id);

CREATE TABLE IF NOT EXISTS review_queue (
  review_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  priority INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  resolved_at TEXT,
  ext_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue(status, priority);

CREATE TABLE IF NOT EXISTS undo_actions (
  action_id TEXT PRIMARY KEY,
  action_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_undo_created_at ON undo_actions(created_at);

CREATE TABLE IF NOT EXISTS fullscan_works (
  work_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  ext_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS fullscan_work_items (
  work_id TEXT NOT NULL REFERENCES fullscan_works(work_id) ON DELETE CASCADE,
  track_id TEXT NOT NULL REFERENCES tracks(track_id) ON DELETE CASCADE,
  queue_index INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'todo',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(work_id, track_id)
);

CREATE INDEX IF NOT EXISTS idx_fullscan_items_work ON fullscan_work_items(work_id, queue_index);

CREATE TABLE IF NOT EXISTS tag_fields (
  tag_name TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);
