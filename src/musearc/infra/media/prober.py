from __future__ import annotations

import html
import unicodedata
from pathlib import Path

import av

from musearc.core.models import ProbeInfo

from .commands import MediaCommandError


def _looks_mojibake(text: str) -> bool:
    """
    检测文本是否看起来像乱码（mojibake）。

    参数:
        text (str): 要检测的文本字符串。

    返回值:
        bool: 如果文本看起来像乱码，返回True；否则返回False。
    """
    # 如果文本为空或None，直接返回False
    if not text:
        return False
    # 确保将输入转换为字符串（可能已经是字符串，但为了安全）
    value = str(text)
    # 检查文本中是否包含常见乱码字符（如拉丁扩展字符），如果包含则认为是乱码
    if any(ch in value for ch in {"Ã", "Â", "Ð", "Ñ", "Ê", "Ë", "Ö", "×", "¹", "º", "»", "¼", "½", "¾", "¿"}):
        return True
    # 检查文本中是否至少包含两个特定乱码字符，并且不包含任何CJK字符（中文、日文、韩文字符），如果满足则认为是乱码
    if sum(1 for ch in value if ch in {"¶", "µ", "È", "Ð", "×", "¿", "Ë", "Ê", "Â", "Ã"}) >= 2 and not any(
        0x4E00 <= ord(ch) <= 0x9FFF for ch in value
    ):
        return True
    # 检查文本中是否包含Unicode替换字符（U+FFFD），这通常表示无效或无法显示的字符
    if any(ch == "\ufffd" for ch in value):
        return True
    # 初始化计数器：latin_ext用于统计拉丁扩展字符数量，cjk用于统计CJK字符数量
    latin_ext = 0
    cjk = 0
    # 遍历文本中的每个字符
    for ch in value:
        code = ord(ch)
        # 如果字符是CJK字符（Unicode范围0x4E00-0x9FFF），增加cjk计数并跳过后续检查
        if 0x4E00 <= code <= 0x9FFF:
            cjk += 1
            continue
        # 获取字符的Unicode名称，如果不存在则返回空字符串
        name = unicodedata.name(ch, "")
        # 如果字符名称包含"LATIN"且编码大于0x007F（即不在基本拉丁字符集），则认为是拉丁扩展字符
        if "LATIN" in name and code > 0x007F:
            latin_ext += 1
    # 最终判断：如果拉丁扩展字符数量大于等于阈值（最大为4或文本长度的1/4）且没有CJK字符，则认为是乱码
    return latin_ext >= max(4, len(value) // 4) and cjk == 0


def _text_score(text: str) -> int:
    """计算文本的质量评分。

    功能：评估输入文本的质量，根据字符类型和乱码情况给出数值评分。
    参数：text (str) - 需要评估的文本字符串。
    返回值：int - 文本的评分，分数越高表示质量越好。
    """
    score = 0  # 初始化评分为0
    for ch in text:
        code = ord(ch)  # 获取字符的Unicode码点
        # 基础字符（空白、字母数字、常见标点）加1分
        if ch.isspace() or ch.isalnum() or ch in "-_()[]{}.,!?/&'\"":
            score += 1
        # CJK统一表意文字、日文假名、韩文音节加2分
        if 0x4E00 <= code <= 0x9FFF or 0x3040 <= code <= 0x30FF or 0xAC00 <= code <= 0xD7AF:
            score += 2
        # Unicode替换字符（U+FFFD）减4分，通常表示无效字符
        if ch == "\ufffd":
            score -= 4
    # 如果文本疑似乱码，则扣分（扣分值为2与文本长度1/5的较大者）
    if _looks_mojibake(text):
        score -= max(2, len(text) // 5)
    return score  # 返回最终评分


def _repair_mojibake(text: str) -> str:
    """修复可能因编码错误（Mojibake）而损坏的文本字符串。

    此函数尝试通过多种字符编码来解码输入文本，以修复因错误编码导致的乱码。
    它会先尝试用 UTF-8 解码，并检查一些典型的乱码特征字符（如 "Ã"、"Â"）。
    如果 UTF-8 修复失败或效果不佳，则会遍历一系列常见的亚洲编码（如 GB18030、GBK 等），
    并使用一个评分函数 (`_text_score`) 来选择最佳修复结果。

    Args:
        text (str): 可能包含编码错误的原始输入字符串。

    Returns:
        str: 修复后的字符串。如果无法修复或输入无效，则返回清理后的原始字符串。
    """
    # 将输入转换为字符串，去除空字符和首尾空白
    value = str(text or "").replace("\x00", "").strip()
    # 如果清理后字符串为空，直接返回空字符串
    if not value:
        return ""
    # 尝试将字符串按 latin-1 编码为字节序列，这是后续修复步骤的基础
    try:
        raw = value.encode("latin1")
    except Exception:
        # 如果编码失败（通常意味着输入不是有效的 latin-1 字符），返回原始清理后的字符串
        return value
    # 检查原始字符串是否包含某些典型的乱码特征字符（可能是 UTF-8 文本被错误解码为 latin-1 的结果）
    if any(ch in value for ch in {"Ã", "Â"}):
        try:
            # 尝试用 UTF-8 解码上述字节序列
            utf8_fixed = raw.decode("utf-8")
            # 检查解码结果是否有效（不为空）且不包含 Unicode 替换字符（U+FFFD）
            if utf8_fixed and "\ufffd" not in utf8_fixed:
                # 如果 UTF-8 解码成功且结果良好，将其作为首选修复结果返回
                return utf8_fixed
        except Exception:
            # 如果 UTF-8 解码失败，忽略错误，继续尝试其他编码
            pass
    # 初始化最佳修复结果和最高评分为原始清理后的值和其评分
    best = value
    best_score = _text_score(value)
    # 遍历一系列常见的字符编码进行尝试修复
    for enc in ("gb18030", "gbk", "big5", "cp932", "shift_jis", "utf-8"):
        try:
            # 尝试用当前编码解码字节序列
            decoded = raw.decode(enc)
        except Exception:
            # 如果当前编码解码失败，跳过此编码，继续下一个
            continue
        # 计算当前解码结果的文本评分
        score = _text_score(decoded)
        # 如果当前解码结果的评分高于之前的最高分，则更新最佳结果
        if score > best_score:
            best = decoded
            best_score = score
    # 返回最终的修复结果（可能是原始值或某种编码解码后的值）
    return best


def _clean_tag_value(value: object) -> str:
    """
    清理标签值，将输入值转换为字符串并处理HTML转义和特殊字符。

    参数：
        value (object): 需要清理的值。

    返回值：
        str: 清理后的字符串。如果输入为空或无效，返回空字符串。
    """
    # 将value转换为字符串，如果value为None或False则使用空字符串，然后处理HTML转义，移除null字符，并去除首尾空白
    text = html.unescape(str(value or "")).replace("\x00", "").strip()
    # 如果文本为空，直接返回空字符串
    if not text:
        return ""
    # 调用修复编码问题的函数处理文本
    return _repair_mojibake(text)


def repair_metadata_text(value: object) -> str:
    """Normalize and repair possible mojibake text for metadata fields."""
    return _clean_tag_value(value)


def seems_mojibake_text(value: object) -> bool:
    return _looks_mojibake(str(value or ""))


def _normalize_tag_key(key: str) -> str:
    return str(key or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")


def _pick_tag(tags: dict[str, str], *keys: str) -> str | None:
    """
    功能：从给定的标签字典中查找指定键的值，返回第一个匹配且非空的值，考虑键名的规范化。
    参数：
        tags: 一个字典，键和值都是字符串。
        *keys: 可变数量的字符串键，用于查找。
    返回值：
        字符串或None。如果找到匹配的值，则返回该值的字符串形式（去除前后空白），否则返回None。
    """
    # 如果tags字典为空，直接返回None
    if not tags:
        return None
    # 遍历所有指定的键
    for key in keys:
        # 检查键是否在字典中，并且对应的值非空（去除空白后）
        if key in tags and str(tags.get(key, "")).strip():
            # 返回值的字符串形式，去除前后空白
            return str(tags[key]).strip()
    # 创建规范化键的集合，只包含非空键
    wanted = {_normalize_tag_key(k) for k in keys if str(k).strip()}
    # 如果没有有效的规范化键，返回None
    if not wanted:
        return None
    # 遍历字典的所有键值对
    for key, value in tags.items():
        # 检查键的规范化形式是否在wanted集合中，并且值非空
        if _normalize_tag_key(key) in wanted and str(value or "").strip():
            # 返回值的字符串形式，去除前后空白
            return str(value).strip()
    # 如果没有找到匹配的值，返回None
    return None


class MediaProbe:
    def probe(self, path: Path) -> ProbeInfo:
        """探测音频文件的基本信息并返回结构化的探测结果。

        该方法使用AV库打开音频文件，提取音频流、元数据、时长等信息，
        并尝试读取封面图片数据，最终将所有信息打包成ProbeInfo对象返回。

        参数:
            path (Path): 音频文件路径

        返回:
            ProbeInfo: 包含音频文件探测结果的对象，包括编码格式、时长、采样率、
                      声道数、比特率、标题、艺术家、专辑、格式名称、封面尺寸、
                      封面字节数以及原始标签信息
        """
        try:
            # 使用AV库打开音频文件容器
            with av.open(str(path)) as container:
                audio_stream = None
                # 遍历容器中的所有流，查找音频流
                for stream in container.streams:
                    if stream.type == "audio":
                        audio_stream = stream
                        break
                # 如果没有找到音频流，则抛出错误
                if audio_stream is None:
                    raise MediaCommandError("no_audio_stream")

                # 初始化标签字典，用于存储合并后的元数据
                tags: dict[str, str] = {}
                # 合并容器和音频流的元数据标签
                for source in (container.metadata or {}, audio_stream.metadata or {}):
                    for raw_key, raw_value in source.items():
                        # 清理标签键：转换为字符串并去除首尾空白
                        key = str(raw_key or "").strip()
                        if not key:
                            continue
                        # 清理标签值
                        value = _clean_tag_value(raw_value)
                        if not value:
                            continue
                        # 仅当键不存在或现有值为空时才添加新标签
                        if key not in tags or not str(tags.get(key, "")).strip():
                            tags[key] = value

                # 计算音频时长（秒）
                duration_sec = 0.0
                # 优先使用音频流的时长和时间基准计算
                if audio_stream.duration is not None and audio_stream.time_base is not None:
                    duration_sec = float(audio_stream.duration * audio_stream.time_base)
                # 如果音频流时长不可用，则使用容器的总时长
                elif container.duration is not None:
                    duration_sec = float(container.duration / av.time_base)

                # 封面图片相关变量初始化
                cover_width = None
                cover_height = None
                cover_bytes = None
                # 遍历容器中的所有流，查找视频流（通常是封面图片）
                for stream in container.streams:
                    if stream.type != "video":
                        continue
                    # 检查流是否为附加图片（封面）
                    try:
                        attached = bool(getattr(stream.disposition, "attached_pic", False))
                    except Exception:
                        attached = False
                    if not attached:
                        continue
                    # 获取封面尺寸信息
                    cover_width = stream.codec_context.width or None
                    cover_height = stream.codec_context.height or None
                    # 解码封面图片数据并计算字节数
                    try:
                        for packet in container.demux(stream):
                            frames = packet.decode()
                            if not frames:
                                continue
                            frame = frames[0]
                            # 将帧转换为RGB24格式的NumPy数组
                            array = frame.to_ndarray(format="rgb24")
                            cover_bytes = int(array.nbytes)
                            break
                    except Exception:
                        cover_bytes = None
                    break

                # 从标签中提取常用字段：标题、艺术家、专辑
                # 使用_pick_tag函数处理不同格式的标签键
                title = _pick_tag(tags, "title", "TIT2", "\u00a9nam")
                artist = _pick_tag(tags, "artist", "album_artist", "TPE1", "TPE2", "\u00a9ART")
                album = _pick_tag(tags, "album", "TALB", "\u00a9alb")

                # 构建并返回ProbeInfo对象，包含所有探测到的信息
                return ProbeInfo(
                    source_path=path,
                    codec=audio_stream.codec_context.name,
                    duration_sec=duration_sec,
                    sample_rate=audio_stream.codec_context.sample_rate,
                    channels=audio_stream.codec_context.channels,
                    bit_rate=audio_stream.bit_rate or container.bit_rate,
                    title=title,
                    artist=artist,
                    album=album,
                    format_name=container.format.name if container.format else None,
                    cover_width=cover_width,
                    cover_height=cover_height,
                    cover_bytes=cover_bytes,
                    tags=tags,
                )
        except MediaCommandError:
            # 重新抛出MediaCommandError异常
            raise
        except Exception as exc:  # pragma: no cover - backend specific
            # 捕获所有其他异常，转换为MediaCommandError并附带原始异常信息
            raise MediaCommandError(f"probe_failed:{path}:{exc}") from exc
