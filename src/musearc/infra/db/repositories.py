from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from musearc.core.models import LyricsInsert, ReviewItem, TrackInsert, UndoAction


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _placeholders(size: int) -> str:
    return ",".join("?" for _ in range(size))


FAVORITES_PLAYLIST_ID = "pl_favorites"
FAVORITES_PLAYLIST_NAME = "收藏"
DEFAULT_TAG_FIELD = "备注"
DEFAULT_TAG_FIELDS = ("备注", "喜爱程度")


def _safe_json_loads(value: str | None) -> dict:
    if not value:
        return {}
    try:
        payload = json.loads(value)
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def _normalize_tags(ext_json_text: str | None) -> tuple[dict, dict[str, str]]:
    payload = _safe_json_loads(ext_json_text)
    tags_raw = payload.get("tags", {})
    if not isinstance(tags_raw, dict):
        tags_raw = {}
    tags: dict[str, str] = {}
    for k, v in tags_raw.items():
        key = str(k).strip()
        if not key:
            continue
        val = str(v) if v is not None else ""
        tags[key] = val
    payload["tags"] = tags
    return payload, tags


class LibraryRepository:
    def __init__(self, conn):
        self.conn = conn

    def _enrich_track_rows(self, rows) -> list[dict]:
        out: list[dict] = []
        for row in rows:
            item = dict(row)
            payload, tags = _normalize_tags(item.get("ext_json"))
            item["ext_json"] = json.dumps(payload, ensure_ascii=False)
            item["tags"] = tags
            for tag_name, tag_value in tags.items():
                item[f"tag:{tag_name}"] = tag_value
            item["format"] = str(item.get("storage_format") or item.get("source_ext") or "").replace(".", "").lower()
            out.append(item)
        return out

    def ensure_default_tag_field(self) -> None:
        now = _utc_now_iso()
        for name in DEFAULT_TAG_FIELDS:
            self.conn.execute(
                "INSERT OR IGNORE INTO tag_fields(tag_name, created_at) VALUES(?, ?)",
                (name, now),
            )

    def list_tag_fields(self) -> list[dict]:
        self.ensure_default_tag_field()
        rows = self.conn.execute("SELECT tag_name FROM tag_fields ORDER BY tag_name COLLATE NOCASE ASC").fetchall()
        fields = [str(r[0]) for r in rows]
        if not fields:
            return []
        tracks_rows = self.conn.execute("SELECT ext_json FROM tracks WHERE deleted_at IS NULL").fetchall()
        counts = {name: 0 for name in fields}
        for row in tracks_rows:
            _, tags = _normalize_tags(row[0] if row else None)
            for name in fields:
                value = str(tags.get(name, "")).strip()
                if value:
                    counts[name] += 1
        return [{"tag_name": name, "track_count": counts.get(name, 0)} for name in fields]

    def create_tag_field(self, tag_name: str) -> bool:
        name = str(tag_name).strip()
        if not name:
            return False
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO tag_fields(tag_name, created_at) VALUES(?, ?)",
            (name, _utc_now_iso()),
        )
        return bool(cursor.rowcount)

    def delete_tag_field(self, tag_name: str) -> int:
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
        return updated

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO library_meta(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM library_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def start_import_batch(self, import_batch_id: str, source_path: str, started_at: datetime) -> None:
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
        self.conn.execute("DELETE FROM import_batches WHERE import_batch_id = ?", (import_batch_id,))

    def list_import_batches(self, limit: int = 200) -> list[dict]:
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

    def insert_track(self, item: TrackInsert) -> None:
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

    def update_tracks_fields(self, track_ids: Iterable[str], fields: dict[str, object]) -> int:
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
        row = self.conn.execute("SELECT lyrics_id FROM lyrics WHERE text_hash = ?", (text_hash,)).fetchone()
        return row[0] if row else None

    def get_lyrics_by_text_hash(self, text_hash: str) -> dict | None:
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
        row = self.conn.execute(
            "SELECT lyrics_id FROM track_lyrics WHERE track_id = ? AND is_primary = 1 ORDER BY created_at DESC LIMIT 1",
            (track_id,),
        ).fetchone()
        return str(row[0]) if row and row[0] else None

    def get_primary_track_id_for_lyrics(self, lyrics_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT track_id FROM track_lyrics WHERE lyrics_id = ? AND is_primary = 1 ORDER BY created_at DESC LIMIT 1",
            (lyrics_id,),
        ).fetchone()
        return str(row[0]) if row and row[0] else None

    def list_lyrics(self, limit: int = 5000) -> list[dict]:
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
        self.conn.execute(
            """
            INSERT INTO review_queue(review_id, kind, title, payload_json, priority, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (review_id, str(item.kind), item.title, json.dumps(item.payload, ensure_ascii=False), item.priority, _utc_now_iso()),
        )

    def list_pending_reviews(self, limit: int = 100) -> list[dict]:
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

    def search_tracks(self, query: str, limit: int = 100) -> list[dict]:
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
        rows = self.search_tracks("", limit=limit)
        return rows

    def list_deleted_tracks(self, limit: int = 5000) -> list[dict]:
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
        ids = [track_id for track_id in track_ids if track_id]
        if not ids:
            return 0
        placeholders = _placeholders(len(ids))
        cursor = self.conn.execute(f"DELETE FROM tracks WHERE track_id IN ({placeholders})", tuple(ids))
        return cursor.rowcount

    def ensure_favorites_playlist(self) -> None:
        now = _utc_now_iso()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO playlists(playlist_id, name, description, created_at, updated_at)
            VALUES(?, ?, '', ?, ?)
            """,
            (FAVORITES_PLAYLIST_ID, FAVORITES_PLAYLIST_NAME, now, now),
        )

    def list_playlists(self) -> list[dict]:
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
        if playlist_id == FAVORITES_PLAYLIST_ID:
            return 0
        cursor = self.conn.execute("DELETE FROM playlists WHERE playlist_id = ?", (playlist_id,))
        return cursor.rowcount

    def list_playlist_items(self, playlist_id: str) -> list[dict]:
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
        cursor = self.conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
        self.conn.execute("UPDATE playlists SET updated_at = ? WHERE playlist_id = ?", (_utc_now_iso(), playlist_id))
        return cursor.rowcount

    def update_playlist_entries(self, playlist_id: str, entries: dict[str, int]) -> int:
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
        rows = self.conn.execute(
            "SELECT track_id FROM playlist_items WHERE playlist_id = ? ORDER BY position ASC",
            (playlist_id,),
        ).fetchall()
        for idx, row in enumerate(rows):
            self.conn.execute(
                "UPDATE playlist_items SET position = ? WHERE playlist_id = ? AND track_id = ?",
                (idx, playlist_id, row[0]),
            )

    def append_undo_action(self, action_id: str, action_type: str, payload: dict, max_keep: int) -> None:
        self.conn.execute(
            """
            INSERT INTO undo_actions(action_id, action_type, payload_json, created_at)
            VALUES(?, ?, ?, ?)
            """,
            (action_id, action_type, json.dumps(payload, ensure_ascii=False), _utc_now_iso()),
        )
        self.prune_undo_actions(max_keep)

    def prune_undo_actions(self, max_keep: int) -> None:
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
        cursor = self.conn.execute("DELETE FROM fullscan_works WHERE work_id = ?", (work_id,))
        return cursor.rowcount

    def _compact_fullscan_queue(self, work_id: str) -> None:
        rows = self.conn.execute(
            "SELECT track_id FROM fullscan_work_items WHERE work_id = ? ORDER BY queue_index ASC",
            (work_id,),
        ).fetchall()
        for idx, row in enumerate(rows):
            self.conn.execute(
                "UPDATE fullscan_work_items SET queue_index = ?, updated_at = ? WHERE work_id = ? AND track_id = ?",
                (idx, _utc_now_iso(), work_id, row[0]),
            )


