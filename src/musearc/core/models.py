from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .enums import DuplicateDecision, FileHealth, ReviewKind, TrackKind


@dataclass(slots=True)
class ProbeInfo:
    source_path: Path
    codec: str | None
    duration_sec: float
    sample_rate: int | None
    channels: int | None
    bit_rate: int | None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    format_name: str | None = None
    cover_width: int | None = None
    cover_height: int | None = None
    cover_bytes: int | None = None


@dataclass(slots=True)
class ImportCandidate:
    path: Path
    stem_normalized: str
    ext: str


@dataclass(slots=True)
class Fingerprint:
    version: int
    vector: list[int]
    digest: str


@dataclass(slots=True)
class TrackInsert:
    track_id: str
    file_name: str
    title: str
    artist: str
    album: str
    language_kind: str
    preference_level: int
    storage_format: str
    kind: TrackKind
    duration_sec: float
    sample_rate: int | None
    channels: int | None
    bit_rate: int | None
    quality_score: float
    storage_relpath: str
    source_relpath: str
    source_fullpath: str
    source_sha256: str
    source_ext: str
    probe_codec: str | None
    file_health: FileHealth
    fingerprint_version: int
    fingerprint_digest: str
    fingerprint_payload: str
    imported_at: datetime
    ext_json: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LyricsInsert:
    lyrics_id: str
    source_relpath: str
    storage_relpath: str
    text_hash: str
    raw_encoding: str
    imported_at: datetime
    lyrics_title: str = ""
    lyrics_artist: str = ""
    lyrics_album: str = ""
    lyrics_author: str = ""
    line_count: int = 0


@dataclass(slots=True)
class DuplicateCandidate:
    track_id: str
    title: str
    artist: str
    duration_sec: float
    quality_score: float
    fingerprint_payload: str


@dataclass(slots=True)
class DuplicateDecisionResult:
    decision: DuplicateDecision
    score: float
    reason: str
    existing_track_id: str | None = None


@dataclass(slots=True)
class ReviewItem:
    kind: ReviewKind
    title: str
    payload: dict[str, Any]
    priority: int = 2


@dataclass(slots=True)
class LyricsMatchDecision:
    track_id: str | None
    score: float
    reason: str
    needs_review: bool


@dataclass(slots=True)
class ImportProgress:
    import_batch_id: str
    source_path: str
    stage: str
    current_file: str
    scanned_files: int
    processed_files: int
    imported_tracks: int
    duplicate_tracks: int
    imported_lyrics: int
    matched_lyrics: int
    review_items: int
    errors: int
    resumed: bool = False
    paused: bool = False
    file_states: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ImportReport:
    import_batch_id: str
    source_path: str
    started_at: datetime
    finished_at: datetime
    scanned_files: int
    imported_tracks: int
    duplicate_tracks: int
    imported_lyrics: int
    matched_lyrics: int
    review_items: int
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    rollback_applied: bool = False
    resume_available: bool = False
    file_states: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["started_at"] = self.started_at.isoformat()
        payload["finished_at"] = self.finished_at.isoformat()
        return payload


@dataclass(slots=True)
class UndoAction:
    action_id: str
    action_type: str
    payload: dict[str, Any]
    created_at: str
