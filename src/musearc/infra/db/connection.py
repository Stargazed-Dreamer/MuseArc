from __future__ import annotations

import sqlite3
from pathlib import Path

from musearc.config.models import LibraryLayout


class DbSession:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._conn is None:
            return
        if exc_type is None:
            if int(self._conn.total_changes) > 0:
                self._conn.commit()
            else:
                self._conn.rollback()
        else:
            self._conn.rollback()
        self._conn.close()


class DbManager:
    def __init__(self, layout: LibraryLayout):
        self.layout = layout

    def ensure_layout(self) -> None:
        self.layout.root.mkdir(parents=True, exist_ok=True)
        self.layout.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.layout.tracks_root.mkdir(parents=True, exist_ok=True)
        self.layout.lyrics_root.mkdir(parents=True, exist_ok=True)
        self.layout.imports_root.mkdir(parents=True, exist_ok=True)
        self.layout.exports_root.mkdir(parents=True, exist_ok=True)
        self.layout.trash_root.mkdir(parents=True, exist_ok=True)

    def init_schema(self) -> None:
        schema_path = Path(__file__).with_name("schema.sql")
        sql = schema_path.read_text(encoding="utf-8")
        with self.session() as conn:
            # 1) migrate old existing tables first (so later index DDL won't fail)
            self._migrate_schema(conn)
            # 2) apply schema for new libraries / missing objects
            conn.executescript(sql)
            # 3) run migration again to keep idempotent behavior for future columns
            self._migrate_schema(conn)

    def session(self) -> DbSession:
        return DbSession(self.layout.db_path)

    @staticmethod
    def _migrate_schema(conn: sqlite3.Connection) -> None:
        def table_exists(table: str) -> bool:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
                (table,),
            ).fetchone()
            return bool(row)

        def has_column(table: str, column: str) -> bool:
            if not table_exists(table):
                return False
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            return any(str(row[1]) == column for row in rows)

        def add_column_if_missing(table: str, ddl: str, column: str) -> None:
            if not table_exists(table):
                return
            if not has_column(table, column):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

        add_column_if_missing("tracks", "file_name TEXT NOT NULL DEFAULT ''", "file_name")
        add_column_if_missing("tracks", "language_kind TEXT NOT NULL DEFAULT ''", "language_kind")
        add_column_if_missing("tracks", "preference_level INTEGER NOT NULL DEFAULT 5", "preference_level")
        add_column_if_missing("tracks", "source_fullpath TEXT NOT NULL DEFAULT ''", "source_fullpath")
        add_column_if_missing("tracks", "storage_format TEXT NOT NULL DEFAULT ''", "storage_format")
        add_column_if_missing("playlist_items", "entry INTEGER NOT NULL DEFAULT -1", "entry")
        add_column_if_missing("lyrics", "lyrics_title TEXT NOT NULL DEFAULT ''", "lyrics_title")
        add_column_if_missing("lyrics", "lyrics_artist TEXT NOT NULL DEFAULT ''", "lyrics_artist")
        add_column_if_missing("lyrics", "lyrics_album TEXT NOT NULL DEFAULT ''", "lyrics_album")
        add_column_if_missing("lyrics", "lyrics_author TEXT NOT NULL DEFAULT ''", "lyrics_author")
        add_column_if_missing("lyrics", "line_count INTEGER NOT NULL DEFAULT 0", "line_count")
        add_column_if_missing("lyrics", "deleted_at TEXT", "deleted_at")

        if table_exists("playlist_items"):
            conn.execute("UPDATE playlist_items SET entry = position WHERE entry < 0")

        # Ensure preference range remains valid for older data.
        if table_exists("tracks"):
            conn.execute(
                """
                UPDATE tracks
                SET preference_level = CASE
                  WHEN preference_level < 1 THEN 1
                  WHEN preference_level > 10 THEN 10
                  ELSE preference_level
                END
                """
            )
            conn.execute(
                """
                UPDATE tracks
                SET storage_format = LOWER(REPLACE(source_ext, '.', ''))
                WHERE TRIM(storage_format) = ''
                """
            )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tag_fields (
              tag_name TEXT PRIMARY KEY,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO tag_fields(tag_name, created_at) VALUES('备注', datetime('now'))"
        )
