from __future__ import annotations

import difflib
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from musearc.config.models import ImportThresholds
from musearc.core.enums import DuplicateDecision, TrackKind
from musearc.core.models import DuplicateDecisionResult
from musearc.core.text_normalize import normalize_text
from musearc.infra.media.fingerprint import AcousticFingerprintEngine


def infer_track_kind(title: str) -> TrackKind:
    t = normalize_text(title)
    if "live" in t or "现场" in t:
        return TrackKind.LIVE
    if "remix" in t or "混音" in t:
        return TrackKind.REMIX
    if "radio edit" in t or "电台" in t:
        return TrackKind.RADIO_EDIT
    if "cover" in t or "翻唱" in t:
        return TrackKind.COVER
    return TrackKind.MAIN


@dataclass(slots=True)
class DuplicateEvaluator:
    fp_engine: AcousticFingerprintEngine
    thresholds: ImportThresholds
    compare_workers: int = 1
    parallel_threshold: int = 48

    def _resolve_workers(self) -> int:
        value = int(self.compare_workers or 0)
        if value <= 0:
            return max(1, min(8, (os.cpu_count() or 4) - 1))
        return max(1, min(16, value))

    def decide(
        self,
        *,
        new_payload: str,
        new_quality: float,
        new_title: str,
        new_artist: str | None = None,
        new_duration_sec: float | None = None,
        new_source_ext: str | None = None,
        new_hash32: int | None = None,
        candidates: list[dict],
    ) -> DuplicateDecisionResult:
        def _norm_threshold(value: float | int | None, fallback: float) -> float:
            try:
                return max(0.0, min(1.0, float(value)))
            except Exception:
                return fallback

        same_min = _norm_threshold(getattr(self.thresholds, "duplicate_high", 0.50), 0.50)
        review_min = _norm_threshold(getattr(self.thresholds, "duplicate_review", 0.30), 0.30)
        instrumental_min = _norm_threshold(getattr(self.thresholds, "duplicate_instrumental_hint", 0.10), 0.10)
        cover_min = _norm_threshold(getattr(self.thresholds, "duplicate_cover_hint", 0.01), 0.01)
        if review_min > same_min:
            review_min = same_min
        if instrumental_min > review_min:
            instrumental_min = review_min
        if cover_min > instrumental_min:
            cover_min = instrumental_min

        def _name_base(value: str) -> str:
            text = re.sub(r"[\(\[【{（].*?[\)\]】}）]", " ", str(value or ""))
            return normalize_text(text)

        def _is_unknown_artist(value: str) -> bool:
            text = normalize_text(str(value or ""))
            return text in {"", "unknown", "unknown artist", "various artists"}

        def _artist_compatible(
            cand_artist: str,
            new_artist_text: str,
            *,
            cand_title: str,
            new_title_text: str,
        ) -> bool:
            cand_norm = normalize_text(cand_artist)
            new_norm = normalize_text(new_artist_text)
            if _is_unknown_artist(cand_norm) or _is_unknown_artist(new_norm):
                return True
            if cand_norm == new_norm:
                return True
            cand_title_norm = _name_base(cand_title)
            new_title_norm = _name_base(new_title_text)
            # 当艺术家字段疑似被标题污染时，降低严格匹配要求。
            suspicious = {
                cand_title_norm,
                new_title_norm,
                normalize_text(cand_title),
                normalize_text(new_title_text),
            }
            if cand_norm in suspicious or new_norm in suspicious:
                return True
            return difflib.SequenceMatcher(None, cand_norm, new_norm).ratio() >= 0.88

        def _format_rank(value: str | None) -> int:
            text = str(value or "").lower().replace(".", "")
            rank = {
                "flac": 90,
                "wav": 85,
                "ape": 80,
                "m4a": 70,
                "aac": 68,
                "opus": 66,
                "ogg": 62,
                "wma": 56,
                "mp3": 50,
            }
            return rank.get(text, 40)

        best_score = 0.0
        best_candidate: dict | None = None
        workers = self._resolve_workers()
        threshold = max(1, int(self.parallel_threshold or 1))

        # hash32 汉明距离预筛：大幅减少需要精确比对的候选数
        _HASH32_HD_LIMIT = 14  # 汉明距离超过此值的几乎不可能相似
        filtered_candidates = candidates
        if new_hash32 is not None and candidates:
            ranked_by_hash: list[tuple[int, dict]] = []
            no_hash: list[dict] = []
            for cand in candidates:
                cand_hash = cand.get("fingerprint_hash32")
                if not isinstance(cand_hash, int):
                    no_hash.append(cand)
                    continue
                hd = (int(new_hash32) ^ int(cand_hash)).bit_count()
                if hd <= _HASH32_HD_LIMIT:
                    ranked_by_hash.append((hd, cand))
            if ranked_by_hash:
                ranked_by_hash.sort(key=lambda item: item[0])
                # 保留汉明距离最小的 top-K，同时保留无 hash32 的少量候选
                filtered_candidates = [cand for _, cand in ranked_by_hash[:48]]
                if no_hash:
                    existing_ids = {str(c.get("track_id", "")) for c in filtered_candidates}
                    for cand in no_hash[:8]:
                        tid = str(cand.get("track_id", ""))
                        if tid and tid not in existing_ids:
                            filtered_candidates.append(cand)
            # 如果 hash32 预筛后无候选，保留无 hash32 的候选
            elif no_hash:
                filtered_candidates = no_hash

        if workers > 1 and len(filtered_candidates) >= threshold:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dup-compare") as pool:
                scores = pool.map(
                    lambda row: self.fp_engine.similarity(new_payload, row["fingerprint_payload"]),
                    filtered_candidates,
                )
                for candidate, score in zip(filtered_candidates, scores):
                    if score > best_score:
                        best_score = score
                        best_candidate = candidate
        else:
            for candidate in filtered_candidates:
                score = self.fp_engine.similarity(new_payload, candidate["fingerprint_payload"])
                if score > best_score:
                    best_score = score
                    best_candidate = candidate

        new_name_base = _name_base(new_title)
        new_artist_text = str(new_artist or "")
        metadata_candidate: dict | None = None
        metadata_candidate_key: tuple[int, int] = (-1, -1)
        if new_name_base and candidates:
            for candidate in candidates:
                cand_title = _name_base(str(candidate.get("title", "") or ""))
                if not cand_title or cand_title != new_name_base:
                    continue
                if new_duration_sec is not None:
                    try:
                        cand_dur = float(candidate.get("duration_sec", 0) or 0)
                    except Exception:
                        cand_dur = 0.0
                    if abs(cand_dur - float(new_duration_sec or 0.0)) > 10.0:
                        continue
                cand_artist = str(candidate.get("artist", "") or "")
                if not _artist_compatible(
                    cand_artist,
                    new_artist_text,
                    cand_title=str(candidate.get("title", "") or ""),
                    new_title_text=new_title,
                ):
                    continue
                fmt_rank = _format_rank(str(candidate.get("storage_format") or candidate.get("source_ext") or ""))
                q_score = int(round(float(candidate.get("quality_score", 0.0) or 0.0) * 1000.0))
                key = (fmt_rank, q_score)
                if key > metadata_candidate_key:
                    metadata_candidate = candidate
                    metadata_candidate_key = key

        if not best_candidate:
            if metadata_candidate:
                return DuplicateDecisionResult(
                    decision=DuplicateDecision.REVIEW,
                    score=0.0,
                    existing_track_id=str(metadata_candidate.get("track_id", "") or ""),
                    reason="name_duration_match_needs_review",
                )
            return DuplicateDecisionResult(
                decision=DuplicateDecision.KEEP_BOTH,
                score=0.0,
                reason="no_candidate",
            )

        existing_title = best_candidate["title"]
        new_kind = infer_track_kind(new_title)
        existing_kind = infer_track_kind(existing_title)

        if best_score >= same_min:
            if new_kind != existing_kind and {new_kind, existing_kind} != {TrackKind.UNKNOWN}:
                return DuplicateDecisionResult(
                    decision=DuplicateDecision.KEEP_BOTH,
                    score=best_score,
                    existing_track_id=best_candidate["track_id"],
                    reason="high_similarity_but_distinct_version",
                )

            existing_quality = float(best_candidate["quality_score"])
            existing_format = best_candidate.get("storage_format") or best_candidate.get("source_ext")
            existing_rank = _format_rank(existing_format)
            new_rank = _format_rank(new_source_ext)
            if new_quality > existing_quality + 0.08:
                return DuplicateDecisionResult(
                    decision=DuplicateDecision.KEEP_NEW,
                    score=best_score,
                    existing_track_id=best_candidate["track_id"],
                    reason="replace_with_higher_quality",
                )
            if new_rank >= existing_rank + 6 and new_quality >= existing_quality - 0.08:
                return DuplicateDecisionResult(
                    decision=DuplicateDecision.KEEP_NEW,
                    score=best_score,
                    existing_track_id=best_candidate["track_id"],
                    reason="replace_with_better_format",
                )
            if new_rank > existing_rank and new_quality >= existing_quality - 0.18:
                return DuplicateDecisionResult(
                    decision=DuplicateDecision.KEEP_NEW,
                    score=best_score,
                    existing_track_id=best_candidate["track_id"],
                    reason="replace_with_better_format",
                )
            if new_rank >= existing_rank + 10 and new_quality >= existing_quality - 0.28:
                return DuplicateDecisionResult(
                    decision=DuplicateDecision.KEEP_NEW,
                    score=best_score,
                    existing_track_id=best_candidate["track_id"],
                    reason="replace_with_better_format",
                )
            if abs(new_quality - existing_quality) <= 0.08 and new_rank > existing_rank:
                return DuplicateDecisionResult(
                    decision=DuplicateDecision.KEEP_NEW,
                    score=best_score,
                    existing_track_id=best_candidate["track_id"],
                    reason="replace_with_better_format",
                )

            return DuplicateDecisionResult(
                decision=DuplicateDecision.KEEP_EXISTING,
                score=best_score,
                existing_track_id=best_candidate["track_id"],
                reason="near_identical_duplicate",
            )

        if best_score >= review_min:
            return DuplicateDecisionResult(
                decision=DuplicateDecision.REVIEW,
                score=best_score,
                existing_track_id=best_candidate["track_id"],
                reason="same_song_similarity_review_band",
            )

        if metadata_candidate:
            return DuplicateDecisionResult(
                decision=DuplicateDecision.REVIEW,
                score=best_score,
                existing_track_id=str(metadata_candidate.get("track_id", "") or ""),
                reason="name_duration_match_needs_review",
            )

        if best_score >= instrumental_min:
            return DuplicateDecisionResult(
                decision=DuplicateDecision.KEEP_BOTH,
                score=best_score,
                existing_track_id=best_candidate["track_id"],
                reason="likely_instrumental_or_original",
            )
        if best_score >= cover_min:
            return DuplicateDecisionResult(
                decision=DuplicateDecision.KEEP_BOTH,
                score=best_score,
                existing_track_id=best_candidate["track_id"],
                reason="likely_cover_version",
            )

        return DuplicateDecisionResult(
            decision=DuplicateDecision.KEEP_BOTH,
            score=best_score,
            existing_track_id=best_candidate["track_id"],
            reason="different_song_low_similarity",
        )
