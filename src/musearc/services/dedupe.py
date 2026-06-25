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
    """根据歌曲标题推断轨道类型。

    参数:
        title (str): 歌曲标题字符串。

    返回值:
        TrackKind: 推断出的轨道类型，如LIVE、REMIX等。
    """
    t = normalize_text(title)  # 标准化标题文本以进行关键词匹配
    if "live" in t or "现场" in t:  # 检查是否包含"live"或"现场"，以识别现场表演类型
        return TrackKind.LIVE
    if "remix" in t or "混音" in t:  # 检查是否包含"remix"或"混音"，以识别混音版本
        return TrackKind.REMIX
    if "radio edit" in t or "电台" in t:  # 检查是否包含"radio edit"或"电台"，以识别电台编辑版本
        return TrackKind.RADIO_EDIT
    if "cover" in t or "翻唱" in t:  # 检查是否包含"cover"或"翻唱"，以识别翻唱版本
        return TrackKind.COVER
    return TrackKind.MAIN  # 如果没有匹配特定关键词，则默认返回主轨道


@dataclass(slots=True)
class DuplicateEvaluator:
    fp_engine: AcousticFingerprintEngine
    thresholds: ImportThresholds
    compare_workers: int = 1
    parallel_threshold: int = 48

    def _resolve_workers(self) -> int:
        """
        确定并返回最终的工作进程数量。
    
        根据实例属性 `compare_workers` 和可用的 CPU 核心数，计算一个合理的 worker 进程数。
        如果 `compare_workers` 无效或为零，则基于 CPU 核心数进行智能估算。
    
        参数：
            self (Self): 类实例。
    
        返回：
            int: 计算得到的 worker 进程数，范围在 1 到 16 之间。
        """
        # 尝试将实例属性转换为整数，如果其为 None 或 Falsy，则使用 0 作为默认值
        value = int(self.compare_workers or 0)
    
        # 如果传入的值无效（小于等于0），则进行自动计算
        if value <= 0:
            # 使用 CPU 核心数减 1 作为基准（保留一个核心给主进程），并用 max(1, ...) 和 min(8, ...) 将结果限制在 1 到 8 之间
            # os.cpu_count() 可能返回 None，使用 (or 4) 作为备用值
            return max(1, min(8, (os.cpu_count() or 4) - 1))
    
        # 如果传入的值有效，则直接使用它，但通过 max(1, ...) 和 min(16, ...) 限制其范围在 1 到 16 之间
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
        """判断新音频是否与候选列表中的某个音频重复，并返回决策结果。
    
        根据指纹相似度、元数据（标题、艺术家、时长、格式）等多维度信息，决定是保留新音频、保留已有音频，还是标记为需要人工审核。
    
        参数:
            new_payload (str): 新音频的指纹数据
            new_quality (float): 新音频的质量评分
            new_title (str): 新音频的标题
            new_artist (str | None): 新音频的艺术家，默认为None
            new_duration_sec (float | None): 新音频的时长（秒），默认为None
            new_source_ext (str | None): 新音频的文件扩展名，默认为None
            new_hash32 (int | None): 新音频的32位哈希值，用于快速筛选，默认为None
            candidates (list[dict]): 候选音频列表，每个元素为包含音频信息的字典
    
        返回值:
            DuplicateDecisionResult: 包含决策类型、相似度分数、已有音频ID和决策原因的对象
        """
    
        def _norm_threshold(value: float | int | None, fallback: float) -> float:
            """标准化阈值，确保值在0.0到1.0之间，无效时返回回退值。"""
            try:
                return max(0.0, min(1.0, float(value)))
            except Exception:
                return fallback

        # 从阈值配置中获取各判断阈值，并进行标准化和调整，确保逻辑一致性
        same_min = _norm_threshold(getattr(self.thresholds, "duplicate_high", 0.50), 0.50)
        review_min = _norm_threshold(getattr(self.thresholds, "duplicate_review", 0.30), 0.30)
        instrumental_min = _norm_threshold(getattr(self.thresholds, "duplicate_instrumental_hint", 0.10), 0.10)
        cover_min = _norm_threshold(getattr(self.thresholds, "duplicate_cover_hint", 0.01), 0.01)
        # 确保阈值之间的逻辑关系：审核阈值不能超过高相似阈值，依此类推
        if review_min > same_min:
            review_min = same_min
        if instrumental_min > review_min:
            instrumental_min = review_min
        if cover_min > instrumental_min:
            cover_min = instrumental_min

        def _name_base(value: str) -> str:
            """从名称字符串中提取基础名称，移除括号及括号内内容，并进行标准化。"""
            # 使用正则移除各种括号及其内部内容，替换为空格
            text = re.sub(r"[\(\[【{（].*?[\)\]】}）]", " ", str(value or ""))
            return normalize_text(text)

        def _is_unknown_artist(value: str) -> bool:
            """检查艺术家名称是否为未知状态（空、"unknown"等）。"""
            text = normalize_text(str(value or ""))
            return text in {"", "unknown", "unknown artist", "various artists"}

        def _artist_compatible(
            cand_artist: str,
            new_artist_text: str,
            *,
            cand_title: str,
            new_title_text: str,
        ) -> bool:
            """判断两个艺术家名称是否兼容（可能相同或属于同一人）。
        
            采用多种策略判断：直接匹配、未知艺术家视为兼容、标题与艺术家字段疑似污染时放宽要求、相似度计算等。
            """
            cand_norm = normalize_text(cand_artist)
            new_norm = normalize_text(new_artist_text)
            # 如果任一方是未知艺术家，则视为兼容
            if _is_unknown_artist(cand_norm) or _is_unknown_artist(new_norm):
                return True
            # 标准化后直接匹配
            if cand_norm == new_norm:
                return True
            # 获取标题的基础名称
            cand_title_norm = _name_base(cand_title)
            new_title_norm = _name_base(new_title_text)
            # 当艺术家字段疑似被标题污染时（如艺术家字段包含标题），降低严格匹配要求
            suspicious = {
                cand_title_norm,
                new_title_norm,
                normalize_text(cand_title),
                normalize_text(new_title_text),
            }
            if cand_norm in suspicious or new_norm in suspicious:
                return True
            # 使用SequenceMatcher计算相似度，阈值0.88
            return difflib.SequenceMatcher(None, cand_norm, new_norm).ratio() >= 0.88

        def _format_rank(value: str | None) -> int:
            """将音频格式字符串映射为数值评分，用于格式优先级比较。"""
            text = str(value or "").lower().replace(".", "")
            # 常见格式的评分表，数值越高格式越优
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
            # 未识别的格式给予较低默认分
            return rank.get(text, 40)

        best_score = 0.0
        best_candidate: dict | None = None
        workers = self._resolve_workers()
        threshold = max(1, int(self.parallel_threshold or 1))

        # hash32 汉明距离预筛：大幅减少需要精确比对的候选数
        _HASH32_HD_LIMIT = 14  # 汉明距离超过此值的几乎不可能相似
        filtered_candidates = candidates
        if new_hash32 is not None and candidates:
            ranked_by_hash: list[tuple[int, dict]] = []  # 存储(汉明距离, 候选)元组
            no_hash: list[dict] = []  # 存储无hash32的候选
            for cand in candidates:
                cand_hash = cand.get("fingerprint_hash32")
                if not isinstance(cand_hash, int):
                    no_hash.append(cand)
                    continue
                # 计算汉明距离（异或后计算二进制1的个数）
                hd = (int(new_hash32) ^ int(cand_hash)).bit_count()
                if hd <= _HASH32_HD_LIMIT:
                    ranked_by_hash.append((hd, cand))
            if ranked_by_hash:
                ranked_by_hash.sort(key=lambda item: item[0])
                # 保留汉明距离最小的 top-K，同时保留无 hash32 的少量候选
                filtered_candidates = [cand for _, cand in ranked_by_hash[:48]]
                if no_hash:
                    existing_ids = {str(c.get("track_id", "")) for c in filtered_candidates}
                    # 添加无hash32的候选，避免重复
                    for cand in no_hash[:8]:
                        tid = str(cand.get("track_id", ""))
                        if tid and tid not in existing_ids:
                            filtered_candidates.append(cand)
            # 如果 hash32 预筛后无候选，保留无 hash32 的候选
            elif no_hash:
                filtered_candidates = no_hash

        # 根据工作线程数和候选数量决定是否并行计算相似度
        if workers > 1 and len(filtered_candidates) >= threshold:
            # 使用线程池并行计算指纹相似度
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
            # 串行计算指纹相似度
            for candidate in filtered_candidates:
                score = self.fp_engine.similarity(new_payload, candidate["fingerprint_payload"])
                if score > best_score:
                    best_score = score
                    best_candidate = candidate

        # 准备用于元数据匹配比较的变量
        new_name_base = _name_base(new_title)
        new_artist_text = str(new_artist or "")
        metadata_candidate: dict | None = None  # 元数据最匹配的候选
        metadata_candidate_key: tuple[int, int] = (-1, -1)  # 用于排序的键（格式评分，质量评分）
        if new_name_base and candidates:
            # 遍历所有原始候选（非预筛后）进行元数据匹配
            for candidate in candidates:
                cand_title = _name_base(str(candidate.get("title", "") or ""))
                if not cand_title or cand_title != new_name_base:
                    continue
                # 如果有时长信息，检查时长差异是否过大（超过10秒）
                if new_duration_sec is not None:
                    try:
                        cand_dur = float(candidate.get("duration_sec", 0) or 0)
                    except Exception:
                        cand_dur = 0.0
                    if abs(cand_dur - float(new_duration_sec or 0.0)) > 10.0:
                        continue
                # 检查艺术家是否兼容
                cand_artist = str(candidate.get("artist", "") or "")
                if not _artist_compatible(
                    cand_artist,
                    new_artist_text,
                    cand_title=str(candidate.get("title", "") or ""),
                    new_title_text=new_title,
                ):
                    continue
                # 计算格式评分和质量评分，用于选择最优匹配
                fmt_rank = _format_rank(str(candidate.get("storage_format") or candidate.get("source_ext") or ""))
                q_score = int(round(float(candidate.get("quality_score", 0.0) or 0.0) * 1000.0))
                key = (fmt_rank, q_score)
                # 更新元数据最匹配的候选
                if key > metadata_candidate_key:
                    metadata_candidate = candidate
                    metadata_candidate_key = key

        # 如果没有找到最佳指纹匹配候选
        if not best_candidate:
            if metadata_candidate:
                # 有元数据匹配但无指纹匹配，需要人工审核
                return DuplicateDecisionResult(
                    decision=DuplicateDecision.REVIEW,
                    score=0.0,
                    existing_track_id=str(metadata_candidate.get("track_id", "") or ""),
                    reason="name_duration_match_needs_review",
                )
            # 无任何匹配，保留新音频
            return DuplicateDecisionResult(
                decision=DuplicateDecision.KEEP_BOTH,
                score=0.0,
                reason="no_candidate",
            )

        # 获取已有音频的标题，并推断新旧音频的类型（原曲、伴奏等）
        existing_title = best_candidate["title"]
        new_kind = infer_track_kind(new_title)
        existing_kind = infer_track_kind(existing_title)

        # 高相似度区域（大于等于same_min）的决策逻辑
        if best_score >= same_min:
            # 如果新旧音频类型不同且不是未知类型，视为不同版本，都保留
            if new_kind != existing_kind and {new_kind, existing_kind} != {TrackKind.UNKNOWN}:
                return DuplicateDecisionResult(
                    decision=DuplicateDecision.KEEP_BOTH,
                    score=best_score,
                    existing_track_id=best_candidate["track_id"],
                    reason="high_similarity_but_distinct_version",
                )

            # 获取已有音频的质量和格式信息，与新音频比较
            existing_quality = float(best_candidate["quality_score"])
            existing_format = best_candidate.get("storage_format") or best_candidate.get("source_ext")
            existing_rank = _format_rank(existing_format)
            new_rank = _format_rank(new_source_ext)
        
            # 决策规则：新音频质量显著更高则替换
            if new_quality > existing_quality + 0.08:
                return DuplicateDecisionResult(
                    decision=DuplicateDecision.KEEP_NEW,
                    score=best_score,
                    existing_track_id=best_candidate["track_id"],
                    reason="replace_with_higher_quality",
                )
            # 规则：新音频格式更优且质量相近则替换
            if new_rank >= existing_rank + 6 and new_quality >= existing_quality - 0.08:
                return DuplicateDecisionResult(
                    decision=DuplicateDecision.KEEP_NEW,
                    score=best_score,
                    existing_track_id=best_candidate["track_id"],
                    reason="replace_with_better_format",
                )
            # 规则：新音频格式更优（即使质量略低）则替换
            if new_rank > existing_rank and new_quality >= existing_quality - 0.18:
                return DuplicateDecisionResult(
                    decision=DuplicateDecision.KEEP_NEW,
                    score=best_score,
                    existing_track_id=best_candidate["track_id"],
                    reason="replace_with_better_format",
                )
            # 规则：新音频格式显著更优且质量可接受则替换
            if new_rank >= existing_rank + 10 and new_quality >= existing_quality - 0.28:
                return DuplicateDecisionResult(
                    decision=DuplicateDecision.KEEP_NEW,
                    score=best_score,
                    existing_track_id=best_candidate["track_id"],
                    reason="replace_with_better_format",
                )
            # 规则：质量相近且格式更优则替换
            if abs(new_quality - existing_quality) <= 0.08 and new_rank > existing_rank:
                return DuplicateDecisionResult(
                    decision=DuplicateDecision.KEEP_NEW,
                    score=best_score,
                    existing_track_id=best_candidate["track_id"],
                    reason="replace_with_better_format",
                )

            # 以上条件都不满足，保留已有音频
            return DuplicateDecisionResult(
                decision=DuplicateDecision.KEEP_EXISTING,
                score=best_score,
                existing_track_id=best_candidate["track_id"],
                reason="near_identical_duplicate",
            )

        # 中等相似度区域（大于等于review_min，小于same_min），需要人工审核
        if best_score >= review_min:
            return DuplicateDecisionResult(
                decision=DuplicateDecision.REVIEW,
                score=best_score,
                existing_track_id=best_candidate["track_id"],
                reason="same_song_similarity_review_band",
            )

        # 如果有元数据匹配但指纹相似度不高，需要人工审核
        if metadata_candidate:
            return DuplicateDecisionResult(
                decision=DuplicateDecision.REVIEW,
                score=best_score,
                existing_track_id=str(metadata_candidate.get("track_id", "") or ""),
                reason="name_duration_match_needs_review",
            )

        # 低相似度区域，根据具体分数判断是伴奏/原曲还是完全不同的歌曲
        if best_score >= instrumental_min:
            # 可能是原曲与伴奏，都保留
            return DuplicateDecisionResult(
                decision=DuplicateDecision.KEEP_BOTH,
                score=best_score,
                existing_track_id=best_candidate["track_id"],
                reason="likely_instrumental_or_original",
            )
        if best_score >= cover_min:
            # 可能是翻唱版本，都保留
            return DuplicateDecisionResult(
                decision=DuplicateDecision.KEEP_BOTH,
                score=best_score,
                existing_track_id=best_candidate["track_id"],
                reason="likely_cover_version",
            )

        # 极低相似度，视为完全不同的歌曲，都保留
        return DuplicateDecisionResult(
            decision=DuplicateDecision.KEEP_BOTH,
            score=best_score,
            existing_track_id=best_candidate["track_id"],
            reason="different_song_low_similarity",
        )
