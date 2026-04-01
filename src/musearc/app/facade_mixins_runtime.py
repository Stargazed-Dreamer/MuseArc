from __future__ import annotations

import json
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from typing import Callable

from pathlib import Path

from musearc.app.action_log import read_action_logs
from musearc.core.ids import new_id
from musearc.infra.media.prober import MediaProbe, repair_metadata_text, seems_mojibake_text
from musearc.services.importer import _derive_title_artist, _is_unknown_text
from musearc.services.library_ops import LibraryOpsService

FAVORITES_PLAYLIST_ID = "pl_favorites"
_RUNTIME_FP_ENGINE = None


def _runtime_worker_fp_engine():
    global _RUNTIME_FP_ENGINE
    if _RUNTIME_FP_ENGINE is None:
        from musearc.infra.media.fingerprint import AcousticFingerprintEngine

        _RUNTIME_FP_ENGINE = AcousticFingerprintEngine()
    return _RUNTIME_FP_ENGINE


def _runtime_compare_row_in_process(
    payload_a: str,
    len_a: int,
    lower: float,
    upper: float,
    candidates: list[tuple[str, str, int]],
) -> str | None:
    """Process worker: return first matched candidate track_id for one row."""
    engine = _runtime_worker_fp_engine()
    allowed_len_delta = max(16, int(len_a or 0) // 3)
    for cand_tid, payload_b, len_b in candidates:
        if not cand_tid or not payload_b:
            continue
        if abs(int(len_b or 0) - int(len_a or 0)) > allowed_len_delta:
            continue
        score = float(engine.similarity(str(payload_a or ""), str(payload_b or "")))
        if float(lower) <= score <= float(upper):
            return str(cand_tid)
    return None

class FacadeRuntimeMixin:
    """Facade mixin: runtime/fullscan/undo-redo workflows."""

    def read_logs(self) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aread_logs\u3002"""
        return read_action_logs(self.ctx.layout.root)

    def save_now(self) -> None:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1asave_now\u3002"""
        with self.ctx.db.session() as conn:
            conn.execute("SELECT 1")
        self._log("save_now")

    def create_fullscan_work(self, name: str) -> str:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1acreate_fullscan_work\u3002"""
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

    def _next_fullscan_work_name(self, base_name: str) -> str:
        base = str(base_name or "").strip() or "全量歌曲筛选"
        rows = self.list_fullscan_works()
        exists = {str(r.get("name", "")).strip() for r in rows if str(r.get("name", "")).strip()}
        if base not in exists:
            return base
        index = 2
        while True:
            candidate = f"{base}{index}"
            if candidate not in exists:
                return candidate
            index += 1

    def create_fullscan_work_all(self, base_name: str = "全量歌曲筛选") -> str:
        return self.create_fullscan_work(self._next_fullscan_work_name(base_name))

    def create_fullscan_work_metadata_similar(
        self,
        base_name: str = "元数据高相似歌曲",
        *,
        progress_callback: Callable[[int, int, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> str:
        import re

        def _name_base(value: str) -> str:
            text = re.sub(r"[\(\[【{（].*?[\)\]】}）]", " ", str(value or ""))
            return " ".join(text.casefold().split())

        rows = self.list_tracks(limit=2_000_000)
        total = max(1, len(rows) * 2)
        progress = 0
        groups: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            if callable(is_cancelled) and is_cancelled():
                return ""
            title_key = _name_base(str(row.get("title", "") or ""))
            artist_key = _name_base(str(row.get("artist", "") or ""))
            if not title_key or not artist_key:
                progress += 1
                if callable(progress_callback):
                    progress_callback(progress, total, "扫描元数据")
                continue
            key = (title_key, artist_key)
            groups.setdefault(key, []).append(row)
            progress += 1
            if callable(progress_callback):
                progress_callback(progress, total, "扫描元数据")

        picked: set[str] = set()
        for items in groups.values():
            if callable(is_cancelled) and is_cancelled():
                return ""
            if len(items) < 2:
                progress += 1
                if callable(progress_callback):
                    progress_callback(progress, total, "筛选高相似分组")
                continue
            durations = []
            for row in items:
                try:
                    durations.append(float(row.get("duration_sec", 0) or 0))
                except Exception:
                    durations.append(0.0)
            if max(durations, default=0.0) - min(durations, default=0.0) <= 10.0:
                for row in items:
                    tid = str(row.get("track_id", "") or "")
                    if tid:
                        picked.add(tid)
            progress += 1
            if callable(progress_callback):
                progress_callback(progress, total, "筛选高相似分组")

        if callable(progress_callback):
            progress_callback(total, total, "创建工作")

        return self._create_fullscan_work_from_track_ids(self._next_fullscan_work_name(base_name), sorted(picked))

    def _resolve_fullscan_fp_process_count(self) -> int:
        cfg = getattr(self.ctx.runtime_config, "ui", None)
        requested = int(getattr(cfg, "fullscan_fp_compare_processes", 0) or 0)
        general_limit = int(getattr(cfg, "general_worker_limit", 0) or 0)
        cpu = max(1, int(os.cpu_count() or 4))
        if requested <= 0:
            workers = max(1, min(8, cpu - 1))
        else:
            workers = max(1, min(32, requested, cpu))
        if general_limit > 0:
            workers = max(1, min(workers, int(general_limit)))
        return workers

    def create_fullscan_work_fingerprint_similar(
        self,
        *,
        min_score: float,
        max_score: float,
        base_name: str = "fingerprint_similar_tracks",
        progress_callback: Callable[[int, int, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> str:
        lower = max(0.0, min(1.0, float(min_score)))
        upper = max(0.0, min(1.0, float(max_score)))
        if upper < lower:
            lower, upper = upper, lower
        if upper <= 0.0:
            return self._create_fullscan_work_from_track_ids(self._next_fullscan_work_name(base_name), [])

        from musearc.infra.media.fingerprint import AcousticFingerprintEngine

        fp = AcousticFingerprintEngine()
        if not fp.chromaprint_available:
            if callable(progress_callback):
                progress_callback(100, 100, "chromaprint unavailable")
            raise RuntimeError("Chromaprint unavailable, configure libchromaprint first.")

        if callable(progress_callback):
            progress_callback(1, 100, "loading tracks")
        raw_rows = self.list_tracks(limit=2_000_000)
        if callable(progress_callback):
            progress_callback(5, 100, "filtering comparable tracks")

        if lower <= 0.0 and upper >= 0.999:
            all_ids = sorted(
                {
                    str(row.get("track_id", "") or "")
                    for row in raw_rows
                    if str(row.get("track_id", "") or "").strip()
                }
            )
            if callable(progress_callback):
                progress_callback(100, 100, "creating work")
            return self._create_fullscan_work_from_track_ids(self._next_fullscan_work_name(base_name), all_ids)

        rows: list[dict] = []
        fp_hash_cache: dict[str, int | None] = {}

        def _payload_tokens(payload: str) -> tuple[str, ...]:
            text = str(payload or "")
            if not text:
                return tuple()
            n = len(text)
            if n <= 12:
                return (text,)
            win = 10
            step = max(6, n // 36)
            toks: list[str] = [text[:win], text[-win:]]
            for pos in range(0, max(0, n - win + 1), step):
                toks.append(text[pos : pos + win])
                if len(toks) >= 48:
                    break
            return tuple(dict.fromkeys(t for t in toks if t))

        for row in raw_rows:
            if callable(is_cancelled) and is_cancelled():
                return ""
            payload = str(row.get("fingerprint_payload", "") or "").strip()
            track_id = str(row.get("track_id", "") or "").strip()
            if not payload or not track_id:
                continue
            try:
                sec = int(round(float(row.get("duration_sec", 0.0) or 0.0)))
            except Exception:
                sec = 0
            hash32 = fp_hash_cache.get(payload)
            if payload not in fp_hash_cache:
                hash32 = fp.fingerprint_hash32(payload)
                fp_hash_cache[payload] = hash32
            rows.append(
                {
                    "track_id": track_id,
                    "payload": payload,
                    "plen": int(len(payload)),
                    "sec": sec,
                    "tokens": _payload_tokens(payload),
                    "hash32": hash32,
                }
            )
        if callable(is_cancelled) and is_cancelled():
            return ""

        by_duration: dict[int, list[dict]] = {}
        row_by_id: dict[str, dict] = {}
        token_index: dict[str, list[str]] = {}
        bucket_total = max(1, len(rows))
        bucket_step = max(1, bucket_total // 200)
        for idx, row in enumerate(rows, 1):
            if callable(is_cancelled) and is_cancelled():
                return ""
            sec = int(row.get("sec", 0) or 0)
            by_duration.setdefault(sec, []).append(row)
            tid = str(row.get("track_id", "") or "")
            if tid:
                row_by_id[tid] = row
                for tok in row.get("tokens", ()) or ():
                    token_index.setdefault(str(tok), []).append(tid)
            if callable(progress_callback) and (idx == bucket_total or idx % bucket_step == 0):
                pct = 5 + int(15 * idx / bucket_total)
                progress_callback(pct, 100, "build duration buckets")

        picked: set[str] = set()
        total_rows = max(1, len(rows))
        if lower >= 0.80:
            max_compare_candidates = 48
        elif lower >= 0.60:
            max_compare_candidates = 64
        else:
            max_compare_candidates = 96

        process_workers = self._resolve_fullscan_fp_process_count()
        enable_process_parallel = process_workers > 1 and len(rows) >= max(256, process_workers * 48)

        pair_score_cache: dict[tuple[str, str], float] = {}
        parallel_jobs: list[tuple[str, str, int, list[tuple[str, str, int]]]] = []
        processed = 0
        prepared = 0
        last_emit_ts = 0.0
        progress_mark = 20

        for sec, bucket in by_duration.items():
            if callable(is_cancelled) and is_cancelled():
                return ""
            candidates: list[dict] = []
            for d in range(sec - 10, sec + 11):
                candidates.extend(by_duration.get(d, []))

            for row in bucket:
                if callable(is_cancelled) and is_cancelled():
                    return ""

                tid_a = str(row.get("track_id", "") or "")
                payload_a = str(row.get("payload", "") or "")
                len_a = int(row.get("plen", 0) or 0)
                hash_a = row.get("hash32")
                tokens_a = tuple(row.get("tokens", ()) or ())
                prepared += 1

                if not tid_a or not payload_a:
                    processed += 1
                    continue

                allowed_len_delta = max(16, len_a // 3)
                token_counter: dict[str, int] = {}
                for tok in tokens_a:
                    if callable(is_cancelled) and is_cancelled():
                        return ""
                    tids = token_index.get(str(tok), [])
                    for cand_tid in tids:
                        if callable(is_cancelled) and is_cancelled():
                            return ""
                        if cand_tid == tid_a:
                            continue
                        cand_row = row_by_id.get(cand_tid)
                        if not cand_row:
                            continue
                        cand_sec = int(cand_row.get("sec", 0) or 0)
                        if cand_sec < sec - 10 or cand_sec > sec + 10:
                            continue
                        token_counter[cand_tid] = int(token_counter.get(cand_tid, 0) or 0) + 1

                candidate_pool: list[dict] = []
                if token_counter:
                    ordered_ids = sorted(
                        token_counter.keys(),
                        key=lambda tid: (
                            -int(token_counter.get(tid, 0) or 0),
                            abs(int(row_by_id.get(tid, {}).get("plen", 0) or 0) - len_a),
                        ),
                    )
                    for cand_tid in ordered_ids[:max_compare_candidates]:
                        cand_row = row_by_id.get(cand_tid)
                        if cand_row:
                            candidate_pool.append(cand_row)
                else:
                    duration_pool: list[dict] = []
                    for cand in candidates:
                        cand_tid = str(cand.get("track_id", "") or "")
                        if not cand_tid or cand_tid == tid_a:
                            continue
                        len_b = int(cand.get("plen", 0) or 0)
                        if abs(len_b - len_a) > allowed_len_delta:
                            continue
                        duration_pool.append(cand)
                    duration_pool.sort(key=lambda c: abs(int(c.get("plen", 0) or 0) - len_a))
                    candidate_pool = duration_pool[:max_compare_candidates]

                if candidate_pool and isinstance(hash_a, int):
                    ranked: list[tuple[int, dict]] = []
                    no_hash: list[dict] = []
                    for cand in candidate_pool:
                        hash_b = cand.get("hash32")
                        if not isinstance(hash_b, int):
                            no_hash.append(cand)
                            continue
                        hd = (int(hash_a) ^ int(hash_b)).bit_count()
                        ranked.append((hd, cand))
                    if ranked:
                        ranked.sort(key=lambda item: item[0])
                        if lower >= 0.85:
                            hd_limit = 7
                            keep_n = 12
                            min_keep = 6
                        elif lower >= 0.70:
                            hd_limit = 10
                            keep_n = 18
                            min_keep = 8
                        else:
                            hd_limit = 14
                            keep_n = 28
                            min_keep = 10
                        selected = [cand for hd, cand in ranked if hd <= hd_limit][:keep_n]
                        if len(selected) < min(min_keep, len(ranked)):
                            selected = [cand for _, cand in ranked[:max(min_keep, min(keep_n, len(ranked)))]]
                        if no_hash:
                            selected_ids = {str(c.get("track_id", "") or "") for c in selected}
                            for cand in no_hash[:4]:
                                tid = str(cand.get("track_id", "") or "")
                                if tid and tid not in selected_ids:
                                    selected.append(cand)
                                    selected_ids.add(tid)
                        candidate_pool = selected

                if not candidate_pool:
                    processed += 1
                    if callable(progress_callback):
                        row_ratio = float(processed) / float(total_rows)
                        pct = 20 + int(75.0 * max(0.0, min(1.0, row_ratio)))
                        progress_mark = max(progress_mark, pct)
                        progress_callback(max(20, min(95, progress_mark)), 100, "compare fingerprints")
                    continue

                if enable_process_parallel:
                    serialized_candidates: list[tuple[str, str, int]] = []
                    for cand in candidate_pool:
                        cand_tid = str(cand.get("track_id", "") or "")
                        payload_b = str(cand.get("payload", "") or "")
                        len_b = int(cand.get("plen", 0) or 0)
                        if cand_tid and payload_b:
                            serialized_candidates.append((cand_tid, payload_b, len_b))
                    if serialized_candidates:
                        parallel_jobs.append((tid_a, payload_a, len_a, serialized_candidates))
                    else:
                        processed += 1
                    if callable(progress_callback) and (prepared == total_rows or prepared % 32 == 0):
                        prep_ratio = float(prepared) / float(total_rows)
                        prep_pct = 20 + int(20.0 * max(0.0, min(1.0, prep_ratio)))
                        progress_callback(max(20, min(45, prep_pct)), 100, "prepare parallel jobs")
                    continue

                matched_tid: str | None = None
                cand_total = max(1, len(candidate_pool))
                for cand_idx, cand in enumerate(candidate_pool, 1):
                    if callable(is_cancelled) and is_cancelled():
                        return ""
                    payload_b = str(cand.get("payload", "") or "")
                    len_b = int(cand.get("plen", 0) or 0)
                    tid_b = str(cand.get("track_id", "") or "")
                    if not payload_b or not tid_b:
                        continue
                    if abs(len_b - len_a) > allowed_len_delta:
                        continue
                    key = (tid_a, tid_b) if tid_a < tid_b else (tid_b, tid_a)
                    score = pair_score_cache.get(key)
                    if score is None:
                        score = float(fp.similarity(payload_a, payload_b))
                        pair_score_cache[key] = score
                    if lower <= score <= upper:
                        matched_tid = tid_b
                        break
                    if cand_idx % 8 == 0:
                        time.sleep(0)
                    now = time.monotonic()
                    if callable(progress_callback) and (now - last_emit_ts) >= 0.25:
                        row_ratio = (float(processed) + (float(cand_idx) / float(cand_total))) / float(total_rows)
                        pct = 20 + int(75.0 * max(0.0, min(1.0, row_ratio)))
                        progress_mark = max(progress_mark, pct)
                        progress_callback(max(20, min(95, progress_mark)), 100, "compare fingerprints")
                        last_emit_ts = now

                if matched_tid:
                    picked.add(tid_a)
                    picked.add(matched_tid)
                processed += 1
                if callable(progress_callback):
                    row_ratio = float(processed) / float(total_rows)
                    pct = 20 + int(75.0 * max(0.0, min(1.0, row_ratio)))
                    progress_mark = max(progress_mark, pct)
                    progress_callback(max(20, min(95, progress_mark)), 100, "compare fingerprints")

        if enable_process_parallel and parallel_jobs:
            total_jobs = len(parallel_jobs)
            completed_jobs = 0
            submitted = 0
            max_inflight = max(4, process_workers * 2)
            inflight: dict = {}
            pool: ProcessPoolExecutor | None = None
            try:
                pool = ProcessPoolExecutor(max_workers=process_workers)

                def _submit_more() -> None:
                    nonlocal submitted
                    while submitted < total_jobs and len(inflight) < max_inflight:
                        tid_a, payload_a, len_a, candidates = parallel_jobs[submitted]
                        fut = pool.submit(
                            _runtime_compare_row_in_process,
                            payload_a,
                            len_a,
                            lower,
                            upper,
                            candidates,
                        )
                        inflight[fut] = tid_a
                        submitted += 1

                _submit_more()
                while inflight:
                    if callable(is_cancelled) and is_cancelled():
                        pool.shutdown(wait=False, cancel_futures=True)
                        return ""
                    done, _ = wait(tuple(inflight.keys()), timeout=0.25, return_when=FIRST_COMPLETED)
                    if not done:
                        if callable(progress_callback):
                            ratio = float(processed) / float(total_rows)
                            pct = 40 + int(55.0 * max(0.0, min(1.0, ratio)))
                            progress_callback(max(40, min(95, pct)), 100, f"parallel compare workers={process_workers}")
                        continue
                    for fut in done:
                        tid_a = str(inflight.pop(fut) or "")
                        matched_tid = None
                        try:
                            matched_tid = fut.result()
                        except Exception:
                            matched_tid = None
                        if tid_a and matched_tid:
                            picked.add(tid_a)
                            picked.add(str(matched_tid))
                        processed += 1
                        completed_jobs += 1
                        if callable(progress_callback):
                            ratio = float(processed) / float(total_rows)
                            pct = 40 + int(55.0 * max(0.0, min(1.0, ratio)))
                            progress_callback(max(40, min(95, pct)), 100, f"parallel compare {completed_jobs}/{total_jobs}")
                    _submit_more()
            except Exception:
                for tid_a, payload_a, len_a, candidates in parallel_jobs:
                    if callable(is_cancelled) and is_cancelled():
                        return ""
                    matched_tid = _runtime_compare_row_in_process(payload_a, len_a, lower, upper, candidates)
                    if tid_a and matched_tid:
                        picked.add(tid_a)
                        picked.add(str(matched_tid))
                    processed += 1
                    if callable(progress_callback):
                        ratio = float(processed) / float(total_rows)
                        pct = 40 + int(55.0 * max(0.0, min(1.0, ratio)))
                        progress_callback(max(40, min(95, pct)), 100, "fallback single-process compare")
            finally:
                if pool is not None:
                    try:
                        pool.shutdown(wait=False, cancel_futures=False)
                    except Exception:
                        pass

        if callable(progress_callback):
            progress_callback(98, 100, "creating work")
        return self._create_fullscan_work_from_track_ids(self._next_fullscan_work_name(base_name), sorted(picked))

    def _create_fullscan_work_from_track_ids(self, name: str, track_ids: list[str]) -> str:
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

    def update_metadata_from_id3_and_lyrics(self, work_id: str) -> dict:
        work = str(work_id or "").strip()
        if not work:
            return {"total": 0, "updated": 0, "skipped": 0, "rows": []}

        rows = self.get_fullscan_work_items(work, limit=2_000_000)
        probe = MediaProbe()
        out_rows: list[dict] = []
        updated = 0
        skipped = 0
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            for row in rows:
                track_id = str(row.get("track_id", "") or "")
                storage_rel = str(row.get("storage_relpath", "") or "")
                target = Path(self.ctx.layout.root) / storage_rel if storage_rel else None
                if not track_id or target is None or not target.exists():
                    skipped += 1
                    out_rows.append(
                        {
                            "track_id": track_id,
                            "file_name": str(row.get("file_name", "") or ""),
                            "status": "skipped",
                            "reason": "文件不存在",
                            "applied": "",
                        }
                    )
                    continue

                patch: dict[str, object] = {}
                reason_parts: list[str] = []
                try:
                    info = probe.probe(target)
                except Exception as exc:
                    skipped += 1
                    out_rows.append(
                        {
                            "track_id": track_id,
                            "file_name": str(row.get("file_name", "") or ""),
                            "status": "skipped",
                            "reason": f"ID3读取失败: {exc}",
                            "applied": "",
                        }
                    )
                    continue

                current_title_raw = str(row.get("title", "") or "")
                current_artist_raw = str(row.get("artist", "") or "")
                current_album_raw = str(row.get("album", "") or "")
                current_title = repair_metadata_text(current_title_raw)
                current_artist = repair_metadata_text(current_artist_raw)
                current_album = repair_metadata_text(current_album_raw)
                derived_title, derived_artist = _derive_title_artist(target, info.title, info.artist, info.tags)

                tag_title = repair_metadata_text(info.title or "")
                tag_artist = repair_metadata_text(info.artist or "")
                tag_album = repair_metadata_text(info.album or "")
                if seems_mojibake_text(tag_title):
                    tag_title = ""
                if seems_mojibake_text(tag_artist):
                    tag_artist = ""
                if seems_mojibake_text(tag_album):
                    tag_album = ""

                next_title = tag_title or current_title
                next_artist = tag_artist or current_artist
                next_album = tag_album or current_album
                if seems_mojibake_text(next_title):
                    next_title = ""
                if seems_mojibake_text(next_artist):
                    next_artist = ""
                if seems_mojibake_text(next_album):
                    next_album = ""
                if _is_unknown_text(next_title, kind="title"):
                    next_title = derived_title
                if _is_unknown_text(next_artist, kind="artist"):
                    next_artist = derived_artist

                lyrics = repo.primary_lyrics_for_track(track_id) or {}
                if _is_unknown_text(next_title, kind="title"):
                    lyrics_title = repair_metadata_text(lyrics.get("lyrics_title", "") or "")
                    if lyrics_title:
                        next_title = lyrics_title
                        reason_parts.append("歌词标题补全")
                if _is_unknown_text(next_artist, kind="artist"):
                    lyrics_artist = repair_metadata_text(lyrics.get("lyrics_artist", "") or "")
                    if lyrics_artist:
                        next_artist = lyrics_artist
                        reason_parts.append("歌词艺术家补全")
                if _is_unknown_text(next_album, kind="album"):
                    lyrics_album = repair_metadata_text(lyrics.get("lyrics_album", "") or "")
                    if lyrics_album:
                        next_album = lyrics_album
                        reason_parts.append("歌词专辑补全")

                if current_title and current_title != current_title_raw and not tag_title:
                    reason_parts.append("标题乱码修复")
                if current_artist and current_artist != current_artist_raw and not tag_artist:
                    reason_parts.append("艺术家乱码修复")
                if current_album and current_album != current_album_raw and not tag_album:
                    reason_parts.append("专辑乱码修复")

                if next_title and next_title != current_title_raw:
                    patch["title"] = next_title
                if next_artist and next_artist != current_artist_raw:
                    patch["artist"] = next_artist
                if next_album and next_album != current_album_raw:
                    patch["album"] = next_album

                if patch:
                    repo.update_tracks_fields([track_id], patch)
                    updated += 1
                    if tag_title or tag_artist or tag_album:
                        reason_parts.insert(0, "ID3覆盖")
                    out_rows.append(
                        {
                            "track_id": track_id,
                            "file_name": str(row.get("file_name", "") or ""),
                            "status": "updated",
                            "reason": "；".join(reason_parts) or "已更新",
                            "applied": ",".join(sorted(patch.keys())),
                        }
                    )
                else:
                    skipped += 1
                    out_rows.append(
                        {
                            "track_id": track_id,
                            "file_name": str(row.get("file_name", "") or ""),
                            "status": "skipped",
                            "reason": "无可更新字段",
                            "applied": "",
                        }
                    )

        self._redo_actions.clear()
        self._log(f"update_metadata_from_id3_and_lyrics work={work} updated={updated} skipped={skipped}")
        return {"total": len(rows), "updated": updated, "skipped": skipped, "rows": out_rows}

    def list_fullscan_works(self) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_fullscan_works\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).list_fullscan_works()

    def get_fullscan_work_items(self, work_id: str, limit: int = 200000) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aget_fullscan_work_items\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).get_fullscan_work_items(work_id, limit)

    def remove_fullscan_items(self, work_id: str, track_ids: list[str]) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aremove_fullscan_items\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            count = LibraryOpsService(LibraryRepository(conn)).remove_fullscan_items(work_id, track_ids)
            if count > 0:
                self._redo_actions.clear()
            return count

    def update_fullscan_items_status(self, work_id: str, track_ids: list[str], status: str) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aupdate_fullscan_items_status\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            count = LibraryOpsService(LibraryRepository(conn)).update_fullscan_items_status(work_id, track_ids, status)
            if count > 0:
                self._redo_actions.clear()
            return count

    def delete_fullscan_work(self, work_id: str) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1adelete_fullscan_work\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            count = LibraryOpsService(LibraryRepository(conn)).delete_fullscan_work(work_id)
            if count > 0:
                self._redo_actions.clear()
            return count

    def list_undo_actions(self, limit: int = 50) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_undo_actions\u3002"""
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
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_redo_actions\u3002"""
        return list(self._redo_actions)

    def list_action_timeline(self, limit: int = 200) -> dict:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_action_timeline\u3002"""
        undo_desc = self.list_undo_actions(limit)
        applied = list(reversed(undo_desc))
        undone = list(reversed(self._redo_actions))[:limit]
        history = applied + undone
        return {"history": history, "current_index": len(applied) - 1}

    def _restore_lyrics_merge_snapshot(self, repo, payload: dict, snapshot_key: str) -> None:
        snap = payload.get(snapshot_key, {}) if isinstance(payload, dict) else {}
        if not isinstance(snap, dict):
            return

        def _json_text(value) -> str:
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                return json.dumps(value, ensure_ascii=False)
            return "{}"

        def _restore_lyrics_row(row_payload: dict) -> None:
            if not isinstance(row_payload, dict):
                return
            lyrics_id = str(row_payload.get("lyrics_id", "") or "")
            if not lyrics_id:
                return
            repo.conn.execute(
                """
                UPDATE lyrics
                SET source_relpath = ?, storage_relpath = ?, text_hash = ?, raw_encoding = ?,
                    lyrics_title = ?, lyrics_artist = ?, lyrics_album = ?, lyrics_author = ?,
                    line_count = ?, imported_at = ?, deleted_at = ?, ext_json = ?
                WHERE lyrics_id = ?
                """,
                (
                    str(row_payload.get("source_relpath", "") or ""),
                    str(row_payload.get("storage_relpath", "") or ""),
                    str(row_payload.get("text_hash", "") or ""),
                    str(row_payload.get("raw_encoding", "") or "utf-8"),
                    str(row_payload.get("lyrics_title", "") or ""),
                    str(row_payload.get("lyrics_artist", "") or ""),
                    str(row_payload.get("lyrics_album", "") or ""),
                    str(row_payload.get("lyrics_author", "") or ""),
                    int(row_payload.get("line_count", 0) or 0),
                    str(row_payload.get("imported_at", "") or ""),
                    row_payload.get("deleted_at"),
                    _json_text(row_payload.get("ext_json", "{}")),
                    lyrics_id,
                ),
            )

        _restore_lyrics_row(snap.get("primary_row", {}))
        _restore_lyrics_row(snap.get("secondary_row", {}))

        primary_rel = str(payload.get("primary_storage_relpath", "") or "")
        secondary_rel = str(payload.get("secondary_storage_relpath", "") or "")
        primary_text = str(snap.get("primary_text", "") or "")
        secondary_text = str(snap.get("secondary_text", "") or "")
        if primary_rel:
            primary_path = self.ctx.layout.root / primary_rel
            primary_path.parent.mkdir(parents=True, exist_ok=True)
            primary_path.write_text(primary_text, encoding="utf-8")
        if secondary_rel:
            secondary_path = self.ctx.layout.root / secondary_rel
            secondary_path.parent.mkdir(parents=True, exist_ok=True)
            secondary_path.write_text(secondary_text, encoding="utf-8")

        track_links = snap.get("track_links", [])
        if isinstance(track_links, list):
            track_ids = sorted({str(r.get("track_id", "") or "") for r in track_links if isinstance(r, dict) and str(r.get("track_id", "") or "").strip()})
            if track_ids:
                placeholders = ",".join("?" for _ in track_ids)
                repo.conn.execute(f"DELETE FROM track_lyrics WHERE track_id IN ({placeholders})", tuple(track_ids))
            for row in track_links:
                if not isinstance(row, dict):
                    continue
                track_id = str(row.get("track_id", "") or "")
                lyrics_id = str(row.get("lyrics_id", "") or "")
                if not track_id or not lyrics_id:
                    continue
                repo.conn.execute(
                    """
                    INSERT INTO track_lyrics(track_id, lyrics_id, confidence, match_method, is_primary, created_at, ext_json)
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(track_id, lyrics_id) DO UPDATE SET
                      confidence = excluded.confidence,
                      match_method = excluded.match_method,
                      is_primary = excluded.is_primary,
                      created_at = excluded.created_at,
                      ext_json = excluded.ext_json
                    """,
                    (
                        track_id,
                        lyrics_id,
                        float(row.get("confidence", 0.0) or 0.0),
                        str(row.get("match_method", "") or ""),
                        int(row.get("is_primary", 0) or 0),
                        str(row.get("created_at", "") or ""),
                        _json_text(row.get("ext_json", "{}")),
                    ),
                )

        review_status = snap.get("review_status", {})
        if isinstance(review_status, dict):
            for review_id, info in review_status.items():
                rid = str(review_id or "")
                if not rid:
                    continue
                if not isinstance(info, dict):
                    continue
                repo.conn.execute(
                    "UPDATE review_queue SET status = ?, resolved_at = ? WHERE review_id = ?",
                    (str(info.get("status", "") or "pending"), info.get("resolved_at"), rid),
                )

    def undo_last_action(self) -> str:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aundo_last_action\u3002"""
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
            if t == "merge_lyrics_for_review":
                self._restore_lyrics_merge_snapshot(repo, payload, "before")
                return "ok:merge_lyrics_for_review"

            return f"unsupported:{t}"

    def redo_last_action(self) -> str:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aredo_last_action\u3002"""
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
            elif t == "merge_lyrics_for_review":
                self._restore_lyrics_merge_snapshot(repo, payload, "after")
            else:
                self._redo_actions.append(action)
                return f"unsupported_redo:{t}"

            repo.append_undo_action(new_id("undo"), t, payload, self._undo_keep())
            return f"ok:redo:{t}"

    def get_runtime_config(self):
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aget_runtime_config\u3002"""
        return self.ctx.runtime_config
