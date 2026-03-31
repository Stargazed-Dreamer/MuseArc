from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from musearc.core.models import LyricsInsert, ReviewItem, TrackInsert, UndoAction
from musearc.infra.db.repositories_common import (
    DEFAULT_TAG_FIELD,
    DEFAULT_TAG_FIELDS,
    FAVORITES_PLAYLIST_ID,
    FAVORITES_PLAYLIST_NAME,
    _normalize_tags,
    _placeholders,
    _safe_json_loads,
    _utc_now_iso,
)

class RepositoryPlaylistsMixin:
    """Repository mixin: playlist and favorites operations."""

    def ensure_favorites_playlist(self) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aensure_favorites_playlist\u3002"""
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO playlists(playlist_id, name, description, created_at, updated_at)
            VALUES(?, ?, '', ?, ?)
            """,
            (FAVORITES_PLAYLIST_ID, FAVORITES_PLAYLIST_NAME, now, now),
        )

    def list_playlists(self) -> list[dict]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1alist_playlists\u3002"""
        self.ensure_favorites_playlist()
        rows = self.conn.execute(
            """
            SELECT p.playlist_id, p.name, p.description, p.created_at, p.updated_at,
                   COALESCE(SUM(CASE WHEN t.track_id IS NOT NULL AND t.deleted_at IS NULL THEN 1 ELSE 0 END), 0) AS track_count
            FROM playlists p
            LEFT JOIN playlist_items i ON i.playlist_id = p.playlist_id
            LEFT JOIN tracks t ON t.track_id = i.track_id
            GROUP BY p.playlist_id
            ORDER BY CASE WHEN p.playlist_id = ? THEN 0 ELSE 1 END, p.updated_at DESC
            """
            ,
            (FAVORITES_PLAYLIST_ID,),
        ).fetchall()
        return [dict(r) for r in rows]

    def create_playlist(self, playlist_id: str, name: str, description: str = "") -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1acreate_playlist\u3002"""
        if playlist_id == FAVORITES_PLAYLIST_ID:
            name = FAVORITES_PLAYLIST_NAME
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO playlists(playlist_id, name, description, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (playlist_id, name, description, now, now),
        )

    def delete_playlist(self, playlist_id: str) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1adelete_playlist\u3002"""
        if playlist_id == FAVORITES_PLAYLIST_ID:
            return 0
        cursor = self.conn.execute("DELETE FROM playlists WHERE playlist_id = ?", (playlist_id,))
        return cursor.rowcount

    def list_playlist_items(self, playlist_id: str) -> list[dict]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1alist_playlist_items\u3002"""
        self.ensure_favorites_playlist()
        rows = self.conn.execute(
            """
            SELECT i.position, i.entry, t.track_id, t.file_name, t.title, t.artist, t.album,
                   t.language_kind, t.preference_level, t.duration_sec, t.source_ext, t.storage_format, t.ext_json,
                   t.source_relpath, t.source_fullpath, t.storage_relpath,
                   l.source_relpath AS lyrics_source,
                   CASE WHEN EXISTS(
                     SELECT 1 FROM playlist_items fi
                     WHERE fi.playlist_id = ? AND fi.track_id = t.track_id
                   ) THEN 1 ELSE 0 END AS is_favorite
            FROM playlist_items i
            JOIN tracks t ON t.track_id = i.track_id
            LEFT JOIN track_lyrics tl ON tl.track_id = t.track_id AND tl.is_primary = 1
            LEFT JOIN lyrics l ON l.lyrics_id = tl.lyrics_id
            WHERE i.playlist_id = ? AND t.deleted_at IS NULL
            ORDER BY i.position ASC
            """,
            (FAVORITES_PLAYLIST_ID, playlist_id),
        ).fetchall()
        return self._enrich_track_rows(rows)

    def add_tracks_to_playlist(self, playlist_id: str, track_ids: list[str]) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aadd_tracks_to_playlist\u3002"""
        self.ensure_favorites_playlist()
        ids = [track_id for track_id in track_ids if track_id]
        if not ids:
            return 0
        row_pos = self.conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM playlist_items WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        row_entry = self.conn.execute(
            "SELECT COALESCE(MAX(entry), -1) FROM playlist_items WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        start_pos = int(row_pos[0]) + 1
        start_entry = int(row_entry[0]) + 1
        now = _utc_now_iso()
        inserted = 0
        for track_id in ids:
            exists = self.conn.execute(
                "SELECT 1 FROM playlist_items WHERE playlist_id = ? AND track_id = ?",
                (playlist_id, track_id),
            ).fetchone()
            if exists:
                continue
            self.conn.execute(
                """
                INSERT INTO playlist_items(playlist_id, position, entry, track_id, added_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (playlist_id, start_pos + inserted, start_entry + inserted, track_id, now),
            )
            inserted += 1
        self.conn.execute(
            "UPDATE playlists SET updated_at = ? WHERE playlist_id = ?",
            (_utc_now_iso(), playlist_id),
        )
        return inserted

    def remove_tracks_from_playlist(self, playlist_id: str, track_ids: list[str]) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aremove_tracks_from_playlist\u3002"""
        ids = [track_id for track_id in track_ids if track_id]
        if not ids:
            return 0
        placeholders = _placeholders(len(ids))
        cursor = self.conn.execute(
            f"DELETE FROM playlist_items WHERE playlist_id = ? AND track_id IN ({placeholders})",
            tuple([playlist_id, *ids]),
        )
        self._compact_playlist_positions(playlist_id)
        self.conn.execute("UPDATE playlists SET updated_at = ? WHERE playlist_id = ?", (_utc_now_iso(), playlist_id))
        return cursor.rowcount

    def clear_playlist(self, playlist_id: str) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aclear_playlist\u3002"""
        cursor = self.conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
        self.conn.execute("UPDATE playlists SET updated_at = ? WHERE playlist_id = ?", (_utc_now_iso(), playlist_id))
        return cursor.rowcount

    def update_playlist_entries(self, playlist_id: str, entries: dict[str, int]) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aupdate_playlist_entries\u3002"""
        if not entries:
            return 0
        updated = 0
        for track_id, entry_value in entries.items():
            cursor = self.conn.execute(
                """
                UPDATE playlist_items
                SET entry = ?
                WHERE playlist_id = ? AND track_id = ?
                """,
                (int(entry_value), playlist_id, track_id),
            )
            updated += int(cursor.rowcount or 0)

        ordered = self.conn.execute(
            """
            SELECT track_id
            FROM playlist_items
            WHERE playlist_id = ?
            ORDER BY entry ASC, position ASC
            """,
            (playlist_id,),
        ).fetchall()

        for idx, row in enumerate(ordered):
            self.conn.execute(
                "UPDATE playlist_items SET position = ? WHERE playlist_id = ? AND track_id = ?",
                (-(idx + 1), playlist_id, row[0]),
            )
        for idx, row in enumerate(ordered):
            self.conn.execute(
                "UPDATE playlist_items SET position = ? WHERE playlist_id = ? AND track_id = ?",
                (idx, playlist_id, row[0]),
            )
        self.conn.execute("UPDATE playlists SET updated_at = ? WHERE playlist_id = ?", (_utc_now_iso(), playlist_id))
        return updated

    def reorder_playlist(self, playlist_id: str, ordered_track_ids: list[str]) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1areorder_playlist\u3002"""
        now = _utc_now_iso()
        ids = [track_id for track_id in ordered_track_ids if track_id]

        # Two-phase update avoids UNIQUE(playlist_id, position) collisions during swaps.
        for idx, track_id in enumerate(ids):
            self.conn.execute(
                """
                UPDATE playlist_items
                SET position = ?
                WHERE playlist_id = ? AND track_id = ?
                """,
                (-(idx + 1), playlist_id, track_id),
            )

        for idx, track_id in enumerate(ids):
            self.conn.execute(
                """
                UPDATE playlist_items
                SET position = ?, entry = ?
                WHERE playlist_id = ? AND track_id = ?
                """,
                (idx, idx, playlist_id, track_id),
            )

        self._compact_playlist_positions(playlist_id)
        self.conn.execute("UPDATE playlists SET updated_at = ? WHERE playlist_id = ?", (now, playlist_id))

    def _compact_playlist_positions(self, playlist_id: str) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1a_compact_playlist_positions\u3002"""
        rows = self.conn.execute(
            "SELECT track_id FROM playlist_items WHERE playlist_id = ? ORDER BY position ASC",
            (playlist_id,),
        ).fetchall()
        for idx, row in enumerate(rows):
            self.conn.execute(
                "UPDATE playlist_items SET position = ? WHERE playlist_id = ? AND track_id = ?",
                (idx, playlist_id, row[0]),
            )
