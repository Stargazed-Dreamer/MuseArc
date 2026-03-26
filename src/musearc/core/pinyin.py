from __future__ import annotations

import re


def first_letter(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return "#"

    ch = value[0]
    if "A" <= ch <= "Z":
        return ch
    if "a" <= ch <= "z":
        return ch.upper()

    try:
        from pypinyin import lazy_pinyin  # type: ignore

        py = "".join(lazy_pinyin(ch))
        if py and re.match(r"[a-zA-Z]", py[0]):
            return py[0].upper()
    except Exception:
        pass

    return "#"
