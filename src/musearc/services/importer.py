from __future__ import annotations

"""\u5bfc\u5165\u670d\u52a1\u3002

\u6838\u5fc3\u6d41\u7a0b\uff1a
- \u626b\u63cf\u5bfc\u5165\u6e90 -> \u97f3\u9891/\u6b4c\u8bcd\u63a2\u6d4b -> \u53bb\u91cd\u5224\u5b9a -> \u5165\u5e93\u6216\u8fdb\u5165\u5ba1\u67e5\u3002
- \u652f\u6301\u65ad\u70b9\u6062\u590d\u3001\u6682\u505c\u53d6\u6d88\u3001\u72b6\u6001\u6e05\u5355\u4e0e\u8def\u5f84\u7ea7\u5feb\u901f\u8df3\u8fc7\u7d22\u5f15\u3002
"""

import json
import hashlib
import html
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from musearc.config.models import RuntimeConfig
from musearc.core.enums import FileHealth
from musearc.core.hashing import sha256_file
from musearc.core.ids import new_id
from musearc.core.models import Fingerprint, ImportProgress, ImportReport, ProbeInfo, ReviewItem, TrackInsert
from musearc.core.paths import ensure_parent, shard_relpath
from musearc.core.text_normalize import normalize_text
from musearc.infra.db.repositories import LibraryRepository
from musearc.infra.llm.client import LmStudioMatcher
from musearc.infra.media.audio_io import decode_audio
from musearc.infra.media.commands import MediaCommandError
from musearc.infra.media.fingerprint import AcousticFingerprintEngine
from musearc.infra.media.prober import MediaProbe, repair_metadata_text, seems_mojibake_text
from musearc.infra.media.transcoder import MediaTranscoder
from musearc.services.dedupe import DuplicateEvaluator, infer_track_kind
from musearc.services.import_runtime import ImportControl, ResumeState, delete_resume_state
from musearc.services.lyrics_match import LyricsMatcher


@dataclass(slots=True)
class ImportDependencies:
    probe: MediaProbe
    transcoder: MediaTranscoder
    fingerprint: AcousticFingerprintEngine


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_tag_key(key: str) -> str:
    return str(key or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")


def _pick_probe_tag(tags: dict[str, str], *keys: str) -> str:
    """根据指定的优先级键名列表，从标签字典中查找并返回第一个匹配的非空标签值。

    该函数首先尝试根据传入的键名列表（keys）在标签字典（tags）中进行精确查找。
    如果精确查找失败，则会对键名进行标准化处理（例如统一大小写），然后在标签字典中
    进行标准化后的模糊匹配，返回第一个匹配到的非空值。

    Args:
        tags (dict[str, str]): 一个键和值均为字符串的标签字典。
        *keys (str): 一个可变参数，表示按优先级排序的待查找键名列表。

    Returns:
        str: 返回找到的第一个非空标签值（字符串）。如果未找到或输入无效，则返回空字符串。
    """
    # 检查传入的tags参数是否为字典类型，如果不是则直接返回空字符串
    if not isinstance(tags, dict):
        return ""
    # 第一轮查找：按传入的键名顺序，在字典中进行精确查找
    for key in keys:
        # 安全获取字典的值：如果key不存在或值为None，则视为空字符串，并去除首尾空格
        value = str(tags.get(key, "") or "").strip()
        if value:  # 如果获取到非空值，则立即返回
            return value
    # 准备进行第二轮标准化查找：将用户提供的keys进行标准化处理，并去重存入一个集合
    # 标准化可以理解为例如忽略大小写或去除特定字符，具体行为由 _normalize_tag_key 函数定义
    wanted = {_normalize_tag_key(key) for key in keys if str(key).strip()}  # 排除掉空白键名
    # 第二轮查找：遍历tags字典的所有键值对
    for key, value in tags.items():
        # 将当前遍历的键（key）进行标准化处理，检查是否在期望的键名集合（wanted）中
        if _normalize_tag_key(str(key)) in wanted:
            # 如果键匹配，则获取其值（同样进行安全转换和去空格处理）
            text = str(value or "").strip()
            if text:  # 如果获取到非空值，则立即返回
                return text
    # 如果两轮查找都未找到任何非空匹配值，则返回空字符串
    return ""


def _is_unknown_text(value: str, *, kind: str) -> bool:
    """
    功能：检查给定文本是否为未知文本，根据类型参数使用不同的判断标准。
    参数：
        value: 字符串，要检查的文本；如果为None或空，将转换为空字符串。
        kind: 字符串，指定文本类型，如"title"（标题）、"artist"（艺术家）、"album"（专辑）。
    返回值：
        如果文本符合对应类型的未知文本条件，则返回True；否则返回False。
    """
    # 将value转换为字符串，处理None值，去除首尾空白，并转换为小写形式以统一比较
    text = str(value or "").strip().casefold()
    # 如果kind为"title"，则检查text是否在标题类型的未知文本集合中
    if kind == "title":
        return text in {"", "unknown", "unknown title"}
    # 如果kind为"artist"，则检查text是否在艺术家类型的未知文本集合中，包括"various artists"
    if kind == "artist":
        return text in {"", "unknown", "unknown artist", "various artists"}
    # 如果kind为"album"，则检查text是否在专辑类型的未知文本集合中
    if kind == "album":
        return text in {"", "unknown", "unknown album"}
    # 默认情况下，如果kind不匹配任何特定类型，则仅当text为空字符串时返回True
    return text == ""


def _parse_title_artist_from_stem(stem: str) -> tuple[str, str]:
    """
    从文件名主干（不含扩展名）中解析出标题和艺术家信息。

    该函数尝试在文件名中查找常见的分隔符（如“ - ”），
    将文件名拆分为两部分，并依据启发式规则判断哪一部分是标题，
    哪一部分是艺术家名。如果无法识别，则提供默认值。

    参数:
        stem (str): 文件名主干，例如“Artist - Title”。

    返回:
        tuple[str, str]: 一个元组，第一个元素是标题，第二个元素是艺术家。
    """
    def _artist_likelihood(text: str) -> int:
        """
        评估给定的文本字符串作为艺术家名的可能性（得分）。
        """
        value = str(text or "").strip()
        if not value:
            return -2  # 空文本的可能性极低
        low = value.casefold()  # 转换为小写，便于不区分大小写的匹配
        score = 0
        # 包含常见的“featuring”或合作标识符，增加艺术家名的得分
        if any(token in low for token in ("feat", "ft.", " ft ", " x ", "&", " with ")):
            score += 2
        # 包含特定标点符号，可能表明是多个艺术家，增加得分
        if any(token in value for token in ("、", "丨", "/", ";", ",")):
            score += 1
        # 文本较长，可能是艺术家组合名
        if len(value) >= 14:
            score += 1
        # 以常见的DJ/MC前缀开头，增加得分
        if low.startswith("dj ") or low.startswith("mc "):
            score += 1
        # 包含可能是“原声带”、“主题曲”等与作品直接相关的词，降低艺术家名得分
        if any(token in low for token in ("ost", "theme", "op", "ed", "instrumental")):
            score -= 1
        return score

    name = str(stem or "").strip()
    if not name:
        return "Unknown Title", "Unknown Artist"  # 输入为空，返回未知值

    # 尝试用多种常见的分隔符拆分文件名
    for sep in (" - ", " — ", " – ", " _ ", "-", "—", "–"):
        if sep not in name:
            continue  # 当前分隔符不在文件名中，尝试下一个
        left, right = name.split(sep, 1)  # 使用当前分隔符拆分，最多拆分一次
        left = left.strip()
        right = right.strip()
        if not left or not right:
            continue  # 拆分后任一部分为空，尝试下一个分隔符

        # 文件名约定默认按 artist - title；当右侧更像“艺术家名”时自动反转。
        left_artist_score = _artist_likelihood(left)
        right_artist_score = _artist_likelihood(right)
        # 如果右侧作为艺术家名的得分比左侧高出至少2分，则认为右侧是艺术家
        if right_artist_score >= left_artist_score + 2:
            return left, right  # 返回 (标题, 艺术家)
        return right, left  # 否则，按默认顺序返回 (艺术家, 标题)

    # 如果没有找到任何分隔符，则将整个字符串作为标题，艺术家未知
    return name, "Unknown Artist"


def _derive_title_artist(
    path: Path,
    probe_title: str | None,
    probe_artist: str | None,
    probe_tags: dict[str, str] | None = None,
) -> tuple[str, str]:
    """从文件路径和探测信息中推导出标题和艺术家。

    参数:
    path: 文件路径对象，类型为Path，用于提取文件信息。
    probe_title: 探测到的标题字符串，可能为None，表示未直接提供标题。
    probe_artist: 探测到的艺术家字符串，可能为None，表示未直接提供艺术家。
    probe_tags: 探测到的标签字典，包含元数据信息，可能为None；如果为None则视为空字典。

    返回值:
    一个元组 (title, artist)，包含推导出的标题和艺术家字符串。
    """
    # 如果probe_tags为None，则初始化为空字典，便于安全访问标签数据
    tags = probe_tags or {}
    # 从标签中选择标题相关键的第一个存在值，并修复文本；键的优先顺序为：title、TIT2、©nam
    tag_title = repair_metadata_text(_pick_probe_tag(tags, "title", "TIT2", "\u00a9nam"))
    # 从标签中选择艺术家相关键的第一个存在值，并修复文本；键的优先顺序为：artist、album_artist、TPE1、TPE2、©ART
    tag_artist = repair_metadata_text(_pick_probe_tag(tags, "artist", "album_artist", "TPE1", "TPE2", "\u00a9ART"))
    # 优先使用probe_title，如果为空则使用标签中的标题，最终默认空字符串
    title = repair_metadata_text(probe_title or tag_title or "")
    # 优先使用probe_artist，如果为空则使用标签中的艺术家，最终默认空字符串
    artist = repair_metadata_text(probe_artist or tag_artist or "")

    # 检查标题是否为乱码文本，如果是则设为空字符串以避免显示错误
    if seems_mojibake_text(title):
        title = ""
    # 检查艺术家是否为乱码文本，如果是则设为空字符串以避免显示错误
    if seems_mojibake_text(artist):
        artist = ""

    # 如果标题和艺术家都不是未知文本，则直接返回，无需进一步处理
    if not _is_unknown_text(title, kind="title") and not _is_unknown_text(artist, kind="artist"):
        return title, artist

    # 从文件路径的stem部分解析出标题和艺术家，用于后备填充
    parsed_title, parsed_artist = _parse_title_artist_from_stem(path.stem)
    # 如果标题是未知文本，则用解析出的标题替换
    if _is_unknown_text(title, kind="title"):
        title = parsed_title
    # 如果艺术家是未知文本，则用解析出的艺术家替换
    if _is_unknown_text(artist, kind="artist"):
        artist = parsed_artist

    # 如果标题仍然是未知文本，则使用文件路径stem的清理版本，或默认为"Unknown Title"
    if _is_unknown_text(title, kind="title"):
        title = path.stem.strip() or "Unknown Title"
    # 如果艺术家仍然是未知文本，则设置为默认值"Unknown Artist"
    if _is_unknown_text(artist, kind="artist"):
        artist = "Unknown Artist"
    # 返回最终推导出的标题和艺术家
    return title, artist


def _quality_score(
    duration_sec: float,
    bit_rate: int | None,
    source_ext: str,
    sample_rate: int | None = None,
    file_size_bytes: int | None = None,
) -> float:
    """
    计算音频文件的质量得分。

    参数：
        duration_sec (float): 音频时长，单位为秒。
        bit_rate (int | None): 比特率，单位为bps。如果为None，则通过其他方式估算。
        source_ext (str): 源文件的扩展名。
        sample_rate (int | None): 采样率，单位为Hz。默认为None。
        file_size_bytes (int | None): 文件大小，单位为字节。默认为None。

    返回：
        float: 质量得分，范围在0.0到1.0之间。
    """
    # 初始分数设为0.12
    score = 0.12
    kbps = 0.0
    # 如果提供了bit_rate，直接转换为kbps（千比特每秒）
    if bit_rate:
        kbps = max(0.0, float(bit_rate) / 1000.0)
    # 否则，如果时长有效且文件大小有效，通过文件大小和时长估算kbps
    elif duration_sec > 0 and file_size_bytes and int(file_size_bytes) > 0:
        kbps = max(0.0, (float(file_size_bytes) * 8.0) / 1000.0 / max(1.0, float(duration_sec)))

    # 基于kbps添加分数，但上限为0.18，避免过高贡献
    if kbps > 0.0:
        score += min(0.18, kbps / 1200.0)
    # 如果提供了采样率，添加基于采样率的分数，上限为0.08
    if sample_rate:
        score += min(0.08, max(0.0, float(sample_rate) - 22050.0) / 88200.0)
    # 根据时长添加固定分数：超过60秒加0.02，超过180秒再加0.03
    if duration_sec >= 60:
        score += 0.02
    if duration_sec >= 180:
        score += 0.03
    # 获取扩展名并标准化为小写和去空格
    ext = source_ext.lower().strip()
    # 定义不同文件格式的加分字典，无损格式加分较高
    format_bonus = {
        ".flac": 0.58,
        ".wav": 0.56,
        ".ape": 0.54,
        ".alac": 0.52,
        ".m4a": 0.45,
        ".aac": 0.43,
        ".opus": 0.42,
        ".ogg": 0.41,
        ".wma": 0.34,
        ".mp3": 0.32,
    }
    # 根据扩展名添加格式加分，如果不在字典中则使用默认值0.30
    score += format_bonus.get(ext, 0.30)
    # 对于无损格式（如FLAC、WAV等），如果kbps大于0，添加额外加分，上限为0.08
    if ext in {".flac", ".wav", ".ape", ".alac"} and kbps > 0.0:
        score += min(0.08, kbps / 2500.0)
    # 如果没有稳定的kbps且是VBR编码格式（如OGG、OPUS等），添加特殊加分以减少误判
    if kbps <= 0.0 and ext in {".ogg", ".opus", ".m4a", ".aac"}:
        # VBR 编码常见无稳定比特率字段，降低缺失带来的误判。
        score += 0.04
    # 返回得分，确保在0.0到1.0之间
    return min(1.0, max(0.0, score))


def _copy_file_and_sha256(source: Path, target: Path, chunk_size: int = 1024 * 1024) -> str:
    """
    复制源文件到目标路径，并计算文件的SHA-256哈希值。

    参数：
        source (Path): 源文件的路径。
        target (Path): 目标文件的路径。
        chunk_size (int): 读取文件时的数据块大小，默认为1MB（1024 * 1024字节）。

    返回值：
        str: 文件内容的SHA-256哈希值的十六进制字符串。
    """
    ensure_parent(target)  # 确保目标文件的父目录存在，如果不存在则创建
    digest = hashlib.sha256()  # 初始化SHA-256哈希对象，用于累积计算哈希值
    with source.open("rb") as src, target.open("wb") as dst:  # 以二进制模式打开源文件进行读取，目标文件进行写入
        while True:  # 循环读取文件内容，直到所有数据处理完毕
            chunk = src.read(chunk_size)  # 从源文件读取指定大小的数据块
            if not chunk:  # 如果读取的数据块为空，表示文件已读取完毕，跳出循环
                break
            digest.update(chunk)  # 将当前数据块更新到哈希计算中
            dst.write(chunk)  # 将当前数据块写入目标文件
    try:
        shutil.copystat(source, target)  # 尝试复制源文件的元数据（如权限、时间戳）到目标文件
    except Exception:  # 如果复制元数据时发生任何异常（如权限问题），则忽略并继续
        pass
    return digest.hexdigest()  # 返回累积计算出的SHA-256哈希值的十六进制表示


def _as_json_dict(value: object) -> dict:
    """将输入对象转换为JSON兼容的字典。

    参数：
        value (object): 要转换的对象，可以是字典、字符串或其他类型。

    返回：
        dict: 返回一个字典。如果输入是字典，返回其副本；如果是有效的JSON字符串，返回解析后的字典；否则返回空字典。
    """
    if isinstance(value, dict):  # 检查输入是否为字典
        return dict(value)  # 返回字典的副本
    if isinstance(value, str):  # 检查输入是否为字符串
        try:
            parsed = json.loads(value)  # 解析JSON字符串
            if isinstance(parsed, dict):  # 检查解析结果是否为字典
                return dict(parsed)  # 返回解析后的字典
        except Exception:  # 如果解析过程中发生异常
            return {}  # 返回空字典
    return {}  # 如果输入不是字典或有效JSON字符串，返回空字典


def _normalize_track_ext_payload(payload: object) -> dict:
    """标准化追踪扩展载荷的格式。

    将输入的载荷（payload）转换为一个标准的字典格式，
    重点确保其中的 "tags" 字段符合规范（即键和值均为字符串）。

    Args:
        payload (object): 需要被标准化和处理的原始载荷数据。

    Returns:
        dict: 一个标准化的字典。该字典确保 "tags" 字段的值
              是一个字典，并且其所有键和值均为字符串类型。
    """
    data = _as_json_dict(payload)
    tags_raw = data.get("tags", {})
    # 如果获取的原始tags不是字典类型（例如，可能是字符串或列表），
    # 则将其重置为空字典，以确保后续处理安全。
    if not isinstance(tags_raw, dict):
        tags_raw = {}
    tags: dict[str, str] = {}
    # 遍历原始tags中的每一项，进行规范化处理。
    for key, value in tags_raw.items():
        # 将键转换为字符串，并去除首尾空格。
        k = str(key).strip()
        # 如果处理后的键为空字符串，则跳过该项。
        if not k:
            continue
        # 将值转换为字符串。如果值为None或空，则视为空字符串。
        tags[k] = str(value or "")
    # 用处理后的规范tags字典，替换原数据中的"tags"字段。
    data["tags"] = tags
    return data


def _cover_payload_from_probe(probe: ProbeInfo) -> dict:
    """
    根据ProbeInfo对象提取封面信息，生成payload字典。

    此函数从probe对象中安全地获取封面宽度、高度和字节数，将它们转换为整数。
    如果所有值都小于等于0，则返回空字典；否则，构建一个字典，只包含大于0的值。

    参数:
    probe (ProbeInfo): 提供封面信息的对象，属性包括cover_width、cover_height和cover_bytes。

    返回:
    dict: 一个字典，包含以下键（如果对应值大于0）：
        - "width": 封面宽度（整数）
        - "height": 封面高度（整数）
        - "bytes": 封面字节数（整数）
    """
    width = _safe_int(probe.cover_width, 0)  # 安全地将cover_width转换为整数，失败时默认为0
    height = _safe_int(probe.cover_height, 0)  # 安全地将cover_height转换为整数，失败时默认为0
    byte_size = _safe_int(probe.cover_bytes, 0)  # 安全地将cover_bytes转换为整数，失败时默认为0
    if width <= 0 and height <= 0 and byte_size <= 0:  # 检查所有值是否都无效（小于等于0）
        return {}  # 如果都无效，返回空字典
    payload: dict[str, int] = {}  # 初始化payload字典
    if width > 0:  # 如果宽度大于0，添加到payload
        payload["width"] = int(width)
    if height > 0:  # 如果高度大于0，添加到payload
        payload["height"] = int(height)
    if byte_size > 0:  # 如果字节数大于0，添加到payload
        payload["bytes"] = int(byte_size)
    return payload  # 返回构建好的payload字典


def _build_track_ext_payload(probe: ProbeInfo) -> dict:
    """
    构建一个跟踪扩展负载字典。

    功能：从探针信息中提取标签和元数据，构建一个包含标签、元数据源和可选封面的字典。
    参数：
        probe (ProbeInfo): 探针信息对象，包含标签和其他元数据。
    返回值：
        dict: 包含以下键的字典：
            - "tags": 标签字典。
            - "metadata_source": 元数据来源，可以是 "id3" 或 "filename_fallback"。
            - "cover": 可选，封面信息字典。
    """
    tags: dict[str, str] = {}  # 初始化一个空字典用于存储标签
    if isinstance(probe.tags, dict):  # 检查探针的标签是否为字典类型
        for key, value in probe.tags.items():  # 遍历标签项
            k = str(key or "").strip()  # 将键转换为字符串并去除首尾空格
            v = str(value or "").strip()  # 将值转换为字符串并去除首尾空格
            if not k or not v:  # 如果键或值为空，则跳过此项
                continue
            tags[k] = v  # 将非空标签添加到字典中
    payload = {"tags": tags}  # 创建负载字典，并设置标签
    payload["metadata_source"] = "id3" if tags else "filename_fallback"  # 如果标签非空，元数据源为"id3"，否则为"filename_fallback"
    cover = _cover_payload_from_probe(probe)  # 从探针信息获取封面信息
    if cover:  # 如果存在封面信息
        payload["cover"] = cover  # 将封面添加到负载中
    return payload  # 返回构建好的负载字典


def _cover_rank(value: object) -> tuple[int, int, int, int]:
    """这个函数根据输入的封面数据计算排名指标。

    参数：
        value (object): 输入对象，预期为字典，否则使用空字典。

    返回值：
        tuple[int, int, int, int]: 包含四个整数的元组：
            - has_cover: 是否有封面（1表示有，0表示无）。
            - area: 封面的面积（宽度乘以高度）。
            - edge: 封面的边缘长度（宽度和高度的最小值）。
            - byte_size: 封面的字节大小。
    """
    cover = value if isinstance(value, dict) else {}  # 如果value是字典，则使用它，否则使用空字典
    width = max(0, _safe_int(cover.get("width", 0), 0))  # 安全获取宽度，确保非负
    height = max(0, _safe_int(cover.get("height", 0), 0))  # 安全获取高度，确保非负
    byte_size = max(0, _safe_int(cover.get("bytes", 0), 0))  # 安全获取字节大小，确保非负
    area = width * height  # 计算面积
    edge = min(width, height) if width > 0 and height > 0 else 0  # 计算边缘长度，仅当宽度和高度都为正时，否则为0
    has_cover = 1 if area > 0 or byte_size > 0 else 0  # 判断是否有封面，面积或字节大小大于0则有封面
    return has_cover, area, edge, byte_size  # 返回指标元组


def _merge_ext_payload_for_duplicate(primary_payload: object, secondary_payload: object) -> dict:
    """
    功能：合并两个扩展负载对象，用于处理重复数据。该函数对负载进行标准化，合并时以primary_payload为主，覆盖secondary_payload的字段，并对标签和封面进行特殊处理。
    参数：
        primary_payload (object): 主负载对象。
        secondary_payload (object): 次要负载对象。
    返回值：
        dict: 合并后的字典，包含合并后的数据和封面来源信息。
    """
    # 标准化主负载和次负载，确保数据格式一致
    primary = _normalize_track_ext_payload(primary_payload)
    secondary = _normalize_track_ext_payload(secondary_payload)
    # 以次负载为基础创建合并字典，然后用主负载更新，使主负载字段优先覆盖次负载
    merged = dict(secondary)
    merged.update(primary)

    # 获取标签数据，如果不存在则默认为空字典，避免键错误
    secondary_tags = secondary.get("tags", {})
    primary_tags = primary.get("tags", {})
    # 初始化合并后的标签字典，用于存储最终合并的标签
    merged_tags: dict[str, str] = {}
    # 如果次负载标签是字典，则处理并添加到合并标签中：键转换为字符串且忽略空白键，值转换为字符串或空字符串
    if isinstance(secondary_tags, dict):
        merged_tags.update({str(k): str(v or "") for k, v in secondary_tags.items() if str(k).strip()})
    # 如果主负载标签是字典，则用主负载标签覆盖次负载标签，确保主负载优先
    if isinstance(primary_tags, dict):
        merged_tags.update({str(k): str(v or "") for k, v in primary_tags.items() if str(k).strip()})
    # 将合并后的标签设置到最终合并字典中
    merged["tags"] = merged_tags

    # 获取封面数据
    primary_cover = primary.get("cover")
    secondary_cover = secondary.get("cover")
    # 比较封面排名，选择排名更高的封面作为最佳封面
    if _cover_rank(secondary_cover) > _cover_rank(primary_cover):
        best_cover = secondary_cover
        from_secondary = True
    else:
        best_cover = primary_cover
        from_secondary = False
    # 如果最佳封面是字典且非空，则将其添加到合并字典，并记录封面来源
    if isinstance(best_cover, dict) and best_cover:
        merged["cover"] = dict(best_cover)
        merged["cover_selected_from"] = "secondary" if from_secondary else "primary"
    else:
        # 如果没有有效封面，则从合并字典中移除相关字段
        merged.pop("cover", None)
        merged.pop("cover_selected_from", None)
    return merged


def _extract_lyrics_meta(text: str) -> tuple[str, int, str, str, str]:
    """从歌词文本中提取元数据信息。
    
    该函数解析歌词文本（如LRC格式），提取作者、标题、歌手、专辑和行数等元数据。
    它只扫描前40行，以确保处理效率。
    
    Args:
        text (str): 包含歌词元数据的文本字符串。
        
    Returns:
        tuple[str, int, str, str, str]: 一个包含以下五个元素的元组：
            - author (str): 作者信息
            - line_count (int): 非空行数
            - title (str): 歌曲标题
            - artist (str): 歌手/艺术家
            - album (str): 专辑名称
    """
    author = ""
    title = ""
    artist = ""
    album = ""

    # 只处理文本的前40行，避免处理过长的歌词文本
    for line in text.splitlines()[:40]:
        s = line.strip()  # 移除行首尾的空白字符
        if not s:  # 跳过空行
            continue
        low = s.casefold()  # 将文本转换为小写，用于不区分大小写的比较

        # 定义一个内部函数，用于从标签中提取值
        # 例如：_tag_value("[by:") 会处理 "[by:作者]" 并返回 "作者"
        def _tag_value(prefix: str) -> str:
            return s[len(prefix) : -1].strip()

        # 检查并提取 [by:] 标签中的作者信息
        if low.startswith("[by:") and s.endswith("]") and not author:
            author = html.unescape(_tag_value("[by:"))
            continue
        # 检查并提取 [ti:] 标签中的标题信息
        if low.startswith("[ti:") and s.endswith("]") and not title:
            title = html.unescape(_tag_value("[ti:"))
            continue
        # 检查并提取 [ar:] 标签中的歌手信息
        if low.startswith("[ar:") and s.endswith("]") and not artist:
            artist = html.unescape(_tag_value("[ar:"))
            continue
        # 检查并提取 [al:] 标签中的专辑信息
        if low.startswith("[al:") and s.endswith("]") and not album:
            album = html.unescape(_tag_value("[al:"))
            continue
        # 检查并提取不带方括号的 by: 标签（兼容另一种格式）
        if low.startswith("by:") and not author:
            author = html.unescape(s[3:].strip())

    # 统计文本中非空行的总数（不仅仅是前40行）
    line_count = len([line for line in text.splitlines() if line.strip()])
    return author, line_count, title, artist, album


def _is_placeholder_empty_lyrics(text: str) -> bool:
    """
    功能：检查输入的文本是否是占位符空歌词。
    参数：
        text (str): 输入的文本字符串。
    返回值：
        bool: 如果文本是占位符空歌词，则返回True；否则返回False。
    """
    if not text:  # 如果文本为空或None，直接返回True
        return True
    # 移除输入文本两端空白字符，然后去除所有空白字符（如空格、制表符等），生成压缩字符串
    compact = "".join(ch for ch in text.strip() if not ch.isspace())
    # 移除压缩字符串中的零宽空格字符（如BOM，Unicode字符\ufeff）
    compact = compact.replace("\ufeff", "")
    # 定义占位符空歌词的标记文本
    marker = "[00:00:00]此歌曲为没有填词的纯音乐，请您欣赏"
    # 对标记文本进行同样的压缩处理，移除所有空白字符
    marker_compact = "".join(ch for ch in marker if not ch.isspace())
    # 比较压缩后的输入文本与压缩后的标记文本是否相等
    return compact == marker_compact


import re
import unicodedata
from typing import Dict, Set, Tuple

# ==========================================
# 预编译正则表达式 (提升性能)
# ==========================================
# 更严格的时间标签匹配，防止误匹配如 [10:23abc]
RE_LRC_TIME = re.compile(r"^\[\d{1,2}:\d{2}(?::\d{2})?(?:\.\d{1,3})?\]\s*")
# 容错元数据标签：允许冒号前后有空格，使用非贪婪匹配
RE_LRC_META = re.compile(r"^\[(ti|ar|al|by|offset)\s*:\s*(.+?)\s*\]$", re.IGNORECASE)
# 拉丁词汇提取
RE_LATIN_WORD = re.compile(r"[a-zA-Z\u00C0-\u024F]+")


# ==========================================
# 静态 Unicode 码点范围检测 (替换 unicodedata.name)
# ==========================================
def _is_latin(c: int) -> bool:
    """判断是否为拉丁字母（含扩展区），性能远高于 unicodedata.name"""
    return ((0x0041 <= c <= 0x007A) or    # 基础拉丁 (A-Z, a-z)，巧妙避开中间的符号
            (0x00C0 <= c <= 0x024F) or    # 拉丁补充、扩展A、扩展B (含法德西葡等字符)
            (0x1E00 <= c <= 0x1EFF))      # 拉丁扩展附加 (如越南语)

def _is_han(c: int) -> bool:
    """判断是否为汉字（包含扩展A区和兼容区，提升生僻字/人名鲁棒性）"""
    return ((0x3400 <= c <= 0x4DBF) or    # CJK 扩展A
            (0x4E00 <= c <= 0x9FFF) or    # CJK 统一汉字基本区
            (0xF900 <= c <= 0xFAFF))      # CJK 兼容汉字

def _infer_lyrics_language_kind(text: str) -> str:
    """基于歌词正文字符分布与特征推断语言类型（改进版）。"""
    if not text:
        return "unknown"

    lines: list[str] = []
    meta_values: list[str] = []

    for raw in text.splitlines():
        s = str(raw or "").strip()
        if not s:
            continue
        
        # 剥离时间标签
        if RE_LRC_TIME.match(s):
            s = RE_LRC_TIME.sub("", s).strip()
        
        # 提取元数据标签（更宽容的匹配）
        m = RE_LRC_META.match(s)
        if m:
            meta_values.append(m.group(2).strip())
            continue
            
        if s:
            lines.append(s)

    body = "\n".join(lines).strip()
    if not body:
        return "unknown"

    # ==========================================
    # 1. 字符级别统计
    # ==========================================
    script_counts: Dict[str, int] = {
        "latin": 0, "han": 0, "hiragana": 0, "katakana": 0,
        "hangul": 0, "cyrillic": 0, "arabic": 0, "hebrew": 0,
        "thai": 0, "devanagari": 0,
    }
    latin_ext = 0
    total_letters = 0

    for ch in body:
        if ch.isspace() or ch.isdigit():
            continue
        # 必须是 Letter (Ll, Lu, Lt, Lo 等)
        if not unicodedata.category(ch).startswith("L"):
            continue
            
        total_letters += 1
        code = ord(ch)
        
        if _is_han(code):
            script_counts["han"] += 1
        elif 0x3040 <= code <= 0x309F:
            script_counts["hiragana"] += 1
        elif 0x30A0 <= code <= 0x30FF:
            script_counts["katakana"] += 1
        elif 0xAC00 <= code <= 0xD7AF:
            script_counts["hangul"] += 1
        elif 0x0400 <= code <= 0x04FF:
            script_counts["cyrillic"] += 1
        elif 0x0600 <= code <= 0x06FF:
            script_counts["arabic"] += 1
        elif 0x0590 <= code <= 0x05FF:
            script_counts["hebrew"] += 1
        elif 0x0E00 <= code <= 0x0E7F:
            script_counts["thai"] += 1
        elif 0x0900 <= code <= 0x097F:
            script_counts["devanagari"] += 1
        elif _is_latin(code):
            script_counts["latin"] += 1
            if code > 0x007F:
                latin_ext += 1

    if total_letters <= 0:
        return "unknown"

    # ==========================================
    # 2. 元数据统计（仅作为辅助证据）
    # ==========================================
    meta_blob = " ".join(meta_values)
    meta_kana = sum(1 for ch in meta_blob if 0x3040 <= ord(ch) <= 0x30FF)
    meta_han = sum(1 for ch in meta_blob if _is_han(ord(ch)))

    # ==========================================
    # 3. 计算比例与主导语种判定 (修复 mixed 漏洞)
    # ==========================================
    def _ratio(value: int) -> float:
        return float(value) / float(total_letters)

    kana_count = script_counts["hiragana"] + script_counts["katakana"]
    han_count = script_counts["han"]
    
    # 日语的科学判定：必须存在假名，或者假名与汉字高度混合
    # 放弃原来的 0.02 极低阈值，要求假名必须有实际存在感
    if kana_count > 0:
        if han_count > 0 and _ratio(kana_count + han_count) >= 0.15:
            return "ja"
        if _ratio(kana_count) >= 0.15:
            return "ja"

    # 其他非拉丁语系：采用“最大值优先”策略，而非死板的 0.65/0.80 绝对阈值
    # 这彻底解决了中英夹杂返回 mixed 的问题
    non_latin_ratios = {
        "zh": _ratio(han_count),
        "ko": _ratio(script_counts["hangul"]),
        "ru": _ratio(script_counts["cyrillic"]),
        "ar": _ratio(script_counts["arabic"]),
        "he": _ratio(script_counts["hebrew"]),
        "th": _ratio(script_counts["thai"]),
        "hi": _ratio(script_counts["devanagari"]),
    }
    
    # 如果非拉丁语系占比超过 40%，则认为是该语言（允许最多 60% 的拉丁字母混血）
    dominant_non_latin = max(non_latin_ratios.items(), key=lambda x: x[1])
    if dominant_non_latin[1] >= 0.40:
        return dominant_non_latin[0]

    # ==========================================
    # 4. 拉丁语系细分 (优化打分科学性)
    # ==========================================
    latin_ratio = _ratio(script_counts["latin"])
    # 如果拉丁字母连主导都不是（比如30%中文，30%日文，30%英文），才返回 mixed
    if latin_ratio < 0.40:
        return "mixed"

    lower_body = body.casefold()
    words = RE_LATIN_WORD.findall(lower_body)
    if not words:
        return "en"

    word_set = set(words)

    # 停用词表
    stop_words: Dict[str, Set[str]] = {
        "en": {"the", "and", "you", "to", "of", "in", "is", "my", "me", "i", "it"},
        "es": {"que", "de", "la", "el", "y", "en", "no", "te", "se", "un", "una"},
        "fr": {"je", "tu", "il", "elle", "de", "la", "le", "et", "pas", "que", "un", "une"},
        "de": {"und", "ich", "nicht", "die", "das", "du", "der", "ein"},
        "pt": {"nao", "não", "de", "que", "eu", "voce", "você", "uma", "com", "um"},
        "it": {"che", "di", "non", "io", "tu", "la", "il", "e", "un", "una"},
        "vi": {"va", "la", "mot", "nhung", "khong", "toi", "em", "anh", "co"},
    }

    # 特征字符（按语言分组）
    lang_marks: Dict[str, Set[str]] = {
        "es": {"á", "é", "í", "ó", "ú", "ñ", "ü", "¿", "¡"},
        "fr": {"à", "â", "ç", "é", "è", "ê", "ë", "î", "ï", "ô", "û", "ù", "ü", "ÿ", "œ", "æ", "«", "»"},
        "de": {"ä", "ö", "ü", "ß"},
        "pt": {"ã", "õ", "ç", "á", "é", "í", "ó", "ú", "â", "ê", "ô"},
        "it": {"à", "è", "é", "ì", "í", "î", "ò", "ó", "ù"},
        "vi": {"ă", "â", "đ", "ê", "ô", "ơ", "ư"},
    }

    # 科学的打分机制
    scores: Dict[str, float] = {lang: 0.0 for lang in stop_words.keys()}

    for lang, stops in stop_words.items():
        # 停用词命中数（强特征）
        scores[lang] += len(word_set.intersection(stops)) * 2.0
        
        # 特殊标点命中（极强特征，如西班牙语的 ¿）
        punctuation_hits = sum(1 for ch in body if ch in lang_marks.get(lang, set()) and not ch.isalpha())
        scores[lang] += punctuation_hits * 5.0

    # 重音字符打分：修正原版“按字符计数”导致长歌词偏差的谬误，改为“按含重音的词数”计数
    for lang, marks in lang_marks.items():
        accent_marks = marks - {ch for ch in marks if not ch.isalpha()} # 剔除标点
        if not accent_marks:
            continue
        # 统计包含该语言特征字符的单词数量
        accent_word_count = sum(1 for w in words if any(m in w for m in accent_marks))
        # 归一化：占总词数的比例，乘以权重。这样长歌词不会因为字符多而虚高
        scores[lang] += (accent_word_count / len(words)) * 15.0

    # 英语的兜底加分（纯 ASCII 拉丁字母占比高）
    pure_latin_ratio = max(0, script_counts["latin"] - latin_ext) / total_letters
    scores["en"] += pure_latin_ratio * 3.0

    # 元数据作为“软加分”，绝不“一票否决”
    if meta_kana >= 2:
        scores["ja"] = scores.get("ja", 0) + 2.0  # 假设可能没初始化 ja
    if meta_han >= 2:
        scores["zh"] = scores.get("zh", 0) + 2.0

    # 决策
    best_lang = max(scores, key=scores.get)
    
    # 如果没有任何特征词，且没有任何扩展拉丁字符，兜底英语
    if best_lang == "en" and scores["en"] <= pure_latin_ratio * 3.0 + 0.1:
        return "en"
        
    return best_lang


def _normalize_name_for_compare(value: str) -> str:
    """标准化名称字符串，去除括号及其内容，以便进行比较。

    参数：
        value (str): 需要标准化的输入字符串。

    返回：
        str: 标准化后的字符串。
    """
    text = str(value or "")  # 将输入转换为字符串，如果是None则使用空字符串
    text = re.sub(r"[\(\[【{（].*?[\)\]】}）]", " ", text)  # 使用正则表达式去除圆括号、方括号、花括号及其内容
    return normalize_text(text)  # 调用normalize_text函数进行进一步标准化


def _lyrics_group_display_name(relpath: str) -> str:
    """从相对路径中提取歌词组的显示名称。

    功能：清理路径的stem部分，移除末尾括号内的内容（如中英文括号），并返回清理后的字符串。如果stem为空，则返回"未分组"。

    参数：relpath (str): 相对路径字符串。

    返回值：str: 清理后的显示名称字符串。
    """
    # 获取路径的文件名部分（stem）并去除空白
    stem = Path(str(relpath or "")).stem.strip()
    # 如果stem为空，返回默认名称"未分组"
    if not stem:
        return "未分组"
    # 使用正则表达式移除stem末尾的括号及其内容，支持中英文括号
    cleaned = re.sub(r"\s*[\(\[（【].*?[\)\]）】]\s*$", "", stem).strip()
    # 返回清理后的名称，如果清理后为空则返回原stem
    return cleaned or stem


def _name_similarity(a: str, b: str) -> float:
    """计算两个名称字符串的相似度。

    通过名称规范化、分词和集合相似度（Jaccard相似系数）来评估两个名称的相似程度。

    参数:
        a (str): 第一个名称字符串。
        b (str): 第二个名称字符串。

    返回:
        float: 相似度分数，范围在0.0到1.0之间。
               - 0.0表示完全不同或输入无效。
               - 1.0表示完全相同。
    """
    # 对两个输入名称进行规范化处理，去除无关字符、统一格式等
    na = _normalize_name_for_compare(a)
    nb = _normalize_name_for_compare(b)
    # 如果任一规范化后的名称为空字符串，相似度直接为0
    if not na or not nb:
        return 0.0
    # 如果规范化后的名称完全相同，相似度直接为1
    if na == nb:
        return 1.0
    # 将规范化后的名称按空格分词，并转换为集合，同时过滤空字符串
    tokens_a = {t for t in na.split() if t}
    tokens_b = {t for t in nb.split() if t}
    # 如果分词后的任一集合为空，相似度为0
    if not tokens_a or not tokens_b:
        return 0.0
    # 计算两个token集合的交集大小（共同部分）
    inter = len(tokens_a.intersection(tokens_b))
    # 计算两个token集合的并集大小（总部分）
    union = len(tokens_a.union(tokens_b))
    # 计算Jaccard相似系数：交集大小除以并集大小。如果并集大小为0则返回0.0
    return 0.0 if union <= 0 else float(inter) / float(union)


def _safe_int(value, default: int = 0) -> int:
    """安全地将给定值转换为整数，如果转换失败则返回默认值。

    参数:
        value: 任意类型，要转换为整数的值。
        default: int, 默认值，默认为0。

    返回值:
        int: 转换后的整数，或转换失败时返回默认值。
    """
    try:
        return int(value)  # 尝试将value转换为整数
    except Exception:
        return default  # 如果转换失败，返回默认值


def _normalize_source_path_key(value: str | Path) -> str:
    """规范化源路径键。

    将输入的值转换为标准化的路径字符串。尝试解析为路径并解决符号链接，如果失败则使用原始值。最后统一斜杠格式并转换为小写。

    参数：
        value (str | Path): 输入的路径字符串或Path对象。

    返回：
        str: 规范化后的路径字符串，使用正斜杠，去除首尾空格，并转换为小写。
    """
    try:
        # 尝试将输入值转换为Path对象，扩展用户目录（如~）并解析为绝对路径
        resolved = Path(value).expanduser().resolve()
        # 将解析后的路径对象转换为字符串
        text = str(resolved)
    except Exception:
        # 如果路径解析失败，使用原始值；如果原始值为空或None则转换为空字符串
        text = str(value or "")
    # 替换反斜杠为正斜杠以统一路径分隔符，去除首尾空格，并转换为小写以确保一致性
    return text.replace("\\", "/").strip().casefold()


class ImportService:
    def __init__(self, library_root: Path, runtime_cfg: RuntimeConfig):
        """\u521d\u59cb\u5316\u5bfc\u5165\u670d\u52a1\u53ca\u5176\u4f9d\u8d56\u7ec4\u4ef6\u3002"""
        self.library_root = library_root
        self.runtime_cfg = runtime_cfg
        self.fingerprint_workers = self._resolve_worker_count(getattr(runtime_cfg.ui, "fingerprint_workers", 0), default_cap=2)
        duplicate_workers = self._resolve_worker_count(getattr(runtime_cfg.ui, "duplicate_compare_workers", 0), default_cap=8)
        lyrics_workers = self._resolve_worker_count(getattr(runtime_cfg.ui, "lyrics_match_workers", 0), default_cap=8)
        duplicate_threshold = max(1, int(getattr(runtime_cfg.ui, "duplicate_compare_parallel_threshold", 48) or 48))
        lyrics_threshold = max(1, int(getattr(runtime_cfg.ui, "lyrics_match_parallel_threshold", 96) or 96))
        self.dependencies = ImportDependencies(
            probe=MediaProbe(),
            transcoder=MediaTranscoder(),
            fingerprint=AcousticFingerprintEngine(),
        )
        self.duplicate_evaluator = DuplicateEvaluator(
            self.dependencies.fingerprint,
            runtime_cfg.thresholds,
            compare_workers=duplicate_workers,
            parallel_threshold=duplicate_threshold,
        )
        llm = LmStudioMatcher(runtime_cfg.lmstudio) if runtime_cfg.lmstudio.enabled else None
        self.lyrics_matcher = LyricsMatcher(
            runtime_cfg.thresholds,
            llm,
            score_workers=lyrics_workers,
            parallel_threshold=lyrics_threshold,
        )

    @staticmethod
    def _resolve_worker_count(value: int | None, *, default_cap: int = 8) -> int:
        """\u5c06\u914d\u7f6e\u4e2d\u7684\u5e76\u53d1\u503c\u89c4\u8303\u5316\u4e3a\u5b89\u5168\u7684\u7ebf\u7a0b\u6570\u3002"""
        parsed = int(value or 0)
        if parsed <= 0:
            return max(1, min(default_cap, (os.cpu_count() or 4) - 1))
        return max(1, min(16, parsed))

    def _skipped_path_registry_file(self) -> Path:
        """\u8fd4\u56de\u5386\u53f2\u8df3\u8fc7\u97f3\u9891\u8def\u5f84\u7d22\u5f15\u6587\u4ef6\u8def\u5f84\u3002"""
        # 历史跳过音频路径索引：用于后续导入快速排除重复来源。
        return self.library_root / "manifests" / "imports" / "skipped_audio_paths.json"

    def _load_skipped_audio_path_keys(self) -> set[str]:
        """\u52a0\u8f7d\u5386\u53f2\u8df3\u8fc7\u97f3\u9891\u8def\u5f84\u7d22\u5f15\u3002"""
        target = self._skipped_path_registry_file()
        if not target.exists():
            return set()
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return set()
        rows = payload.get("paths") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            return set()
        return {_normalize_source_path_key(v) for v in rows if str(v).strip()}

    def _save_skipped_audio_path_keys(self, keys: set[str]) -> None:
        """\u6301\u4e45\u5316\u5386\u53f2\u8df3\u8fc7\u97f3\u9891\u8def\u5f84\u7d22\u5f15\u3002"""
        target = self._skipped_path_registry_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "paths": sorted({str(v).strip() for v in keys if str(v).strip()}),
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _lyrics_seen_registry_file(self) -> Path:
        """\u8fd4\u56de\u5386\u53f2\u5df2\u5904\u7406\u6b4c\u8bcd\u8def\u5f84\u7d22\u5f15\u6587\u4ef6\u8def\u5f84\u3002"""
        # 历史已处理歌词路径索引：避免同路径重复进入导入与审查。
        return self.library_root / "manifests" / "imports" / "seen_lyrics_paths.json"

    def _load_seen_lyrics_path_keys(self) -> set[str]:
        """\u52a0\u8f7d\u5386\u53f2\u5df2\u5904\u7406\u6b4c\u8bcd\u8def\u5f84\u7d22\u5f15\u3002"""
        target = self._lyrics_seen_registry_file()
        if not target.exists():
            return set()
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return set()
        rows = payload.get("paths") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            return set()
        return {_normalize_source_path_key(v) for v in rows if str(v).strip()}

    def _save_seen_lyrics_path_keys(self, keys: set[str]) -> None:
        """\u6301\u4e45\u5316\u5386\u53f2\u5df2\u5904\u7406\u6b4c\u8bcd\u8def\u5f84\u7d22\u5f15\u3002"""
        target = self._lyrics_seen_registry_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "paths": sorted({str(v).strip() for v in keys if str(v).strip()}),
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _suggest_similar_tracks_by_name(self, source_stem: str, candidates: list[dict], limit: int = 6) -> list[dict]:
        """\u6309\u540d\u79f0\u76f8\u4f3c\u5ea6\u7ed9\u51fa\u5019\u9009\u6b4c\u66f2\u5efa\u8bae\u3002"""
        scored: list[tuple[float, dict]] = []
        for row in candidates:
            candidate_name = str(row.get("title") or row.get("file_name") or "")
            score = _name_similarity(source_stem, candidate_name)
            if score <= 0.0:
                continue
            scored.append(
                (
                    score,
                    {
                        "track_id": str(row.get("track_id", "")),
                        "title": str(row.get("title", "")),
                        "artist": str(row.get("artist", "")),
                        "score": round(score, 4),
                    },
                )
            )
        scored.sort(key=lambda p: p[0], reverse=True)
        return [item for _score, item in scored[:limit]]

    def _fingerprint_with_loudness_normalization(
        self,
        source_path: Path,
        target_lufs: float = -14.0,
    ) -> Fingerprint:
        """\u5f52\u4e00\u54cd\u5ea6\u540e\u751f\u6210\u97f3\u9891\u6307\u7eb9\u3002"""
        try:
            decoded = decode_audio(
                source_path,
                target_rate=22050,
                target_layout="mono",
                apply_loudnorm=True,
                target_lufs=target_lufs,
            )
        except MediaCommandError:
            raise
        except Exception as exc:
            raise MediaCommandError(f"decode_failed:{source_path}:{exc}") from exc

        samples = decoded.samples
        if samples.size <= 0:
            vector: list[int] = []
        else:
            vector = self.dependencies.fingerprint._fingerprint_vector(samples, decoded.sample_rate)
        if not vector:
            raise MediaCommandError(f"chromaprint_unavailable_or_failed:{source_path}")

        payload = self.dependencies.fingerprint.encode_vector(vector)
        digest = hashlib.sha1(payload.encode("ascii")).hexdigest()
        return Fingerprint(version=self.dependencies.fingerprint.version, vector=vector, digest=digest)

    def import_track_for_duplicate_review(
        self,
        repo: LibraryRepository,
        source_path: Path,
        *,
        existing_track_id: str | None = None,
        replace_existing: bool = True,
    ) -> dict:
        """\u5c06\u5355\u9996\u6b4c\u66f2\u5bfc\u5165\u5e76\u7528\u4e8e\u91cd\u590d\u5ba1\u67e5\u66ff\u6362\u6d41\u7a0b\u3002"""
        source = Path(source_path).expanduser().resolve()
        probe = self.dependencies.probe.probe(source)
        fp = self._fingerprint_with_loudness_normalization(source, target_lufs=-14.0)
        title, artist = _derive_title_artist(source, probe.title, probe.artist, probe.tags)
        file_size = None
        try:
            file_size = int(source.stat().st_size)
        except Exception:
            file_size = None
        quality = _quality_score(
            probe.duration_sec,
            probe.bit_rate,
            source.suffix,
            probe.sample_rate,
            file_size,
        )
        fp_payload = self.dependencies.fingerprint.encode_vector(fp.vector)
        fp_hash32 = self.dependencies.fingerprint.fingerprint_hash32(fp_payload)
        ext_payload = _build_track_ext_payload(probe)
        source_sha = sha256_file(source)

        existing_by_sha = repo.get_track_by_source_sha(source_sha)
        if existing_by_sha and not existing_by_sha.get("deleted_at"):
            return {"status": "already_exists", "track_id": str(existing_by_sha.get("track_id", "") or "")}

        track_id = new_id("trk")
        ext_no_dot = source.suffix.lower().strip(".") or "bin"
        storage_rel = shard_relpath("data/tracks", track_id, ext_no_dot)
        storage_abs = self.library_root / Path(storage_rel)
        ensure_parent(storage_abs)
        shutil.copy2(source, storage_abs)

        track_row = TrackInsert(
            track_id=track_id,
            file_name=source.name,
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
            source_relpath=source.name,
            source_fullpath=str(source),
            source_sha256=source_sha,
            source_ext=source.suffix,
            probe_codec=probe.codec,
            file_health=FileHealth.OK,
            fingerprint_version=fp.version,
            fingerprint_digest=fp.digest,
            fingerprint_hash32=fp_hash32,
            fingerprint_payload=fp_payload,
            imported_at=_utc_now(),
            ext_json=ext_payload,
        )
        try:
            repo.insert_track(track_row)
        except Exception:
            storage_abs.unlink(missing_ok=True)
            raise

        replaced_track_id = ""
        if existing_track_id:
            existing_rows = repo.get_tracks_by_ids([existing_track_id])
            existing = existing_rows[0] if existing_rows else {}
            if existing:
                merge_patch: dict[str, object] = {}
                if str(title or "").strip().casefold() in {"", "unknown", "unknown title"} and str(existing.get("title", "")).strip():
                    merge_patch["title"] = str(existing.get("title", "")).strip()
                if str(artist or "").strip().casefold() in {"", "unknown", "unknown artist"} and str(existing.get("artist", "")).strip():
                    merge_patch["artist"] = str(existing.get("artist", "")).strip()
                if not str(repair_metadata_text(probe.album or "")).strip() and str(existing.get("album", "")).strip():
                    merge_patch["album"] = str(existing.get("album", "")).strip()
                if merge_patch:
                    repo.update_tracks_fields([track_id], merge_patch)

                existing_ext = _normalize_track_ext_payload(existing.get("ext_json"))
                current_new_ext = _normalize_track_ext_payload(track_row.ext_json)
                merged_new_ext = _merge_ext_payload_for_duplicate(current_new_ext, existing_ext)
                if merged_new_ext != current_new_ext:
                    repo.update_track_ext_json(track_id, merged_new_ext)

            if replace_existing:
                old_relpaths = [
                    str(r.get("storage_relpath", "") or "")
                    for r in repo.get_tracks_by_ids([existing_track_id])
                    if str(r.get("storage_relpath", "") or "").strip()
                ]
                deleted = repo.soft_delete_tracks([existing_track_id])
                if deleted > 0:
                    replaced_track_id = str(existing_track_id)
                    for rel in old_relpaths:
                        try:
                            (self.library_root / rel).unlink(missing_ok=True)
                        except Exception:
                            pass

        return {
            "status": "imported",
            "track_id": track_id,
            "replaced_track_id": replaced_track_id,
        }

    def import_path(
        self,
        repo: LibraryRepository,
        source_path: Path,
        *,
        progress_callback: Callable[[ImportProgress], None] | None = None,
        control: ImportControl | None = None,
        resume: bool = True,
    ) -> ImportReport:
        """\u5bfc\u5165\u5165\u53e3\uff1a\u8c03\u7528\u62c6\u5206\u540e\u7684\u5bfc\u5165\u7ba1\u7ebf\u5e76\u8fd4\u56de\u5bfc\u5165\u62a5\u544a\u3002"""
        from musearc.services.importer_pipeline import run_import_path

        return run_import_path(
            self,
            repo,
            source_path,
            progress_callback=progress_callback,
            control=control,
            resume=resume,
        )

    def _save_state(self, state_file: Path, repo: LibraryRepository, state: ResumeState) -> None:
        """\u4fdd\u5b58\u65ad\u70b9\u6062\u590d\u72b6\u6001\u5e76\u540c\u6b65\u5bfc\u5165\u6279\u6b21\u8fdb\u5ea6\u3002"""
        from musearc.services.import_runtime import save_resume_state

        save_resume_state(state_file, state)
        repo.update_import_batch_progress(
            state.import_batch_id,
            scanned_files=state.scanned_files,
            imported_tracks=state.imported_tracks,
            duplicate_tracks=state.duplicate_tracks,
            imported_lyrics=state.imported_lyrics,
            matched_lyrics=state.matched_lyrics,
            review_items=state.review_items,
            errors=state.errors,
        )

    def _wait_control(
        self,
        control: ImportControl | None,
        emit,
        current_file: str,
        *,
        on_paused=None,
    ) -> tuple[bool, str]:
        """\u6839\u636e\u6682\u505c/\u53d6\u6d88\u63a7\u5236\u963b\u585e\u6216\u7ec8\u6b62\u5f53\u524d\u5bfc\u5165\u3002"""
        if control is None:
            return False, "keep"
        pause_notified = False
        while control.is_paused():
            if not pause_notified and callable(on_paused):
                try:
                    on_paused()
                except Exception:
                    pass
                pause_notified = True
            emit("paused", current_file, force=True, paused=True)
            control.wait_if_paused(timeout_sec=0.2)
            cancelled, mode, _ = control.snapshot()
            if cancelled:
                return True, mode
        cancelled, mode, _ = control.snapshot()
        return cancelled, mode

    def _handle_cancel(
        self,
        repo: LibraryRepository,
        state: ResumeState,
        state_file: Path,
        start_time: datetime,
        *,
        rollback: bool,
        emit,
    ) -> ImportReport:
        """\u5904\u7406\u5bfc\u5165\u53d6\u6d88\uff0c\u652f\u6301\u4fdd\u7559\u8fdb\u5ea6\u6216\u56de\u6eda\u3002"""
        end_time = _utc_now()
        rollback_applied = False
        resume_available = True
        state.file_states = list(state.file_states or [])

        if rollback:
            self._rollback_partial(repo, state)
            repo.delete_import_batch(state.import_batch_id)
            delete_resume_state(state_file)
            rollback_applied = True
            resume_available = False
        else:
            self._save_state(state_file, repo, state)

        report = ImportReport(
            import_batch_id=state.import_batch_id,
            source_path=state.source_path,
            started_at=start_time,
            finished_at=end_time,
            scanned_files=state.scanned_files,
            imported_tracks=state.imported_tracks,
            duplicate_tracks=state.duplicate_tracks,
            imported_lyrics=state.imported_lyrics,
            matched_lyrics=state.matched_lyrics,
            review_items=state.review_items,
            errors=state.errors,
            cancelled=True,
            rollback_applied=rollback_applied,
            resume_available=resume_available,
            file_states=list(state.file_states or []),
        )
        self._write_manifest(report)
        emit("cancelled", force=True)
        return report

    def _rollback_partial(self, repo: LibraryRepository, state: ResumeState) -> None:
        """\u56de\u6eda\u5f53\u524d\u6279\u6b21\u5df2\u5199\u5165\u7684\u6570\u636e\u5e93\u548c\u6587\u4ef6\u53d8\u66f4\u3002"""
        repo.hard_delete_tracks(state.created_track_ids)
        repo.delete_lyrics_by_ids(state.created_lyrics_ids)
        repo.restore_tracks(state.soft_deleted_existing_ids)
        for rel in set(state.created_storage_relpaths):
            target = self.library_root / rel
            if target.exists() and target.is_file():
                target.unlink(missing_ok=True)

    def _enqueue_review(self, repo: LibraryRepository, item: ReviewItem) -> None:
        """\u5411\u5ba1\u67e5\u961f\u5217\u5199\u5165\u4e00\u6761\u5ba1\u67e5\u9879\u3002"""
        repo.enqueue_review(new_id("rev"), item)

    def _write_manifest(self, report: ImportReport) -> None:
        """\u5199\u5165\u5bfc\u5165\u62a5\u544a\u6e05\u5355\u6587\u4ef6\u3002"""
        manifests = self.library_root / "manifests" / "imports"
        manifests.mkdir(parents=True, exist_ok=True)
        target = manifests / f"{report.import_batch_id}.json"
        target.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
