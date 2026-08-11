from __future__ import annotations

import difflib
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from musearc.config.models import ImportThresholds
from musearc.core.models import LyricsMatchDecision
from musearc.core.text_normalize import lrc_visible_lines, normalize_text, token_set
from musearc.infra.llm.client import LlmMatchResult, LmStudioMatcher


def read_text_guess_encoding(path: Path) -> tuple[str, str]:
    """
    功能：猜测文件编码并读取文本内容。
    参数：path - Path对象，表示要读取的文件路径。
    返回值：tuple[str, str] - 包含解码后的文本字符串和使用的编码字符串。
    """
    data = path.read_bytes()  # 读取文件为字节数据
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16", "big5"):  # 尝试不同的编码格式
        try:
            return data.decode(enc), enc  # 尝试用当前编码解码字节数据
        except UnicodeDecodeError:
            continue  # 如果解码失败，继续尝试下一个编码
    return data.decode("utf-8", errors="ignore"), "utf-8-ignore"  # 如果所有尝试都失败，使用utf-8并忽略错误


class LyricsMatcher:
    def __init__(
        self,
        thresholds: ImportThresholds,
        llm: LmStudioMatcher | None = None,
        *,
        score_workers: int = 1,
        parallel_threshold: int = 96,
    ):
        self.thresholds = thresholds
        self.llm = llm
        self.score_workers = int(score_workers or 1)
        self.parallel_threshold = int(parallel_threshold or 96)

    def _resolve_workers(self) -> int:
        """
        解析并返回合理的工作进程数量。

        功能：根据配置和系统环境确定要使用的工作进程数。
        参数：无（除 self 外）
        返回值：int，表示工作进程的数量，确保在 1 到 16 之间。
        """
        # 将配置值转换为整数，如果为空或 False 则默认为 0
        workers = int(self.score_workers or 0)

        # 如果配置值无效（小于等于 0），则根据系统 CPU 核心数动态计算默认值
        if workers <= 0:
            # 计算默认值：使用 CPU 核心数减 1，保底 1 个，上限 8 个
            return max(1, min(8, (os.cpu_count() or 4) - 1))

        # 如果配置值有效，确保在 1 到 16 的范围内
        return max(1, min(16, workers))

    def _score_track_rule(
        self,
        track: dict,
        *,
        lyrics_name_norm: str,
        lyric_title_hint: str,
        lyric_artist_hint: str,
        lyrics_tokens: set[str],
    ) -> tuple[float, str]:
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
        return score, "rule_based"

    def match_one(self, lyrics_stem: str, lyrics_text: str, tracks: list[dict]) -> LyricsMatchDecision:
        """匹配单行歌词与候选音轨列表，返回最佳匹配的决策结果。

        该方法首先对歌词和音轨信息进行预处理和规范化，然后通过规则（或可选的大语言模型LLM）
        对每个候选音轨进行评分，最终根据预设阈值决定是接受、需要复审还是拒绝匹配。

        Args:
            lyrics_stem (str): 歌曲的简化标识，通常包含标题和艺术家信息。
            lyrics_text (str): 歌词文本内容。
            tracks (list[dict]): 候选音轨信息列表，每个音轨为一个字典，包含track_id等信息。

        Returns:
            LyricsMatchDecision: 匹配决策对象，包含匹配的音轨ID、匹配分数、匹配原因和是否需要人工复审的标志。
        """
        best_track_id: str | None = None  # 用于存储最佳匹配音轨的ID
        best_score = 0.0  # 当前最佳匹配分数
        best_reason = "no_match"  # 当前最佳匹配的原因
        # 从歌词文本中提取最多10行可见行（去除时间标签等非歌词信息）
        lines = lrc_visible_lines(lyrics_text, max_lines=10)
        # 将提取的行规范化并合并为一个字符串，用于后续文本分析
        lines_norm = " ".join(normalize_text(line) for line in lines)

        # 从歌词词干中解析可能的标题和艺术家提示
        lyric_title_hint, lyric_artist_hint = self._parse_title_artist_hint(lyrics_stem)
        # 规范化歌词名称
        lyrics_name_norm = self._normalize_name(lyrics_stem)
        # 提取歌词名称和正文的文本标记集合，合并为一个总集合
        lyrics_tokens = token_set(lyrics_name_norm) | token_set(lines_norm)

        # 确定可用于并行处理的工作线程数
        workers = self._resolve_workers()
        # 判断是否允许并行处理的条件：没有配置LLM、工作线程数大于1、音轨数量达到阈值
        allow_parallel = self.llm is None and workers > 1 and len(tracks) >= max(1, self.parallel_threshold)

        if allow_parallel:
            # 使用线程池并行处理音轨匹配评分
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="lyrics-match") as pool:
                # 使用map方法并行计算每个音轨的规则匹配分数，结果为(track_id, score, reason)的生成器
                scored = pool.map(
                    lambda t: (
                        str(t.get("track_id") or ""),  # 提取track_id并转为字符串
                        *self._score_track_rule(  # 解包规则评分函数返回的元组(score, reason)
                            t,
                            lyrics_name_norm=lyrics_name_norm,
                            lyric_title_hint=lyric_title_hint,
                            lyric_artist_hint=lyric_artist_hint,
                            lyrics_tokens=lyrics_tokens,
                        ),
                    ),
                    tracks,
                )
                # 遍历并行计算的结果，更新最佳匹配
                for track_id, score, reason in scored:
                    if score > best_score:
                        best_score = score
                        best_track_id = track_id
                        best_reason = reason
        else:
            # 顺序遍历每个音轨进行匹配评分
            for track in tracks:
                # 计算当前音轨的规则匹配分数和原因
                score, reason = self._score_track_rule(
                    track,
                    lyrics_name_norm=lyrics_name_norm,
                    lyric_title_hint=lyric_title_hint,
                    lyric_artist_hint=lyric_artist_hint,
                    lyrics_tokens=lyrics_tokens,
                )

                # 如果配置了LLM，则计算LLM分数并与规则分数加权合并
                if self.llm:
                    llm = self._llm_score(track, lyrics_stem, lines)
                    if llm:
                        # 使用加权平均合并规则分数和LLM分数，规则权重0.55，LLM权重0.45
                        score = score * 0.55 + llm.score * 0.45
                        # 更新匹配原因为合并后的原因
                        reason = f"rule+llm:{llm.reason}"

                # 如果当前音轨分数高于最佳分数，则更新最佳匹配信息
                if score > best_score:
                    best_score = score
                    best_track_id = str(track.get("track_id") or "")
                    best_reason = reason

        # 根据最终最佳分数与阈值比较，返回不同的决策结果
        if best_score >= self.thresholds.lyrics_match_accept:
            # 分数达到“接受”阈值，返回接受决策（无需复审）
            return LyricsMatchDecision(
                track_id=best_track_id,
                score=best_score,
                reason=best_reason,
                needs_review=False,
            )

        if best_score >= self.thresholds.lyrics_match_review:
            # 分数达到“复审”阈值，返回需要复审的决策
            return LyricsMatchDecision(
                track_id=best_track_id,
                score=best_score,
                reason=best_reason,
                needs_review=True,
            )

        # 分数低于“复审”阈值，返回拒绝决策（需要复审，但音轨ID为空）
        return LyricsMatchDecision(track_id=None, score=best_score, reason=best_reason, needs_review=True)

    @staticmethod
    def _normalize_name(value: str) -> str:
        """规范化名称字符串：移除括号及其内部内容，并进行文本规范化处理。

        参数：
            value (str): 需要规范化的原始字符串。

        返回值：
            str: 规范化后的字符串。
        """
        # 将输入值转换为字符串，若为空则使用空字符串
        text = str(value or "")
        # 使用正则表达式移除所有括号（包括中英文括号）及其内部的内容，替换为空格
        text = re.sub(r"[\(\[【{（].*?[\)\]】}）]", " ", text)
        # 调用文本规范化函数处理字符串并返回结果
        return normalize_text(text)

    def _parse_title_artist_hint(self, stem: str) -> tuple[str, str]:
        """解析标题和艺术家提示。

        从给定的字符串中提取标题和艺术家。如果字符串包含" - "分隔符，则分割并基于token数量决定哪个作为标题（或艺术家）。返回一个元组，包含标题和艺术家字符串；如果没有分隔符，则艺术家为空。

        参数:
            stem (str): 待解析的字符串。

        返回:
            tuple[str, str]: 包含标题和艺术家的元组。
        """
        # 标准化输入字符串，以统一格式处理
        normalized = self._normalize_name(stem)
        # 检查标准化字符串中是否包含" - "分隔符，用于分割标题和艺术家
        if " - " in normalized:
            # 使用" - "作为分隔符分割字符串，只分割一次以避免多次分割
            left, right = normalized.split(" - ", 1)
            # 去除左右部分的首尾空白字符，确保干净比较
            left = left.strip()
            right = right.strip()
            # 检查分割后的左右部分是否都非空，以确保有效内容
            if left and right:
                # 计算左右部分的token数量（例如单词数），用于决定哪个更可能是标题
                left_tokens = len(token_set(left))
                right_tokens = len(token_set(right))
                # 如果左边token数量大于等于右边，则左边作为标题，右边作为艺术家
                if left_tokens >= right_tokens:
                    return left, right
                # 否则，右边作为标题，左边作为艺术家
                return right, left
        # 如果没有分隔符或分割后部分为空，则返回标准化字符串作为标题，艺术家设为空字符串
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
        """计算两个字符串集合之间的 Jaccard 相似系数。

        Args:
            a: 第一个字符串集合。
            b: 第二个字符串集合。

        Returns:
            两个集合的 Jaccard 相似度，是一个介于 0.0 到 1.0 之间的浮点数。
            如果任一集合为空，则返回 0.0。
        """
        # 如果任一集合为空，相似度为0
        if not a or not b:
            return 0.0
        # 计算交集元素个数
        inter = len(a & b)
        # 计算并集元素个数
        union = len(a | b)
        # 防止分母为零的情况（理论上如果上面不为空，这里也不会为0，但作为安全检查）
        if union == 0:
            return 0.0
        # Jaccard 系数 = 交集大小 / 并集大小
        return inter / union
