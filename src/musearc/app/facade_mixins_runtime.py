from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

from musearc.app.action_log import read_action_logs
from musearc.core.ids import new_id
from musearc.infra.media.prober import MediaProbe, repair_metadata_text, seems_mojibake_text
from musearc.services.importer import _derive_title_artist, _is_unknown_text
from musearc.services.library_ops import LibraryOpsService

_RUNTIME_FP_ENGINE = None


def _runtime_worker_fp_engine():
    """获取或初始化运行时的声学指纹引擎单例。

    此函数检查全局变量 _RUNTIME_FP_ENGINE，如果为 None，则导入 AcousticFingerprintEngine 并初始化一个实例，
    然后返回该实例。如果已初始化，则直接返回现有实例。

    参数：无。
    返回值：AcousticFingerprintEngine 的实例。
    """
    global _RUNTIME_FP_ENGINE  # 声明使用全局变量 _RUNTIME_FP_ENGINE
    if _RUNTIME_FP_ENGINE is None:  # 检查引擎是否已初始化
        from musearc.infra.media.fingerprint import (
            AcousticFingerprintEngine,  # 从指定模块导入 AcousticFingerprintEngine 类
        )

        _RUNTIME_FP_ENGINE = AcousticFingerprintEngine()  # 创建一个新的 AcousticFingerprintEngine 实例并赋值给全局变量
    return _RUNTIME_FP_ENGINE  # 返回全局引擎实例


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
        """生成下一个不重复的全量扫描工作名称。

        根据传入的基础名称，结合数据库中已存在的工作名称列表，
        生成一个不重复的新名称。如果基础名称已存在，会自动追加数字后缀。

        Args:
            base_name (str): 用于生成新名称的基础字符串。

        Returns:
            str: 一个在当前工作中不存在的唯一名称。
        """
        # 处理输入的基础名称：将其转为字符串，去除首尾空格，若为空则使用默认名称
        base = str(base_name or "").strip() or "全量歌曲筛选"
        # 获取现有的全量扫描工作列表
        rows = self.list_fullscan_works()
        # 构建一个包含所有已存在且非空的工作名称的集合，用于快速查找
        exists = {str(r.get("name", "")).strip() for r in rows if str(r.get("name", "")).strip()}
        # 如果基础名称在已有名称集合中不存在，可直接返回
        if base not in exists:
            return base
        # 如果基础名称已存在，则从索引2开始循环尝试追加数字后缀
        index = 2
        while True:
            # 生成带数字后缀的候选名称
            candidate = f"{base}{index}"
            # 检查候选名称是否已存在
            if candidate not in exists:
                return candidate
            # 若候选名称已存在，则索引加1，继续尝试
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
        """基于歌曲元数据（标题、艺术家）的相似性，创建扫描工作。
        功能：从曲库中找出元数据高度相似（标题和艺术家归一化后相同）且时长接近（相差≤10秒）的歌曲分组，并创建扫描任务。
        参数：
            base_name (str): 生成的扫描工作的基础名称，默认为“元数据高相似歌曲”。
            progress_callback (Callable | None): 进度回调函数，接收 (当前进度, 总步数, 阶段描述) 参数。
            is_cancelled (Callable | None): 用于检查是否应取消操作的回调函数。
        返回值：
            str: 创建成功的扫描工作的名称。如果操作被取消或没有找到符合条件的歌曲，则返回空字符串。
        """
        import re  # 用于文本清洗的正则表达式模块

        def _name_base(value: str) -> str:
            """将输入字符串标准化，移除括号内容并转换为小写单词列表。"""
            # 使用正则表达式移除字符串中任何括号及其内的内容（支持多种括号格式）
            text = re.sub(r"[\(\[【{（].*?[\)\]】}）]", " ", str(value or ""))
            # 转换为小写，然后按空格分割并重新合并为标准格式的字符串，用于作为分组键
            return " ".join(text.casefold().split())

        # 从曲库获取所有曲目记录（最多200万条，这是一个很大的限制）
        rows = self.list_tracks(limit=2_000_000)
        # 计算总步数，为曲目数量的2倍（对应两个主要处理阶段）
        total = max(1, len(rows) * 2)
        progress = 0  # 当前进度计数器
        # 用于按（标准化后的标题，标准化后的艺术家）键来分组曲目的字典
        groups: dict[tuple[str, str], list[dict]] = {}
        # 第一阶段：遍历所有曲目，按元数据分组
        for row in rows:
            # 检查是否请求了取消操作
            if callable(is_cancelled) and is_cancelled():
                return ""  # 取消则立即返回空字符串
            # 获取标题和艺术家字段，并进行标准化处理
            title_key = _name_base(str(row.get("title", "") or ""))
            artist_key = _name_base(str(row.get("artist", "") or ""))
            # 如果标题或艺术家标准化后为空，则跳过此条记录
            if not title_key or not artist_key:
                progress += 1
                if callable(progress_callback):
                    progress_callback(progress, total, "扫描元数据")
                continue
            # 使用元组(title, artist)作为分组键
            key = (title_key, artist_key)
            # 将当前曲目添加到对应分组中
            groups.setdefault(key, []).append(row)
            progress += 1
            if callable(progress_callback):
                progress_callback(progress, total, "扫描元数据")

        picked: set[str] = set()  # 用于存储符合相似条件的曲目ID集合
        # 第二阶段：遍历每个分组，筛选出“高相似”分组
        for items in groups.values():
            if callable(is_cancelled) and is_cancelled():
                return ""
            # 如果分组内曲目少于2条，则不可能形成“相似”对，跳过
            if len(items) < 2:
                progress += 1
                if callable(progress_callback):
                    progress_callback(progress, total, "筛选高相似分组")
                continue
            # 收集该分组内所有曲目的时长
            durations = []
            for row in items:
                try:
                    durations.append(float(row.get("duration_sec", 0) or 0))
                except Exception:
                    durations.append(0.0)  # 如果时长字段无效，则默认为0
            # 判断分组内的曲目时长是否足够接近（最大值与最小值之差不超过10秒）
            if max(durations, default=0.0) - min(durations, default=0.0) <= 10.0:
                # 时长接近，将该分组内所有曲目的ID加入待处理集合
                for row in items:
                    tid = str(row.get("track_id", "") or "")
                    if tid:
                        picked.add(tid)
            progress += 1
            if callable(progress_callback):
                progress_callback(progress, total, "筛选高相似分组")

        # 第二阶段结束，更新进度到完成状态
        if callable(progress_callback):
            progress_callback(total, total, "创建工作")

        # 使用收集到的ID创建扫描工作，并返回工作名称
        return self._create_fullscan_work_from_track_ids(self._next_fullscan_work_name(base_name), sorted(picked))

    def _resolve_fullscan_fp_process_count(self) -> int:
        """确定执行全扫描指纹对比所需的工作进程数量。

        根据运行时配置和系统资源计算一个合理的进程数。
        优先使用用户指定的进程数（若有效），否则根据CPU核心数自动生成，
        并受通用工作进程上限的约束。

        Args:
            self: 类的实例，用于访问上下文配置。

        Returns:
            int: 最终确定的工作进程数量（至少为1）。
        """
        cfg = getattr(self.ctx.runtime_config, "ui", None) # 从运行时配置中安全地获取UI子配置
        requested = int(getattr(cfg, "fullscan_fp_compare_processes", 0) or 0) # 获取用户请求的进程数，若未设置或为空则为0
        general_limit = int(getattr(cfg, "general_worker_limit", 0) or 0) # 获取通用的工作进程上限
        cpu = max(1, int(os.cpu_count() or 4)) # 获取CPU核心数，若获取失败则默认为4，并确保至少为1

        # 根据是否指定了进程数，使用不同的策略计算基础工作进程数
        if requested <= 0:
            # 未指定或指定无效时，基于CPU核心数计算：最少1个，最多8个，且不超过(cpu-1)
            workers = max(1, min(8, cpu - 1))
        else:
            # 指定了进程数时，取其值、CPU核心数和32中的最小值，并确保至少为1
            workers = max(1, min(32, requested, cpu))

        # 如果配置了通用工作进程上限，确保最终进程数不超过该上限（同时至少为1）
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
        """基于音频指纹相似度创建全扫描工作，选择在指定相似度范围内的曲目。

        该方法通过计算曲目的音频指纹相似度，找出相似度在[min_score, max_score]范围内的曲目对，
        并将这些曲目组合成一个新的全扫描工作。

        参数:
            min_score (float): 最小相似度分数，范围0.0到1.0。
            max_score (float): 最大相似度分数，范围0.0到1.0。
            base_name (str): 工作名称的基础字符串，默认为"fingerprint_similar_tracks"。
            progress_callback (Callable[[int, int, str], None] | None): 进度回调函数，
                接收当前进度、总进度和状态消息。如果为None则不报告进度。
            is_cancelled (Callable[[], bool] | None): 取消检查回调函数，
                如果返回True则立即停止处理并返回空字符串。如果为None则不会取消。

        返回值:
            str: 返回创建的工作名称。如果操作被取消，返回空字符串。
        """
        # 将相似度分数限制在0.0到1.0的有效范围内
        lower = max(0.0, min(1.0, float(min_score)))
        upper = max(0.0, min(1.0, float(max_score)))
        # 确保下限不大于上限，如果相反则交换
        if upper < lower:
            lower, upper = upper, lower
        # 如果上限为0，则没有有效的相似度范围，返回空工作
        if upper <= 0.0:
            return self._create_fullscan_work_from_track_ids(self._next_fullscan_work_name(base_name), [])

        # 导入音频指纹处理引擎
        from musearc.infra.media.fingerprint import AcousticFingerprintEngine

        # 初始化指纹引擎并检查chromaprint是否可用
        fp = AcousticFingerprintEngine()
        if not fp.chromaprint_available:
            # 如果不可用，报告进度并抛出异常
            if callable(progress_callback):
                progress_callback(100, 100, "chromaprint unavailable")
            raise RuntimeError("Chromaprint unavailable, configure libchromaprint first.")

        # 开始加载曲目数据
        if callable(progress_callback):
            progress_callback(1, 100, "loading tracks")
        # 获取所有曲目，最多200万条
        raw_rows = self.list_tracks(limit=2_000_000)
        if callable(progress_callback):
            progress_callback(5, 100, "filtering comparable tracks")

        # 如果相似度范围包含几乎全部值（0.0到0.999），则直接返回所有曲目ID
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

        # 初始化存储处理后的曲目数据和指纹哈希缓存
        rows: list[dict] = []
        fp_hash_cache: dict[str, int | None] = {}

        def _payload_tokens(payload: str) -> tuple[str, ...]:
            """将指纹载荷转换为token元组，用于快速相似度预筛选。

            通过提取指纹载荷的子字符串来生成token，用于快速比较曲目相似度。
            短载荷直接作为单个token，长载荷则提取多个子字符串。

            参数:
                payload (str): 指纹载荷字符串。

            返回值:
                tuple[str, ...]: 提取的token元组。
            """
            text = str(payload or "")
            if not text:
                return ()
            n = len(text)
            # 短字符串（<=12字符）直接作为单个token
            if n <= 12:
                return (text,)
            # 设置窗口大小和步长
            win = 10
            step = max(6, n // 36)
            # 提取首尾两个token和中间部分token
            toks: list[str] = [text[:win], text[-win:]]
            for pos in range(0, max(0, n - win + 1), step):
                toks.append(text[pos : pos + win])
                # 限制最多48个token，防止过多token影响性能
                if len(toks) >= 48:
                    break
            # 使用dict.fromkeys去重并保持顺序，过滤空字符串
            return tuple(dict.fromkeys(t for t in toks if t))

        # 处理每一行曲目数据
        for row in raw_rows:
            # 检查是否取消操作
            if callable(is_cancelled) and is_cancelled():
                return ""
            # 获取指纹载荷和曲目ID
            payload = str(row.get("fingerprint_payload", "") or "").strip()
            track_id = str(row.get("track_id", "") or "").strip()
            # 跳过无效数据
            if not payload or not track_id:
                continue
            # 安全获取时长，转换为整数秒
            try:
                sec = int(round(float(row.get("duration_sec", 0.0) or 0.0)))
            except Exception:
                sec = 0
            # 从缓存中获取或计算指纹哈希值
            hash32 = fp_hash_cache.get(payload)
            if payload not in fp_hash_cache:
                hash32 = fp.fingerprint_hash32(payload)
                fp_hash_cache[payload] = hash32
            # 将处理后的数据添加到rows列表
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
        # 再次检查是否取消
        if callable(is_cancelled) and is_cancelled():
            return ""

        # 建立时长索引和token索引，用于快速查找相似曲目
        by_duration: dict[int, list[dict]] = {}
        row_by_id: dict[str, dict] = {}
        token_index: dict[str, list[str]] = {}
        # 计算进度报告的间隔
        bucket_total = max(1, len(rows))
        bucket_step = max(1, bucket_total // 200)
        for idx, row in enumerate(rows, 1):
            # 检查是否取消
            if callable(is_cancelled) and is_cancelled():
                return ""
            sec = int(row.get("sec", 0) or 0)
            # 按时长分组
            by_duration.setdefault(sec, []).append(row)
            tid = str(row.get("track_id", "") or "")
            if tid:
                # 建立曲目ID到数据的映射
                row_by_id[tid] = row
                # 为每个token建立索引
                for tok in row.get("tokens", ()) or ():
                    token_index.setdefault(str(tok), []).append(tid)
            # 定期报告进度
            if callable(progress_callback) and (idx == bucket_total or idx % bucket_step == 0):
                pct = 5 + int(15 * idx / bucket_total)
                progress_callback(pct, 100, "build duration buckets")

        # 初始化结果集合和进度变量
        picked: set[str] = set()
        total_rows = max(1, len(rows))
        # 根据最低相似度设置比较候选数上限
        if lower >= 0.80:
            max_compare_candidates = 48
        elif lower >= 0.60:
            max_compare_candidates = 64
        else:
            max_compare_candidates = 96

        # 决定是否启用多进程并行处理
        process_workers = self._resolve_fullscan_fp_process_count()
        enable_process_parallel = process_workers > 1 and len(rows) >= max(256, process_workers * 48)

        # 初始化缓存和并行作业列表
        pair_score_cache: dict[tuple[str, str], float] = {}
        parallel_jobs: list[tuple[str, str, int, list[tuple[str, str, int]]]] = []
        processed = 0
        prepared = 0
        last_emit_ts = 0.0
        progress_mark = 20

        # 主循环：按时长分组处理，寻找相似曲目
        for sec, bucket in by_duration.items():
            # 检查是否取消
            if callable(is_cancelled) and is_cancelled():
                return ""
            # 收集时长相近（±10秒）的候选曲目
            candidates: list[dict] = []
            for d in range(sec - 10, sec + 11):
                candidates.extend(by_duration.get(d, []))

            # 处理每个时长桶中的曲目
            for row in bucket:
                # 检查是否取消
                if callable(is_cancelled) and is_cancelled():
                    return ""

                # 获取当前曲目的标识和载荷
                tid_a = str(row.get("track_id", "") or "")
                payload_a = str(row.get("payload", "") or "")
                len_a = int(row.get("plen", 0) or 0)
                hash_a = row.get("hash32")
                tokens_a = tuple(row.get("tokens", ()) or ())
                prepared += 1

                # 跳过无效数据
                if not tid_a or not payload_a:
                    processed += 1
                    continue

                # 设置允许的载荷长度差异
                allowed_len_delta = max(16, len_a // 3)
                # 通过token索引快速筛选候选曲目
                token_counter: dict[str, int] = {}
                for tok in tokens_a:
                    # 检查是否取消
                    if callable(is_cancelled) and is_cancelled():
                        return ""
                    tids = token_index.get(str(tok), [])
                    for cand_tid in tids:
                        # 检查是否取消
                        if callable(is_cancelled) and is_cancelled():
                            return ""
                        # 跳过自身
                        if cand_tid == tid_a:
                            continue
                        cand_row = row_by_id.get(cand_tid)
                        if not cand_row:
                            continue
                        cand_sec = int(cand_row.get("sec", 0) or 0)
                        # 检查时长是否在范围内
                        if cand_sec < sec - 10 or cand_sec > sec + 10:
                            continue
                        # 统计token匹配次数
                        token_counter[cand_tid] = int(token_counter.get(cand_tid, 0) or 0) + 1

                # 构建候选曲目池
                candidate_pool: list[dict] = []
                if token_counter:
                    # 如果有token匹配，按匹配次数和长度差异排序
                    ordered_ids = sorted(
                        token_counter.keys(),
                        key=lambda tid: (
                            -int(token_counter.get(tid, 0) or 0),  # 匹配次数多的优先
                            abs(int(row_by_id.get(tid, {}).get("plen", 0) or 0) - len_a),  # 长度差异小的优先
                        ),
                    )
                    # 限制候选数量
                    for cand_tid in ordered_ids[:max_compare_candidates]:
                        cand_row = row_by_id.get(cand_tid)
                        if cand_row:
                            candidate_pool.append(cand_row)
                else:
                    # 没有token匹配时，按时长差异筛选候选
                    duration_pool: list[dict] = []
                    for cand in candidates:
                        cand_tid = str(cand.get("track_id", "") or "")
                        if not cand_tid or cand_tid == tid_a:
                            continue
                        len_b = int(cand.get("plen", 0) or 0)
                        # 检查载荷长度差异
                        if abs(len_b - len_a) > allowed_len_delta:
                            continue
                        duration_pool.append(cand)
                    # 按长度差异排序
                    duration_pool.sort(key=lambda c: abs(int(c.get("plen", 0) or 0) - len_a))
                    candidate_pool = duration_pool[:max_compare_candidates]

                # 如果有候选曲目且当前曲目有哈希值，通过汉明距离进一步筛选
                if candidate_pool and isinstance(hash_a, int):
                    ranked: list[tuple[int, dict]] = []
                    no_hash: list[dict] = []
                    for cand in candidate_pool:
                        hash_b = cand.get("hash32")
                        # 没有哈希值的单独处理
                        if not isinstance(hash_b, int):
                            no_hash.append(cand)
                            continue
                        # 计算汉明距离
                        hd = (int(hash_a) ^ int(hash_b)).bit_count()
                        ranked.append((hd, cand))
                    if ranked:
                        # 按汉明距离排序
                        ranked.sort(key=lambda item: item[0])
                        # 根据相似度阈值设置筛选参数
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
                        # 选择汉明距离在限制内的候选
                        selected = [cand for hd, cand in ranked if hd <= hd_limit][:keep_n]
                        # 如果选择的数量不足最小值，补充一些候选
                        if len(selected) < min(min_keep, len(ranked)):
                            selected = [cand for _, cand in ranked[:max(min_keep, min(keep_n, len(ranked)))]]
                        # 将部分无哈希值的候选加入
                        if no_hash:
                            selected_ids = {str(c.get("track_id", "") or "") for c in selected}
                            for cand in no_hash[:4]:
                                tid = str(cand.get("track_id", "") or "")
                                if tid and tid not in selected_ids:
                                    selected.append(cand)
                                    selected_ids.add(tid)
                        candidate_pool = selected

                # 如果没有候选曲目，更新进度并继续
                if not candidate_pool:
                    processed += 1
                    if callable(progress_callback):
                        row_ratio = float(processed) / float(total_rows)
                        pct = 20 + int(75.0 * max(0.0, min(1.0, row_ratio)))
                        progress_mark = max(progress_mark, pct)
                        progress_callback(max(20, min(95, progress_mark)), 100, "compare fingerprints")
                    continue

                # 如果启用多进程，将比较任务加入并行作业列表
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
                    # 定期报告准备进度
                    if callable(progress_callback) and (prepared == total_rows or prepared % 32 == 0):
                        prep_ratio = float(prepared) / float(total_rows)
                        prep_pct = 20 + int(20.0 * max(0.0, min(1.0, prep_ratio)))
                        progress_callback(max(20, min(45, prep_pct)), 100, "prepare parallel jobs")
                    continue

                # 单线程模式下直接进行相似度比较
                matched_tid: str | None = None
                cand_total = max(1, len(candidate_pool))
                for cand_idx, cand in enumerate(candidate_pool, 1):
                    # 检查是否取消
                    if callable(is_cancelled) and is_cancelled():
                        return ""
                    payload_b = str(cand.get("payload", "") or "")
                    len_b = int(cand.get("plen", 0) or 0)
                    tid_b = str(cand.get("track_id", "") or "")
                    if not payload_b or not tid_b:
                        continue
                    # 再次检查载荷长度差异
                    if abs(len_b - len_a) > allowed_len_delta:
                        continue
                    # 生成缓存键（确保一致性）
                    key = (tid_a, tid_b) if tid_a < tid_b else (tid_b, tid_a)
                    score = pair_score_cache.get(key)
                    # 如果缓存中没有，则计算相似度
                    if score is None:
                        score = float(fp.similarity(payload_a, payload_b))
                        pair_score_cache[key] = score
                    # 检查是否在相似度范围内
                    if lower <= score <= upper:
                        matched_tid = tid_b
                        break
                    # 定期让出CPU时间，避免阻塞
                    if cand_idx % 8 == 0:
                        time.sleep(0)
                    # 定期报告进度
                    now = time.monotonic()
                    if callable(progress_callback) and (now - last_emit_ts) >= 0.25:
                        row_ratio = (float(processed) + (float(cand_idx) / float(cand_total))) / float(total_rows)
                        pct = 20 + int(75.0 * max(0.0, min(1.0, row_ratio)))
                        progress_mark = max(progress_mark, pct)
                        progress_callback(max(20, min(95, progress_mark)), 100, "compare fingerprints")
                        last_emit_ts = now

                # 如果找到匹配曲目，将两者都加入结果集
                if matched_tid:
                    picked.add(tid_a)
                    picked.add(matched_tid)
                processed += 1
                # 更新进度
                if callable(progress_callback):
                    row_ratio = float(processed) / float(total_rows)
                    pct = 20 + int(75.0 * max(0.0, min(1.0, row_ratio)))
                    progress_mark = max(progress_mark, pct)
                    progress_callback(max(20, min(95, progress_mark)), 100, "compare fingerprints")

        # 处理多进程并行比较任务
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
                    """向进程池提交更多并行作业。"""
                    nonlocal submitted
                    # 控制同时进行的任务数量
                    while submitted < total_jobs and len(inflight) < max_inflight:
                        tid_a, payload_a, len_a, candidates = parallel_jobs[submitted]
                        # 提交任务到进程池
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

                # 开始提交作业
                _submit_more()
                # 等待并处理完成的任务
                while inflight:
                    # 检查是否取消
                    if callable(is_cancelled) and is_cancelled():
                        pool.shutdown(wait=False, cancel_futures=True)
                        return ""
                    # 等待任务完成，设置超时以支持取消检查
                    done, _ = wait(tuple(inflight.keys()), timeout=0.25, return_when=FIRST_COMPLETED)
                    if not done:
                        # 没有任务完成时更新进度
                        if callable(progress_callback):
                            ratio = float(processed) / float(total_rows)
                            pct = 40 + int(55.0 * max(0.0, min(1.0, ratio)))
                            progress_callback(max(40, min(95, pct)), 100, f"parallel compare workers={process_workers}")
                        continue
                    # 处理完成的任务
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
                        # 更新进度
                        if callable(progress_callback):
                            ratio = float(processed) / float(total_rows)
                            pct = 40 + int(55.0 * max(0.0, min(1.0, ratio)))
                            progress_callback(max(40, min(95, pct)), 100, f"parallel compare {completed_jobs}/{total_jobs}")
                    # 提交更多作业
                    _submit_more()
            except Exception:
                # 如果多进程失败，回退到单线程处理
                for tid_a, payload_a, len_a, candidates in parallel_jobs:
                    if callable(is_cancelled) and is_cancelled():
                        return ""
                    matched_tid = _runtime_compare_row_in_process(payload_a, len_a, lower, upper, candidates)
                    if tid_a and matched_tid:
                        picked.add(tid_a)
                        picked.add(str(matched_tid))
                    processed += 1
                    # 更新进度
                    if callable(progress_callback):
                        ratio = float(processed) / float(total_rows)
                        pct = 40 + int(55.0 * max(0.0, min(1.0, ratio)))
                        progress_callback(max(40, min(95, pct)), 100, "fallback single-process compare")
            finally:
                # 清理进程池
                if pool is not None:
                    try:
                        pool.shutdown(wait=False, cancel_futures=False)
                    except Exception:
                        pass

        # 报告最终进度并返回结果
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
        """
        基于ID3标签和歌词信息，批量更新指定工作集中音频文件的元数据。

        本方法会扫描给定工作（work）下的所有音频文件，通过ID3标签探测和歌词信息查询，
        修复或补全数据库中的标题（title）、艺术家（artist）、专辑（album）字段。
        处理逻辑包括：修复乱码文本、从文件名推导信息、使用歌词信息补全未知字段等。

        参数:
            work_id (str): 要处理的工作ID。

        返回:
            dict: 包含处理结果的字典，包含以下键：
                - "total": 处理的文件总数。
                - "updated": 成功更新的文件数量。
                - "skipped": 跳过的文件数量（包括文件不存在、信息无变化等）。
                - "rows": 详细结果列表，每个元素为一个字典，包含track_id、状态、原因等信息。
        """
        work = str(work_id or "").strip()
        if not work:
            return {"total": 0, "updated": 0, "skipped": 0, "rows": []}

        # 获取该工作项下的所有待扫描文件，设置一个较大的限制数量
        rows = self.get_fullscan_work_items(work, limit=2_000_000)
        # 创建媒体探测器实例，用于读取音频文件元数据
        probe = MediaProbe()
        out_rows: list[dict] = []
        updated = 0
        skipped = 0
        # 使用数据库会话上下文
        with self.ctx.db.session() as conn:
            # 在会话内导入并实例化数据仓库
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            # 遍历获取到的每一行记录（即每个待处理文件）
            for row in rows:
                track_id = str(row.get("track_id", "") or "")
                storage_rel = str(row.get("storage_relpath", "") or "")
                # 根据存储相对路径构建完整的文件路径对象
                target = Path(self.ctx.layout.root) / storage_rel if storage_rel else None
                # 检查 track_id 是否存在、目标路径是否有效且文件是否存在
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

                # 用于存储需要更新的字段及其新值
                patch: dict[str, object] = {}
                # 用于记录本次更新的原因，便于日志和结果输出
                reason_parts: list[str] = []
                try:
                    # 使用探测器读取音频文件的元数据信息
                    info = probe.probe(target)
                except Exception as exc:
                    # 如果探测失败（例如文件格式损坏、不支持），则跳过该文件
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

                # 从数据库当前记录中获取原始的、可能未处理的元数据文本
                current_title_raw = str(row.get("title", "") or "")
                current_artist_raw = str(row.get("artist", "") or "")
                current_album_raw = str(row.get("album", "") or "")
                # 对当前数据库中的文本进行基础的乱码修复
                current_title = repair_metadata_text(current_title_raw)
                current_artist = repair_metadata_text(current_artist_raw)
                current_album = repair_metadata_text(current_album_raw)
                # 尝试从文件名和标签中推导出更合理的标题和艺术家
                derived_title, derived_artist = _derive_title_artist(target, info.title, info.artist, info.tags)

                # 从ID3标签获取并初步修复文本
                tag_title = repair_metadata_text(info.title or "")
                tag_artist = repair_metadata_text(info.artist or "")
                tag_album = repair_metadata_text(info.album or "")
                # 检查标签文本是否看起来是乱码，如果是，则视为空值（后续将用其他来源补全）
                if seems_mojibake_text(tag_title):
                    tag_title = ""
                if seems_mojibake_text(tag_artist):
                    tag_artist = ""
                if seems_mojibake_text(tag_album):
                    tag_album = ""

                # 确定最终要采用的元数据：优先使用非乱码的标签值，否则使用当前数据库的值
                next_title = tag_title or current_title
                next_artist = tag_artist or current_artist
                next_album = tag_album or current_album
                # 再次检查确定后的文本是否是乱码，如果是则置空
                if seems_mojibake_text(next_title):
                    next_title = ""
                if seems_mojibake_text(next_artist):
                    next_artist = ""
                if seems_mojibake_text(next_album):
                    next_album = ""
                # 如果确定后的标题或艺术家仍然是未知/默认值，则使用从文件名推导出的值
                if _is_unknown_text(next_title, kind="title"):
                    next_title = derived_title
                if _is_unknown_text(next_artist, kind="artist"):
                    next_artist = derived_artist

                # 查询该曲目关联的主要歌词信息
                lyrics = repo.primary_lyrics_for_track(track_id) or {}
                # 如果标题仍然是未知值，尝试从歌词信息中补全
                if _is_unknown_text(next_title, kind="title"):
                    lyrics_title = repair_metadata_text(lyrics.get("lyrics_title", "") or "")
                    if lyrics_title:
                        next_title = lyrics_title
                        reason_parts.append("歌词标题补全")
                # 如果艺术家仍然是未知值，尝试从歌词信息中补全
                if _is_unknown_text(next_artist, kind="artist"):
                    lyrics_artist = repair_metadata_text(lyrics.get("lyrics_artist", "") or "")
                    if lyrics_artist:
                        next_artist = lyrics_artist
                        reason_parts.append("歌词艺术家补全")
                # 如果专辑仍然是未知值，尝试从歌词信息中补全
                if _is_unknown_text(next_album, kind="album"):
                    lyrics_album = repair_metadata_text(lyrics.get("lyrics_album", "") or "")
                    if lyrics_album:
                        next_album = lyrics_album
                        reason_parts.append("歌词专辑补全")

                # 检查是否发生了乱码修复（当前修复后的值与原始值不同，且不是来自标签覆盖）
                if current_title and current_title != current_title_raw and not tag_title:
                    reason_parts.append("标题乱码修复")
                if current_artist and current_artist != current_artist_raw and not tag_artist:
                    reason_parts.append("艺术家乱码修复")
                if current_album and current_album != current_album_raw and not tag_album:
                    reason_parts.append("专辑乱码修复")

                # 比较确定的最终值与数据库中的原始值，如果不同且非空，则将该字段加入待更新补丁
                if next_title and next_title != current_title_raw:
                    patch["title"] = next_title
                if next_artist and next_artist != current_artist_raw:
                    patch["artist"] = next_artist
                if next_album and next_album != current_album_raw:
                    patch["album"] = next_album

                # 如果存在需要更新的字段
                if patch:
                    # 调用仓库方法更新数据库中的记录
                    repo.update_tracks_fields([track_id], patch)
                    updated += 1
                    # 如果本次更新中使用了ID3标签的值，则在原因列表最前面添加说明
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
                    # 如果没有需要更新的字段，则跳过
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

        # 清除可能存在的缓存操作标记
        self._redo_actions.clear()
        # 记录本次操作的摘要日志
        self._log(f"update_metadata_from_id3_and_lyrics work={work} updated={updated} skipped={skipped}")
        # 返回包含总览和详细结果的字典
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
        """
        恢复合并操作的歌词快照数据。

        从payload中提取指定快照键对应的歌词快照数据，并恢复歌词行、存储文件、曲目关联和审核状态。

        参数:
            repo: 仓库对象，用于数据库连接。
            payload: 包含快照数据的字典。
            snapshot_key: 快照在payload中的键名。

        返回值:
            None
        """
        # 从payload中提取指定键的快照，如果payload不是字典则返回空字典
        snap = payload.get(snapshot_key, {}) if isinstance(payload, dict) else {}
        # 如果快照不是字典则直接返回
        if not isinstance(snap, dict):
            return

        # 辅助函数：将值转换为JSON文本字符串
        def _json_text(value) -> str:
            # 如果已经是字符串则直接返回
            if isinstance(value, str):
                return value
            # 如果是字典则转换为JSON字符串，保留非ASCII字符
            if isinstance(value, dict):
                return json.dumps(value, ensure_ascii=False)
            # 其他情况返回空JSON对象字符串
            return "{}"

        # 辅助函数：恢复单条歌词行数据
        def _restore_lyrics_row(row_payload: dict) -> None:
            # 如果输入不是字典则直接返回
            if not isinstance(row_payload, dict):
                return
            # 提取歌词ID，确保是字符串且非空
            lyrics_id = str(row_payload.get("lyrics_id", "") or "")
            if not lyrics_id:
                return
            # 执行SQL更新语句，恢复歌词行的所有字段
            repo.conn.execute(
                """
                UPDATE lyrics
                SET source_relpath = ?, storage_relpath = ?, text_hash = ?, raw_encoding = ?,
                    lyrics_title = ?, lyrics_artist = ?, lyrics_album = ?, lyrics_author = ?,
                    line_count = ?, imported_at = ?, deleted_at = ?, ext_json = ?
                WHERE lyrics_id = ?
                """,
                (
                    # 提取各字段值，确保类型转换并处理空值情况
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

        # 恢复主歌词行和次歌词行
        _restore_lyrics_row(snap.get("primary_row", {}))
        _restore_lyrics_row(snap.get("secondary_row", {}))

        # 恢复歌词存储文件
        # 提取主歌词和次歌词的存储相对路径
        primary_rel = str(payload.get("primary_storage_relpath", "") or "")
        secondary_rel = str(payload.get("secondary_storage_relpath", "") or "")
        # 提取主歌词和次歌词的文本内容
        primary_text = str(snap.get("primary_text", "") or "")
        secondary_text = str(snap.get("secondary_text", "") or "")

        # 如果存在主歌词路径，则创建目录并写入文件
        if primary_rel:
            primary_path = self.ctx.layout.root / primary_rel
            primary_path.parent.mkdir(parents=True, exist_ok=True)
            primary_path.write_text(primary_text, encoding="utf-8")

        # 如果存在次歌词路径，则创建目录并写入文件
        if secondary_rel:
            secondary_path = self.ctx.layout.root / secondary_rel
            secondary_path.parent.mkdir(parents=True, exist_ok=True)
            secondary_path.write_text(secondary_text, encoding="utf-8")

        # 恢复曲目关联数据
        track_links = snap.get("track_links", [])
        if isinstance(track_links, list):
            # 提取所有有效曲目ID并排序去重
            track_ids = sorted({str(r.get("track_id", "") or "") for r in track_links if isinstance(r, dict) and str(r.get("track_id", "") or "").strip()})
            if track_ids:
                # 生成SQL占位符并删除原有关联记录
                placeholders = ",".join("?" for _ in track_ids)
                repo.conn.execute(f"DELETE FROM track_lyrics WHERE track_id IN ({placeholders})", tuple(track_ids))
            # 循环处理每条关联记录
            for row in track_links:
                if not isinstance(row, dict):
                    continue
                track_id = str(row.get("track_id", "") or "")
                lyrics_id = str(row.get("lyrics_id", "") or "")
                if not track_id or not lyrics_id:
                    continue
                # 执行插入或更新语句，恢复关联记录
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

        # 恢复审核状态
        review_status = snap.get("review_status", {})
        if isinstance(review_status, dict):
            # 循环处理每个审核项的状态
            for review_id, info in review_status.items():
                rid = str(review_id or "")
                if not rid:
                    continue
                if not isinstance(info, dict):
                    continue
                # 更新审核队列中对应记录的状态和解决时间
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
