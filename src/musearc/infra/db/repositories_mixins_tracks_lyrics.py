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

class RepositoryTracksLyricsMixin:
    """Repository mixin: track/lyrics/review persistence operations."""

    def insert_track(self, item: TrackInsert) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1ainsert_track\u3002"""
        data = asdict(item)
        imported_at = data.pop("imported_at").isoformat()
        ext_payload = data.pop("ext_json", {})
        if not isinstance(ext_payload, dict):
            ext_payload = {}
        values = (
            data["track_id"],
            data["file_name"],
            data["title"],
            data["artist"],
            data["album"],
            data["language_kind"],
            data["preference_level"],
            data["storage_format"],
            str(data["kind"]),
            data["duration_sec"],
            data["sample_rate"],
            data["channels"],
            data["bit_rate"],
            data["quality_score"],
            data["storage_relpath"],
            data["source_relpath"],
            data["source_fullpath"],
            data["source_sha256"],
            data["source_ext"],
            data["probe_codec"],
            str(data["file_health"]),
            data["fingerprint_version"],
            data["fingerprint_digest"],
            data["fingerprint_payload"],
            imported_at,
            imported_at,
            json.dumps(ext_payload, ensure_ascii=False),
        )
        self.conn.execute(
            """
            INSERT INTO tracks(
              track_id, file_name, title, artist, album, language_kind, preference_level, storage_format,
              kind, duration_sec, sample_rate, channels, bit_rate, quality_score,
              storage_relpath, source_relpath, source_fullpath, source_sha256, source_ext,
              probe_codec, file_health, fingerprint_version, fingerprint_digest,
              fingerprint_payload, imported_at, updated_at, ext_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )

    def get_tracks_by_ids(self, track_ids: Iterable[str]) -> list[dict]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aget_tracks_by_ids\u3002"""
        ids = [track_id for track_id in track_ids if track_id]
        if not ids:
            return []
        placeholders = _placeholders(len(ids))
        rows = self.conn.execute(
            f"""
            SELECT track_id, file_name, title, artist, album, language_kind, preference_level,
                   duration_sec, quality_score, storage_relpath, source_relpath, source_fullpath, source_sha256,
                   source_ext, storage_format, ext_json
            FROM tracks
            WHERE track_id IN ({placeholders})
            """,
            tuple(ids),
        ).fetchall()
        return self._enrich_track_rows(rows)

    def get_track_by_source_sha(self, source_sha256: str) -> dict | None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aget_track_by_source_sha\u3002"""
        key = str(source_sha256 or "").strip()
        if not key:
            return None
        row = self.conn.execute(
            """
            SELECT track_id, file_name, title, artist, album, language_kind,
                   storage_relpath, source_relpath, source_fullpath, source_sha256,
                   source_ext, storage_format, imported_at, updated_at, deleted_at, ext_json
            FROM tracks
            WHERE source_sha256 = ?
            LIMIT 1
            """,
            (key,),
        ).fetchone()
        if not row:
            return None
        enriched = self._enrich_track_rows([row])
        return enriched[0] if enriched else None

    def get_track_by_source_fullpath(self, source_fullpath: str, *, include_deleted: bool = True) -> dict | None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aget_track_by_source_fullpath\u3002"""
        key = str(source_fullpath or "").strip()
        if not key:
            return None
        if include_deleted:
            sql = """
                SELECT track_id, file_name, title, artist, album, language_kind,
                       storage_relpath, source_relpath, source_fullpath, source_sha256,
                       source_ext, storage_format, imported_at, updated_at, deleted_at, ext_json
                FROM tracks
                WHERE LOWER(source_fullpath) = LOWER(?)
                LIMIT 1
            """
            args = (key,)
        else:
            sql = """
                SELECT track_id, file_name, title, artist, album, language_kind,
                       storage_relpath, source_relpath, source_fullpath, source_sha256,
                       source_ext, storage_format, imported_at, updated_at, deleted_at, ext_json
                FROM tracks
                WHERE LOWER(source_fullpath) = LOWER(?)
                  AND deleted_at IS NULL
                LIMIT 1
            """
            args = (key,)
        row = self.conn.execute(sql, args).fetchone()
        if not row:
            return None
        enriched = self._enrich_track_rows([row])
        return enriched[0] if enriched else None

    def list_track_source_fullpaths(self, *, include_deleted: bool = True) -> list[str]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1alist_track_source_fullpaths\u3002"""
        if include_deleted:
            rows = self.conn.execute(
                """
                SELECT DISTINCT source_fullpath
                FROM tracks
                WHERE TRIM(source_fullpath) != ''
                """
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT DISTINCT source_fullpath
                FROM tracks
                WHERE deleted_at IS NULL
                  AND TRIM(source_fullpath) != ''
                """
            ).fetchall()
        return [str(row[0]) for row in rows if str(row[0]).strip()]

    def list_lyrics_source_relpaths(self, *, include_deleted: bool = True) -> list[str]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1alist_lyrics_source_relpaths\u3002"""
        if include_deleted:
            rows = self.conn.execute(
                """
                SELECT DISTINCT source_relpath
                FROM lyrics
                WHERE TRIM(source_relpath) != ''
                """
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT DISTINCT source_relpath
                FROM lyrics
                WHERE deleted_at IS NULL
                  AND TRIM(source_relpath) != ''
                """
            ).fetchall()
        out: list[str] = []
        for row in rows:
            text = str(row[0] or "").replace("\\", "/").strip()
            if text:
                out.append(text)
        return out

    def update_tracks_fields(self, track_ids: Iterable[str], fields: dict[str, object]) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aupdate_tracks_fields\u3002"""
        ids = [track_id for track_id in track_ids if track_id]
        if not ids or not fields:
            return 0

        allowed = {
            "file_name",
            "title",
            "artist",
            "album",
            "language_kind",
            "preference_level",
        }
        patch_raw = {k: v for k, v in fields.items() if k in allowed}
        patch: dict[str, object] = {}
        for key, value in patch_raw.items():
            if key == "preference_level":
                try:
                    parsed = int(value)
                except Exception:
                    if isinstance(value, (list, tuple)) and value:
                        try:
                            parsed = int(value[0])
                        except Exception:
                            continue
                    else:
                        continue
                patch[key] = max(1, min(10, parsed))
                continue

            if isinstance(value, (list, tuple)):
                value = value[0] if value else ""
            elif isinstance(value, set):
                value = next(iter(value)) if value else ""
            elif isinstance(value, dict):
                value = ""
            patch[key] = str(value or "").strip()
        if not patch:
            return 0

        set_items = [f"{k} = ?" for k in patch.keys()]
        set_items.append("updated_at = ?")
        placeholders = _placeholders(len(ids))
        params = [*patch.values(), _utc_now_iso(), *ids]
        cursor = self.conn.execute(
            f"""
            UPDATE tracks
            SET {', '.join(set_items)}
            WHERE track_id IN ({placeholders})
            """,
            tuple(params),
        )
        return cursor.rowcount

    def update_track_ext_json(self, track_id: str, payload: dict) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aupdate_track_ext_json\u3002"""
        tid = str(track_id or "").strip()
        if not tid:
            return 0
        data = payload if isinstance(payload, dict) else {}
        cursor = self.conn.execute(
            "UPDATE tracks SET ext_json = ?, updated_at = ? WHERE track_id = ?",
            (json.dumps(data, ensure_ascii=False), _utc_now_iso(), tid),
        )
        return int(cursor.rowcount or 0)

    def get_lyrics_id_by_hash(self, text_hash: str) -> str | None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aget_lyrics_id_by_hash\u3002"""
        row = self.conn.execute("SELECT lyrics_id FROM lyrics WHERE text_hash = ?", (text_hash,)).fetchone()
        return row[0] if row else None

    def get_lyrics_by_text_hash(self, text_hash: str) -> dict | None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aget_lyrics_by_text_hash\u3002"""
        key = str(text_hash or "").strip()
        if not key:
            return None
        row = self.conn.execute(
            """
            SELECT lyrics_id, source_relpath, storage_relpath, text_hash, raw_encoding,
                   lyrics_title, lyrics_artist, lyrics_album, lyrics_author, line_count,
                   imported_at, deleted_at, ext_json
            FROM lyrics
            WHERE text_hash = ?
            LIMIT 1
            """,
            (key,),
        ).fetchone()
        return dict(row) if row else None

    def get_lyrics_by_ids(self, lyrics_ids: Iterable[str]) -> list[dict]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aget_lyrics_by_ids\u3002"""
        ids = [v for v in lyrics_ids if v]
        if not ids:
            return []
        placeholders = _placeholders(len(ids))
        rows = self.conn.execute(
            f"""
            SELECT lyrics_id, source_relpath, storage_relpath, text_hash, raw_encoding,
                   lyrics_title, lyrics_artist, lyrics_album, lyrics_author, line_count,
                   imported_at, deleted_at, ext_json
            FROM lyrics
            WHERE lyrics_id IN ({placeholders})
            """,
            tuple(ids),
        ).fetchall()
        out: list[dict] = []
        for row in rows:
            item = dict(row)
            src = str(item.get("source_relpath", "") or "")
            item["file_name"] = src.replace("\\", "/").split("/")[-1] if src else ""
            out.append(item)
        return out

    def insert_lyrics(self, item: LyricsInsert) -> str:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1ainsert_lyrics\u3002"""
        existing = self.get_lyrics_id_by_hash(item.text_hash)
        if existing:
            return existing
        self.conn.execute(
            """
            INSERT INTO lyrics(
              lyrics_id, source_relpath, storage_relpath, text_hash, raw_encoding,
              lyrics_title, lyrics_artist, lyrics_album,
              lyrics_author, line_count, imported_at, deleted_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.lyrics_id,
                item.source_relpath,
                item.storage_relpath,
                item.text_hash,
                item.raw_encoding,
                item.lyrics_title,
                item.lyrics_artist,
                item.lyrics_album,
                item.lyrics_author,
                int(item.line_count),
                item.imported_at.isoformat(),
                None,
            ),
        )
        return item.lyrics_id

    def delete_lyrics_by_ids(self, lyrics_ids: Iterable[str]) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1adelete_lyrics_by_ids\u3002"""
        ids = [v for v in lyrics_ids if v]
        if not ids:
            return 0
        placeholders = _placeholders(len(ids))
        cursor = self.conn.execute(f"DELETE FROM lyrics WHERE lyrics_id IN ({placeholders})", tuple(ids))
        return cursor.rowcount

    def link_lyrics(
        self,
        *,
        track_id: str,
        lyrics_id: str,
        confidence: float,
        match_method: str,
        is_primary: bool = True,
    ) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1alink_lyrics\u3002"""
        self.conn.execute(
            """
            INSERT INTO track_lyrics(track_id, lyrics_id, confidence, match_method, is_primary, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(track_id, lyrics_id) DO UPDATE SET
              confidence = excluded.confidence,
              match_method = excluded.match_method,
              is_primary = excluded.is_primary
            """,
            (track_id, lyrics_id, confidence, match_method, 1 if is_primary else 0, _utc_now_iso()),
        )

    def primary_lyrics_for_track(self, track_id: str) -> dict | None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aprimary_lyrics_for_track\u3002"""
        row = self.conn.execute(
            """
            SELECT l.lyrics_id, l.source_relpath, l.storage_relpath,
                   l.lyrics_title, l.lyrics_artist, l.lyrics_album, l.lyrics_author, l.line_count
            FROM track_lyrics tl
            JOIN lyrics l ON l.lyrics_id = tl.lyrics_id
            WHERE tl.track_id = ? AND tl.is_primary = 1 AND l.deleted_at IS NULL
            ORDER BY tl.created_at DESC
            LIMIT 1
            """,
            (track_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_primary_lyrics_id_for_track(self, track_id: str) -> str | None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aget_primary_lyrics_id_for_track\u3002"""
        row = self.conn.execute(
            "SELECT lyrics_id FROM track_lyrics WHERE track_id = ? AND is_primary = 1 ORDER BY created_at DESC LIMIT 1",
            (track_id,),
        ).fetchone()
        return str(row[0]) if row and row[0] else None

    def get_primary_track_id_for_lyrics(self, lyrics_id: str) -> str | None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aget_primary_track_id_for_lyrics\u3002"""
        row = self.conn.execute(
            "SELECT track_id FROM track_lyrics WHERE lyrics_id = ? AND is_primary = 1 ORDER BY created_at DESC LIMIT 1",
            (lyrics_id,),
        ).fetchone()
        return str(row[0]) if row and row[0] else None

    def list_lyrics(self, limit: int = 5000) -> list[dict]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1alist_lyrics\u3002"""
        rows = self.conn.execute(
            """
            SELECT l.lyrics_id, l.source_relpath, l.storage_relpath,
                   l.lyrics_title, l.lyrics_artist, l.lyrics_album,
                   l.lyrics_author, l.line_count, l.imported_at, l.deleted_at,
                   t.track_id, t.file_name AS mapped_track_file_name
            FROM lyrics l
            LEFT JOIN track_lyrics tl ON tl.lyrics_id = l.lyrics_id AND tl.is_primary = 1
            LEFT JOIN tracks t ON t.track_id = tl.track_id
            WHERE l.deleted_at IS NULL
            ORDER BY l.imported_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out: list[dict] = []
        for row in rows:
            item = dict(row)
            src = str(item.get("source_relpath", "") or "")
            item["file_name"] = src.replace("\\", "/").split("/")[-1] if src else ""
            item["mapped_track"] = (
                f"{item.get('mapped_track_file_name', '')} ({item.get('track_id', '')})"
                if item.get("track_id") and item.get("mapped_track_file_name")
                else ""
            )
            out.append(item)
        return out

    def list_deleted_lyrics(self, limit: int = 5000) -> list[dict]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1alist_deleted_lyrics\u3002"""
        rows = self.conn.execute(
            """
            SELECT l.lyrics_id, l.source_relpath, l.storage_relpath,
                   l.lyrics_title, l.lyrics_artist, l.lyrics_album,
                   l.lyrics_author, l.line_count, l.imported_at, l.deleted_at,
                   t.track_id, t.file_name AS mapped_track_file_name
            FROM lyrics l
            LEFT JOIN track_lyrics tl ON tl.lyrics_id = l.lyrics_id AND tl.is_primary = 1
            LEFT JOIN tracks t ON t.track_id = tl.track_id
            WHERE l.deleted_at IS NOT NULL
            ORDER BY l.deleted_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out: list[dict] = []
        for row in rows:
            item = dict(row)
            src = str(item.get("source_relpath", "") or "")
            item["file_name"] = src.replace("\\", "/").split("/")[-1] if src else ""
            item["mapped_track"] = (
                f"{item.get('mapped_track_file_name', '')} ({item.get('track_id', '')})"
                if item.get("track_id") and item.get("mapped_track_file_name")
                else ""
            )
            out.append(item)
        return out

    def set_primary_lyrics_for_track(self, track_id: str, lyrics_id: str | None) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aset_primary_lyrics_for_track\u3002"""
        self.conn.execute("UPDATE track_lyrics SET is_primary = 0 WHERE track_id = ?", (track_id,))
        if not lyrics_id:
            return
        self.conn.execute("UPDATE track_lyrics SET is_primary = 0 WHERE lyrics_id = ?", (lyrics_id,))
        self.conn.execute(
            """
            INSERT INTO track_lyrics(track_id, lyrics_id, confidence, match_method, is_primary, created_at)
            VALUES(?, ?, 1.0, 'manual', 1, ?)
            ON CONFLICT(track_id, lyrics_id) DO UPDATE SET
              confidence = excluded.confidence,
              match_method = excluded.match_method,
              is_primary = excluded.is_primary,
              created_at = excluded.created_at
            """,
            (track_id, lyrics_id, _utc_now_iso()),
        )

    def set_primary_track_for_lyrics(self, lyrics_id: str, track_id: str | None) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aset_primary_track_for_lyrics\u3002"""
        self.conn.execute("UPDATE track_lyrics SET is_primary = 0 WHERE lyrics_id = ?", (lyrics_id,))
        if not track_id:
            return
        self.conn.execute("UPDATE track_lyrics SET is_primary = 0 WHERE track_id = ?", (track_id,))
        self.conn.execute(
            """
            INSERT INTO track_lyrics(track_id, lyrics_id, confidence, match_method, is_primary, created_at)
            VALUES(?, ?, 1.0, 'manual', 1, ?)
            ON CONFLICT(track_id, lyrics_id) DO UPDATE SET
              confidence = excluded.confidence,
              match_method = excluded.match_method,
              is_primary = excluded.is_primary,
              created_at = excluded.created_at
            """,
            (track_id, lyrics_id, _utc_now_iso()),
        )

    def update_lyrics_author(self, lyrics_ids: Iterable[str], author: str) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aupdate_lyrics_author\u3002"""
        ids = [v for v in lyrics_ids if v]
        if not ids:
            return 0
        placeholders = _placeholders(len(ids))
        cursor = self.conn.execute(
            f"UPDATE lyrics SET lyrics_author = ? WHERE lyrics_id IN ({placeholders}) AND deleted_at IS NULL",
            tuple([str(author), *ids]),
        )
        return int(cursor.rowcount or 0)

    def update_lyrics_fields(self, lyrics_ids: Iterable[str], fields: dict[str, object]) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aupdate_lyrics_fields\u3002"""
        ids = [v for v in lyrics_ids if v]
        if not ids or not fields:
            return 0

        direct_allowed = {"lyrics_title", "lyrics_artist", "lyrics_album", "lyrics_author"}
        patch_direct_raw = {k: v for k, v in fields.items() if k in direct_allowed}
        patch_direct: dict[str, str] = {}
        for key, value in patch_direct_raw.items():
            if isinstance(value, (list, tuple)):
                value = value[0] if value else ""
            elif isinstance(value, set):
                value = next(iter(value)) if value else ""
            elif isinstance(value, dict):
                value = ""
            patch_direct[key] = str(value or "").strip()

        updated = 0
        placeholders = _placeholders(len(ids))
        if patch_direct:
            set_items = [f"{k} = ?" for k in patch_direct.keys()]
            params = [*patch_direct.values(), *ids]
            cursor = self.conn.execute(
                f"""
                UPDATE lyrics
                SET {", ".join(set_items)}
                WHERE lyrics_id IN ({placeholders})
                  AND deleted_at IS NULL
                """,
                tuple(params),
            )
            updated += int(cursor.rowcount or 0)

        if "file_name" in fields:
            file_name_raw = fields.get("file_name")
            if isinstance(file_name_raw, (list, tuple)):
                file_name_raw = file_name_raw[0] if file_name_raw else ""
            elif isinstance(file_name_raw, set):
                file_name_raw = next(iter(file_name_raw)) if file_name_raw else ""
            elif isinstance(file_name_raw, dict):
                file_name_raw = ""
            target_name = str(file_name_raw or "").strip()
            rows = self.conn.execute(
                f"SELECT lyrics_id, source_relpath FROM lyrics WHERE lyrics_id IN ({placeholders}) AND deleted_at IS NULL",
                tuple(ids),
            ).fetchall()
            for row in rows:
                old_rel = str(row["source_relpath"] or "")
                old_path = Path(old_rel.replace("\\", "/"))
                parent = old_path.parent.as_posix()
                old_name = old_path.name or f"{row['lyrics_id']}.lrc"
                new_name = target_name or old_name
                new_name = new_name.replace("\\", "/").split("/")[-1].strip()
                if not new_name:
                    new_name = old_name
                if "." not in Path(new_name).name:
                    suffix = Path(old_name).suffix
                    if suffix:
                        new_name = f"{new_name}{suffix}"
                new_rel = new_name if parent in {"", "."} else f"{parent.rstrip('/')}/{new_name}"
                if new_rel == old_rel:
                    continue
                cursor = self.conn.execute(
                    "UPDATE lyrics SET source_relpath = ? WHERE lyrics_id = ? AND deleted_at IS NULL",
                    (new_rel, row["lyrics_id"]),
                )
                updated += int(cursor.rowcount or 0)
        return updated

    def delete_lyrics(self, lyrics_ids: Iterable[str]) -> list[str]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1adelete_lyrics\u3002"""
        ids = [v for v in lyrics_ids if v]
        if not ids:
            return []
        placeholders = _placeholders(len(ids))
        rows = self.conn.execute(
            f"SELECT lyrics_id, storage_relpath FROM lyrics WHERE lyrics_id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
        if not rows:
            return []
        now = _utc_now_iso()
        self.conn.execute(
            f"UPDATE lyrics SET deleted_at = ? WHERE lyrics_id IN ({placeholders})",
            tuple([now, *ids]),
        )
        return [str(r["storage_relpath"] or "") for r in rows if str(r["storage_relpath"] or "")]

    def move_lyrics_to_trash(self, lyrics_ids: Iterable[str]) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1amove_lyrics_to_trash\u3002"""
        ids = [v for v in lyrics_ids if v]
        if not ids:
            return 0
        placeholders = _placeholders(len(ids))
        now = _utc_now_iso()
        cursor = self.conn.execute(
            f"UPDATE lyrics SET deleted_at = ? WHERE lyrics_id IN ({placeholders})",
            tuple([now, *ids]),
        )
        return int(cursor.rowcount or 0)

    def restore_lyrics(self, lyrics_ids: Iterable[str]) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1arestore_lyrics\u3002"""
        ids = [v for v in lyrics_ids if v]
        if not ids:
            return 0
        placeholders = _placeholders(len(ids))
        cursor = self.conn.execute(
            f"UPDATE lyrics SET deleted_at = NULL WHERE lyrics_id IN ({placeholders})",
            tuple(ids),
        )
        return int(cursor.rowcount or 0)

    def linked_lyrics_ids_for_tracks(self, track_ids: Iterable[str]) -> list[str]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1alinked_lyrics_ids_for_tracks\u3002"""
        ids = [track_id for track_id in track_ids if track_id]
        if not ids:
            return []
        placeholders = _placeholders(len(ids))
        rows = self.conn.execute(
            f"SELECT DISTINCT lyrics_id FROM track_lyrics WHERE track_id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
        return [str(r[0]) for r in rows if r and r[0]]

    def linked_lyrics_storage_relpaths_for_tracks(self, track_ids: Iterable[str]) -> list[str]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1alinked_lyrics_storage_relpaths_for_tracks\u3002"""
        ids = [track_id for track_id in track_ids if track_id]
        if not ids:
            return []
        placeholders = _placeholders(len(ids))
        rows = self.conn.execute(
            f"""
            SELECT DISTINCT l.storage_relpath
            FROM track_lyrics tl
            JOIN lyrics l ON l.lyrics_id = tl.lyrics_id
            WHERE tl.track_id IN ({placeholders})
              AND l.storage_relpath IS NOT NULL
              AND l.storage_relpath != ''
            """,
            tuple(ids),
        ).fetchall()
        return [str(r[0]) for r in rows if r and r[0]]

    def unlink_lyrics_for_tracks(self, track_ids: Iterable[str]) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aunlink_lyrics_for_tracks\u3002"""
        ids = [track_id for track_id in track_ids if track_id]
        if not ids:
            return 0
        placeholders = _placeholders(len(ids))
        cursor = self.conn.execute(
            f"DELETE FROM track_lyrics WHERE track_id IN ({placeholders})",
            tuple(ids),
        )
        return int(cursor.rowcount or 0)

    def restore_lyrics_for_tracks(self, track_ids: Iterable[str]) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1arestore_lyrics_for_tracks\u3002"""
        ids = [track_id for track_id in track_ids if track_id]
        if not ids:
            return 0
        placeholders = _placeholders(len(ids))
        rows = self.conn.execute(
            f"SELECT DISTINCT lyrics_id FROM track_lyrics WHERE track_id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
        lyrics_ids = [str(r[0]) for r in rows if r and r[0]]
        if not lyrics_ids:
            return 0
        lyrics_placeholders = _placeholders(len(lyrics_ids))
        cursor = self.conn.execute(
            f"UPDATE lyrics SET deleted_at = NULL WHERE lyrics_id IN ({lyrics_placeholders})",
            tuple(lyrics_ids),
        )
        return int(cursor.rowcount or 0)

    def find_duplicate_candidates(self, duration_sec: float, tolerance_sec: float = 6.0) -> list[dict]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1afind_duplicate_candidates\u3002"""
        rows = self.conn.execute(
            """
            SELECT track_id, title, artist, duration_sec, quality_score, fingerprint_payload, source_ext, storage_format
            FROM tracks
            WHERE deleted_at IS NULL
              AND duration_sec BETWEEN ? AND ?
            """,
            (duration_sec - tolerance_sec, duration_sec + tolerance_sec),
        ).fetchall()
        return [dict(r) for r in rows]

    def add_variant(
        self,
        *,
        variant_id: str,
        primary_track_id: str,
        variant_track_id: str,
        relation_type: str,
        similarity_score: float,
        reason: str,
    ) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aadd_variant\u3002"""
        self.conn.execute(
            """
            INSERT OR IGNORE INTO track_variants(
              variant_id, primary_track_id, variant_track_id,
              relation_type, similarity_score, reason, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                variant_id,
                primary_track_id,
                variant_track_id,
                relation_type,
                similarity_score,
                reason,
                _utc_now_iso(),
            ),
        )

    def enqueue_review(self, review_id: str, item: ReviewItem) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aenqueue_review\u3002"""
        self.conn.execute(
            """
            INSERT INTO review_queue(review_id, kind, title, payload_json, priority, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (review_id, str(item.kind), item.title, json.dumps(item.payload, ensure_ascii=False), item.priority, _utc_now_iso()),
        )

    def list_pending_reviews(self, limit: int = 100) -> list[dict]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1alist_pending_reviews\u3002"""
        rows = self.conn.execute(
            """
            SELECT review_id, kind, title, payload_json, priority, created_at
            FROM review_queue
            WHERE status = 'pending'
            ORDER BY priority DESC, created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out = []
        for row in rows:
            record = dict(row)
            record["payload"] = json.loads(record.pop("payload_json"))
            out.append(record)
        return out

    def resolve_reviews(self, review_ids: Iterable[str], status: str = "resolved") -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aresolve_reviews\u3002"""
        ids = [rid for rid in review_ids if rid]
        if not ids:
            return 0
        final_status = "ignored" if status == "ignored" else "resolved"
        placeholders = _placeholders(len(ids))
        cursor = self.conn.execute(
            f"""
            UPDATE review_queue
            SET status = ?, resolved_at = ?
            WHERE review_id IN ({placeholders}) AND status = 'pending'
            """,
            tuple([final_status, _utc_now_iso(), *ids]),
        )
        return int(cursor.rowcount or 0)

    def set_reviews_status(self, review_ids: Iterable[str], status: str) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1aset_reviews_status\u3002"""
        ids = [rid for rid in review_ids if rid]
        if not ids:
            return 0
        final_status = str(status or "pending")
        placeholders = _placeholders(len(ids))
        if final_status == "pending":
            cursor = self.conn.execute(
                f"""
                UPDATE review_queue
                SET status = 'pending', resolved_at = NULL
                WHERE review_id IN ({placeholders})
                """,
                tuple(ids),
            )
            return int(cursor.rowcount or 0)

        resolved_at = _utc_now_iso()
        cursor = self.conn.execute(
            f"""
            UPDATE review_queue
            SET status = ?, resolved_at = ?
            WHERE review_id IN ({placeholders})
            """,
            tuple([final_status, resolved_at, *ids]),
        )
        return int(cursor.rowcount or 0)
