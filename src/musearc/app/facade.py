from __future__ import annotations

"""应用外观层（Facade）。

职责：
1) 为 UI 提供稳定 API，避免 UI 直接依赖仓储/服务细节；
2) 管理数据库会话边界与撤销日志；
3) 编排导入、导出、统计、标签、歌单、审查等跨模块流程。
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from musearc.app.action_log import append_action_log, read_action_logs
from musearc.core.hashing import sha1_text
from musearc.core.ids import new_id
from musearc.core.paths import ensure_parent, shard_relpath
from musearc.services.exporter import ExportService
from musearc.services.import_runtime import ImportControl, list_resume_states
from musearc.services.importer import ImportService
from musearc.services.library import open_or_create_library
from musearc.services.library_ops import LibraryOpsService

FAVORITES_PLAYLIST_ID = "pl_favorites"


class MuseArcFacade:
    """UI/External callers should integrate with this facade, not service internals."""

    def __init__(self, library_path: str | None = None):
        self.ctx = open_or_create_library(library_path)
        self._redo_actions: list[dict] = []

    @property
    def library_root(self) -> Path:
        return self.ctx.layout.root

    def _undo_keep(self) -> int:
        return max(1, int(self.ctx.runtime_config.ui.undo_max_actions))

    def _append_undo(self, repo, action_type: str, payload: dict) -> None:
        repo.append_undo_action(new_id("undo"), action_type, payload, self._undo_keep())
        # Any new operation invalidates redo branch.
        self._redo_actions.clear()

    def _log(self, message: str, level: str = "info") -> None:
        cfg = self.ctx.runtime_config
        append_action_log(
            self.ctx.layout.root,
            enabled=bool(cfg.ui.enable_logs),
            message=message,
            level=level,
            keep=10,
        )

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        if isinstance(value, (list, tuple, dict, set)):
            return default
        try:
            return int(value or 0)
        except Exception:
            return default

    @staticmethod
    def _safe_nonneg_int(value, default: int = 0) -> int:
        return max(0, MuseArcFacade._safe_int(value, default))

    def _stats_state_path(self) -> Path:
        base = self.ctx.layout.root / "manifests"
        base.mkdir(parents=True, exist_ok=True)
        return base / "stats_import_state.json"

    def _load_stats_state(self) -> dict:
        path = self._stats_state_path()
        if not path.exists():
            return {"history": [], "contributions": {}}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"history": [], "contributions": {}}
        if not isinstance(payload, dict):
            return {"history": [], "contributions": {}}
        history = payload.get("history")
        contributions = payload.get("contributions")
        if not isinstance(history, list):
            history = []
        if not isinstance(contributions, dict):
            contributions = {}
        return {"history": history, "contributions": contributions}

    def _save_stats_state(self, payload: dict) -> None:
        path = self._stats_state_path()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _compute_love_score(
        *,
        play_count: int,
        manual_play_count: int,
        play_seconds: int,
        early_skip_count: int,
        total_play_count_all: int,
        duration_sec: float,
    ) -> int:
        a = max(0, int(play_count))
        b = max(0, int(manual_play_count))
        c = max(0, int(play_seconds))
        d = max(0, int(early_skip_count))
        e = max(1, int(total_play_count_all))
        f = max(1.0, float(duration_sec or 0.0))
        a_safe = max(1, a)

        # 平均播放比例 0.4
        t1 = float(c) / f / float(a_safe)
        # 主动播放比例 0.5
        t2 = float(b) / float(a_safe)
        # 全库播放占比 0.1
        t3 = float(a) / float(e)
        # 跳过比例 -1
        t4 = float(d) / float(a_safe)
        t = 0.1 * t3 + 0.4 * t1 + 0.5 * t2 - t4
        return max(-100, min(100, int(round(t * 100.0))))

    def import_from(self, source_path: str, *, control: ImportControl | None = None, progress_callback=None) -> dict:
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
        return list_resume_states(self.ctx.layout.root)

    def search(self, query: str, limit: int = 100) -> list[dict]:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).search(query, limit)

    def list_tracks(self, limit: int = 5000) -> list[dict]:
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
    ) -> list[str]:
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
    ) -> list[str]:
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
            )
        self._log(f"export_with_plan tracks={len(track_ids)} out={out}")
        return [str(p) for p in paths]

    def export_playlist_package(self, track_ids: list[str], out_dir: str, *, playlist_name: str = "") -> str:
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

    def list_stats_import_history(self, limit: int = 200) -> list[dict]:
        state = self._load_stats_state()
        rows = [r for r in state.get("history", []) if isinstance(r, dict)]
        rows.sort(key=lambda r: str(r.get("imported_at", "")), reverse=True)
        return rows[: max(1, int(limit))]

    def import_playlist_stats(self, file_path: str) -> dict:
        path = Path(file_path).expanduser().resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid_stats_payload")
        schema = str(payload.get("schema", "") or "")
        if schema != "musearc_playlist_export_v1":
            raise ValueError(f"unsupported_schema:{schema}")
        playlist_hash = str(payload.get("playlist_hash", "") or "").strip()
        if not playlist_hash:
            raise ValueError("missing_playlist_hash")
        tracks_raw = payload.get("tracks")
        if not isinstance(tracks_raw, list):
            tracks_raw = []

        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            all_rows = repo.list_tracks(limit=2_000_000)
            by_id = {str(r.get("track_id", "")): r for r in all_rows if r.get("track_id")}
            by_storage = {str(r.get("storage_relpath", "")).replace("\\", "/"): r for r in all_rows if str(r.get("storage_relpath", "")).strip()}
            by_sha = {str(r.get("source_sha256", "")).strip().lower(): r for r in all_rows if str(r.get("source_sha256", "")).strip()}

            state = self._load_stats_state()
            history = [r for r in state.get("history", []) if isinstance(r, dict)]
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
                if row is None and storage_rel:
                    row = by_storage.get(storage_rel)
                if row is None:
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
                }
                track_map = contributions.get(real_tid)
                if not isinstance(track_map, dict):
                    track_map = {}
                track_map[playlist_hash] = stat_payload
                contributions[real_tid] = track_map
                imported_track_ids.add(real_tid)
                impacted_track_ids.add(real_tid)
                applied += 1

            total_play_count_all = 0
            for row_map in contributions.values():
                if not isinstance(row_map, dict):
                    continue
                for v in row_map.values():
                    if not isinstance(v, dict):
                        continue
                    total_play_count_all += self._safe_nonneg_int(v.get("play_count", 0), 0)

            for tid in impacted_track_ids:
                row_map = contributions.get(tid)
                if not isinstance(row_map, dict):
                    row_map = {}
                total = {"play_count": 0, "manual_play_count": 0, "play_seconds": 0, "early_skip_count": 0}
                for v in row_map.values():
                    if not isinstance(v, dict):
                        continue
                    total["play_count"] += self._safe_nonneg_int(v.get("play_count", 0), 0)
                    total["manual_play_count"] += self._safe_nonneg_int(v.get("manual_play_count", 0), 0)
                    total["play_seconds"] += self._safe_nonneg_int(v.get("play_seconds", 0), 0)
                    total["early_skip_count"] += self._safe_nonneg_int(v.get("early_skip_count", 0), 0)
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
                )
                repo.update_track_tag_values([tid], "播放次数", str(total["play_count"]))
                repo.update_track_tag_values([tid], "指定播放次数", str(total["manual_play_count"]))
                repo.update_track_tag_values([tid], "播放秒数", str(total["play_seconds"]))
                repo.update_track_tag_values([tid], "早期跳过次数", str(total["early_skip_count"]))
                repo.update_track_tag_values([tid], "喜爱程度", str(love_score))

            now_iso = datetime.now(timezone.utc).isoformat()
            history = [r for r in history if str(r.get("playlist_hash", "")) != playlist_hash]
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
            self._save_stats_state({"history": history, "contributions": contributions})

        self._redo_actions.clear()
        self._log(f"import_playlist_stats hash={playlist_hash} applied={applied} skipped={skipped}")
        return {
            "playlist_hash": playlist_hash,
            "applied_tracks": applied,
            "skipped_rows": skipped,
            "source_file": str(path),
        }

    def recompute_love_score_tag(self) -> int:
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
                )
                repo.update_track_tag_values([track_id], "喜爱程度", str(love_score))
                updated += 1
        if updated > 0:
            self._redo_actions.clear()
            self._log(f"recompute_love_score_tag updated={updated}")
        return updated

    def sync_preference_from_love_tag(self) -> int:
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

    def pending_reviews(self, limit: int = 100) -> list[dict]:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).pending_reviews(limit)

    def resolve_reviews(self, review_ids: list[str], status: str = "resolved") -> int:
        ids = [rid for rid in review_ids if rid]
        if not ids:
            return 0
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            pending_map = {str(r.get("review_id", "")): r for r in repo.list_pending_reviews(limit=200_000)}
            target_ids = [rid for rid in ids if rid in pending_map]
            if not target_ids:
                return 0
            count = LibraryOpsService(repo).resolve_reviews(target_ids, status=status)
            if count > 0:
                self._append_undo(
                    repo,
                    "resolve_reviews",
                    {
                        "review_ids": target_ids,
                        "status_after": "ignored" if status == "ignored" else "resolved",
                    },
                )
        if count > 0:
            self._redo_actions.clear()
            self._log(f"resolve_reviews count={count} status={status}")
        return count

    def import_track_from_review(
        self,
        source_path: str,
        *,
        existing_track_id: str | None = None,
        replace_existing: bool = True,
    ) -> dict:
        source = Path(str(source_path or "")).expanduser().resolve()
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            result = ImportService(self.ctx.layout.root, self.ctx.runtime_config).import_track_for_duplicate_review(
                repo,
                source,
                existing_track_id=existing_track_id,
                replace_existing=replace_existing,
            )
        if str(result.get("status", "")) == "imported":
            self._redo_actions.clear()
            self._log(
                f"import_track_from_review source={source} track={result.get('track_id','')} replaced={result.get('replaced_track_id','')}"
            )
        return result

    def delete_tracks(self, track_ids: list[str], *, mode: str = "move_linked_lyrics") -> int:
        delete_mode = mode if mode in {"move_linked_lyrics", "unlink_only"} else "move_linked_lyrics"
        count = 0
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            affected = repo.get_tracks_by_ids(track_ids)
            count = LibraryOpsService(repo).delete_tracks(track_ids, mode=delete_mode)
            if count > 0:
                self._append_undo(
                    repo,
                    "soft_delete_tracks",
                    {"track_ids": [r["track_id"] for r in affected], "mode": delete_mode},
                )
                self._log(f"delete_tracks count={count} mode={delete_mode}")
        return count

    def list_deleted_tracks(self, limit: int = 5000, *, include_missing: bool = True) -> list[dict]:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            rows = LibraryOpsService(LibraryRepository(conn)).list_deleted_tracks(limit)
        if include_missing:
            return rows
        visible: list[dict] = []
        for row in rows:
            rel = str(row.get("storage_relpath", "") or "").strip()
            if not rel:
                visible.append(row)
                continue
            if (self.ctx.layout.root / rel).exists():
                visible.append(row)
        return visible

    def list_deleted_items(self, limit: int = 5000) -> list[dict]:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            tracks = repo.list_deleted_tracks(limit=limit)
            lyrics = repo.list_deleted_lyrics(limit=limit)

        out: list[dict] = []
        for row in tracks:
            rel = str(row.get("storage_relpath", "") or "").strip()
            out.append(
                {
                    "item_type": "track",
                    "item_type_label": "歌曲",
                    "item_id": str(row.get("track_id", "") or ""),
                    "file_name": str(row.get("file_name", "") or ""),
                    "title": str(row.get("title", "") or ""),
                    "artist": str(row.get("artist", "") or ""),
                    "album": str(row.get("album", "") or ""),
                    "storage_relpath": rel,
                    "deleted_at": str(row.get("deleted_at", "") or ""),
                    "file_exists": bool(rel and (self.ctx.layout.root / rel).exists()),
                }
            )
        for row in lyrics:
            rel = str(row.get("storage_relpath", "") or "").strip()
            out.append(
                {
                    "item_type": "lyrics",
                    "item_type_label": "歌词",
                    "item_id": str(row.get("lyrics_id", "") or ""),
                    "file_name": str(row.get("file_name", "") or ""),
                    "title": str(row.get("lyrics_title", "") or ""),
                    "artist": str(row.get("lyrics_artist", "") or ""),
                    "album": str(row.get("lyrics_album", "") or ""),
                    "storage_relpath": rel,
                    "deleted_at": str(row.get("deleted_at", "") or ""),
                    "file_exists": bool(rel and (self.ctx.layout.root / rel).exists()),
                }
            )
        out.sort(key=lambda r: str(r.get("deleted_at", "")), reverse=True)
        return out[: max(1, int(limit))]

    def restore_tracks(self, track_ids: list[str]) -> int:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            count = LibraryOpsService(repo).restore_tracks(track_ids)
            if count > 0:
                self._append_undo(repo, "restore_tracks", {"track_ids": track_ids})
                self._log(f"restore_tracks count={count}")
            return count

    def purge_deleted_track_files(self, track_ids: list[str]) -> int:
        ids = [str(v) for v in track_ids if str(v)]
        if not ids:
            return 0
        deleted_rows = {str(r.get("track_id", "")): r for r in self.list_deleted_tracks(limit=2_000_000, include_missing=True)}
        processed = 0
        for tid in ids:
            row = deleted_rows.get(tid) or {}
            if not row:
                continue
            processed += 1
            rel = str(row.get("storage_relpath", "") or "")
            if not rel:
                continue
            target = self.ctx.layout.root / rel
            try:
                target.unlink(missing_ok=True)
            except Exception:
                continue
        if processed > 0:
            self._redo_actions.clear()
            self._log(f"purge_deleted_track_files count={processed}")
        return processed

    def purge_deleted_lyrics_files(self, lyrics_ids: list[str]) -> int:
        ids = [str(v) for v in lyrics_ids if str(v)]
        if not ids:
            return 0
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            rows = LibraryRepository(conn).list_deleted_lyrics(limit=2_000_000)
        deleted_rows = {str(r.get("lyrics_id", "")): r for r in rows}
        processed = 0
        for lid in ids:
            row = deleted_rows.get(lid) or {}
            if not row:
                continue
            processed += 1
            rel = str(row.get("storage_relpath", "") or "")
            if not rel:
                continue
            target = self.ctx.layout.root / rel
            try:
                target.unlink(missing_ok=True)
            except Exception:
                continue
        if processed > 0:
            self._redo_actions.clear()
            self._log(f"purge_deleted_lyrics_files count={processed}")
        return processed

    def restore_deleted_items(self, items: list[dict]) -> dict:
        track_ids = [str(i.get("item_id", "")) for i in items if str(i.get("item_type", "")) == "track" and i.get("item_id")]
        lyrics_ids = [str(i.get("item_id", "")) for i in items if str(i.get("item_type", "")) == "lyrics" and i.get("item_id")]
        track_count = self.restore_tracks(track_ids) if track_ids else 0
        lyrics_count = self.restore_lyrics(lyrics_ids) if lyrics_ids else 0
        return {"tracks": track_count, "lyrics": lyrics_count, "total": track_count + lyrics_count}

    def purge_deleted_item_files(self, items: list[dict]) -> dict:
        track_ids = [str(i.get("item_id", "")) for i in items if str(i.get("item_type", "")) == "track" and i.get("item_id")]
        lyrics_ids = [str(i.get("item_id", "")) for i in items if str(i.get("item_type", "")) == "lyrics" and i.get("item_id")]
        track_count = self.purge_deleted_track_files(track_ids) if track_ids else 0
        lyrics_count = self.purge_deleted_lyrics_files(lyrics_ids) if lyrics_ids else 0
        return {"tracks": track_count, "lyrics": lyrics_count, "total": track_count + lyrics_count}

    def update_tracks_fields(self, track_ids: list[str], fields: dict[str, object]) -> int:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            before = repo.get_tracks_by_ids(track_ids)
            count = LibraryOpsService(repo).update_tracks_fields(track_ids, fields)
            if count > 0:
                rollback_values = []
                for row in before:
                    rollback_values.append(
                        {
                            "track_id": row["track_id"],
                            "file_name": row.get("file_name"),
                            "title": row.get("title"),
                            "artist": row.get("artist"),
                            "album": row.get("album"),
                            "language_kind": row.get("language_kind"),
                            "preference_level": row.get("preference_level"),
                        }
                    )
                self._append_undo(
                    repo,
                    "update_tracks_fields",
                    {
                        "track_ids": track_ids,
                        "applied_fields": dict(fields),
                        "rollback_values": rollback_values,
                    },
                )
                self._log(f"update_tracks_fields count={count} fields={list(fields.keys())}")
            return count

    def list_import_batches(self, limit: int = 200) -> list[dict]:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).list_import_batches(limit)

    def get_import_batch_detail(self, import_batch_id: str) -> dict | None:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            batch = LibraryOpsService(LibraryRepository(conn)).get_import_batch(import_batch_id)
        if not batch:
            return None
        manifest = self.ctx.layout.root / "manifests" / "imports" / f"{import_batch_id}.json"
        file_states: list[dict] = []
        if manifest.exists():
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                raw = payload.get("file_states") or []
                if isinstance(raw, list):
                    file_states = [r for r in raw if isinstance(r, dict)]
            except Exception:
                file_states = []
        result = dict(batch)
        result["file_states"] = file_states
        return result

    def list_playlists(self) -> list[dict]:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).list_playlists()

    def create_playlist(self, name: str, description: str = "") -> str:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            playlist_id = LibraryOpsService(repo).create_playlist(name, description)
            self._append_undo(
                repo,
                "create_playlist",
                {"playlist_id": playlist_id, "name": name, "description": description},
            )
            return playlist_id

    def delete_playlist(self, playlist_id: str) -> int:
        if playlist_id == FAVORITES_PLAYLIST_ID:
            return 0
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            items = repo.list_playlist_items(playlist_id)
            details = [p for p in repo.list_playlists() if p.get("playlist_id") == playlist_id]
            meta = details[0] if details else {"name": "", "description": ""}
            count = LibraryOpsService(repo).delete_playlist(playlist_id)
            if count > 0:
                self._append_undo(
                    repo,
                    "delete_playlist",
                    {
                        "playlist_id": playlist_id,
                        "name": meta.get("name", ""),
                        "description": meta.get("description", ""),
                        "items": items,
                    },
                )
            return count

    def clear_playlist(self, playlist_id: str) -> int:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            items_before = repo.list_playlist_items(playlist_id)
            count = LibraryOpsService(repo).clear_playlist(playlist_id)
            if count > 0:
                self._append_undo(
                    repo,
                    "clear_playlist",
                    {"playlist_id": playlist_id, "items_before": items_before},
                )
            return count

    def list_playlist_items(self, playlist_id: str) -> list[dict]:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryRepository(conn).list_playlist_items(playlist_id)

    def add_tracks_to_playlist(self, playlist_id: str, track_ids: list[str]) -> int:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            count = LibraryOpsService(repo).add_tracks_to_playlist(playlist_id, track_ids)
            if count > 0:
                self._append_undo(repo, "add_tracks_to_playlist", {"playlist_id": playlist_id, "track_ids": track_ids})
            return count

    def remove_tracks_from_playlist(self, playlist_id: str, track_ids: list[str]) -> int:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            items_before = repo.list_playlist_items(playlist_id)
            count = LibraryOpsService(repo).remove_tracks_from_playlist(playlist_id, track_ids)
            if count > 0:
                self._append_undo(
                    repo,
                    "remove_tracks_from_playlist",
                    {
                        "playlist_id": playlist_id,
                        "items_before": items_before,
                        "track_ids_removed": track_ids,
                    },
                )
            return count

    def reorder_playlist(self, playlist_id: str, ordered_track_ids: list[str]) -> None:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            before = [r["track_id"] for r in repo.list_playlist_items(playlist_id)]
            LibraryOpsService(repo).reorder_playlist(playlist_id, ordered_track_ids)
            self._append_undo(
                repo,
                "reorder_playlist",
                {
                    "playlist_id": playlist_id,
                    "ordered_track_ids_before": before,
                    "ordered_track_ids_after": ordered_track_ids,
                },
            )

    def update_playlist_entries(self, playlist_id: str, entries: dict[str, int]) -> int:
        if not entries:
            return 0
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            before_rows = repo.list_playlist_items(playlist_id)
            before_entries = {str(r.get("track_id", "")): int(r.get("entry", 0) or 0) for r in before_rows if r.get("track_id")}
            count = LibraryOpsService(repo).update_playlist_entries(playlist_id, entries)
            if count > 0:
                self._append_undo(
                    repo,
                    "update_playlist_entries",
                    {
                        "playlist_id": playlist_id,
                        "before_entries": before_entries,
                        "after_entries": {str(k): int(v) for k, v in entries.items() if k},
                    },
                )
            return count

    def add_to_favorites(self, track_ids: list[str]) -> int:
        return self.add_tracks_to_playlist(FAVORITES_PLAYLIST_ID, track_ids)

    def remove_from_favorites(self, track_ids: list[str]) -> int:
        return self.remove_tracks_from_playlist(FAVORITES_PLAYLIST_ID, track_ids)

    def list_tag_fields(self) -> list[dict]:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).list_tag_fields()

    def create_tag_field(self, tag_name: str) -> bool:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            ok = LibraryOpsService(LibraryRepository(conn)).create_tag_field(tag_name)
            if ok:
                self._redo_actions.clear()
                self._log(f"create_tag_field name={tag_name}")
            return ok

    def delete_tag_field(self, tag_name: str) -> int:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            count = LibraryOpsService(LibraryRepository(conn)).delete_tag_field(tag_name)
            if count > 0:
                self._redo_actions.clear()
                self._log(f"delete_tag_field name={tag_name}")
            return count

    def update_track_tag_values(self, track_ids: list[str], tag_name: str, value: str) -> int:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            count = LibraryOpsService(LibraryRepository(conn)).update_track_tag_values(track_ids, tag_name, value)
            if count > 0:
                self._redo_actions.clear()
                self._log(f"update_track_tag_values tag={tag_name} count={count}")
            return count

    def list_lyrics(self, limit: int = 5000) -> list[dict]:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).list_lyrics(limit)

    def set_primary_lyrics_for_track(self, track_id: str, lyrics_id: str | None) -> None:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            old_lyrics = repo.get_primary_lyrics_id_for_track(track_id)
            old_track_for_new = repo.get_primary_track_id_for_lyrics(lyrics_id) if lyrics_id else None
            LibraryOpsService(repo).set_primary_lyrics_for_track(track_id, lyrics_id)
            self._append_undo(
                repo,
                "set_primary_lyrics_for_track",
                {
                    "track_id": track_id,
                    "new_lyrics_id": lyrics_id,
                    "old_lyrics_id": old_lyrics,
                    "old_track_for_new_lyrics": old_track_for_new,
                },
            )
        self._redo_actions.clear()
        self._log(f"set_primary_lyrics_for_track track={track_id} lyrics={lyrics_id}")

    def set_primary_track_for_lyrics(self, lyrics_id: str, track_id: str | None) -> None:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            old_track = repo.get_primary_track_id_for_lyrics(lyrics_id)
            old_lyrics_for_new = repo.get_primary_lyrics_id_for_track(track_id) if track_id else None
            LibraryOpsService(repo).set_primary_track_for_lyrics(lyrics_id, track_id)
            self._append_undo(
                repo,
                "set_primary_track_for_lyrics",
                {
                    "lyrics_id": lyrics_id,
                    "new_track_id": track_id,
                    "old_track_id": old_track,
                    "old_lyrics_for_new_track": old_lyrics_for_new,
                },
            )
        self._redo_actions.clear()
        self._log(f"set_primary_track_for_lyrics lyrics={lyrics_id} track={track_id}")

    def update_lyrics_author(self, lyrics_ids: list[str], author: str) -> int:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            count = LibraryOpsService(LibraryRepository(conn)).update_lyrics_author(lyrics_ids, author)
        if count > 0:
            self._redo_actions.clear()
            self._log(f"update_lyrics_author count={count}")
        return count

    def update_lyrics_fields(self, lyrics_ids: list[str], fields: dict[str, object]) -> int:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            before = repo.get_lyrics_by_ids(lyrics_ids)
            count = LibraryOpsService(repo).update_lyrics_fields(lyrics_ids, fields)
            if count > 0:
                rollback_values = []
                for row in before:
                    rollback_values.append(
                        {
                            "lyrics_id": row.get("lyrics_id"),
                            "file_name": row.get("file_name"),
                            "lyrics_title": row.get("lyrics_title"),
                            "lyrics_artist": row.get("lyrics_artist"),
                            "lyrics_album": row.get("lyrics_album"),
                            "lyrics_author": row.get("lyrics_author"),
                        }
                    )
                self._append_undo(
                    repo,
                    "update_lyrics_fields",
                    {
                        "lyrics_ids": lyrics_ids,
                        "applied_fields": dict(fields),
                        "rollback_values": rollback_values,
                    },
                )
        if count > 0:
            self._redo_actions.clear()
            self._log(f"update_lyrics_fields count={count} fields={list(fields.keys())}")
        return count

    def delete_lyrics(self, lyrics_ids: list[str]) -> int:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            relpaths = LibraryOpsService(repo).delete_lyrics(lyrics_ids)
            if relpaths:
                self._append_undo(repo, "delete_lyrics", {"lyrics_ids": lyrics_ids, "relpaths": relpaths})
        if relpaths:
            self._redo_actions.clear()
            self._log(f"move_lyrics_to_trash count={len(relpaths)}")
        return len(relpaths)

    def restore_lyrics(self, lyrics_ids: list[str]) -> int:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            count = LibraryOpsService(repo).restore_lyrics(lyrics_ids)
            if count > 0:
                self._append_undo(repo, "restore_lyrics", {"lyrics_ids": lyrics_ids})
                self._log(f"restore_lyrics count={count}")
            return count

    def read_logs(self) -> list[dict]:
        return read_action_logs(self.ctx.layout.root)

    def save_now(self) -> None:
        with self.ctx.db.session() as conn:
            conn.execute("SELECT 1")
        self._log("save_now")

    def create_fullscan_work(self, name: str) -> str:
        tracks = self.list_tracks(limit=2_000_000)
        track_ids = [row["track_id"] for row in tracks]
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            work_id = LibraryOpsService(repo).create_fullscan_work(name, track_ids)
            self._append_undo(
                repo,
                "create_fullscan_work",
                {"work_id": work_id, "name": name, "track_ids": track_ids},
            )
            return work_id

    def list_fullscan_works(self) -> list[dict]:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).list_fullscan_works()

    def get_fullscan_work_items(self, work_id: str, limit: int = 200000) -> list[dict]:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).get_fullscan_work_items(work_id, limit)

    def remove_fullscan_items(self, work_id: str, track_ids: list[str]) -> int:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            count = LibraryOpsService(LibraryRepository(conn)).remove_fullscan_items(work_id, track_ids)
            if count > 0:
                self._redo_actions.clear()
            return count

    def update_fullscan_items_status(self, work_id: str, track_ids: list[str], status: str) -> int:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            count = LibraryOpsService(LibraryRepository(conn)).update_fullscan_items_status(work_id, track_ids, status)
            if count > 0:
                self._redo_actions.clear()
            return count

    def delete_fullscan_work(self, work_id: str) -> int:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            count = LibraryOpsService(LibraryRepository(conn)).delete_fullscan_work(work_id)
            if count > 0:
                self._redo_actions.clear()
            return count

    def list_undo_actions(self, limit: int = 50) -> list[dict]:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            rows = LibraryRepository(conn).list_undo_actions(limit)
            return [
                {
                    "action_id": row.action_id,
                    "action_type": row.action_type,
                    "payload": row.payload,
                    "created_at": row.created_at,
                }
                for row in rows
            ]

    def list_redo_actions(self) -> list[dict]:
        return list(self._redo_actions)

    def list_action_timeline(self, limit: int = 200) -> dict:
        undo_desc = self.list_undo_actions(limit)
        applied = list(reversed(undo_desc))
        undone = list(reversed(self._redo_actions))[:limit]
        history = applied + undone
        return {"history": history, "current_index": len(applied) - 1}

    def undo_last_action(self) -> str:
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            action = repo.pop_latest_undo_action()
            if not action:
                return "no_action"

            payload = action.payload
            t = action.action_type
            self._redo_actions.append(
                {
                    "action_id": action.action_id,
                    "action_type": t,
                    "payload": payload,
                    "created_at": action.created_at,
                }
            )

            if t == "soft_delete_tracks":
                track_ids = payload.get("track_ids", [])
                repo.restore_tracks(track_ids)
                if payload.get("mode", "move_linked_lyrics") == "move_linked_lyrics":
                    repo.restore_lyrics_for_tracks(track_ids)
                return "ok:restore_tracks"
            if t == "restore_tracks":
                LibraryOpsService(repo).delete_tracks(payload.get("track_ids", []), mode="move_linked_lyrics")
                return "ok:soft_delete_tracks"
            if t == "delete_lyrics":
                repo.restore_lyrics(payload.get("lyrics_ids", []))
                return "ok:delete_lyrics"
            if t == "restore_lyrics":
                repo.move_lyrics_to_trash(payload.get("lyrics_ids", []))
                return "ok:restore_lyrics"
            if t == "update_tracks_fields":
                for row in payload.get("rollback_values", []):
                    track_id = row.get("track_id")
                    patch = {k: v for k, v in row.items() if k != "track_id"}
                    if track_id:
                        repo.update_tracks_fields([track_id], patch)
                return "ok:update_tracks_fields"
            if t == "update_lyrics_fields":
                for row in payload.get("rollback_values", []):
                    lyrics_id = row.get("lyrics_id")
                    if not lyrics_id:
                        continue
                    patch = {
                        "file_name": row.get("file_name", ""),
                        "lyrics_title": row.get("lyrics_title", ""),
                        "lyrics_artist": row.get("lyrics_artist", ""),
                        "lyrics_album": row.get("lyrics_album", ""),
                        "lyrics_author": row.get("lyrics_author", ""),
                    }
                    repo.update_lyrics_fields([lyrics_id], patch)
                return "ok:update_lyrics_fields"
            if t == "set_primary_lyrics_for_track":
                track_id = str(payload.get("track_id", "") or "")
                old_lyrics_id = payload.get("old_lyrics_id")
                new_lyrics_id = payload.get("new_lyrics_id")
                old_track_for_new = payload.get("old_track_for_new_lyrics")
                if track_id:
                    repo.set_primary_lyrics_for_track(track_id, old_lyrics_id)
                if new_lyrics_id:
                    if old_track_for_new and str(old_track_for_new) != track_id:
                        repo.set_primary_lyrics_for_track(str(old_track_for_new), str(new_lyrics_id))
                    elif not old_track_for_new:
                        repo.set_primary_track_for_lyrics(str(new_lyrics_id), None)
                return "ok:set_primary_lyrics_for_track"
            if t == "set_primary_track_for_lyrics":
                lyrics_id = str(payload.get("lyrics_id", "") or "")
                old_track_id = payload.get("old_track_id")
                new_track_id = payload.get("new_track_id")
                old_lyrics_for_new = payload.get("old_lyrics_for_new_track")
                if lyrics_id:
                    repo.set_primary_track_for_lyrics(lyrics_id, old_track_id)
                if new_track_id:
                    if old_lyrics_for_new and str(old_lyrics_for_new) != lyrics_id:
                        repo.set_primary_lyrics_for_track(str(new_track_id), str(old_lyrics_for_new))
                    elif not old_lyrics_for_new:
                        repo.set_primary_lyrics_for_track(str(new_track_id), None)
                return "ok:set_primary_track_for_lyrics"
            if t == "create_playlist":
                repo.delete_playlist(payload.get("playlist_id", ""))
                return "ok:delete_playlist"
            if t == "delete_playlist":
                playlist_id = payload.get("playlist_id") or new_id("pl")
                repo.create_playlist(playlist_id, payload.get("name", ""), payload.get("description", ""))
                items = payload.get("items", [])
                ordered = [it.get("track_id") for it in sorted(items, key=lambda x: int(x.get("position", 0))) if it.get("track_id")]
                repo.add_tracks_to_playlist(playlist_id, ordered)
                if ordered:
                    repo.reorder_playlist(playlist_id, ordered)
                entry_patch = {str(it.get("track_id")): int(it.get("entry", idx)) for idx, it in enumerate(items) if it.get("track_id")}
                if entry_patch:
                    repo.update_playlist_entries(playlist_id, entry_patch)
                return "ok:restore_playlist"
            if t == "add_tracks_to_playlist":
                repo.remove_tracks_from_playlist(payload.get("playlist_id", ""), payload.get("track_ids", []))
                return "ok:remove_tracks_from_playlist"
            if t == "remove_tracks_from_playlist":
                playlist_id = payload.get("playlist_id", "")
                items_before = payload.get("items_before", [])
                ordered = [it.get("track_id") for it in sorted(items_before, key=lambda x: int(x.get("position", 0))) if it.get("track_id")]
                repo.add_tracks_to_playlist(playlist_id, ordered)
                if ordered:
                    repo.reorder_playlist(playlist_id, ordered)
                entry_patch = {
                    str(it.get("track_id")): int(it.get("entry", idx))
                    for idx, it in enumerate(items_before)
                    if it.get("track_id")
                }
                if entry_patch:
                    repo.update_playlist_entries(playlist_id, entry_patch)
                return "ok:restore_playlist_items"
            if t == "clear_playlist":
                playlist_id = payload.get("playlist_id", "")
                items_before = payload.get("items_before", [])
                ordered = [it.get("track_id") for it in sorted(items_before, key=lambda x: int(x.get("position", 0))) if it.get("track_id")]
                repo.add_tracks_to_playlist(playlist_id, ordered)
                if ordered:
                    repo.reorder_playlist(playlist_id, ordered)
                entry_patch = {
                    str(it.get("track_id")): int(it.get("entry", idx))
                    for idx, it in enumerate(items_before)
                    if it.get("track_id")
                }
                if entry_patch:
                    repo.update_playlist_entries(playlist_id, entry_patch)
                return "ok:restore_playlist_items"
            if t == "reorder_playlist":
                repo.reorder_playlist(payload.get("playlist_id", ""), payload.get("ordered_track_ids_before", []))
                return "ok:reorder_playlist"
            if t == "update_playlist_entries":
                repo.update_playlist_entries(payload.get("playlist_id", ""), payload.get("before_entries", {}))
                return "ok:update_playlist_entries"
            if t == "create_fullscan_work":
                repo.delete_fullscan_work(payload.get("work_id", ""))
                return "ok:delete_fullscan_work"
            if t == "resolve_reviews":
                repo.set_reviews_status(payload.get("review_ids", []), "pending")
                return "ok:restore_reviews_pending"

            return f"unsupported:{t}"

    def redo_last_action(self) -> str:
        if not self._redo_actions:
            return "no_action"

        action = self._redo_actions.pop()
        payload = action.get("payload", {})
        t = str(action.get("action_type", ""))

        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)

            if t == "soft_delete_tracks":
                mode = payload.get("mode", "move_linked_lyrics")
                LibraryOpsService(repo).delete_tracks(payload.get("track_ids", []), mode=mode)
            elif t == "restore_tracks":
                LibraryOpsService(repo).restore_tracks(payload.get("track_ids", []))
            elif t == "delete_lyrics":
                repo.move_lyrics_to_trash(payload.get("lyrics_ids", []))
            elif t == "restore_lyrics":
                repo.restore_lyrics(payload.get("lyrics_ids", []))
            elif t == "update_tracks_fields":
                repo.update_tracks_fields(payload.get("track_ids", []), payload.get("applied_fields", {}))
            elif t == "update_lyrics_fields":
                repo.update_lyrics_fields(payload.get("lyrics_ids", []), payload.get("applied_fields", {}))
            elif t == "set_primary_lyrics_for_track":
                repo.set_primary_lyrics_for_track(payload.get("track_id", ""), payload.get("new_lyrics_id"))
            elif t == "set_primary_track_for_lyrics":
                repo.set_primary_track_for_lyrics(payload.get("lyrics_id", ""), payload.get("new_track_id"))
            elif t == "create_playlist":
                repo.create_playlist(payload.get("playlist_id", new_id("pl")), payload.get("name", ""), payload.get("description", ""))
            elif t == "delete_playlist":
                repo.delete_playlist(payload.get("playlist_id", ""))
            elif t == "add_tracks_to_playlist":
                repo.add_tracks_to_playlist(payload.get("playlist_id", ""), payload.get("track_ids", []))
            elif t == "remove_tracks_from_playlist":
                repo.remove_tracks_from_playlist(payload.get("playlist_id", ""), payload.get("track_ids_removed", []))
            elif t == "clear_playlist":
                repo.clear_playlist(payload.get("playlist_id", ""))
            elif t == "reorder_playlist":
                repo.reorder_playlist(payload.get("playlist_id", ""), payload.get("ordered_track_ids_after", []))
            elif t == "update_playlist_entries":
                repo.update_playlist_entries(payload.get("playlist_id", ""), payload.get("after_entries", {}))
            elif t == "create_fullscan_work":
                repo.create_fullscan_work(
                    payload.get("work_id", new_id("work")),
                    payload.get("name", ""),
                    payload.get("track_ids", []),
                )
            elif t == "resolve_reviews":
                repo.set_reviews_status(payload.get("review_ids", []), payload.get("status_after", "resolved"))
            else:
                self._redo_actions.append(action)
                return f"unsupported_redo:{t}"

            repo.append_undo_action(new_id("undo"), t, payload, self._undo_keep())
            return f"ok:redo:{t}"

    def get_runtime_config(self):
        return self.ctx.runtime_config
