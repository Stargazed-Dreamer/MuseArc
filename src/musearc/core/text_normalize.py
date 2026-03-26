from __future__ import annotations

import re
import unicodedata

_WEAK_CHARS = {
    "∅": "0",
    "Ø": "0",
}


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    fixed = "".join(_WEAK_CHARS.get(ch, ch) for ch in value)
    fixed = unicodedata.normalize("NFKC", fixed)
    fixed = fixed.casefold()
    fixed = re.sub(r"\[[^\]]*\]", " ", fixed)
    fixed = re.sub(r"\([^)]*\)", " ", fixed)
    fixed = re.sub(r"[^\w\u4e00-\u9fff]+", " ", fixed, flags=re.UNICODE)
    return re.sub(r"\s+", " ", fixed).strip()


def token_set(value: str | None) -> set[str]:
    norm = normalize_text(value)
    if not norm:
        return set()
    return set(norm.split(" "))


def lrc_visible_lines(text: str, max_lines: int = 10) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        cleaned = re.sub(r"\[[^\]]+\]", "", line).strip()
        if cleaned:
            lines.append(cleaned)
        if len(lines) >= max_lines:
            break
    return lines
