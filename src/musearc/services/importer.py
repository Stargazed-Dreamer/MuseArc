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
    if not isinstance(tags, dict):
        return ""
    for key in keys:
        value = str(tags.get(key, "") or "").strip()
        if value:
            return value
    wanted = {_normalize_tag_key(key) for key in keys if str(key).strip()}
    for key, value in tags.items():
        if _normalize_tag_key(str(key)) in wanted:
            text = str(value or "").strip()
            if text:
                return text
    return ""


def _is_unknown_text(value: str, *, kind: str) -> bool:
    text = str(value or "").strip().casefold()
    if kind == "title":
        return text in {"", "unknown", "unknown title"}
    if kind == "artist":
        return text in {"", "unknown", "unknown artist", "various artists"}
    if kind == "album":
        return text in {"", "unknown", "unknown album"}
    return text == ""


def _parse_title_artist_from_stem(stem: str) -> tuple[str, str]:
    def _artist_likelihood(text: str) -> int:
        value = str(text or "").strip()
        if not value:
            return -2
        low = value.casefold()
        score = 0
        if any(token in low for token in ("feat", "ft.", " ft ", " x ", "&", " with ")):
            score += 2
        if any(token in value for token in ("、", "丨", "/", ";", ",")):
            score += 1
        if len(value) >= 14:
            score += 1
        if low.startswith("dj ") or low.startswith("mc "):
            score += 1
        if any(token in low for token in ("ost", "theme", "op", "ed", "instrumental")):
            score -= 1
        return score

    name = str(stem or "").strip()
    if not name:
        return "Unknown Title", "Unknown Artist"
    for sep in (" - ", " — ", " – ", " _ ", "-", "—", "–"):
        if sep not in name:
            continue
        left, right = name.split(sep, 1)
        left = left.strip()
        right = right.strip()
        if not left or not right:
            continue
        # 文件名约定默认按 artist - title；当右侧更像“艺术家名”时自动反转。
        left_artist_score = _artist_likelihood(left)
        right_artist_score = _artist_likelihood(right)
        if right_artist_score >= left_artist_score + 2:
            return left, right
        return right, left
    return name, "Unknown Artist"


def _derive_title_artist(
    path: Path,
    probe_title: str | None,
    probe_artist: str | None,
    probe_tags: dict[str, str] | None = None,
) -> tuple[str, str]:
    tags = probe_tags or {}
    tag_title = repair_metadata_text(_pick_probe_tag(tags, "title", "TIT2", "\u00a9nam"))
    tag_artist = repair_metadata_text(_pick_probe_tag(tags, "artist", "album_artist", "TPE1", "TPE2", "\u00a9ART"))
    title = repair_metadata_text(probe_title or tag_title or "")
    artist = repair_metadata_text(probe_artist or tag_artist or "")

    if seems_mojibake_text(title):
        title = ""
    if seems_mojibake_text(artist):
        artist = ""

    if not _is_unknown_text(title, kind="title") and not _is_unknown_text(artist, kind="artist"):
        return title, artist

    parsed_title, parsed_artist = _parse_title_artist_from_stem(path.stem)
    if _is_unknown_text(title, kind="title"):
        title = parsed_title
    if _is_unknown_text(artist, kind="artist"):
        artist = parsed_artist

    if _is_unknown_text(title, kind="title"):
        title = path.stem.strip() or "Unknown Title"
    if _is_unknown_text(artist, kind="artist"):
        artist = "Unknown Artist"
    return title, artist


def _quality_score(
    duration_sec: float,
    bit_rate: int | None,
    source_ext: str,
    sample_rate: int | None = None,
    file_size_bytes: int | None = None,
) -> float:
    score = 0.12
    kbps = 0.0
    if bit_rate:
        kbps = max(0.0, float(bit_rate) / 1000.0)
    elif duration_sec > 0 and file_size_bytes and int(file_size_bytes) > 0:
        kbps = max(0.0, (float(file_size_bytes) * 8.0) / 1000.0 / max(1.0, float(duration_sec)))

    if kbps > 0.0:
        score += min(0.18, kbps / 1200.0)
    if sample_rate:
        score += min(0.08, max(0.0, float(sample_rate) - 22050.0) / 88200.0)
    if duration_sec >= 60:
        score += 0.02
    if duration_sec >= 180:
        score += 0.03
    ext = source_ext.lower().strip()
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
    score += format_bonus.get(ext, 0.30)
    if ext in {".flac", ".wav", ".ape", ".alac"} and kbps > 0.0:
        score += min(0.08, kbps / 2500.0)
    if kbps <= 0.0 and ext in {".ogg", ".opus", ".m4a", ".aac"}:
        # VBR 编码常见无稳定比特率字段，降低缺失带来的误判。
        score += 0.04
    return min(1.0, max(0.0, score))


def _copy_file_and_sha256(source: Path, target: Path, chunk_size: int = 1024 * 1024) -> str:
    ensure_parent(target)
    digest = hashlib.sha256()
    with source.open("rb") as src, target.open("wb") as dst:
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
            dst.write(chunk)
    try:
        shutil.copystat(source, target)
    except Exception:
        pass
    return digest.hexdigest()


def _as_json_dict(value: object) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return dict(parsed)
        except Exception:
            return {}
    return {}


def _normalize_track_ext_payload(payload: object) -> dict:
    data = _as_json_dict(payload)
    tags_raw = data.get("tags", {})
    if not isinstance(tags_raw, dict):
        tags_raw = {}
    tags: dict[str, str] = {}
    for key, value in tags_raw.items():
        k = str(key).strip()
        if not k:
            continue
        tags[k] = str(value or "")
    data["tags"] = tags
    return data


def _cover_payload_from_probe(probe: ProbeInfo) -> dict:
    width = _safe_int(probe.cover_width, 0)
    height = _safe_int(probe.cover_height, 0)
    byte_size = _safe_int(probe.cover_bytes, 0)
    if width <= 0 and height <= 0 and byte_size <= 0:
        return {}
    payload: dict[str, int] = {}
    if width > 0:
        payload["width"] = int(width)
    if height > 0:
        payload["height"] = int(height)
    if byte_size > 0:
        payload["bytes"] = int(byte_size)
    return payload


def _build_track_ext_payload(probe: ProbeInfo) -> dict:
    tags: dict[str, str] = {}
    if isinstance(probe.tags, dict):
        for key, value in probe.tags.items():
            k = str(key or "").strip()
            v = str(value or "").strip()
            if not k or not v:
                continue
            tags[k] = v
    payload = {"tags": tags}
    payload["metadata_source"] = "id3" if tags else "filename_fallback"
    cover = _cover_payload_from_probe(probe)
    if cover:
        payload["cover"] = cover
    return payload


def _cover_rank(value: object) -> tuple[int, int, int, int]:
    cover = value if isinstance(value, dict) else {}
    width = max(0, _safe_int(cover.get("width", 0), 0))
    height = max(0, _safe_int(cover.get("height", 0), 0))
    byte_size = max(0, _safe_int(cover.get("bytes", 0), 0))
    area = width * height
    edge = min(width, height) if width > 0 and height > 0 else 0
    has_cover = 1 if area > 0 or byte_size > 0 else 0
    return has_cover, area, edge, byte_size


def _merge_ext_payload_for_duplicate(primary_payload: object, secondary_payload: object) -> dict:
    primary = _normalize_track_ext_payload(primary_payload)
    secondary = _normalize_track_ext_payload(secondary_payload)
    merged = dict(secondary)
    merged.update(primary)

    secondary_tags = secondary.get("tags", {})
    primary_tags = primary.get("tags", {})
    merged_tags: dict[str, str] = {}
    if isinstance(secondary_tags, dict):
        merged_tags.update({str(k): str(v or "") for k, v in secondary_tags.items() if str(k).strip()})
    if isinstance(primary_tags, dict):
        merged_tags.update({str(k): str(v or "") for k, v in primary_tags.items() if str(k).strip()})
    merged["tags"] = merged_tags

    primary_cover = primary.get("cover")
    secondary_cover = secondary.get("cover")
    if _cover_rank(secondary_cover) > _cover_rank(primary_cover):
        best_cover = secondary_cover
        from_secondary = True
    else:
        best_cover = primary_cover
        from_secondary = False
    if isinstance(best_cover, dict) and best_cover:
        merged["cover"] = dict(best_cover)
        merged["cover_selected_from"] = "secondary" if from_secondary else "primary"
    else:
        merged.pop("cover", None)
        merged.pop("cover_selected_from", None)
    return merged


def _extract_lyrics_meta(text: str) -> tuple[str, int, str, str, str]:
    author = ""
    title = ""
    artist = ""
    album = ""

    for line in text.splitlines()[:40]:
        s = line.strip()
        if not s:
            continue
        low = s.casefold()

        def _tag_value(prefix: str) -> str:
            return s[len(prefix) : -1].strip()

        if low.startswith("[by:") and s.endswith("]") and not author:
            author = html.unescape(_tag_value("[by:"))
            continue
        if low.startswith("[ti:") and s.endswith("]") and not title:
            title = html.unescape(_tag_value("[ti:"))
            continue
        if low.startswith("[ar:") and s.endswith("]") and not artist:
            artist = html.unescape(_tag_value("[ar:"))
            continue
        if low.startswith("[al:") and s.endswith("]") and not album:
            album = html.unescape(_tag_value("[al:"))
            continue
        if low.startswith("by:") and not author:
            author = html.unescape(s[3:].strip())

    line_count = len([line for line in text.splitlines() if line.strip()])
    return author, line_count, title, artist, album


def _is_placeholder_empty_lyrics(text: str) -> bool:
    if not text:
        return True
    compact = "".join(ch for ch in text.strip() if not ch.isspace())
    compact = compact.replace("\ufeff", "")
    marker = "[00:00:00]此歌曲为没有填词的纯音乐，请您欣赏"
    marker_compact = "".join(ch for ch in marker if not ch.isspace())
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
    text = str(value or "")
    text = re.sub(r"[\(\[【{（].*?[\)\]】}）]", " ", text)
    return normalize_text(text)


def _lyrics_group_display_name(relpath: str) -> str:
    stem = Path(str(relpath or "")).stem.strip()
    if not stem:
        return "未分组"
    cleaned = re.sub(r"\s*[\(\[（【].*?[\)\]）】]\s*$", "", stem).strip()
    return cleaned or stem


def _name_similarity(a: str, b: str) -> float:
    na = _normalize_name_for_compare(a)
    nb = _normalize_name_for_compare(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    tokens_a = {t for t in na.split() if t}
    tokens_b = {t for t in nb.split() if t}
    if not tokens_a or not tokens_b:
        return 0.0
    inter = len(tokens_a.intersection(tokens_b))
    union = len(tokens_a.union(tokens_b))
    return 0.0 if union <= 0 else float(inter) / float(union)


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _normalize_source_path_key(value: str | Path) -> str:
    try:
        resolved = Path(value).expanduser().resolve()
        text = str(resolved)
    except Exception:
        text = str(value or "")
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
