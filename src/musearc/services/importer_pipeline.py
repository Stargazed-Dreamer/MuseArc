from __future__ import annotations

"""\u5bfc\u5165\u6d41\u7a0b\u7ba1\u7ebf\u3002

\u8be5\u6a21\u5757\u627f\u8f7d `ImportService.import_path` \u7684\u4e3b\u8981\u6d41\u7a0b\u903b\u8f91\uff0c
\u5c06\u8d85\u957f\u65b9\u6cd5\u4ece\u670d\u52a1\u7c7b\u4e2d\u62c6\u51fa\uff0c\u964d\u4f4e `importer.py` \u590d\u6742\u5ea6\u3002
"""

import difflib
import html
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Callable

from musearc.core.enums import DuplicateDecision, FileHealth, ReviewKind
from musearc.core.hashing import sha1_text
from musearc.core.ids import new_id
from musearc.core.models import ImportProgress, ImportReport, LyricsInsert, ReviewItem, TrackInsert
from musearc.core.paths import ensure_parent, shard_relpath
from musearc.core.text_normalize import lrc_visible_lines, normalize_text
from musearc.infra.db.repositories import LibraryRepository
from musearc.infra.media.commands import MediaCommandError
from musearc.infra.media.prober import repair_metadata_text
from musearc.services.import_runtime import ImportControl, ResumeState, delete_resume_state, load_resume_state, resume_state_path
from musearc.services.lyrics_match import read_text_guess_encoding
from musearc.services.scanner import scan_import_source

from musearc.services.importer import (
    _build_track_ext_payload,
    _copy_file_and_sha256,
    _derive_title_artist,
    _extract_lyrics_meta,
    _infer_lyrics_language_kind,
    _is_placeholder_empty_lyrics,
    _lyrics_group_display_name,
    _merge_ext_payload_for_duplicate,
    _name_similarity,
    _normalize_name_for_compare,
    _normalize_source_path_key,
    _normalize_track_ext_payload,
    _quality_score,
    _utc_now,
)
from musearc.services.dedupe import infer_track_kind


def run_import_path(
    service,
    repo: LibraryRepository,
    source_path: Path,
    *,
    progress_callback: Callable[[ImportProgress], None] | None = None,
    control: ImportControl | None = None,
    resume: bool = True,
) -> ImportReport:
    """\u6267\u884c\u5bfc\u5165\u4e3b\u6d41\u7a0b\uff08\u626b\u63cf\u3001\u53bb\u91cd\u3001\u5165\u5e93\u3001\u5339\u914d\u3001\u5ba1\u67e5\u3001\u65ad\u70b9\u6062\u590d\uff09\u3002"""
    state_save_every_files = 25
    state_save_every_seconds = 1.5
    source_path = source_path.resolve()
    state_file = resume_state_path(service.library_root, source_path)
    state = load_resume_state(state_file) if resume else None
    resumed = state is not None

    audio_files, lyrics_files = scan_import_source(source_path)
    scanned_files = len(audio_files) + len(lyrics_files)
    all_relpaths = [str(c.path.relative_to(source_path)).replace("\\", "/") for c in [*audio_files, *lyrics_files]]

    if state is None:
        start_time = _utc_now()
        state = ResumeState(
            version=1,
            import_batch_id=new_id("imp"),
            source_path=str(source_path),
            started_at=start_time.isoformat(),
            scanned_files=scanned_files,
            processed_files=0,
        )
        repo.start_import_batch(state.import_batch_id, str(source_path), start_time)
        service._save_state(state_file, repo, state)
    else:
        start_time = datetime.fromisoformat(state.started_at)
        state.scanned_files = scanned_files
        repo.start_import_batch(state.import_batch_id, str(source_path), start_time)

    file_state_map: dict[str, dict] = {}
    for rel in all_relpaths:
        file_state_map[rel] = {
            "relpath": rel,
            "file_name": Path(rel).name,
            "status": "待处理",
            "status_code": "pending",
            "reason": "",
        }
    for row in state.file_states or []:
        if not isinstance(row, dict):
            continue
        rel = str(row.get("relpath", "")).replace("\\", "/")
        if rel and rel in file_state_map:
            merged = dict(file_state_map[rel])
            merged.update({k: v for k, v in row.items() if k in {"status", "status_code", "reason"}})
            file_state_map[rel] = merged

    processed_relpaths = set(state.processed_relpaths)
    existing_source_path_keys = {
        _normalize_source_path_key(v)
        for v in repo.list_track_source_fullpaths(include_deleted=True)
        if str(v).strip()
    }
    skipped_audio_path_keys = service._load_skipped_audio_path_keys()
    seen_lyrics_path_keys = service._load_seen_lyrics_path_keys()
    pending_review_rows = repo.list_pending_reviews(limit=500_000)
    pending_audio_path_keys: set[str] = set()
    pending_lyrics_relpath_keys: set[str] = set()
    for review in pending_review_rows:
        payload = review.get("payload") if isinstance(review, dict) else {}
        if not isinstance(payload, dict):
            continue
        review_path = str(payload.get("path", "")).strip()
        if review_path:
            pending_audio_path_keys.add(_normalize_source_path_key(review_path))
        lyrics_source = str(payload.get("lyrics_source", "")).replace("\\", "/").strip()
        if lyrics_source:
            pending_lyrics_relpath_keys.add(lyrics_source.casefold())
    skipped_audio_registry_dirty = False
    seen_lyrics_registry_dirty = False

    file_state_rows = [file_state_map[rel] for rel in all_relpaths if rel in file_state_map]
    last_state_save_ts = time.monotonic()
    last_saved_processed = int(state.processed_files or 0)
    duplicate_candidates_cache: dict[tuple[int, int], list[dict]] = {}

    def snapshot_file_states() -> list[dict]:
        """\u5feb\u7167\u5f53\u524d\u6279\u6b21\u6bcf\u4e2a\u6587\u4ef6\u7684\u72b6\u6001\u3002"""
        return [dict(row) for row in file_state_rows]

    def set_processing(relpath: str, step: str) -> None:
        """\u5c06\u6587\u4ef6\u72b6\u6001\u6807\u8bb0\u4e3a\u5904\u7406\u4e2d\u3002"""
        row = file_state_map.get(relpath)
        if not row:
            return
        row["status_code"] = "processing"
        row["status"] = f"处理中-{step}"
        row["reason"] = step

    def set_archived(relpath: str) -> None:
        """\u5c06\u6587\u4ef6\u72b6\u6001\u6807\u8bb0\u4e3a\u5df2\u5f52\u6863\u3002"""
        row = file_state_map.get(relpath)
        if not row:
            return
        row["status_code"] = "archived"
        row["status"] = "已归档"
        row["reason"] = ""

    def set_review(relpath: str, reason: str) -> None:
        """\u5c06\u6587\u4ef6\u72b6\u6001\u6807\u8bb0\u4e3a\u5f85\u5ba1\u67e5\u5e76\u8bb0\u5f55\u539f\u56e0\u3002"""
        row = file_state_map.get(relpath)
        if not row:
            return
        text = str(reason or "").strip() or "待人工确认"
        row["status_code"] = "review"
        row["status"] = f"待审查-{text}"
        row["reason"] = text

    def set_skipped(relpath: str, reason: str, *, source_path: Path | None = None) -> None:
        """\u5c06\u6587\u4ef6\u72b6\u6001\u6807\u8bb0\u4e3a\u5df2\u8df3\u8fc7\u5e76\u53ef\u767b\u8bb0\u8def\u5f84\u7d22\u5f15\u3002"""
        nonlocal skipped_audio_registry_dirty
        row = file_state_map.get(relpath)
        if not row:
            return
        text = str(reason or "").strip() or "已跳过"
        row["status_code"] = "skipped"
        row["status"] = f"已跳过-{text}"
        row["reason"] = text
        if source_path is None:
            return
        key = _normalize_source_path_key(source_path)
        if not key:
            return
        if key not in skipped_audio_path_keys:
            skipped_audio_path_keys.add(key)
            skipped_audio_registry_dirty = True

    def flush_skipped_audio_registry() -> None:
        """\u6309\u9700\u843d\u76d8\u5df2\u8df3\u8fc7\u97f3\u9891\u8def\u5f84\u7d22\u5f15\u3002"""
        nonlocal skipped_audio_registry_dirty
        if not skipped_audio_registry_dirty:
            return
        service._save_skipped_audio_path_keys(skipped_audio_path_keys)
        skipped_audio_registry_dirty = False

    def mark_seen_lyrics_path(source_path: Path) -> None:
        """\u767b\u8bb0\u6b4c\u8bcd\u6e90\u8def\u5f84\u4e3a\u5df2\u5904\u7406\u3002"""
        nonlocal seen_lyrics_registry_dirty
        key = _normalize_source_path_key(source_path)
        if not key:
            return
        if key not in seen_lyrics_path_keys:
            seen_lyrics_path_keys.add(key)
            seen_lyrics_registry_dirty = True

    def flush_seen_lyrics_registry() -> None:
        """\u6309\u9700\u843d\u76d8\u5df2\u5904\u7406\u6b4c\u8bcd\u8def\u5f84\u7d22\u5f15\u3002"""
        nonlocal seen_lyrics_registry_dirty
        if not seen_lyrics_registry_dirty:
            return
        service._save_seen_lyrics_path_keys(seen_lyrics_path_keys)
        seen_lyrics_registry_dirty = False

    def maybe_checkpoint_state(*, force: bool = False) -> None:
        """\u6309\u9891\u7387\u68c0\u67e5\u70b9\u4fdd\u5b58\u65ad\u70b9\u4e0e\u6279\u6b21\u8fdb\u5ea6\uff0c\u907f\u514d\u6bcf\u6587\u4ef6\u5168\u91cf\u843d\u76d8\u3002"""
        nonlocal last_state_save_ts, last_saved_processed
        now = time.monotonic()
        processed = int(state.processed_files or 0)
        should_save = force
        if not should_save and processed > last_saved_processed:
            file_delta = processed - last_saved_processed
            time_delta = now - last_state_save_ts
            should_save = file_delta >= state_save_every_files or time_delta >= state_save_every_seconds
        if not should_save:
            return
        state.file_states = snapshot_file_states()
        flush_skipped_audio_registry()
        flush_seen_lyrics_registry()
        service._save_state(state_file, repo, state)
        # 提前提交当前事务，避免长时间写锁阻塞其它会话；后续写操作会自动开启新事务。
        try:
            repo.conn.commit()
        except Exception:
            pass
        last_state_save_ts = now
        last_saved_processed = processed

    def get_duplicate_candidates(duration_sec: float, *, tolerance_sec: float = 6.0) -> list[dict]:
        """\u7f13\u5b58\u76f8\u540c\u65f6\u957f\u7a97\u53e3\u7684\u53bb\u91cd\u5019\u9009\uff0c\u964d\u4f4e\u9891\u7e41 SQL \u67e5\u8be2\u5f00\u9500\u3002"""
        key = (int(round(float(duration_sec) * 10.0)), int(round(float(tolerance_sec) * 10.0)))
        cached = duplicate_candidates_cache.get(key)
        if cached is not None:
            return cached
        rows = repo.find_duplicate_candidates(duration_sec, tolerance_sec=tolerance_sec)
        duplicate_candidates_cache[key] = rows
        return rows

    batch_track_records: list[dict] = []
    if state.created_track_ids:
        prior = repo.get_tracks_by_ids(state.created_track_ids)
        for item in prior:
            source_relpath = str(item.get("source_relpath") or "").replace("\\", "/")
            source_dir_key = str(Path(source_relpath).parent).replace("\\", "/").strip().casefold()
            if source_dir_key in {"", "."}:
                source_dir_key = ""
            batch_track_records.append(
                {
                    "track_id": item.get("track_id"),
                    "title": item.get("title", ""),
                    "artist": item.get("artist", ""),
                    "album": item.get("album", ""),
                    "source_stem": Path(source_relpath).stem,
                    "source_relpath": source_relpath,
                    "source_dir_key": source_dir_key,
                    "storage_relpath": item.get("storage_relpath", ""),
                }
            )
    library_track_records: list[dict] = []
    for item in repo.list_tracks(limit=2_000_000):
        source_relpath = str(item.get("source_relpath") or "").replace("\\", "/")
        source_dir_key = str(Path(source_relpath).parent).replace("\\", "/").strip().casefold()
        if source_dir_key in {"", "."}:
            source_dir_key = ""
        library_track_records.append(
            {
                "track_id": item.get("track_id"),
                "title": item.get("title", ""),
                "artist": item.get("artist", ""),
                "album": item.get("album", ""),
                "source_stem": Path(source_relpath).stem,
                "source_relpath": source_relpath,
                "source_dir_key": source_dir_key,
                "storage_relpath": item.get("storage_relpath", ""),
            }
        )

    def build_lyrics_suggestions(stem: str, preferred: list[dict], fallback: list[dict], limit: int = 6) -> list[dict]:
        """\u57fa\u4e8e\u540d\u79f0\u76f8\u4f3c\u5ea6\u6784\u5efa\u6b4c\u8bcd\u7ed1\u5b9a\u5efa\u8bae\u5217\u8868\u3002"""
        picked: list[dict] = []
        seen_track_ids: set[str] = set()

        def _consume(records: list[dict]) -> None:
            """\u5408\u5e76\u5019\u9009\u6b4c\u66f2\u5e76\u53bb\u91cd\u3002"""
            for item in records:
                track_id = str(item.get("track_id", "") or "")
                if not track_id or track_id in seen_track_ids:
                    continue
                score = _name_similarity(stem, str(item.get("title") or item.get("source_stem") or ""))
                if score <= 0.0:
                    continue
                picked.append(
                    {
                        "track_id": track_id,
                        "title": str(item.get("title", "")),
                        "artist": str(item.get("artist", "")),
                        "score": round(score, 4),
                    }
                )
                seen_track_ids.add(track_id)

        _consume(preferred)
        _consume(fallback)
        picked.sort(key=lambda r: float(r.get("score", 0.0)), reverse=True)
        return picked[:limit]

    def _dir_key_from_relpath(relpath_text: str) -> str:
        text = str(relpath_text or "").replace("\\", "/").strip()
        if not text:
            return ""
        parent = str(Path(text).parent).replace("\\", "/").strip().casefold()
        if parent in {"", "."}:
            return ""
        return parent

    def _merge_track_records(*groups: list[dict]) -> list[dict]:
        merged: list[dict] = []
        seen: set[str] = set()
        for group in groups:
            for item in group:
                if not isinstance(item, dict):
                    continue
                tid = str(item.get("track_id", "") or "").strip()
                if not tid or tid in seen:
                    continue
                seen.add(tid)
                merged.append(item)
        return merged

    def _split_track_records_by_dir(records: list[dict], folder_key: str) -> tuple[list[dict], list[dict]]:
        same: list[dict] = []
        other: list[dict] = []
        key = str(folder_key or "").strip().casefold()
        for item in records:
            source_dir = str(item.get("source_dir_key", "") or "").strip().casefold()
            if source_dir == key:
                same.append(item)
            else:
                other.append(item)
        return same, other

    lyrics_review_groups: list[dict] = []

    def resolve_lyrics_group_key(relpath: str, lyrics_text: str) -> tuple[str, str]:
        """\u4e3a\u6b4c\u8bcd\u5ba1\u67e5\u9879\u751f\u6210\u5206\u7ec4\u952e\u4e0e\u5206\u7ec4\u6807\u9898\u3002"""
        stem_norm = _normalize_name_for_compare(Path(relpath).stem)
        lines = lrc_visible_lines(lyrics_text, max_lines=80)
        text_norm = " ".join(normalize_text(line) for line in lines)
        for group in lyrics_review_groups:
            name_sim = difflib.SequenceMatcher(None, stem_norm, group["stem_norm"]).ratio()
            text_sim = (
                difflib.SequenceMatcher(None, text_norm, group["text_norm"]).ratio()
                if text_norm and group["text_norm"]
                else 0.0
            )
            if name_sim >= 0.92 or text_sim >= 0.90:
                return str(group["group_key"]), str(group.get("group_title") or group["group_key"])

        group_key = _lyrics_group_display_name(relpath)
        lyrics_review_groups.append(
            {
                "group_key": group_key,
                "group_title": _lyrics_group_display_name(relpath),
                "stem_norm": stem_norm,
                "text_norm": text_norm,
            }
        )
        return group_key, _lyrics_group_display_name(relpath)

    last_emit_ts = 0.0
    last_file_states_emit_ts = -9999.0

    def emit(stage: str, current_file: str = "", force: bool = False, paused: bool = False) -> None:
        """\u8282\u6d41\u63a8\u9001\u5bfc\u5165\u8fdb\u5ea6\u4e8b\u4ef6\u3002"""
        nonlocal last_emit_ts, last_file_states_emit_ts
        if not progress_callback:
            return
        now = time.monotonic()
        if not force and now - last_emit_ts < 0.2:
            return
        last_emit_ts = now
        include_file_states = bool(force or paused or (now - last_file_states_emit_ts >= 5.0))
        if include_file_states:
            last_file_states_emit_ts = now
        progress_callback(
            ImportProgress(
                import_batch_id=state.import_batch_id,
                source_path=str(source_path),
                stage=stage,
                current_file=current_file,
                scanned_files=state.scanned_files,
                processed_files=state.processed_files,
                imported_tracks=state.imported_tracks,
                duplicate_tracks=state.duplicate_tracks,
                imported_lyrics=state.imported_lyrics,
                matched_lyrics=state.matched_lyrics,
                review_items=state.review_items,
                errors=len(state.errors),
                resumed=resumed,
                paused=paused,
                file_states=snapshot_file_states() if include_file_states else [],
            )
        )

    def mark_processed(relpath: str) -> None:
        """\u6807\u8bb0\u6587\u4ef6\u5904\u7406\u5b8c\u6210\u5e76\u4fdd\u5b58\u65ad\u70b9\u3002"""
        if relpath in processed_relpaths:
            return
        processed_relpaths.add(relpath)
        state.processed_relpaths.append(relpath)
        state.processed_files = len(processed_relpaths)
        maybe_checkpoint_state(force=False)

    state.file_states = snapshot_file_states()
    emit("start", force=True)
    with nullcontext():
        def on_pause_checkpoint() -> None:
            maybe_checkpoint_state(force=True)

        def process_audio_after_fingerprint(
            candidate,
            relpath: str,
            source_path_key: str,
            probe,
            fp,
            fp_error: MediaCommandError | None,
        ) -> None:
            pending_review_reason: str | None = None
            if fp_error is not None:
                err_text = str(fp_error).casefold()
                issue_title = "指纹提取失败"
                issue_reason = "指纹提取失败"
                if "loudnorm" in err_text:
                    issue_title = "响度归一不可用"
                    issue_reason = "响度归一不可用"
                state.errors.append(f"fingerprint_failed:{candidate.path}:{fp_error}")
                state.review_items += 1
                suggest_pool = get_duplicate_candidates(probe.duration_sec, tolerance_sec=30.0)
                suggestions = service._suggest_similar_tracks_by_name(candidate.path.stem, suggest_pool)
                service._enqueue_review(
                    repo,
                    ReviewItem(
                        kind=ReviewKind.FILE_ISSUE,
                        title=issue_title,
                        payload={
                            "path": str(candidate.path),
                            "error": str(fp_error),
                            "title_hint": candidate.path.stem,
                            "suggest_candidates": suggestions,
                            "group_key": suggestions[0].get("track_id") if suggestions else "",
                        },
                        priority=3,
                    ),
                )
                set_review(relpath, issue_reason)
                mark_processed(relpath)
                return

            title, artist = _derive_title_artist(candidate.path, probe.title, probe.artist, probe.tags)
            file_size = None
            try:
                file_size = int(candidate.path.stat().st_size)
            except Exception:
                file_size = None
            quality = _quality_score(
                probe.duration_sec,
                probe.bit_rate,
                candidate.ext,
                probe.sample_rate,
                file_size,
            )
            fp_payload = service.dependencies.fingerprint.encode_vector(fp.vector)
            new_ext_payload = _build_track_ext_payload(probe)
            set_processing(relpath, "源去重")

            dedupe_candidates = get_duplicate_candidates(probe.duration_sec)
            decision = service.duplicate_evaluator.decide(
                new_payload=fp_payload,
                new_quality=quality,
                new_title=title,
                new_artist=artist,
                new_duration_sec=probe.duration_sec,
                new_source_ext=candidate.ext,
                candidates=dedupe_candidates,
            )

            if decision.decision == DuplicateDecision.KEEP_EXISTING:
                existing: dict = {}
                if decision.existing_track_id:
                    existing_rows = repo.get_tracks_by_ids([decision.existing_track_id])
                    existing = existing_rows[0] if existing_rows else {}
                if existing:
                    existing_ext = _normalize_track_ext_payload(existing.get("ext_json"))
                    merged_existing_ext = _merge_ext_payload_for_duplicate(existing_ext, new_ext_payload)
                    if merged_existing_ext != existing_ext:
                        repo.update_track_ext_json(str(existing.get("track_id", "") or decision.existing_track_id), merged_existing_ext)
                state.duplicate_tracks += 1
                set_skipped(relpath, "重复且保留已有", source_path=candidate.path)
                mark_processed(relpath)
                return

            if decision.decision == DuplicateDecision.REVIEW:
                state.review_items += 1
                service._enqueue_review(
                    repo,
                    ReviewItem(
                        kind=ReviewKind.DUPLICATE,
                        title="疑似重复音频",
                        payload={
                            "path": str(candidate.path),
                            "score": decision.score,
                            "existing_track_id": decision.existing_track_id,
                            "reason": decision.reason,
                            "deferred_import": True,
                            "replace_existing_suggested": True,
                        },
                        priority=3,
                    ),
                )
                set_review(relpath, "疑似重复音频")
                mark_processed(relpath)
                return

            track_id = new_id("trk")
            ext_no_dot = candidate.ext.lower().strip(".") or "bin"
            storage_rel = shard_relpath("data/tracks", track_id, ext_no_dot)
            storage_abs = service.library_root / Path(storage_rel)
            ensure_parent(storage_abs)

            set_processing(relpath, "归档")
            emit("audio_copy", relpath)
            try:
                source_sha = _copy_file_and_sha256(candidate.path, storage_abs)
            except Exception as exc:
                state.errors.append(f"copy_failed:{candidate.path}:{exc}")
                state.review_items += 1
                service._enqueue_review(
                    repo,
                    ReviewItem(
                        kind=ReviewKind.FILE_ISSUE,
                        title="复制归档失败",
                        payload={"path": str(candidate.path), "error": str(exc)},
                        priority=3,
                    ),
                )
                if storage_abs.exists():
                    storage_abs.unlink(missing_ok=True)
                set_review(relpath, "复制归档失败")
                mark_processed(relpath)
                return

            existing_by_sha = repo.get_track_by_source_sha(source_sha)
            if existing_by_sha:
                if storage_abs.exists():
                    storage_abs.unlink(missing_ok=True)
                if existing_by_sha.get("deleted_at"):
                    state.review_items += 1
                    service._enqueue_review(
                        repo,
                        ReviewItem(
                            kind=ReviewKind.DUPLICATE,
                            title="已删除歌曲重新导入",
                            payload={
                                "path": str(candidate.path),
                                "score": 1.0,
                                "reason": "命中已删除曲目",
                                "existing_track_id": str(existing_by_sha.get("track_id", "") or ""),
                                "group_key": str(existing_by_sha.get("track_id", "") or "")[:8],
                                "deferred_import": True,
                            },
                            priority=2,
                        ),
                    )
                    set_review(relpath, "命中已删除歌曲，待确认是否重新导入")
                else:
                    state.duplicate_tracks += 1
                    set_skipped(relpath, "source_sha256??", source_path=candidate.path)
                mark_processed(relpath)
                return

            track_row = TrackInsert(
                track_id=track_id,
                file_name=candidate.path.name,
                title=title,
                artist=artist,
                album=repair_metadata_text(probe.album or ""),
                language_kind="unknown",
                preference_level=5,
                storage_format=ext_no_dot,
                kind=infer_track_kind(title),
                duration_sec=probe.duration_sec,
                sample_rate=probe.sample_rate,
                channels=probe.channels,
                bit_rate=probe.bit_rate,
                quality_score=quality,
                storage_relpath=storage_rel,
                source_relpath=relpath,
                source_fullpath=str(candidate.path.resolve()),
                source_sha256=source_sha,
                source_ext=candidate.ext,
                probe_codec=probe.codec,
                file_health=FileHealth.OK,
                fingerprint_version=fp.version,
                fingerprint_digest=fp.digest,
                fingerprint_payload=fp_payload,
                imported_at=_utc_now(),
                ext_json=new_ext_payload,
            )
            try:
                repo.insert_track(track_row)
            except Exception as exc:
                msg = str(exc).lower()
                if "unique constraint failed: tracks.source_sha256" in msg:
                    state.duplicate_tracks += 1
                    if storage_abs.exists():
                        storage_abs.unlink(missing_ok=True)
                    set_skipped(relpath, "source_sha256??", source_path=candidate.path)
                    mark_processed(relpath)
                    return
                state.errors.append(f"insert_failed:{candidate.path}:{exc}")
                state.review_items += 1
                service._enqueue_review(
                    repo,
                    ReviewItem(
                        kind=ReviewKind.FILE_ISSUE,
                        title="写入数据库失败",
                        payload={"path": str(candidate.path), "error": str(exc)},
                        priority=3,
                    ),
                )
                if storage_abs.exists():
                    storage_abs.unlink(missing_ok=True)
                set_review(relpath, "写入数据库失败")
                mark_processed(relpath)
                return

            if decision.decision == DuplicateDecision.KEEP_NEW and decision.existing_track_id:
                existing_rows = repo.get_tracks_by_ids([decision.existing_track_id])
                existing = existing_rows[0] if existing_rows else {}
                if existing:
                    merge_patch: dict[str, object] = {}

                    def _needs_fill(field_key: str, current: str) -> bool:
                        text = str(current or "").strip().casefold()
                        if field_key == "title":
                            return text in {"", "unknown title", "unknown"}
                        if field_key == "artist":
                            return text in {"", "unknown artist", "unknown"}
                        if field_key == "language_kind":
                            return text in {"", "unknown"}
                        return text == ""

                    if _needs_fill("title", title) and str(existing.get("title", "")).strip():
                        merge_patch["title"] = str(existing.get("title", "")).strip()
                    if _needs_fill("artist", artist) and str(existing.get("artist", "")).strip():
                        merge_patch["artist"] = str(existing.get("artist", "")).strip()
                    if _needs_fill("album", repair_metadata_text(probe.album or "")) and str(existing.get("album", "")).strip():
                        merge_patch["album"] = str(existing.get("album", "")).strip()
                    if _needs_fill("language_kind", "unknown") and str(existing.get("language_kind", "")).strip():
                        merge_patch["language_kind"] = str(existing.get("language_kind", "")).strip()
                    if merge_patch:
                        repo.update_tracks_fields([track_id], merge_patch)
                        title = str(merge_patch.get("title", title))
                        artist = str(merge_patch.get("artist", artist))

                    existing_ext = _normalize_track_ext_payload(existing.get("ext_json"))
                    current_new_ext = _normalize_track_ext_payload(track_row.ext_json)
                    merged_new_ext = _merge_ext_payload_for_duplicate(current_new_ext, existing_ext)
                    if merged_new_ext != current_new_ext:
                        repo.update_track_ext_json(track_id, merged_new_ext)
                        track_row.ext_json = merged_new_ext

            state.imported_tracks += 1
            duplicate_candidates_cache.clear()
            existing_source_path_keys.add(source_path_key)
            state.created_track_ids.append(track_id)
            state.created_storage_relpaths.append(storage_rel)

            batch_track_records.append(
                {
                    "track_id": track_id,
                    "title": title,
                    "artist": artist,
                    "album": repair_metadata_text(probe.album or ""),
                    "source_stem": candidate.path.stem,
                    "source_relpath": relpath,
                    "source_dir_key": str(Path(relpath).parent).replace("\\", "/").strip().casefold()
                    if str(Path(relpath).parent).replace("\\", "/").strip() not in {"", "."}
                    else "",
                    "storage_relpath": storage_rel,
                }
            )

            should_add_variant = False
            relation_type = "similar_version"
            if decision.existing_track_id:
                if decision.score >= service.runtime_cfg.thresholds.duplicate_review:
                    should_add_variant = True
                elif decision.reason == "likely_instrumental_or_original":
                    should_add_variant = True
                    relation_type = "instrumental_variant_hint"
                elif decision.reason == "likely_cover_version":
                    should_add_variant = True
                    relation_type = "cover_version_hint"
            if should_add_variant:
                repo.add_variant(
                    variant_id=new_id("var"),
                    primary_track_id=decision.existing_track_id,
                    variant_track_id=track_id,
                    relation_type=relation_type,
                    similarity_score=decision.score,
                    reason=decision.reason,
                )

            if decision.decision == DuplicateDecision.KEEP_NEW and decision.existing_track_id:
                old_relpaths = [
                    str(r.get("storage_relpath", "") or "")
                    for r in repo.get_tracks_by_ids([decision.existing_track_id])
                    if str(r.get("storage_relpath", "") or "").strip()
                ]
                deleted = repo.soft_delete_tracks([decision.existing_track_id])
                if deleted > 0:
                    state.soft_deleted_existing_ids.append(decision.existing_track_id)
                    for rel in old_relpaths:
                        try:
                            (service.library_root / rel).unlink(missing_ok=True)
                        except Exception:
                            pass
                else:
                    state.review_items += 1
                    pending_review_reason = pending_review_reason or "预期替换旧版本但未删除"
                    service._enqueue_review(
                        repo,
                        ReviewItem(
                            kind=ReviewKind.DUPLICATE,
                            title="预期替换旧版本但未删除",
                            payload={
                                "new_track_id": track_id,
                                "existing_track_id": decision.existing_track_id,
                            },
                            priority=2,
                        ),
                    )

            if pending_review_reason:
                set_review(relpath, pending_review_reason)
            else:
                set_archived(relpath)
            mark_processed(relpath)

        def process_fingerprint_batch(batch: list[tuple], fp_executor: ThreadPoolExecutor | None) -> ImportReport | None:
            if not batch:
                return None

            def _cancelled_report(relpath: str, mode: str) -> ImportReport:
                maybe_checkpoint_state(force=True)
                return service._handle_cancel(repo, state, state_file, start_time, rollback=(mode == "rollback"), emit=emit)

            if fp_executor is not None and len(batch) > 1:
                futures = [
                    fp_executor.submit(
                        service._fingerprint_with_loudness_normalization,
                        candidate.path,
                        -14.0,
                    )
                    for candidate, _relpath, _source_key, _probe in batch
                ]
                for (candidate, relpath, source_key, probe), future in zip(batch, futures):
                    cancelled, mode = service._wait_control(control, emit, relpath, on_paused=on_pause_checkpoint)
                    if cancelled:
                        return _cancelled_report(relpath, mode)
                    fp = None
                    fp_error: MediaCommandError | None = None
                    try:
                        fp = future.result()
                    except MediaCommandError as exc:
                        fp_error = exc
                    except Exception as exc:
                        fp_error = MediaCommandError(f"fingerprint_failed:{candidate.path}:{exc}")
                    process_audio_after_fingerprint(candidate, relpath, source_key, probe, fp, fp_error)
                return None

            for candidate, relpath, source_key, probe in batch:
                cancelled, mode = service._wait_control(control, emit, relpath, on_paused=on_pause_checkpoint)
                if cancelled:
                    return _cancelled_report(relpath, mode)
                fp = None
                fp_error: MediaCommandError | None = None
                try:
                    fp = service._fingerprint_with_loudness_normalization(
                        candidate.path,
                        target_lufs=-14.0,
                    )
                except MediaCommandError as exc:
                    fp_error = exc
                except Exception as exc:
                    fp_error = MediaCommandError(f"fingerprint_failed:{candidate.path}:{exc}")
                process_audio_after_fingerprint(candidate, relpath, source_key, probe, fp, fp_error)
            return None

        fp_workers = max(1, int(getattr(service, "fingerprint_workers", 1) or 1))
        fp_batch_size = max(1, min(12, fp_workers * 2))
        fp_executor = ThreadPoolExecutor(max_workers=fp_workers, thread_name_prefix="fp-gen") if fp_workers > 1 else None

        try:
            fp_batch: list[tuple] = []
            for candidate in audio_files:
                relpath = str(candidate.path.relative_to(source_path)).replace("\\", "/")
                if relpath in processed_relpaths:
                    continue
                source_path_key = _normalize_source_path_key(candidate.path)
                if source_path_key in existing_source_path_keys:
                    state.duplicate_tracks += 1
                    set_skipped(relpath, "源路径重复（库内或已删除）", source_path=candidate.path)
                    mark_processed(relpath)
                    continue
                if source_path_key in skipped_audio_path_keys:
                    state.duplicate_tracks += 1
                    set_skipped(relpath, "源路径命中历史跳过记录", source_path=candidate.path)
                    mark_processed(relpath)
                    continue
                if source_path_key in pending_audio_path_keys:
                    state.duplicate_tracks += 1
                    set_skipped(relpath, "源路径已在待审查队列", source_path=candidate.path)
                    mark_processed(relpath)
                    continue

                cancelled, mode = service._wait_control(control, emit, relpath, on_paused=on_pause_checkpoint)
                if cancelled:
                    maybe_checkpoint_state(force=True)
                    return service._handle_cancel(repo, state, state_file, start_time, rollback=(mode == "rollback"), emit=emit)

                set_processing(relpath, "音频探测")
                emit("audio_probe", relpath)
                try:
                    probe = service.dependencies.probe.probe(candidate.path)
                except MediaCommandError as exc:
                    state.errors.append(f"probe_failed:{candidate.path}:{exc}")
                    state.review_items += 1
                    service._enqueue_review(
                        repo,
                        ReviewItem(
                            kind=ReviewKind.FILE_ISSUE,
                            title="音频探测失败",
                            payload={"path": str(candidate.path), "error": str(exc)},
                            priority=3,
                        ),
                    )
                    set_review(relpath, "音频探测失败")
                    mark_processed(relpath)
                    continue

                if probe.duration_sec < service.runtime_cfg.thresholds.min_track_duration_sec:
                    state.review_items += 1
                    service._enqueue_review(
                        repo,
                        ReviewItem(
                            kind=ReviewKind.FILE_ISSUE,
                            title="疑似试听或哑文件",
                            payload={"path": str(candidate.path), "duration_sec": probe.duration_sec},
                            priority=2,
                        ),
                    )
                    set_review(relpath, "疑似试听或哑文件")
                    mark_processed(relpath)
                    continue

                set_processing(relpath, "指纹提取")
                emit("audio_loudnorm", relpath)
                emit("audio_fingerprint", relpath)
                fp_batch.append((candidate, relpath, source_path_key, probe))
                if len(fp_batch) >= fp_batch_size:
                    cancelled_report = process_fingerprint_batch(fp_batch, fp_executor)
                    fp_batch = []
                    if cancelled_report is not None:
                        return cancelled_report

            if fp_batch:
                cancelled_report = process_fingerprint_batch(fp_batch, fp_executor)
                if cancelled_report is not None:
                    return cancelled_report
        finally:
            if fp_executor is not None:
                fp_executor.shutdown(wait=True, cancel_futures=False)

    for candidate in lyrics_files:
        relpath = str(candidate.path.relative_to(source_path)).replace("\\", "/")
        if relpath in processed_relpaths:
            continue
        relpath_key = relpath.casefold()
        lyrics_dir_key = _dir_key_from_relpath(relpath)
        lyrics_dir_display = str(Path(relpath).parent).replace("\\", "/").strip()
        if lyrics_dir_display in {"", "."}:
            lyrics_dir_display = "(root)"
        lyrics_source_key = _normalize_source_path_key(candidate.path)
        if lyrics_source_key in seen_lyrics_path_keys:
            set_skipped(relpath, "歌词源路径命中历史记录")
            mark_seen_lyrics_path(candidate.path)
            mark_processed(relpath)
            continue
        if relpath_key in pending_lyrics_relpath_keys:
            set_skipped(relpath, "歌词源路径已在待审查队列")
            mark_seen_lyrics_path(candidate.path)
            mark_processed(relpath)
            continue

        cancelled, mode = service._wait_control(control, emit, relpath, on_paused=on_pause_checkpoint)
        if cancelled:
            maybe_checkpoint_state(force=True)
            return service._handle_cancel(repo, state, state_file, start_time, rollback=(mode == "rollback"), emit=emit)

        set_processing(relpath, "读取歌词")
        emit("lyrics_read", relpath)
        try:
            text, enc = read_text_guess_encoding(candidate.path)
            text = html.unescape(text)
        except Exception as exc:
            state.errors.append(f"lyrics_read_failed:{candidate.path}:{exc}")
            state.review_items += 1
            service._enqueue_review(
                repo,
                ReviewItem(
                    kind=ReviewKind.FILE_ISSUE,
                    title="歌词读取失败",
                    payload={"path": str(candidate.path), "error": str(exc)},
                    priority=2,
                ),
            )
            set_review(relpath, "歌词读取失败")
            mark_processed(relpath)
            continue

        if _is_placeholder_empty_lyrics(text):
            try:
                same_batch, _other_batch = _split_track_records_by_dir(batch_track_records, lyrics_dir_key)
                placeholder_pool = same_batch if same_batch else batch_track_records
                placeholder_match = service.lyrics_matcher.match_one(candidate.stem_normalized, text, placeholder_pool)
                if placeholder_match.track_id and float(placeholder_match.score or 0.0) >= 0.65:
                    repo.update_tracks_fields([str(placeholder_match.track_id)], {"language_kind": "instrumental"})
            except Exception:
                pass
            set_skipped(relpath, "纯音乐占位歌词")
            mark_seen_lyrics_path(candidate.path)
            mark_processed(relpath)
            continue

        text_hash = sha1_text(text)
        lyrics_author, line_count, lyrics_title, lyrics_artist, lyrics_album = _extract_lyrics_meta(text)
        lyrics_language_kind = _infer_lyrics_language_kind(text)
        existing_lyrics = repo.get_lyrics_by_text_hash(text_hash)
        if existing_lyrics and existing_lyrics.get("deleted_at"):
            state.review_items += 1
            lyrics_group_key, lyrics_group_title = resolve_lyrics_group_key(relpath, text)
            service._enqueue_review(
                repo,
                ReviewItem(
                    kind=ReviewKind.LYRICS_MATCH,
                    title="已删除歌词重新导入",
                    payload={
                        "lyrics_source": relpath,
                        "lyrics_id": str(existing_lyrics.get("lyrics_id", "") or ""),
                        "suggest_track_id": "",
                        "score": 1.0,
                        "reason": "命中已删除歌词",
                        "lyrics_preview": text.splitlines()[:10],
                        "group_key": lyrics_group_key,
                        "lyrics_group_key": lyrics_group_key,
                        "lyrics_group_title": lyrics_group_title,
                    },
                    priority=2,
                ),
            )
            set_review(relpath, "命中已删除歌词，待确认是否重新导入")
            pending_lyrics_relpath_keys.add(relpath_key)
            mark_seen_lyrics_path(candidate.path)
            mark_processed(relpath)
            continue

        if existing_lyrics:
            set_skipped(relpath, "歌词文本重复")
            mark_seen_lyrics_path(candidate.path)
            mark_processed(relpath)
            continue
        else:
            lyrics_id = new_id("lrc")
            lyrics_rel = shard_relpath("data/lyrics", lyrics_id, "lrc")
            lyrics_abs = service.library_root / Path(lyrics_rel)
            ensure_parent(lyrics_abs)
            try:
                lyrics_abs.write_text(text, encoding="utf-8")
                repo.insert_lyrics(
                    LyricsInsert(
                        lyrics_id=lyrics_id,
                        source_relpath=relpath,
                        storage_relpath=lyrics_rel,
                        text_hash=text_hash,
                        raw_encoding=enc,
                        lyrics_title=lyrics_title,
                        lyrics_artist=lyrics_artist,
                        lyrics_album=lyrics_album,
                        lyrics_author=lyrics_author,
                        line_count=line_count,
                        imported_at=_utc_now(),
                        ext_json={"language_kind": lyrics_language_kind},
                    )
                )
            except Exception as exc:
                state.errors.append(f"lyrics_insert_failed:{candidate.path}:{exc}")
                state.review_items += 1
                service._enqueue_review(
                    repo,
                    ReviewItem(
                        kind=ReviewKind.FILE_ISSUE,
                        title="歌词写入失败",
                        payload={"path": str(candidate.path), "error": str(exc)},
                        priority=2,
                    ),
                )
                if lyrics_abs.exists():
                    lyrics_abs.unlink(missing_ok=True)
                set_review(relpath, "歌词写入失败")
                mark_processed(relpath)
                continue
            state.imported_lyrics += 1
            state.created_lyrics_ids.append(lyrics_id)
            state.created_storage_relpaths.append(lyrics_rel)
            mark_seen_lyrics_path(candidate.path)

        set_processing(relpath, "歌词匹配")
        emit("lyrics_match", relpath)
        try:
            batch_same, batch_other = _split_track_records_by_dir(batch_track_records, lyrics_dir_key)
            library_same, library_other = _split_track_records_by_dir(library_track_records, lyrics_dir_key)
            same_folder_pool = _merge_track_records(batch_same, library_same)
            cross_folder_pool = _merge_track_records(batch_other, library_other)
            cross_folder_track_map = {
                str(item.get("track_id", "") or ""): item
                for item in cross_folder_pool
                if str(item.get("track_id", "") or "").strip()
            }

            # Folder-first policy:
            # 1) only evaluate same-folder candidates first
            # 2) evaluate cross-folder candidates only when same-folder has no track hit
            match = service.lyrics_matcher.match_one(candidate.stem_normalized, text, same_folder_pool)
            match_origin = "same_folder"
            if (not match.track_id) and cross_folder_pool:
                cross_match = service.lyrics_matcher.match_one(candidate.stem_normalized, text, cross_folder_pool)
                if cross_match.track_id:
                    match = cross_match
                    match_origin = "cross_folder"

            folder_mismatch = bool(match.track_id and match_origin == "cross_folder")
            target_dir_display = ""
            if folder_mismatch:
                target_track = cross_folder_track_map.get(str(match.track_id), {})
                target_source_rel = str(target_track.get("source_relpath", "") or "").replace("\\", "/").strip()
                target_dir_display = str(Path(target_source_rel).parent).replace("\\", "/").strip()
                if target_dir_display in {"", "."}:
                    target_dir_display = "(root)"

            if match.track_id and not match.needs_review and not folder_mismatch:
                repo.link_lyrics(
                    track_id=match.track_id,
                    lyrics_id=lyrics_id,
                    confidence=match.score,
                    match_method=f"same_folder:{match.reason}",
                )
                state.matched_lyrics += 1
                set_archived(relpath)
            else:
                state.review_items += 1
                lyrics_group_key, lyrics_group_title = resolve_lyrics_group_key(relpath, text)
                review_reason = str(match.reason or "").strip() or "no_match"
                discard_reason = "match_confidence_low"
                if folder_mismatch:
                    review_reason = (
                        f"{review_reason}; 目标歌曲文件夹不对应 "
                        f"(lyrics={lyrics_dir_display}, track={target_dir_display})"
                    )
                    discard_reason = "目标歌曲文件夹不对应"
                lyrics_suggestions = build_lyrics_suggestions(
                    candidate.path.stem,
                    preferred=same_folder_pool,
                    fallback=cross_folder_pool,
                    limit=6,
                )
                service._enqueue_review(
                    repo,
                    ReviewItem(
                        kind=ReviewKind.LYRICS_MATCH,
                        title=(
                            "歌词匹配待人工审查"
                            if not folder_mismatch
                            else "歌词跨目录匹配待审查"
                        ),
                        payload={
                            "lyrics_source": relpath,
                            "lyrics_id": lyrics_id,
                            "suggest_track_id": match.track_id,
                            "score": match.score,
                            "reason": review_reason,
                            "discard_reason": discard_reason,
                            "lyrics_preview": text.splitlines()[:10],
                            "title_hint": candidate.path.stem,
                            "suggest_candidates": lyrics_suggestions,
                            "group_key": lyrics_suggestions[0].get("track_id") if lyrics_suggestions else lyrics_group_key,
                            "lyrics_group_key": lyrics_group_key,
                            "lyrics_group_title": lyrics_group_title,
                            "folder_mismatch": folder_mismatch,
                            "lyrics_dir": lyrics_dir_display if folder_mismatch else "",
                            "track_dir": target_dir_display if folder_mismatch else "",
                        },
                        priority=2,
                    ),
                )
                if match.track_id and not folder_mismatch:
                    repo.link_lyrics(
                        track_id=match.track_id,
                        lyrics_id=lyrics_id,
                        confidence=match.score,
                        match_method=f"review:{match.reason}",
                        is_primary=False,
                    )
                set_review(
                    relpath,
                    "歌词匹配待人工审查" if not folder_mismatch else "目标歌曲文件夹不对应",
                )
                pending_lyrics_relpath_keys.add(relpath_key)

        except Exception as exc:
            state.errors.append(f"lyrics_match_failed:{candidate.path}:{exc}")
            state.review_items += 1
            service._enqueue_review(
                repo,
                ReviewItem(
                    kind=ReviewKind.FILE_ISSUE,
                    title="歌词匹配执行失败",
                    payload={"path": str(candidate.path), "error": str(exc)},
                    priority=2,
                ),
            )
            set_review(relpath, "歌词匹配执行失败")
            pending_lyrics_relpath_keys.add(relpath_key)

        mark_processed(relpath)

    end_time = _utc_now()
    maybe_checkpoint_state(force=True)
    state.file_states = snapshot_file_states()
    repo.finish_import_batch(
        state.import_batch_id,
        scanned_files=state.scanned_files,
        imported_tracks=state.imported_tracks,
        duplicate_tracks=state.duplicate_tracks,
        imported_lyrics=state.imported_lyrics,
        matched_lyrics=state.matched_lyrics,
        review_items=state.review_items,
        errors=state.errors,
        finished_at=end_time,
    )
    delete_resume_state(state_file)

    report = ImportReport(
        import_batch_id=state.import_batch_id,
        source_path=str(source_path),
        started_at=start_time,
        finished_at=end_time,
        scanned_files=state.scanned_files,
        imported_tracks=state.imported_tracks,
        duplicate_tracks=state.duplicate_tracks,
        imported_lyrics=state.imported_lyrics,
        matched_lyrics=state.matched_lyrics,
        review_items=state.review_items,
        errors=state.errors,
        cancelled=False,
        rollback_applied=False,
        resume_available=False,
        file_states=state.file_states,
    )

    service._write_manifest(report)
    emit("done", force=True)
    return report
