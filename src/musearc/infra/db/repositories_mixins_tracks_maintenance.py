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

class RepositoryTracksMaintenanceMixin:
    """Repository mixin: track search/delete/restore maintenance operations."""

    def search_tracks(self, query: str, limit: int = 100) -> list[dict]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1asearch_tracks\u3002"""
        if query.strip():
            token = f"%{query.strip()}%"
            rows = self.conn.execute(
                """
                SELECT t.track_id, t.file_name, t.title, t.artist, t.album, t.language_kind,
                       t.preference_level, t.duration_sec, t.quality_score, t.source_ext, t.storage_format, t.ext_json,
                       t.storage_relpath, t.source_relpath, t.source_fullpath, t.source_sha256,
                       l.source_relpath AS lyrics_source,
                       CASE WHEN EXISTS(
                         SELECT 1 FROM playlist_items fi
                         WHERE fi.playlist_id = ? AND fi.track_id = t.track_id
                       ) THEN 1 ELSE 0 END AS is_favorite
                FROM tracks t
                LEFT JOIN track_lyrics tl ON tl.track_id = t.track_id AND tl.is_primary = 1
                LEFT JOIN lyrics l ON l.lyrics_id = tl.lyrics_id
                WHERE t.deleted_at IS NULL
                  AND (
                    t.file_name LIKE ? OR
                    t.title LIKE ? OR
                    t.artist LIKE ? OR
                    t.album LIKE ? OR
                    t.source_relpath LIKE ? OR
                    t.source_fullpath LIKE ?
                  )
                ORDER BY t.artist, t.title
                LIMIT ?
                """,
                (
                    FAVORITES_PLAYLIST_ID,
                    token,
                    token,
                    token,
                    token,
                    token,
                    token,
                    limit,
                ),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT t.track_id, t.file_name, t.title, t.artist, t.album, t.language_kind,
                       t.preference_level, t.duration_sec, t.quality_score, t.source_ext, t.storage_format, t.ext_json,
                       t.storage_relpath, t.source_relpath, t.source_fullpath, t.source_sha256,
                       l.source_relpath AS lyrics_source,
                       CASE WHEN EXISTS(
                         SELECT 1 FROM playlist_items fi
                         WHERE fi.playlist_id = ? AND fi.track_id = t.track_id
                       ) THEN 1 ELSE 0 END AS is_favorite
                FROM tracks t
                LEFT JOIN track_lyrics tl ON tl.track_id = t.track_id AND tl.is_primary = 1
                LEFT JOIN lyrics l ON l.lyrics_id = tl.lyrics_id
                WHERE t.deleted_at IS NULL
                ORDER BY t.artist, t.title
                LIMIT ?
                """,
                (FAVORITES_PLAYLIST_ID, limit),
            ).fetchall()
        return self._enrich_track_rows(rows)

    def list_tracks(self, limit: int = 5000) -> list[dict]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1alist_tracks\u3002"""
        rows = self.search_tracks("", limit=limit)
        return rows

    def list_deleted_tracks(self, limit: int = 5000) -> list[dict]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1alist_deleted_tracks\u3002"""
        rows = self.conn.execute(
            """
            SELECT t.track_id, t.file_name, t.title, t.artist, t.album, t.language_kind,
                   t.preference_level, t.duration_sec, t.quality_score, t.source_ext, t.storage_format, t.ext_json,
                   t.storage_relpath, t.source_relpath, t.source_fullpath, t.deleted_at,
                   l.source_relpath AS lyrics_source,
                   CASE WHEN EXISTS(
                     SELECT 1 FROM playlist_items fi
                     WHERE fi.playlist_id = ? AND fi.track_id = t.track_id
                   ) THEN 1 ELSE 0 END AS is_favorite
            FROM tracks t
            LEFT JOIN track_lyrics tl ON tl.track_id = t.track_id AND tl.is_primary = 1
            LEFT JOIN lyrics l ON l.lyrics_id = tl.lyrics_id
            WHERE t.deleted_at IS NOT NULL
            ORDER BY t.deleted_at DESC
            LIMIT ?
            """,
            (FAVORITES_PLAYLIST_ID, limit),
        ).fetchall()
        return self._enrich_track_rows(rows)

    def soft_delete_tracks(self, track_ids: Iterable[str]) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1asoft_delete_tracks\u3002"""
        ids = [track_id for track_id in track_ids if track_id]
        if not ids:
            return 0
        placeholders = _placeholders(len(ids))
        now = _utc_now_iso()
        cursor = self.conn.execute(
            f"""
            UPDATE tracks
            SET deleted_at = ?, updated_at = ?
            WHERE track_id IN ({placeholders})
              AND deleted_at IS NULL
            """,
            tuple([now, now, *ids]),
        )
        return cursor.rowcount

    def cleanup_relations_after_soft_delete(self, track_ids: Iterable[str]) -> dict[str, int]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1acleanup_relations_after_soft_delete\u3002"""
        ids = [track_id for track_id in track_ids if track_id]
        if not ids:
            return {"fullscan_items_removed": 0, "review_items_removed": 0}

        placeholders = _placeholders(len(ids))

        fullscan_cursor = self.conn.execute(
            f"DELETE FROM fullscan_work_items WHERE track_id IN ({placeholders})",
            tuple(ids),
        )

        removed_reviews = 0
        for track_id in ids:
            cursor = self.conn.execute(
                "DELETE FROM review_queue WHERE status = 'pending' AND payload_json LIKE ?",
                (f"%{track_id}%",),
            )
            removed_reviews += int(cursor.rowcount or 0)

        return {
            "fullscan_items_removed": int(fullscan_cursor.rowcount or 0),
            "review_items_removed": removed_reviews,
        }

    def restore_tracks(self, track_ids: Iterable[str]) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1arestore_tracks\u3002"""
        ids = [track_id for track_id in track_ids if track_id]
        if not ids:
            return 0
        placeholders = _placeholders(len(ids))
        now = _utc_now_iso()
        cursor = self.conn.execute(
            f"""
            UPDATE tracks
            SET deleted_at = NULL, updated_at = ?
            WHERE track_id IN ({placeholders})
            """,
            tuple([now, *ids]),
        )
        return cursor.rowcount

    def cascade_delete_lyrics_for_tracks(self, track_ids: Iterable[str]) -> list[str]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1acascade_delete_lyrics_for_tracks\u3002"""
        ids = [track_id for track_id in track_ids if track_id]
        if not ids:
            return []
        placeholders = _placeholders(len(ids))

        linked_rows = self.conn.execute(
            f"""
            SELECT DISTINCT l.lyrics_id, l.storage_relpath
            FROM lyrics l
            JOIN track_lyrics tl ON tl.lyrics_id = l.lyrics_id
            WHERE tl.track_id IN ({placeholders})
            """,
            tuple(ids),
        ).fetchall()
        if not linked_rows:
            return []

        self.conn.execute(
            f"DELETE FROM track_lyrics WHERE track_id IN ({placeholders})",
            tuple(ids),
        )

        deleted_relpaths: list[str] = []
        for row in linked_rows:
            lyrics_id = str(row["lyrics_id"])
            rel = str(row["storage_relpath"] or "")
            still_used = self.conn.execute(
                "SELECT 1 FROM track_lyrics WHERE lyrics_id = ? LIMIT 1",
                (lyrics_id,),
            ).fetchone()
            if still_used:
                continue
            self.conn.execute("DELETE FROM lyrics WHERE lyrics_id = ?", (lyrics_id,))
            if rel:
                deleted_relpaths.append(rel)
        return deleted_relpaths

    def hard_delete_tracks(self, track_ids: Iterable[str]) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1ahard_delete_tracks\u3002"""
        ids = [track_id for track_id in track_ids if track_id]
        if not ids:
            return 0
        placeholders = _placeholders(len(ids))
        cursor = self.conn.execute(f"DELETE FROM tracks WHERE track_id IN ({placeholders})", tuple(ids))
        return cursor.rowcount
