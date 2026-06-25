from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path

from musearc.core.models import LyricsInsert, ReviewItem, TrackInsert
from musearc.infra.db.repositories_common import (
    _placeholders,
    _safe_json_loads,
    _utc_now_iso,
)

logger = logging.getLogger(__name__)

class RepositoryTracksLyricsMixin:
    """Repository mixin: track/lyrics/review persistence operations."""

    def insert_track(self, item: TrackInsert) -> None:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1ainsert_track\u3002"""
        data = asdict(item)
        imported_at = data.pop("imported_at").isoformat()
        ext_payload = data.pop("ext_json", {})
        if not isinstance(ext_payload, dict):
            ext_payload = {}
        fp_hash32 = data.pop("fingerprint_hash32", None)
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
            fp_hash32,
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
              fingerprint_hash32, fingerprint_payload, imported_at, updated_at, ext_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            logger.debug("[Repo] update_tracks_fields: 过滤后 patch 为空, 原始 fields=%s", list(fields.keys()))
            return 0

        set_items = [f"{k} = ?" for k in patch.keys()]
        set_items.append("updated_at = ?")
        placeholders = _placeholders(len(ids))
        params = [*patch.values(), _utc_now_iso(), *ids]
        logger.info("[Repo] update_tracks_fields: ids=%s patch=%s", ids, list(patch.keys()))
        print(f"[repo] update_tracks_fields: ids={ids} patch={list(patch.keys())}")
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
        if not row:
            return None
        item = dict(row)
        payload = _safe_json_loads(str(item.get("ext_json", "") or ""))
        item["lyrics_language"] = str(payload.get("language_kind") or payload.get("language") or "unknown")
        return item

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
            payload = _safe_json_loads(str(item.get("ext_json", "") or ""))
            item["lyrics_language"] = str(payload.get("language_kind") or payload.get("language") or "unknown")
            out.append(item)
        return out

    def insert_lyrics(self, item: LyricsInsert) -> str:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1ainsert_lyrics\u3002"""
        existing = self.get_lyrics_id_by_hash(item.text_hash)
        if existing:
            return existing
        ext_payload = item.ext_json if isinstance(item.ext_json, dict) else {}
        self.conn.execute(
            """
            INSERT INTO lyrics(
              lyrics_id, source_relpath, storage_relpath, text_hash, raw_encoding,
              lyrics_title, lyrics_artist, lyrics_album,
              lyrics_author, line_count, imported_at, deleted_at, ext_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(ext_payload, ensure_ascii=False),
            ),
        )
        return item.lyrics_id

    def _lyrics_language_kind(self, lyrics_id: str) -> str:
        """根据给定的歌词ID，查询并返回其语言类型。

        通过查询数据库获取歌词对应的ext_json字段，解析其中的语言信息。
        会依次尝试获取 'language_kind' 或 'language' 字段的值，并进行清理和标准化。

        Args:
            lyrics_id (str): 要查询的歌词唯一标识ID。

        Returns:
            str: 识别出的语言类型字符串（已转为小写并清理空格）。
                 如果查询无结果、数据异常或语言类型为未知/混合，则返回空字符串。
        """
        # 执行SQL查询，根据lyrics_id从lyrics表中获取ext_json字段
        row = self.conn.execute("SELECT ext_json FROM lyrics WHERE lyrics_id = ? LIMIT 1", (lyrics_id,)).fetchone()
        # 如果查询结果为空，则返回空字符串
        if not row:
            return ""
        # 安全地解析JSON数据。处理row可能为空或结构异常的情况
        payload = _safe_json_loads(row[0] if row and len(row) > 0 else "")
        # 从解析后的字典中尝试获取语言类型字段，依次尝试 'language_kind' 或 'language'
        # 将获取到的值转为字符串，去除首尾空格，并统一转为小写（casefold），以便后续比较
        value = str(payload.get("language_kind") or payload.get("language") or "").strip().casefold()
        # 如果最终获取到的语言类型为空、'unknown'或'mixed'，则视为无效，返回空字符串
        if value in {"", "unknown", "mixed"}:
            return ""
        # 返回有效的语言类型字符串
        return value

    def _sync_track_language_from_lyrics_if_unknown(self, track_id: str, lyrics_id: str) -> None:
        """如果数据库中指定歌曲的语言为'未知'或为空，则从其歌词信息中获取语言并同步更新。
        Args:
            track_id (str): 目标歌曲的唯一标识符。
            lyrics_id (str): 与歌曲关联的歌词信息的唯一标识符。
        Returns:
            None: 此方法不返回任何值，其作用是更新数据库。
        """
        # 根据歌词ID查询其语言种类
        lang = self._lyrics_language_kind(lyrics_id)
        # 如果未能从歌词中获取到有效语言，则直接结束方法
        if not lang:
            return
        # 查询目标歌曲在数据库中的当前语言记录
        row = self.conn.execute("SELECT language_kind FROM tracks WHERE track_id = ? LIMIT 1", (track_id,)).fetchone()
        # 安全地获取当前语言值：若查询结果为空或结构异常则默认为空字符串，然后去除首尾空白并统一为小写以便比较
        current = str(row[0] if row and len(row) > 0 else "").strip().casefold()
        # 如果歌曲语言已知且不是“unknown”或空字符串，则无需更新，直接返回
        if current not in {"", "unknown"}:
            return
        # 更新数据库中对应歌曲的语言种类和最后更新时间
        self.conn.execute(
            "UPDATE tracks SET language_kind = ?, updated_at = ? WHERE track_id = ?",
            (lang, _utc_now_iso(), track_id),
        )

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
        if is_primary:
            # 约束一对一主映射：同一首歌/同一条歌词在主映射维度都只保留一条。
            self.conn.execute("UPDATE track_lyrics SET is_primary = 0 WHERE track_id = ?", (track_id,))
            self.conn.execute("UPDATE track_lyrics SET is_primary = 0 WHERE lyrics_id = ?", (lyrics_id,))
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
        self._sync_track_language_from_lyrics_if_unknown(track_id, lyrics_id)

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
                   l.lyrics_author, l.line_count, l.imported_at, l.deleted_at, l.ext_json,
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
            payload = _safe_json_loads(str(item.get("ext_json", "") or ""))
            item["lyrics_language"] = str(payload.get("language_kind") or payload.get("language") or "unknown")
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
                   l.lyrics_author, l.line_count, l.imported_at, l.deleted_at, l.ext_json,
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
            payload = _safe_json_loads(str(item.get("ext_json", "") or ""))
            item["lyrics_language"] = str(payload.get("language_kind") or payload.get("language") or "unknown")
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
        self._sync_track_language_from_lyrics_if_unknown(track_id, lyrics_id)

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
        self._sync_track_language_from_lyrics_if_unknown(track_id, lyrics_id)

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
        logger.info("[Repo] update_lyrics_fields: ids=%s fields=%s updated=%d", ids, list(fields.keys()), updated)
        print(f"[repo] update_lyrics_fields: ids={ids} fields={list(fields.keys())} updated={updated}")
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

    def has_linked_lyrics_for_tracks(self, track_ids: Iterable[str]) -> bool:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1ahas_linked_lyrics_for_tracks\u3002"""
        ids = [track_id for track_id in track_ids if track_id]
        if not ids:
            return False
        placeholders = _placeholders(len(ids))
        row = self.conn.execute(
            f"""
            SELECT 1
            FROM track_lyrics tl
            JOIN lyrics l ON l.lyrics_id = tl.lyrics_id
            WHERE tl.track_id IN ({placeholders})
              AND l.deleted_at IS NULL
            LIMIT 1
            """,
            tuple(ids),
        ).fetchone()
        return bool(row)

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

    def sync_track_language_from_primary_lyrics(self, *, only_unknown: bool = True) -> int:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1async_track_language_from_primary_lyrics\u3002"""
        rows = self.conn.execute(
            """
            SELECT t.track_id, t.language_kind, l.ext_json
            FROM tracks t
            JOIN track_lyrics tl ON tl.track_id = t.track_id AND tl.is_primary = 1
            JOIN lyrics l ON l.lyrics_id = tl.lyrics_id
            WHERE t.deleted_at IS NULL
              AND l.deleted_at IS NULL
            """
        ).fetchall()
        updated = 0
        for row in rows:
            track_id = str(row["track_id"] or "")
            current = str(row["language_kind"] or "").strip().casefold()
            if only_unknown and current not in {"", "unknown"}:
                continue
            payload = _safe_json_loads(str(row["ext_json"] or ""))
            lang = str(payload.get("language_kind") or payload.get("language") or "").strip().casefold()
            if lang in {"", "unknown", "mixed"}:
                continue
            if lang == current:
                continue
            cursor = self.conn.execute(
                "UPDATE tracks SET language_kind = ?, updated_at = ? WHERE track_id = ?",
                (lang, _utc_now_iso(), track_id),
            )
            updated += int(cursor.rowcount or 0)
        return updated

    def find_duplicate_candidates(self, duration_sec: float, tolerance_sec: float = 6.0) -> list[dict]:
        """仓储方法：find_duplicate_candidates。"""
        rows = self.conn.execute(
            """
            SELECT track_id, title, artist, duration_sec, quality_score, fingerprint_payload,
                   fingerprint_hash32, source_ext, storage_format
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
