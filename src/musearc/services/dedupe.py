from __future__ import annotations

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

    def decide(
        self,
        *,
        new_payload: str,
        new_quality: float,
        new_title: str,
        new_source_ext: str | None = None,
        candidates: list[dict],
    ) -> DuplicateDecisionResult:
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

        for candidate in candidates:
            score = self.fp_engine.similarity(new_payload, candidate["fingerprint_payload"])
            if score > best_score:
                best_score = score
                best_candidate = candidate

        if not best_candidate:
            return DuplicateDecisionResult(
                decision=DuplicateDecision.KEEP_BOTH,
                score=0.0,
                reason="no_candidate",
            )

        existing_title = best_candidate["title"]
        new_kind = infer_track_kind(new_title)
        existing_kind = infer_track_kind(existing_title)

        if best_score >= self.thresholds.duplicate_high:
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

        if best_score >= self.thresholds.duplicate_review:
            return DuplicateDecisionResult(
                decision=DuplicateDecision.REVIEW,
                score=best_score,
                existing_track_id=best_candidate["track_id"],
                reason="possible_duplicate_needs_review",
            )

        return DuplicateDecisionResult(
            decision=DuplicateDecision.KEEP_BOTH,
            score=best_score,
            existing_track_id=best_candidate["track_id"],
            reason="similar_but_not_duplicate",
        )
