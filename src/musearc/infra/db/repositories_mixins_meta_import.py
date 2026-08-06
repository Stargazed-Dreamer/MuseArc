from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import datetime

from musearc.core.constants import DEFAULT_TAG_FIELDS
from musearc.infra.db.repositories_common import (
    _normalize_tags,
    _placeholders,
    _utc_now_iso,
)

logger = logging.getLogger(__name__)

class RepositoryMetaImportMixin:
    """Repository mixin: tag/meta/import-batch operations."""

    def ensure_default_tag_field(self) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aensure_default_tag_field\u3002"""
        now = _utc_now_iso()
        for name in DEFAULT_TAG_FIELDS:
            self.conn.execute(
                "INSERT OR IGNORE INTO tag_fields(tag_name, created_at) VALUES(?, ?)",
                (name, now),
            )

    def list_tag_fields(self) -> list[dict]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1alist_tag_fields\u3002"""
        self.ensure_default_tag_field()
        rows = self.conn.execute("SELECT tag_name FROM tag_fields ORDER BY tag_name COLLATE NOCASE ASC").fetchall()
        fields = [str(r[0]) for r in rows]
        if not fields:
            return []
        tracks_rows = self.conn.execute("SELECT ext_json FROM tracks WHERE deleted_at IS NULL").fetchall()
        counts = dict.fromkeys(fields, 0)
        for row in tracks_rows:
            _, tags = _normalize_tags(row[0] if row else None)
            for name in fields:
                value = str(tags.get(name, "")).strip()
                if value:
                    counts[name] += 1
        return [{"tag_name": name, "track_count": counts.get(name, 0)} for name in fields]

    def create_tag_field(self, tag_name: str) -> bool:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1acreate_tag_field\u3002"""
        name = str(tag_name).strip()
        if not name:
            return False
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO tag_fields(tag_name, created_at) VALUES(?, ?)",
            (name, _utc_now_iso()),
        )
        return bool(cursor.rowcount)

    def delete_tag_field(self, tag_name: str) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1adelete_tag_field\u3002"""
        name = str(tag_name).strip()
        if not name:
            return 0
        self.ensure_default_tag_field()
        if name in DEFAULT_TAG_FIELDS:
            return 0
        cursor = self.conn.execute("DELETE FROM tag_fields WHERE tag_name = ?", (name,))
        if cursor.rowcount <= 0:
            return 0
        rows = self.conn.execute("SELECT track_id, ext_json FROM tracks").fetchall()
        for row in rows:
            track_id = str(row["track_id"])
            payload, tags = _normalize_tags(row["ext_json"])
            if name in tags:
                tags.pop(name, None)
                payload["tags"] = tags
                self.conn.execute("UPDATE tracks SET ext_json = ?, updated_at = ? WHERE track_id = ?", (json.dumps(payload, ensure_ascii=False), _utc_now_iso(), track_id))
        return int(cursor.rowcount)

    def update_track_tag_values(self, track_ids: Iterable[str], tag_name: str, value: str) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aupdate_track_tag_values\u3002"""
        ids = [track_id for track_id in track_ids if track_id]
        name = str(tag_name).strip()
        if not ids or not name:
            return 0
        self.create_tag_field(name)
        cleaned = str(value) if value is not None else ""
        placeholders = _placeholders(len(ids))
        rows = self.conn.execute(
            f"SELECT track_id, ext_json FROM tracks WHERE track_id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
        updated = 0
        for row in rows:
            payload, tags = _normalize_tags(row["ext_json"])
            if cleaned.strip():
                tags[name] = cleaned
            else:
                tags.pop(name, None)
            payload["tags"] = tags
            self.conn.execute(
                "UPDATE tracks SET ext_json = ?, updated_at = ? WHERE track_id = ?",
                (json.dumps(payload, ensure_ascii=False), _utc_now_iso(), row["track_id"]),
            )
            updated += 1
        logger.info("[Repo] update_track_tag_values: ids=%s tag=%s val=%r updated=%d", ids, name, cleaned, updated)
        return updated

    def set_meta(self, key: str, value: str) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aset_meta\u3002"""
        self.conn.execute(
            """
            INSERT INTO library_meta(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )

    def get_meta(self, key: str) -> str | None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aget_meta\u3002"""
        row = self.conn.execute("SELECT value FROM library_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def start_import_batch(self, import_batch_id: str, source_path: str, started_at: datetime) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1astart_import_batch\u3002"""
        self.conn.execute(
            """
            INSERT OR IGNORE INTO import_batches(import_batch_id, source_path, started_at)
            VALUES(?, ?, ?)
            """,
            (import_batch_id, source_path, started_at.isoformat()),
        )

    def update_import_batch_progress(
        self,
        import_batch_id: str,
        *,
        scanned_files: int,
        imported_tracks: int,
        duplicate_tracks: int,
        imported_lyrics: int,
        matched_lyrics: int,
        review_items: int,
        errors: list[str],
    ) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aupdate_import_batch_progress\u3002"""
        self.conn.execute(
            """
            UPDATE import_batches
            SET scanned_files = ?,
                imported_tracks = ?,
                duplicate_tracks = ?,
                imported_lyrics = ?,
                matched_lyrics = ?,
                review_items = ?,
                errors_json = ?
            WHERE import_batch_id = ?
            """,
            (
                scanned_files,
                imported_tracks,
                duplicate_tracks,
                imported_lyrics,
                matched_lyrics,
                review_items,
                json.dumps(errors, ensure_ascii=False),
                import_batch_id,
            ),
        )

    def finish_import_batch(
        self,
        import_batch_id: str,
        *,
        scanned_files: int,
        imported_tracks: int,
        duplicate_tracks: int,
        imported_lyrics: int,
        matched_lyrics: int,
        review_items: int,
        errors: list[str],
        finished_at: datetime,
    ) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1afinish_import_batch\u3002"""
        self.conn.execute(
            """
            UPDATE import_batches
            SET finished_at = ?,
                scanned_files = ?,
                imported_tracks = ?,
                duplicate_tracks = ?,
                imported_lyrics = ?,
                matched_lyrics = ?,
                review_items = ?,
                errors_json = ?
            WHERE import_batch_id = ?
            """,
            (
                finished_at.isoformat(),
                scanned_files,
                imported_tracks,
                duplicate_tracks,
                imported_lyrics,
                matched_lyrics,
                review_items,
                json.dumps(errors, ensure_ascii=False),
                import_batch_id,
            ),
        )

    def delete_import_batch(self, import_batch_id: str) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1adelete_import_batch\u3002"""
        self.conn.execute("DELETE FROM import_batches WHERE import_batch_id = ?", (import_batch_id,))

    def list_import_batches(self, limit: int = 200) -> list[dict]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1alist_import_batches\u3002"""
        rows = self.conn.execute(
            """
            SELECT import_batch_id, source_path, started_at, finished_at,
                   scanned_files, imported_tracks, duplicate_tracks,
                   imported_lyrics, matched_lyrics, review_items, errors_json
            FROM import_batches
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["errors"] = json.loads(item.pop("errors_json") or "[]")
            out.append(item)
        return out

    def get_import_batch(self, import_batch_id: str) -> dict | None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aget_import_batch\u3002"""
        row = self.conn.execute(
            """
            SELECT import_batch_id, source_path, started_at, finished_at,
                   scanned_files, imported_tracks, duplicate_tracks,
                   imported_lyrics, matched_lyrics, review_items, errors_json
            FROM import_batches
            WHERE import_batch_id = ?
            LIMIT 1
            """,
            (import_batch_id,),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["errors"] = json.loads(item.pop("errors_json") or "[]")
        return item
