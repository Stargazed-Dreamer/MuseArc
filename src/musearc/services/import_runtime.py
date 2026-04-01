from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_int(value, default: int = 0) -> int:
    if isinstance(value, (list, tuple, set, dict)):
        return default
    try:
        return int(value or 0)
    except Exception:
        return default


class ImportControl:
    def __init__(self):
        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._cancel_requested = False
        self._cancel_mode = "keep"

    def request_cancel(self, mode: str) -> None:
        normalized = mode if mode in {"keep", "rollback"} else "keep"
        with self._lock:
            self._cancel_requested = True
            self._cancel_mode = normalized
        self._pause_event.set()

    def request_pause(self) -> None:
        self._pause_event.clear()

    def request_resume(self) -> None:
        self._pause_event.set()

    def wait_if_paused(self, timeout_sec: float = 0.2) -> bool:
        return self._pause_event.wait(timeout=timeout_sec)

    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def snapshot(self) -> tuple[bool, str, bool]:
        with self._lock:
            return self._cancel_requested, self._cancel_mode, self.is_paused()


@dataclass(slots=True)
class ResumeState:
    version: int
    import_batch_id: str
    source_path: str
    started_at: str
    scanned_files: int
    processed_files: int
    processed_relpaths: list[str] = field(default_factory=list)
    imported_tracks: int = 0
    duplicate_tracks: int = 0
    imported_lyrics: int = 0
    matched_lyrics: int = 0
    review_items: int = 0
    errors: list[str] = field(default_factory=list)
    file_states: list[dict] = field(default_factory=list)
    created_track_ids: list[str] = field(default_factory=list)
    created_lyrics_ids: list[str] = field(default_factory=list)
    created_storage_relpaths: list[str] = field(default_factory=list)
    soft_deleted_existing_ids: list[str] = field(default_factory=list)


def _resume_dir(library_root: Path) -> Path:
    return library_root / "manifests" / "imports" / "resume"


def _source_key(source_path: Path) -> str:
    import hashlib

    text = str(source_path.resolve()).lower()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def resume_state_path(library_root: Path, source_path: Path) -> Path:
    return _resume_dir(library_root) / f"resume_{_source_key(source_path)}.json"


def save_resume_state(path: Path, state: ResumeState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": state.version,
        "import_batch_id": state.import_batch_id,
        "source_path": state.source_path,
        "started_at": state.started_at,
        "scanned_files": state.scanned_files,
        "processed_files": state.processed_files,
        "processed_relpaths": state.processed_relpaths,
        "imported_tracks": state.imported_tracks,
        "duplicate_tracks": state.duplicate_tracks,
        "imported_lyrics": state.imported_lyrics,
        "matched_lyrics": state.matched_lyrics,
        "review_items": state.review_items,
        "errors": state.errors,
        "file_states": state.file_states,
        "created_track_ids": state.created_track_ids,
        "created_lyrics_ids": state.created_lyrics_ids,
        "created_storage_relpaths": state.created_storage_relpaths,
        "soft_deleted_existing_ids": state.soft_deleted_existing_ids,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def load_resume_state(path: Path) -> ResumeState | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ResumeState(
        version=_safe_int(payload.get("version", 1), 1),
        import_batch_id=str(payload["import_batch_id"]),
        source_path=str(payload["source_path"]),
        started_at=str(payload["started_at"]),
        scanned_files=_safe_int(payload.get("scanned_files", 0), 0),
        processed_files=_safe_int(payload.get("processed_files", 0), 0),
        processed_relpaths=list(payload.get("processed_relpaths", [])),
        imported_tracks=_safe_int(payload.get("imported_tracks", 0), 0),
        duplicate_tracks=_safe_int(payload.get("duplicate_tracks", 0), 0),
        imported_lyrics=_safe_int(payload.get("imported_lyrics", 0), 0),
        matched_lyrics=_safe_int(payload.get("matched_lyrics", 0), 0),
        review_items=_safe_int(payload.get("review_items", 0), 0),
        errors=list(payload.get("errors", [])),
        file_states=list(payload.get("file_states", [])),
        created_track_ids=list(payload.get("created_track_ids", [])),
        created_lyrics_ids=list(payload.get("created_lyrics_ids", [])),
        created_storage_relpaths=list(payload.get("created_storage_relpaths", [])),
        soft_deleted_existing_ids=list(payload.get("soft_deleted_existing_ids", [])),
    )


def delete_resume_state(path: Path) -> None:
    if path.exists():
        path.unlink()


def list_resume_states(library_root: Path) -> list[dict]:
    folder = _resume_dir(library_root)
    if not folder.exists():
        return []
    rows: list[dict] = []
    for file in sorted(folder.glob("resume_*.json")):
        state = load_resume_state(file)
        if not state:
            continue
        rows.append(
            {
                "file": str(file),
                "import_batch_id": state.import_batch_id,
                "source_path": state.source_path,
                "started_at": state.started_at,
                "scanned_files": state.scanned_files,
                "processed_files": state.processed_files,
            }
        )
    return rows
