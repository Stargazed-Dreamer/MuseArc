from __future__ import annotations

import json

from musearc.core.models import UndoAction
from musearc.infra.db.repositories_common import (
    FAVORITES_PLAYLIST_ID,
    _placeholders,
    _utc_now_iso,
)

class RepositoryOpsMixin:
    """Repository mixin: undo and fullscan queue operations."""

    def append_undo_action(self, action_id: str, action_type: str, payload: dict, max_keep: int) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aappend_undo_action\u3002"""
        self.conn.execute(
            """
            INSERT INTO undo_actions(action_id, action_type, payload_json, created_at)
            VALUES(?, ?, ?, ?)
            """,
            (action_id, action_type, json.dumps(payload, ensure_ascii=False), _utc_now_iso()),
        )
        self.prune_undo_actions(max_keep)

    def prune_undo_actions(self, max_keep: int) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aprune_undo_actions\u3002"""
        if max_keep <= 0:
            self.conn.execute("DELETE FROM undo_actions")
            return
        self.conn.execute(
            """
            DELETE FROM undo_actions
            WHERE action_id IN (
              SELECT action_id
              FROM undo_actions
              ORDER BY created_at DESC
              LIMIT -1 OFFSET ?
            )
            """,
            (max_keep,),
        )

    def list_undo_actions(self, limit: int = 50) -> list[UndoAction]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1alist_undo_actions\u3002"""
        rows = self.conn.execute(
            """
            SELECT action_id, action_type, payload_json, created_at
            FROM undo_actions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out: list[UndoAction] = []
        for row in rows:
            out.append(
                UndoAction(
                    action_id=row[0],
                    action_type=row[1],
                    payload=json.loads(row[2]),
                    created_at=row[3],
                )
            )
        return out

    def pop_latest_undo_action(self) -> UndoAction | None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1apop_latest_undo_action\u3002"""
        row = self.conn.execute(
            """
            SELECT action_id, action_type, payload_json, created_at
            FROM undo_actions
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        self.conn.execute("DELETE FROM undo_actions WHERE action_id = ?", (row[0],))
        return UndoAction(action_id=row[0], action_type=row[1], payload=json.loads(row[2]), created_at=row[3])

    def create_fullscan_work(self, work_id: str, name: str, track_ids: list[str]) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1acreate_fullscan_work\u3002"""
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO fullscan_works(work_id, name, status, created_at, updated_at)
            VALUES(?, ?, 'active', ?, ?)
            """,
            (work_id, name, now, now),
        )
        for idx, track_id in enumerate(track_ids):
            self.conn.execute(
                """
                INSERT INTO fullscan_work_items(work_id, track_id, queue_index, status, note, created_at, updated_at)
                VALUES(?, ?, ?, 'todo', '', ?, ?)
                """,
                (work_id, track_id, idx, now, now),
            )

    def list_fullscan_works(self) -> list[dict]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1alist_fullscan_works\u3002"""
        rows = self.conn.execute(
            """
            SELECT w.work_id, w.name, w.status, w.created_at, w.updated_at,
                   COUNT(i.track_id) AS total_items,
                   SUM(CASE WHEN i.status = 'todo' THEN 1 ELSE 0 END) AS todo_items
            FROM fullscan_works w
            LEFT JOIN fullscan_work_items i ON i.work_id = w.work_id
            GROUP BY w.work_id
            ORDER BY w.updated_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def get_fullscan_work_items(self, work_id: str, limit: int = 200000) -> list[dict]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aget_fullscan_work_items\u3002"""
        rows = self.conn.execute(
            """
            SELECT i.work_id, i.track_id, i.queue_index, i.status AS work_status, i.note,
                   t.file_name, t.title, t.artist, t.album, t.language_kind,
                   t.preference_level, t.duration_sec, t.source_ext, t.storage_format, t.ext_json,
                   t.source_relpath, t.source_fullpath,
                   t.storage_relpath, l.source_relpath AS lyrics_source,
                   CASE WHEN EXISTS(
                     SELECT 1 FROM playlist_items fi
                     WHERE fi.playlist_id = ? AND fi.track_id = t.track_id
                   ) THEN 1 ELSE 0 END AS is_favorite
            FROM fullscan_work_items i
            JOIN tracks t ON t.track_id = i.track_id
            LEFT JOIN track_lyrics tl ON tl.track_id = t.track_id AND tl.is_primary = 1
            LEFT JOIN lyrics l ON l.lyrics_id = tl.lyrics_id
            WHERE i.work_id = ?
            ORDER BY i.queue_index ASC
            LIMIT ?
            """,
            (FAVORITES_PLAYLIST_ID, work_id, limit),
        ).fetchall()
        return self._enrich_track_rows(rows)

    def remove_fullscan_items(self, work_id: str, track_ids: list[str]) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aremove_fullscan_items\u3002"""
        ids = [v for v in track_ids if v]
        if not ids:
            return 0
        placeholders = _placeholders(len(ids))
        cursor = self.conn.execute(
            f"DELETE FROM fullscan_work_items WHERE work_id = ? AND track_id IN ({placeholders})",
            tuple([work_id, *ids]),
        )
        self._compact_fullscan_queue(work_id)
        self.conn.execute("UPDATE fullscan_works SET updated_at = ? WHERE work_id = ?", (_utc_now_iso(), work_id))
        return cursor.rowcount

    def update_fullscan_items_status(self, work_id: str, track_ids: list[str], status: str) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aupdate_fullscan_items_status\u3002"""
        ids = [v for v in track_ids if v]
        if not ids:
            return 0
        placeholders = _placeholders(len(ids))
        cursor = self.conn.execute(
            f"UPDATE fullscan_work_items SET status = ?, updated_at = ? WHERE work_id = ? AND track_id IN ({placeholders})",
            tuple([status, _utc_now_iso(), work_id, *ids]),
        )
        self.conn.execute("UPDATE fullscan_works SET updated_at = ? WHERE work_id = ?", (_utc_now_iso(), work_id))
        return cursor.rowcount

    def delete_fullscan_work(self, work_id: str) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1adelete_fullscan_work\u3002"""
        cursor = self.conn.execute("DELETE FROM fullscan_works WHERE work_id = ?", (work_id,))
        return cursor.rowcount

    def _compact_fullscan_queue(self, work_id: str) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1a_compact_fullscan_queue\u3002"""
        rows = self.conn.execute(
            "SELECT track_id FROM fullscan_work_items WHERE work_id = ? ORDER BY queue_index ASC",
            (work_id,),
        ).fetchall()
        for idx, row in enumerate(rows):
            self.conn.execute(
                "UPDATE fullscan_work_items SET queue_index = ?, updated_at = ? WHERE work_id = ? AND track_id = ?",
                (idx, _utc_now_iso(), work_id, row[0]),
            )
