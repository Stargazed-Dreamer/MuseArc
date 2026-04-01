from __future__ import annotations

import html
import unicodedata
from pathlib import Path

import av

from musearc.core.models import ProbeInfo

from .commands import MediaCommandError


def _looks_mojibake(text: str) -> bool:
    if not text:
        return False
    value = str(text)
    if any(ch in value for ch in {"Ã", "Â", "Ð", "Ñ", "Ê", "Ë", "Ö", "×", "¹", "º", "»", "¼", "½", "¾", "¿"}):
        return True
    if sum(1 for ch in value if ch in {"¶", "µ", "È", "Ð", "×", "¿", "Ë", "Ê", "Â", "Ã"}) >= 2 and not any(
        0x4E00 <= ord(ch) <= 0x9FFF for ch in value
    ):
        return True
    if any(ch == "\ufffd" for ch in value):
        return True
    latin_ext = 0
    cjk = 0
    for ch in value:
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF:
            cjk += 1
            continue
        name = unicodedata.name(ch, "")
        if "LATIN" in name and code > 0x007F:
            latin_ext += 1
    return latin_ext >= max(4, len(value) // 4) and cjk == 0


def _text_score(text: str) -> int:
    score = 0
    for ch in text:
        code = ord(ch)
        if ch.isspace() or ch.isalnum() or ch in "-_()[]{}.,!?/&'\"":
            score += 1
        if 0x4E00 <= code <= 0x9FFF or 0x3040 <= code <= 0x30FF or 0xAC00 <= code <= 0xD7AF:
            score += 2
        if ch == "\ufffd":
            score -= 4
    if _looks_mojibake(text):
        score -= max(2, len(text) // 5)
    return score


def _repair_mojibake(text: str) -> str:
    value = str(text or "").replace("\x00", "").strip()
    if not value:
        return ""
    try:
        raw = value.encode("latin1")
    except Exception:
        return value
    if any(ch in value for ch in {"Ã", "Â"}):
        try:
            utf8_fixed = raw.decode("utf-8")
            if utf8_fixed and "\ufffd" not in utf8_fixed:
                return utf8_fixed
        except Exception:
            pass
    best = value
    best_score = _text_score(value)
    for enc in ("gb18030", "gbk", "big5", "cp932", "shift_jis", "utf-8"):
        try:
            decoded = raw.decode(enc)
        except Exception:
            continue
        score = _text_score(decoded)
        if score > best_score:
            best = decoded
            best_score = score
    return best


def _clean_tag_value(value: object) -> str:
    text = html.unescape(str(value or "")).replace("\x00", "").strip()
    if not text:
        return ""
    return _repair_mojibake(text)


def repair_metadata_text(value: object) -> str:
    """Normalize and repair possible mojibake text for metadata fields."""
    return _clean_tag_value(value)


def seems_mojibake_text(value: object) -> bool:
    return _looks_mojibake(str(value or ""))


def _normalize_tag_key(key: str) -> str:
    return str(key or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")


def _pick_tag(tags: dict[str, str], *keys: str) -> str | None:
    if not tags:
        return None
    for key in keys:
        if key in tags and str(tags.get(key, "")).strip():
            return str(tags[key]).strip()
    wanted = {_normalize_tag_key(k) for k in keys if str(k).strip()}
    if not wanted:
        return None
    for key, value in tags.items():
        if _normalize_tag_key(key) in wanted and str(value or "").strip():
            return str(value).strip()
    return None


class MediaProbe:
    def probe(self, path: Path) -> ProbeInfo:
        try:
            with av.open(str(path)) as container:
                audio_stream = None
                for stream in container.streams:
                    if stream.type == "audio":
                        audio_stream = stream
                        break
                if audio_stream is None:
                    raise MediaCommandError("no_audio_stream")

                tags: dict[str, str] = {}
                for source in (container.metadata or {}, audio_stream.metadata or {}):
                    for raw_key, raw_value in source.items():
                        key = str(raw_key or "").strip()
                        if not key:
                            continue
                        value = _clean_tag_value(raw_value)
                        if not value:
                            continue
                        if key not in tags or not str(tags.get(key, "")).strip():
                            tags[key] = value

                duration_sec = 0.0
                if audio_stream.duration is not None and audio_stream.time_base is not None:
                    duration_sec = float(audio_stream.duration * audio_stream.time_base)
                elif container.duration is not None:
                    duration_sec = float(container.duration / av.time_base)

                cover_width = None
                cover_height = None
                cover_bytes = None
                for stream in container.streams:
                    if stream.type != "video":
                        continue
                    try:
                        attached = bool(getattr(stream.disposition, "attached_pic", False))
                    except Exception:
                        attached = False
                    if not attached:
                        continue
                    cover_width = stream.codec_context.width or None
                    cover_height = stream.codec_context.height or None
                    try:
                        for packet in container.demux(stream):
                            frames = packet.decode()
                            if not frames:
                                continue
                            frame = frames[0]
                            array = frame.to_ndarray(format="rgb24")
                            cover_bytes = int(array.nbytes)
                            break
                    except Exception:
                        cover_bytes = None
                    break

                title = _pick_tag(tags, "title", "TIT2", "\u00a9nam")
                artist = _pick_tag(tags, "artist", "album_artist", "TPE1", "TPE2", "\u00a9ART")
                album = _pick_tag(tags, "album", "TALB", "\u00a9alb")

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
            raise
        except Exception as exc:  # pragma: no cover - backend specific
            raise MediaCommandError(f"probe_failed:{path}:{exc}") from exc
