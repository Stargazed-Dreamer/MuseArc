from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from musearc.core.hashing import sha1_text
from musearc.core.ids import new_id
from musearc.core.paths import ensure_parent, shard_relpath
from musearc.services.exporter import ExportService
from musearc.services.import_runtime import ImportControl, list_resume_states
from musearc.services.importer import ImportService
from musearc.services.library_ops import LibraryOpsService

FAVORITES_PLAYLIST_ID = "pl_favorites"

class FacadeImportExportMixin:
    """Facade mixin: import/export and stats workflows."""

    def import_from(self, source_path: str, *, control: ImportControl | None = None, progress_callback=None) -> dict:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aimport_from\u3002"""
        source = Path(source_path).expanduser().resolve()
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            report = ImportService(self.ctx.layout.root, self.ctx.runtime_config).import_path(
                repo,
                source,
                control=control,
                progress_callback=progress_callback,
                resume=True,
            )
        if report.imported_tracks > 0 or report.imported_lyrics > 0:
            self._redo_actions.clear()
        self._log(
            f"import source={source} tracks={report.imported_tracks} lyrics={report.imported_lyrics} cancelled={report.cancelled}"
        )
        return report.to_dict()

    def list_resume_imports(self) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_resume_imports\u3002"""
        return list_resume_states(self.ctx.layout.root)

    def search(self, query: str, limit: int = 100) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1asearch\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).search(query, limit)

    def list_tracks(self, limit: int = 5000) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_tracks\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).list_tracks(limit)

    def fetch_lrclib_lyrics_for_tracks(
        self,
        track_ids: list[str],
        *,
        replace_existing_links: bool = False,
        progress_callback=None,
    ) -> dict:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1afetch_lrclib_lyrics_for_tracks\u3002"""
        ids = [str(v) for v in track_ids if str(v)]
        summary = {"total": len(ids), "success": 0, "skipped": 0, "failed": 0, "rows": []}
        if not ids:
            return summary

        with self.ctx.db.session() as conn:
            from musearc.core.models import LyricsInsert
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            tracks = {str(r.get("track_id", "")): r for r in repo.get_tracks_by_ids(ids)}
            for track_id in ids:
                row = tracks.get(track_id) or {}
                file_name = str(row.get("file_name", "") or "")
                title = str(row.get("title", "") or "").strip()
                artist = str(row.get("artist", "") or "").strip()
                album = str(row.get("album", "") or "").strip()
                duration = int(float(row.get("duration_sec", 0) or 0))
                reason = ""
                status = "failed"

                if not title or not artist or not album or duration <= 0:
                    status = "skipped"
                    reason = "缺少 API 所需字段"
                else:
                    try:
                        response = requests.get(
                            "https://lrclib.net/api/get",
                            params={
                                "track_name": title,
                                "artist_name": artist,
                                "album_name": album,
                                "duration": duration,
                            },
                            headers={"User-Agent": "MuseArc/0.1 (+https://example.invalid)"},
                            timeout=20,
                        )
                    except Exception as exc:
                        status = "failed"
                        reason = f"请求失败: {exc}"
                    else:
                        if response.status_code == 404:
                            status = "skipped"
                            reason = "未匹配到歌词"
                        elif response.status_code != 200:
                            status = "failed"
                            reason = f"HTTP {response.status_code}"
                        else:
                            payload = response.json() if response.content else {}
                            synced = str(payload.get("syncedLyrics", "") or "").strip()
                            plain = str(payload.get("plainLyrics", "") or "").strip()
                            instrumental = bool(payload.get("instrumental", False))
                            text = synced or plain
                            if instrumental or not text:
                                status = "skipped"
                                reason = "纯音乐或歌词为空"
                            else:
                                text_hash = sha1_text(text)
                                existing = repo.get_lyrics_by_text_hash(text_hash)
                                if existing:
                                    lyrics_id = str(existing.get("lyrics_id", "") or "")
                                    if existing.get("deleted_at"):
                                        repo.restore_lyrics([lyrics_id])
                                else:
                                    lyrics_id = new_id("lrc")
                                    lyrics_rel = shard_relpath("data/lyrics", lyrics_id, "lrc")
                                    lyrics_abs = self.ctx.layout.root / Path(lyrics_rel)
                                    ensure_parent(lyrics_abs)
                                    lyrics_abs.write_text(text, encoding="utf-8")
                                    repo.insert_lyrics(
                                        LyricsInsert(
                                            lyrics_id=lyrics_id,
                                            source_relpath=f"lrclib/{track_id}.lrc",
                                            storage_relpath=lyrics_rel,
                                            text_hash=text_hash,
                                            raw_encoding="utf-8",
                                            lyrics_title=title,
                                            lyrics_artist=artist,
                                            lyrics_album=album,
                                            lyrics_author="lrclib",
                                            line_count=len([ln for ln in text.splitlines() if ln.strip()]),
                                            imported_at=datetime.now(timezone.utc),
                                        )
                                    )

                                if replace_existing_links or not repo.get_primary_lyrics_id_for_track(track_id):
                                    repo.set_primary_lyrics_for_track(track_id, lyrics_id)
                                repo.update_track_tag_values([track_id], "歌词来自lrclib", "是")
                                status = "success"
                                reason = "已导入并绑定"

                if status == "success":
                    summary["success"] += 1
                elif status == "skipped":
                    summary["skipped"] += 1
                else:
                    summary["failed"] += 1
                summary["rows"].append(
                    {
                        "track_id": track_id,
                        "file_name": file_name,
                        "status": status,
                        "reason": reason,
                    }
                )
                if progress_callback is not None:
                    try:
                        progress_callback(summary["rows"][-1], len(summary["rows"]), len(ids))
                    except Exception:
                        pass

        if summary["success"] > 0:
            self._redo_actions.clear()
        self._log(
            f"lrclib_fetch total={summary['total']} success={summary['success']} skipped={summary['skipped']} failed={summary['failed']}"
        )
        return summary

    def export(
        self,
        track_ids: list[str],
        out_dir: str,
        fmt: str,
        bitrate: str | None = None,
        sample_rate: int | None = None,
        copy_bound_lyrics: bool = False,
    ) -> list[str]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aexport\u3002"""
        out = Path(out_dir).expanduser().resolve()
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            paths = ExportService(self.ctx.layout.root).export_tracks(
                LibraryRepository(conn),
                track_ids,
                out,
                fmt=fmt,
                bitrate=bitrate,
                sample_rate=sample_rate,
                copy_bound_lyrics=copy_bound_lyrics,
            )
        self._log(f"export tracks={len(track_ids)} fmt={fmt} out={out}")
        return [str(p) for p in paths]

    def export_with_plan(
        self,
        track_ids: list[str],
        out_dir: str,
        format_plan: dict[str, str],
        *,
        bitrate: str | None = None,
        sample_rate: int | None = None,
        copy_bound_lyrics: bool = False,
    ) -> list[str]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aexport_with_plan\u3002"""
        out = Path(out_dir).expanduser().resolve()
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            paths = ExportService(self.ctx.layout.root).export_tracks_with_plan(
                LibraryRepository(conn),
                track_ids,
                out,
                format_plan=format_plan,
                bitrate=bitrate,
                sample_rate=sample_rate,
                copy_bound_lyrics=copy_bound_lyrics,
            )
        self._log(f"export_with_plan tracks={len(track_ids)} out={out}")
        return [str(p) for p in paths]

    def export_playlist_package(self, track_ids: list[str], out_dir: str, *, playlist_name: str = "") -> str:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aexport_playlist_package\u3002"""
        ids = [str(v) for v in track_ids if str(v)]
        if not ids:
            raise ValueError("empty_track_ids")
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            rows = repo.get_tracks_by_ids(ids)
            by_id = {str(r.get("track_id", "")): r for r in rows if r.get("track_id")}
            ordered_rows = [by_id[tid] for tid in ids if tid in by_id]
            exported_at = datetime.now(timezone.utc).isoformat()
            hash_seed = f"{'|'.join(ids)}|{exported_at}"
            playlist_hash = hashlib.sha1(hash_seed.encode("utf-8")).hexdigest()
            tracks_out: list[dict] = []
            for row in ordered_rows:
                track_id = str(row.get("track_id", "") or "")
                lyrics = repo.primary_lyrics_for_track(track_id) or {}
                tracks_out.append(
                    {
                        "track_id": track_id,
                        "storage_relpath": str(row.get("storage_relpath", "") or ""),
                        "title": str(row.get("title", "") or ""),
                        "artist": str(row.get("artist", "") or ""),
                        "album": str(row.get("album", "") or ""),
                        "lyrics_storage_relpath": str(lyrics.get("storage_relpath", "") or ""),
                        "source_sha256": str(row.get("source_sha256", "") or ""),
                        "stats": {
                            "play_count": 0,
                            "manual_play_count": 0,
                            "play_seconds": 0,
                            "early_skip_count": 0,
                        },
                    }
                )

        out_root = Path(out_dir).expanduser().resolve()
        out_root.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(ch if ch not in "\\/:*?\"<>|" else "_" for ch in (playlist_name.strip() or "playlist")).strip()
        if not safe_name:
            safe_name = "playlist"
        file_path = out_root / f"{safe_name}_{playlist_hash[:10]}.muse_playlist.json"
        payload = {
            "schema": "musearc_playlist_export_v1",
            "playlist_hash": playlist_hash,
            "playlist_name": playlist_name.strip(),
            "exported_at": exported_at,
            "database_location": str(self.library_root),
            "track_count": len(tracks_out),
            "stats_summary": {},
            "tracks": tracks_out,
        }
        file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._log(f"export_playlist_package tracks={len(tracks_out)} file={file_path}")
        return str(file_path)

    @staticmethod
    def _norm_path_for_compare(value: str | Path) -> str:
        """规范化路径以便比较，将输入转换为小写形式的绝对路径。

        参数：
            value (str | Path): 输入值，可以是字符串或Path对象。

        返回值：
            str: 规范化后的路径字符串。如果输入为空，返回空字符串；如果解析失败，返回手动处理后的字符串。
        """
        text = str(value or "").strip()  # 将输入转换为字符串，处理None或空值，并去除前后空白
        if not text:  # 如果文本为空，表示无效输入
            return ""  # 返回空字符串
        try:
            return str(Path(text).expanduser().resolve()).casefold()  # 尝试规范化路径：展开用户目录、解析为绝对路径，并转换为小写
        except Exception:  # 如果路径解析过程中出现任何异常（如路径无效）
            return text.replace("\\", "/").casefold()  # 降级处理：手动将反斜杠替换为正斜杠，并转换为小写以确保一致性

    @staticmethod
    def _resolve_track_from_export_item(
        item: dict,
        *,
        by_sha: dict[str, dict],
        by_id: dict[str, dict],
        by_storage: dict[str, dict],
    ) -> dict | None:
        """
        根据导出项在多个查找字典中解析并返回对应的曲目记录。

        该方法尝试通过三种方式（SHA256哈希、曲目ID、存储相对路径）从提供的字典中查找匹配的曲目。
        查找按顺序进行，一旦找到就立即返回。

        Args:
            item: 包含导出项信息的字典，可能包含 track_id, storage_relpath, source_sha256 等键。
            by_sha: 以 source_sha256 为键，曲目记录字典为值的查找字典。
            by_id: 以 track_id 为键，曲目记录字典为值的查找字典。
            by_storage: 以 storage_relpath 为键，曲目记录字典为值的查找字典。

        Returns:
            如果找到匹配的曲目，则返回该曲目的字典记录；否则返回 None。
        """
        # 从导出项中提取并清理相关字段，确保它们是干净的字符串
        tid = str(item.get("track_id", "") or "").strip()
        storage_rel = str(item.get("storage_relpath", "") or "").replace("\\", "/").strip()
        source_sha256 = str(item.get("source_sha256", "") or "").strip().lower()
        # 初始化结果为 None，表示尚未找到匹配项
        row = None
        # 第一步：尝试通过源文件的 SHA256 哈希值进行查找
        if source_sha256:
            row = by_sha.get(source_sha256)
        # 第二步：如果第一步未找到结果，并且存在曲目ID，则通过曲目ID进行查找
        if row is None and tid:
            row = by_id.get(tid)
        # 第三步：如果前两步都未找到结果，并且存在存储相对路径，则通过该路径进行查找
        if row is None and storage_rel:
            row = by_storage.get(storage_rel)
        # 返回最终找到的曲目记录或 None
        return row

    def inspect_playlist_package(self, file_path: str) -> dict:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1ainspect_playlist_package\u3002"""
        path = Path(file_path).expanduser().resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid_playlist_payload")
        schema = str(payload.get("schema", "") or "")
        if schema != "musearc_playlist_export_v1":
            raise ValueError(f"unsupported_schema:{schema}")
        playlist_hash = str(payload.get("playlist_hash", "") or "").strip()
        if not playlist_hash:
            raise ValueError("missing_playlist_hash")
        playlist_name = str(payload.get("playlist_name", "") or "").strip() or path.stem
        tracks_raw = payload.get("tracks")
        track_count = len(tracks_raw) if isinstance(tracks_raw, list) else 0
        db_location = str(payload.get("database_location", "") or "").strip()
        db_match = self._norm_path_for_compare(db_location) == self._norm_path_for_compare(self.library_root)
        existing = next((r for r in self.list_playlists() if str(r.get("name", "")).strip() == playlist_name), None)
        return {
            "playlist_hash": playlist_hash,
            "playlist_name": playlist_name,
            "track_count": track_count,
            "database_location": db_location,
            "database_location_match": db_match,
            "existing_playlist_id": str((existing or {}).get("playlist_id", "") or ""),
            "existing_playlist_name": str((existing or {}).get("name", "") or ""),
            "source_file": str(path),
        }

    def list_stats_import_history(self, limit: int = 200) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_stats_import_history\u3002"""
        state = self._load_stats_state()
        rows = [r for r in state.get("history", []) if isinstance(r, dict)]
        rows.sort(key=lambda r: str(r.get("imported_at", "")), reverse=True)
        return rows[: max(1, int(limit))]

    def list_playlist_import_history(self, limit: int = 200) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_playlist_import_history\u3002"""
        state = self._load_stats_state()
        rows = [r for r in state.get("playlist_import_history", []) if isinstance(r, dict)]
        rows.sort(key=lambda r: str(r.get("imported_at", "")), reverse=True)
        return rows[: max(1, int(limit))]

    @staticmethod
    def _is_museplayer_playback_stats(payload: dict) -> bool:
        """检测是否为 MusePlayer playback_stats.json 格式。"""
        if payload.get("schema"):
            return False
        tracks = payload.get("tracks")
        if not isinstance(tracks, dict) or not tracks:
            return False
        first = next(iter(tracks.values()))
        return isinstance(first, dict) and ("play_count" in first or "played_seconds_total" in first)

    @staticmethod
    def _convert_museplayer_playback_stats(payload: dict, file_path: str) -> dict:
        """将 MusePlayer playback_stats.json 转换为 MuseArc 内部格式。"""
        tracks_dict = payload.get("tracks", {})
        library_tracks: dict = {}
        library_path = Path(file_path).expanduser().resolve().with_name("library.json")
        if library_path.exists():
            try:
                library_payload = json.loads(library_path.read_text(encoding="utf-8-sig"))
                candidate_tracks = library_payload.get("tracks") if isinstance(library_payload, dict) else {}
                if isinstance(candidate_tracks, dict):
                    library_tracks = candidate_tracks
            except Exception:
                library_tracks = {}
        tracks_list: list[dict] = []
        for entry_key, entry in tracks_dict.items():
            if not isinstance(entry, dict):
                continue
            museplayer_track_id = str(entry.get("track_id", "") or entry_key or "").strip()
            library_entry = library_tracks.get(museplayer_track_id)
            if not isinstance(library_entry, dict):
                library_entry = {}
            tracks_list.append({
                "track_id": str(library_entry.get("source_track_id", "") or museplayer_track_id).strip(),
                "storage_relpath": str(library_entry.get("source_storage_relpath", "") or "").replace("\\", "/").strip(),
                "source_sha256": str(library_entry.get("source_sha256", "") or "").strip().lower(),
                "museplayer_track_id": museplayer_track_id,
                "stats": {
                    "play_count": entry.get("play_count", 0),
                    "manual_play_count": entry.get("active_play_count", 0),
                    "complete_play_count": entry.get("complete_play_count", 0),
                    "play_seconds": int(entry.get("played_seconds_total", 0) or 0),
                    "early_skip_count": entry.get("early_skip_count", 0),
                    "peak_session_play_count": entry.get("peak_session_play_count", 0),
                    "peak_session_play_at": entry.get("peak_session_play_at", 0.0),
                },
            })
        content_hash = hashlib.sha256(
            json.dumps(tracks_list, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
        return {
            "schema": "musearc_playlist_export_v1",
            "playlist_hash": f"museplayer_stats_{content_hash}",
            "playlist_name": Path(file_path).stem,
            "tracks": tracks_list,
        }

    def import_playlist_stats(self, file_path: str) -> dict:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aimport_playlist_stats\u3002"""
        path = Path(file_path).expanduser().resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid_stats_payload")
        schema = str(payload.get("schema", "") or "")
        if schema == "musearc_playlist_export_v1":
            print(f"[import_playlist_stats] 检测到 MuseArc 原生格式 schema={schema}")
        elif self._is_museplayer_playback_stats(payload):
            print(f"[import_playlist_stats] 检测到 MusePlayer playback_stats 格式，正在转换...")
            payload = self._convert_museplayer_playback_stats(payload, file_path)
            print(f"[import_playlist_stats] 转换完成: playlist_hash={payload.get('playlist_hash')}, tracks数={len(payload.get('tracks', []))}")
        else:
            raise ValueError(f"unsupported_schema:{schema}")
        playlist_hash = str(payload.get("playlist_hash", "") or "").strip()
        if not playlist_hash:
            raise ValueError("missing_playlist_hash")
        tracks_raw = payload.get("tracks")
        if not isinstance(tracks_raw, list):
            tracks_raw = []
        print(f"[import_playlist_stats] playlist_hash={playlist_hash}, tracks数={len(tracks_raw)}")

        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            all_rows = repo.list_tracks(limit=2_000_000)
            by_id = {str(r.get("track_id", "")): r for r in all_rows if r.get("track_id")}
            by_storage = {str(r.get("storage_relpath", "")).replace("\\", "/"): r for r in all_rows if str(r.get("storage_relpath", "")).strip()}
            by_sha = {str(r.get("source_sha256", "")).strip().lower(): r for r in all_rows if str(r.get("source_sha256", "")).strip()}

            state = self._load_stats_state()
            history = [r for r in state.get("history", []) if isinstance(r, dict)]
            playlist_import_history = [r for r in state.get("playlist_import_history", []) if isinstance(r, dict)]
            contributions = state.get("contributions")
            if not isinstance(contributions, dict):
                contributions = {}

            impacted_track_ids: set[str] = set()
            for track_id, mapping in list(contributions.items()):
                if not isinstance(mapping, dict):
                    continue
                if playlist_hash in mapping:
                    mapping.pop(playlist_hash, None)
                    impacted_track_ids.add(str(track_id))
                if not mapping:
                    contributions.pop(track_id, None)

            applied = 0
            skipped = 0
            imported_track_ids: set[str] = set()
            print(f"[import_playlist_stats] 开始匹配: 库内歌曲={len(all_rows)}, 待匹配tracks={len(tracks_raw)}")
            for item in tracks_raw:
                if not isinstance(item, dict):
                    skipped += 1
                    continue
                tid = str(item.get("track_id", "") or "").strip()
                storage_rel = str(item.get("storage_relpath", "") or "").replace("\\", "/").strip()
                source_sha256 = str(item.get("source_sha256", "") or "").strip().lower()
                row = None
                if source_sha256:
                    row = by_sha.get(source_sha256)
                if row is None and tid:
                    row = by_id.get(tid)
                    # 兼容 MusePlayer 的纯 UUID 格式 track_id
                    if row is None and not tid.startswith("trk_"):
                        row = by_id.get(f"trk_{tid}")
                if row is None and storage_rel:
                    row = by_storage.get(storage_rel)
                if row is None:
                    print(f"[import_playlist_stats] 跳过(未匹配): tid={tid}, sha256={source_sha256[:16] if source_sha256 else 'N/A'}, storage={storage_rel or 'N/A'}")
                    skipped += 1
                    continue
                real_tid = str(row.get("track_id", "") or "")
                stats = item.get("stats")
                if not isinstance(stats, dict):
                    stats = {}
                stat_payload = {
                    "play_count": self._safe_nonneg_int(stats.get("play_count", 0), 0),
                    "manual_play_count": self._safe_nonneg_int(stats.get("manual_play_count", 0), 0),
                    "play_seconds": self._safe_nonneg_int(stats.get("play_seconds", 0), 0),
                    "early_skip_count": self._safe_nonneg_int(stats.get("early_skip_count", 0), 0),
                    "complete_play_count": self._safe_nonneg_int(stats.get("complete_play_count", 0), 0),
                    "peak_session_play_count": self._safe_nonneg_int(stats.get("peak_session_play_count", 0), 0),
                    "peak_session_play_at": stats.get("peak_session_play_at", 0.0),
                }
                track_map = contributions.get(real_tid)
                if not isinstance(track_map, dict):
                    track_map = {}
                track_map[playlist_hash] = stat_payload
                contributions[real_tid] = track_map
                imported_track_ids.add(real_tid)
                impacted_track_ids.add(real_tid)
                applied += 1

            self._recompute_stats_contributions_and_write_tags(
                repo=repo,
                contributions=contributions,
                impacted_track_ids=impacted_track_ids,
                by_id=by_id,
            )

            now_iso = datetime.now(timezone.utc).isoformat()
            source_norm = self._norm_path_for_compare(path)
            history = [
                r
                for r in history
                if not (
                    str(r.get("playlist_hash", "")) == playlist_hash
                    or self._norm_path_for_compare(str(r.get("source_file", "") or "")) == source_norm
                )
            ]
            history.append(
                {
                    "playlist_hash": playlist_hash,
                    "source_file": str(path),
                    "imported_at": now_iso,
                    "applied_tracks": len(imported_track_ids),
                    "skipped_rows": skipped,
                }
            )
            history.sort(key=lambda r: str(r.get("imported_at", "")), reverse=True)
            history = history[:500]
            self._save_stats_state(
                {
                    "history": history,
                    "contributions": contributions,
                    "playlist_import_history": playlist_import_history,
                }
            )

        self._redo_actions.clear()
        self._log(f"import_playlist_stats hash={playlist_hash} applied={applied} skipped={skipped}")
        return {
            "playlist_hash": playlist_hash,
            "applied_tracks": applied,
            "skipped_rows": skipped,
            "source_file": str(path),
        }

    def _recompute_stats_contributions_and_write_tags(
        self,
        *,
        repo,
        contributions: dict,
        impacted_track_ids: set[str],
        by_id: dict[str, dict],
    ) -> None:
        total_play_count_all = 0
        for row_map in contributions.values():
            if not isinstance(row_map, dict):
                continue
            for item in row_map.values():
                if not isinstance(item, dict):
                    continue
                total_play_count_all += self._safe_nonneg_int(item.get("play_count", 0), 0)

        for tid in impacted_track_ids:
            row_map = contributions.get(tid)
            if not isinstance(row_map, dict):
                row_map = {}
            total = {"play_count": 0, "manual_play_count": 0, "play_seconds": 0, "early_skip_count": 0, "complete_play_count": 0, "peak_session_play_count": 0, "peak_session_play_at": 0.0}
            for item in row_map.values():
                if not isinstance(item, dict):
                    continue
                total["play_count"] += self._safe_nonneg_int(item.get("play_count", 0), 0)
                total["manual_play_count"] += self._safe_nonneg_int(item.get("manual_play_count", 0), 0)
                total["play_seconds"] += self._safe_nonneg_int(item.get("play_seconds", 0), 0)
                total["early_skip_count"] += self._safe_nonneg_int(item.get("early_skip_count", 0), 0)
                total["complete_play_count"] += self._safe_nonneg_int(item.get("complete_play_count", 0), 0)
                # peak_session_play_count 取各来源最大值（非累加）
                item_peak = self._safe_nonneg_int(item.get("peak_session_play_count", 0), 0)
                if item_peak > total["peak_session_play_count"]:
                    total["peak_session_play_count"] = item_peak
                    total["peak_session_play_at"] = item.get("peak_session_play_at", 0.0)

            duration_sec = 0.0
            row = by_id.get(tid)
            if row:
                try:
                    duration_sec = float(row.get("duration_sec", 0.0) or 0.0)
                except Exception:
                    duration_sec = 0.0
            love_score = self._compute_love_score(
                play_count=total["play_count"],
                manual_play_count=total["manual_play_count"],
                play_seconds=total["play_seconds"],
                early_skip_count=total["early_skip_count"],
                total_play_count_all=total_play_count_all,
                duration_sec=duration_sec,
                complete_play_count=total["complete_play_count"],
                peak_session_play_count=total["peak_session_play_count"],
            )
            repo.update_track_tag_values([tid], "播放次数", str(total["play_count"]))
            repo.update_track_tag_values([tid], "指定播放次数", str(total["manual_play_count"]))
            repo.update_track_tag_values([tid], "播放秒数", str(total["play_seconds"]))
            repo.update_track_tag_values([tid], "早期跳过次数", str(total["early_skip_count"]))
            repo.update_track_tag_values([tid], "完播次数", str(total["complete_play_count"]))
            repo.update_track_tag_values([tid], "最高密集播放次数", str(total["peak_session_play_count"]))
            if total["peak_session_play_at"]:
                repo.update_track_tag_values([tid], "最高密集播放时间", str(total["peak_session_play_at"]))
            repo.update_track_tag_values([tid], "喜爱程度", str(love_score))

    def revert_playlist_stats_import(self, playlist_hash: str) -> dict:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1arevert_playlist_stats_import\u3002"""
        hash_value = str(playlist_hash or "").strip()
        if not hash_value:
            raise ValueError("missing_playlist_hash")

        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            all_rows = repo.list_tracks(limit=2_000_000)
            by_id = {str(r.get("track_id", "")): r for r in all_rows if r.get("track_id")}

            state = self._load_stats_state()
            history = [r for r in state.get("history", []) if isinstance(r, dict)]
            playlist_history = [r for r in state.get("playlist_import_history", []) if isinstance(r, dict)]
            contributions = state.get("contributions")
            if not isinstance(contributions, dict):
                contributions = {}

            has_stats_history = any(
                str(row.get("playlist_hash", "")) == hash_value
                for row in history
            )

            impacted_track_ids: set[str] = set()
            removed_rows = 0
            for track_id, mapping in list(contributions.items()):
                if not isinstance(mapping, dict):
                    continue
                if hash_value in mapping:
                    mapping.pop(hash_value, None)
                    impacted_track_ids.add(str(track_id))
                    removed_rows += 1
                if not mapping:
                    contributions.pop(track_id, None)

            if removed_rows <= 0 and not has_stats_history:
                raise ValueError("playlist_hash_not_found")

            if impacted_track_ids:
                self._recompute_stats_contributions_and_write_tags(
                    repo=repo,
                    contributions=contributions,
                    impacted_track_ids=impacted_track_ids,
                    by_id=by_id,
                )

            history = [r for r in history if str(r.get("playlist_hash", "")) != hash_value]
            playlist_history = [r for r in playlist_history if str(r.get("playlist_hash", "")) != hash_value]
            self._save_stats_state(
                {
                    "history": history[:500],
                    "contributions": contributions,
                    "playlist_import_history": playlist_history[:500],
                }
            )

        self._redo_actions.clear()
        self._log(f"revert_playlist_stats_import hash={hash_value} affected={len(impacted_track_ids)}")
        return {
            "playlist_hash": hash_value,
            "affected_tracks": len(impacted_track_ids),
            "removed_rows": removed_rows,
        }

    def import_playlist_package(self, file_path: str, *, duplicate_mode: str = "rename") -> dict:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aimport_playlist_package\u3002"""
        mode = "overwrite" if str(duplicate_mode) == "overwrite" else "rename"
        path = Path(file_path).expanduser().resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid_playlist_payload")
        schema = str(payload.get("schema", "") or "")
        if schema != "musearc_playlist_export_v1":
            raise ValueError(f"unsupported_schema:{schema}")
        playlist_hash = str(payload.get("playlist_hash", "") or "").strip()
        if not playlist_hash:
            raise ValueError("missing_playlist_hash")
        db_location = str(payload.get("database_location", "") or "").strip()
        if self._norm_path_for_compare(db_location) != self._norm_path_for_compare(self.library_root):
            raise ValueError("database_location_mismatch")
        playlist_name = str(payload.get("playlist_name", "") or "").strip() or path.stem
        tracks_raw = payload.get("tracks")
        if not isinstance(tracks_raw, list):
            tracks_raw = []

        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            rows = repo.list_tracks(limit=2_000_000)
            by_id = {str(r.get("track_id", "")): r for r in rows if r.get("track_id")}
            by_storage = {
                str(r.get("storage_relpath", "")).replace("\\", "/"): r
                for r in rows
                if str(r.get("storage_relpath", "")).strip()
            }
            by_sha = {
                str(r.get("source_sha256", "")).strip().lower(): r
                for r in rows
                if str(r.get("source_sha256", "")).strip()
            }

            matched_track_ids: list[str] = []
            failed_items: list[dict] = []
            for item in tracks_raw:
                if not isinstance(item, dict):
                    continue
                resolved = self._resolve_track_from_export_item(
                    item,
                    by_sha=by_sha,
                    by_id=by_id,
                    by_storage=by_storage,
                )
                if resolved is None:
                    failed_items.append(
                        {
                            "track_id": str(item.get("track_id", "") or ""),
                            "title": str(item.get("title", "") or ""),
                            "artist": str(item.get("artist", "") or ""),
                        }
                    )
                    continue
                matched_track_ids.append(str(resolved.get("track_id", "") or ""))

            existing_playlists = [r for r in repo.list_playlists() if str(r.get("playlist_id", "")) != FAVORITES_PLAYLIST_ID]
            existing_by_name = {str(r.get("name", "")): str(r.get("playlist_id", "")) for r in existing_playlists}
            target_name = playlist_name
            target_playlist_id = existing_by_name.get(target_name, "")

            if target_playlist_id and mode == "overwrite":
                repo.clear_playlist(target_playlist_id)
                replaced_existing = True
            else:
                replaced_existing = False
                if target_playlist_id:
                    base = target_name
                    idx = 2
                    while target_name in existing_by_name:
                        target_name = f"{base}({idx})"
                        idx += 1
                    target_playlist_id = ""
                if not target_playlist_id:
                    target_playlist_id = new_id("pl")
                    repo.create_playlist(target_playlist_id, target_name, "")

            added_count = int(repo.add_tracks_to_playlist(target_playlist_id, matched_track_ids) or 0)

            state = self._load_stats_state()
            history = [r for r in state.get("history", []) if isinstance(r, dict)]
            contributions = state.get("contributions")
            if not isinstance(contributions, dict):
                contributions = {}
            playlist_import_history = [r for r in state.get("playlist_import_history", []) if isinstance(r, dict)]
            playlist_history = playlist_import_history

            source_norm = self._norm_path_for_compare(path)
            playlist_history = [
                r
                for r in playlist_history
                if not (
                    str(r.get("playlist_hash", "")) == playlist_hash
                    and self._norm_path_for_compare(str(r.get("source_file", "") or "")) == source_norm
                )
            ]
            playlist_history.append(
                {
                    "imported_at": datetime.now(timezone.utc).isoformat(),
                    "playlist_hash": playlist_hash,
                    "source_file": str(path),
                    "playlist_name": playlist_name,
                    "target_playlist_id": target_playlist_id,
                    "target_playlist_name": target_name,
                    "matched_tracks": len(matched_track_ids),
                    "added_tracks": added_count,
                    "failed_tracks": len(failed_items),
                    "mode": mode,
                    "replaced_existing": bool(replaced_existing),
                    "failed_items": failed_items[:500],
                }
            )
            playlist_history.sort(key=lambda r: str(r.get("imported_at", "")), reverse=True)
            playlist_history = playlist_history[:500]
            self._save_stats_state(
                {
                    "history": history,
                    "contributions": contributions,
                    "playlist_import_history": playlist_history,
                }
            )

        self._redo_actions.clear()
        self._log(
            f"import_playlist_package hash={playlist_hash} mode={mode} matched={len(matched_track_ids)} "
            f"added={added_count} failed={len(failed_items)}"
        )
        return {
            "playlist_hash": playlist_hash,
            "playlist_name": playlist_name,
            "target_playlist_id": target_playlist_id,
            "target_playlist_name": target_name,
            "matched_tracks": len(matched_track_ids),
            "added_tracks": added_count,
            "failed_tracks": len(failed_items),
            "failed_items": failed_items,
            "source_file": str(path),
            "mode": mode,
            "replaced_existing": bool(replaced_existing),
        }

    def recompute_love_score_tag(self) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1arecompute_love_score_tag\u3002"""
        updated = 0
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            rows = repo.list_tracks(limit=2_000_000)
            total_play_count_all = 0
            for row in rows:
                tags = row.get("tags")
                if not isinstance(tags, dict):
                    continue
                total_play_count_all += self._safe_nonneg_int(tags.get("播放次数", 0), 0)

            for row in rows:
                track_id = str(row.get("track_id", "") or "")
                if not track_id:
                    continue
                tags = row.get("tags")
                if not isinstance(tags, dict):
                    tags = {}
                play_count = self._safe_nonneg_int(tags.get("播放次数", 0), 0)
                manual_play_count = self._safe_nonneg_int(tags.get("指定播放次数", 0), 0)
                play_seconds = self._safe_nonneg_int(tags.get("播放秒数", 0), 0)
                early_skip_count = self._safe_nonneg_int(tags.get("早期跳过次数", 0), 0)
                complete_play_count = self._safe_nonneg_int(tags.get("完播次数", 0), 0)
                peak_session_play_count = self._safe_nonneg_int(tags.get("最高密集播放次数", 0), 0)
                try:
                    duration_sec = float(row.get("duration_sec", 0.0) or 0.0)
                except Exception:
                    duration_sec = 0.0
                love_score = self._compute_love_score(
                    play_count=play_count,
                    manual_play_count=manual_play_count,
                    play_seconds=play_seconds,
                    early_skip_count=early_skip_count,
                    total_play_count_all=total_play_count_all,
                    duration_sec=duration_sec,
                    complete_play_count=complete_play_count,
                    peak_session_play_count=peak_session_play_count,
                )
                repo.update_track_tag_values([track_id], "喜爱程度", str(love_score))
                updated += 1
        if updated > 0:
            self._redo_actions.clear()
            self._log(f"recompute_love_score_tag updated={updated}")
        return updated

    def sync_preference_from_love_tag(self) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1async_preference_from_love_tag\u3002"""
        updated = 0
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            rows = repo.list_tracks(limit=2_000_000)
            for row in rows:
                track_id = str(row.get("track_id", "") or "")
                if not track_id:
                    continue
                tags = row.get("tags")
                if not isinstance(tags, dict):
                    continue
                raw_love = str(tags.get("喜爱程度", "")).strip()
                if not raw_love:
                    continue
                try:
                    love_value = float(raw_love)
                except Exception:
                    continue
                # 需求：喜爱程度 -> 喜好(1-10) 使用除以10后四舍五入。
                pref_value = int((love_value / 10) + 0.5)
                pref_value = max(1, min(10, pref_value))
                current_pref = self._safe_int(row.get("preference_level", 0), 0)
                if current_pref == pref_value:
                    continue
                repo.update_tracks_fields([track_id], {"preference_level": pref_value})
                updated += 1
        if updated > 0:
            self._redo_actions.clear()
            self._log(f"sync_preference_from_love_tag updated={updated}")
        return updated
