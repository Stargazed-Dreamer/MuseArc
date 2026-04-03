from __future__ import annotations

from pathlib import Path


def write_basic_metadata_tags(
    path: Path,
    *,
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
) -> tuple[bool, str]:
    """Best-effort metadata writeback for common audio containers.

    Returns:
      (ok, reason)
    """
    target = Path(path)
    if not target.exists():
        return False, "file_missing"
    try:
        from mutagen import File as MutagenFile  # type: ignore
    except Exception:
        return False, "mutagen_unavailable"

    try:
        audio = MutagenFile(str(target), easy=True)
    except Exception as exc:
        return False, f"open_failed:{exc}"
    if audio is None:
        return False, "unsupported_format"
    if getattr(audio, "tags", None) is None:
        try:
            audio.add_tags()
        except Exception:
            pass

    def _set_or_delete(key: str, value: str | None) -> None:
        text = str(value or "").strip()
        if text:
            audio[key] = [text]
        else:
            try:
                del audio[key]
            except Exception:
                pass

    try:
        _set_or_delete("title", title)
        _set_or_delete("artist", artist)
        _set_or_delete("album", album)
        audio.save()
    except Exception as exc:
        return False, f"save_failed:{exc}"
    return True, "ok"
