from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class ImportThresholds(BaseModel):
    duplicate_high: float = 0.50
    duplicate_review: float = 0.30
    duplicate_instrumental_hint: float = 0.10
    duplicate_cover_hint: float = 0.01
    lyrics_match_accept: float = 0.82
    lyrics_match_review: float = 0.60
    min_track_duration_sec: float = 45.0


class LmStudioConfig(BaseModel):
    enabled: bool = False
    endpoint: str = "http://127.0.0.1:1234/v1/chat/completions"
    model: str = "local-model"
    timeout_sec: float = 20.0


class UiConfig(BaseModel):
    force_save_threshold: int = 10
    undo_max_actions: int = 50
    button_scale: float = 1.3
    prompt_empty_edit_confirm: bool = True
    enable_logs: bool = False
    db_autosave_minutes: int = 5
    delete_tracks_mode_default: str = "move_linked_lyrics"
    lyrics_cell_action_default: str = "change_mapping"
    player_mode: str = "external"
    external_player_path: str = ""
    fingerprint_workers: int = 0
    duplicate_compare_workers: int = 0
    duplicate_compare_parallel_threshold: int = 48
    lyrics_match_workers: int = 0
    lyrics_match_parallel_threshold: int = 96
    general_worker_limit: int = 0
    fullscan_fp_compare_processes: int = 0


class RuntimeConfig(BaseModel):
    last_library_path: str | None = None
    thresholds: ImportThresholds = Field(default_factory=ImportThresholds)
    lmstudio: LmStudioConfig = Field(default_factory=LmStudioConfig)
    ui: UiConfig = Field(default_factory=UiConfig)
    fingerprint_profile_version: int = 3


class LibraryLayout(BaseModel):
    root: Path

    @property
    def db_path(self) -> Path:
        return self.root / "db" / "musearc.db"

    @property
    def tracks_root(self) -> Path:
        return self.root / "data" / "tracks"

    @property
    def lyrics_root(self) -> Path:
        return self.root / "data" / "lyrics"

    @property
    def imports_root(self) -> Path:
        return self.root / "manifests" / "imports"

    @property
    def exports_root(self) -> Path:
        return self.root / "exports"

    @property
    def trash_root(self) -> Path:
        return self.root / "trash"
