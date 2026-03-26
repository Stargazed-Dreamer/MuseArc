from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ReviewStatusCode = Literal["pending", "processing", "archived", "review", "skipped"]


@dataclass(slots=True)
class ImportFileState:
    relpath: str
    file_name: str
    status: str
    status_code: ReviewStatusCode
    reason: str = ""


@dataclass(slots=True)
class ReviewGroupAction:
    group_key: str
    kept_review_ids: list[str] = field(default_factory=list)
    ignored_review_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LyricsSuggestionRow:
    review_id: str
    lyrics_id: str
    lyrics_source: str
    suggested_track_id: str
    score: float
    reason: str

