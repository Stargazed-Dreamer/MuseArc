from __future__ import annotations

from pathlib import Path

from musearc.core.models import ImportCandidate
from musearc.core.text_normalize import normalize_text

AUDIO_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".wma",
    ".ape",
}

LYRICS_EXTENSIONS = {".lrc"}


def scan_import_source(source_root: Path) -> tuple[list[ImportCandidate], list[ImportCandidate]]:
    audio: list[ImportCandidate] = []
    lyrics: list[ImportCandidate] = []

    for path in source_root.rglob("*"):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        stem = normalize_text(path.stem)
        candidate = ImportCandidate(path=path, stem_normalized=stem, ext=ext)
        if ext in AUDIO_EXTENSIONS:
            audio.append(candidate)
        elif ext in LYRICS_EXTENSIONS:
            lyrics.append(candidate)

    return audio, lyrics
