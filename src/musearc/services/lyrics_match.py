from __future__ import annotations

import difflib
import re
from pathlib import Path

from musearc.config.models import ImportThresholds
from musearc.core.models import LyricsMatchDecision
from musearc.core.text_normalize import lrc_visible_lines, normalize_text, token_set
from musearc.infra.llm.client import LlmMatchResult, LmStudioMatcher


def read_text_guess_encoding(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16", "big5"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore"), "utf-8-ignore"


class LyricsMatcher:
    def __init__(self, thresholds: ImportThresholds, llm: LmStudioMatcher | None = None):
        self.thresholds = thresholds
        self.llm = llm

    def match_one(self, lyrics_stem: str, lyrics_text: str, tracks: list[dict]) -> LyricsMatchDecision:
        best_track_id: str | None = None
        best_score = 0.0
        best_reason = "no_match"
        lines = lrc_visible_lines(lyrics_text, max_lines=10)
        lines_norm = " ".join(normalize_text(line) for line in lines)

        lyric_title_hint, lyric_artist_hint = self._parse_title_artist_hint(lyrics_stem)
        lyrics_name_norm = self._normalize_name(lyrics_stem)
        lyrics_tokens = token_set(lyrics_name_norm) | token_set(lines_norm)

        for track in tracks:
            title = str(track.get("title") or "")
            artist = str(track.get("artist") or "")
            stem = str(track.get("source_stem") or title)

            title_norm = normalize_text(title)
            artist_norm = normalize_text(artist)
            stem_norm = self._normalize_name(stem)
            stem_title_hint, stem_artist_hint = self._parse_title_artist_hint(stem)

            track_combo_title_artist = f"{title_norm} {artist_norm}".strip()
            track_combo_artist_title = f"{artist_norm} {title_norm}".strip()

            name_sim = max(
                difflib.SequenceMatcher(None, lyrics_name_norm, stem_norm).ratio(),
                difflib.SequenceMatcher(None, lyrics_name_norm, track_combo_title_artist).ratio(),
                difflib.SequenceMatcher(None, lyrics_name_norm, track_combo_artist_title).ratio(),
            )
            title_sim = max(
                difflib.SequenceMatcher(None, lyric_title_hint or lyrics_name_norm, title_norm).ratio(),
                difflib.SequenceMatcher(None, lyric_title_hint or lyrics_name_norm, stem_title_hint or stem_norm).ratio(),
            )
            artist_sim = max(
                difflib.SequenceMatcher(None, lyric_artist_hint, artist_norm).ratio() if lyric_artist_hint and artist_norm else 0.0,
                difflib.SequenceMatcher(None, lyric_artist_hint, stem_artist_hint).ratio() if lyric_artist_hint and stem_artist_hint else 0.0,
            )
            token_overlap = self._jaccard(
                lyrics_tokens,
                token_set(title_norm) | token_set(artist_norm) | token_set(stem_norm) | token_set(track_combo_title_artist),
            )

            score = (name_sim * 0.32) + (title_sim * 0.30) + (artist_sim * 0.20) + (token_overlap * 0.18)
            title_base = self._normalize_name(title)
            exact_name_hit = lyrics_name_norm and (lyrics_name_norm == title_base or lyrics_name_norm == stem_norm)
            if exact_name_hit:
                score = max(score, 0.90)
            elif name_sim >= 0.95 and title_sim >= 0.90:
                score = max(score, 0.86)
            if lyric_title_hint and lyric_artist_hint and artist_sim > 0.65 and title_sim > 0.65:
                score = min(1.0, score + 0.07)
            reason = "rule_based"

            if self.llm:
                llm = self._llm_score(track, lyrics_stem, lines)
                if llm:
                    score = score * 0.55 + llm.score * 0.45
                    reason = f"rule+llm:{llm.reason}"

            if score > best_score:
                best_score = score
                best_track_id = str(track.get("track_id") or "")
                best_reason = reason

        if best_score >= self.thresholds.lyrics_match_accept:
            return LyricsMatchDecision(
                track_id=best_track_id,
                score=best_score,
                reason=best_reason,
                needs_review=False,
            )

        if best_score >= self.thresholds.lyrics_match_review:
            return LyricsMatchDecision(
                track_id=best_track_id,
                score=best_score,
                reason=best_reason,
                needs_review=True,
            )

        return LyricsMatchDecision(track_id=None, score=best_score, reason=best_reason, needs_review=True)

    @staticmethod
    def _normalize_name(value: str) -> str:
        text = str(value or "")
        text = re.sub(r"[\(\[【{（].*?[\)\]】}）]", " ", text)
        return normalize_text(text)

    def _parse_title_artist_hint(self, stem: str) -> tuple[str, str]:
        normalized = self._normalize_name(stem)
        if " - " in normalized:
            left, right = normalized.split(" - ", 1)
            left = left.strip()
            right = right.strip()
            if left and right:
                left_tokens = len(token_set(left))
                right_tokens = len(token_set(right))
                if left_tokens >= right_tokens:
                    return left, right
                return right, left
        return normalized, ""

    def _llm_score(self, track: dict, lyrics_stem: str, lines: list[str]) -> LlmMatchResult | None:
        if not self.llm:
            return None
        return self.llm.score_audio_lyrics(
            {
                "title": track.get("title"),
                "artist": track.get("artist"),
                "album": track.get("album"),
                "filename": track.get("source_stem"),
            },
            {
                "filename": lyrics_stem,
                "first_lines": lines,
            },
        )

    @staticmethod
    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        inter = len(a & b)
        union = len(a | b)
        if union == 0:
            return 0.0
        return inter / union
