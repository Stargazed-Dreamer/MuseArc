from __future__ import annotations

"""Application facade entry."""

import json
from pathlib import Path


from musearc.app.action_log import append_action_log
from musearc.core.ids import new_id
from musearc.services.library import open_or_create_library
from musearc.app.facade_mixins_import_export import FacadeImportExportMixin
from musearc.app.facade_mixins_library import FacadeLibraryMixin
from musearc.app.facade_mixins_runtime import FacadeRuntimeMixin

FAVORITES_PLAYLIST_ID = "pl_favorites"


class MuseArcFacade(FacadeImportExportMixin, FacadeLibraryMixin, FacadeRuntimeMixin):
    """Facade mixin / facade class."""

    def __init__(self, library_path: str | None = None):
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1a__init__\u3002"""
        self.ctx = open_or_create_library(library_path)
        self._redo_actions: list[dict] = []

    @property
    def library_root(self) -> Path:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alibrary_root\u3002"""
        return self.ctx.layout.root

    def _undo_keep(self) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1a_undo_keep\u3002"""
        return max(1, int(self.ctx.runtime_config.ui.undo_max_actions))

    def _append_undo(self, repo, action_type: str, payload: dict) -> None:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1a_append_undo\u3002"""
        repo.append_undo_action(new_id("undo"), action_type, payload, self._undo_keep())
        # Any new operation invalidates redo branch.
        self._redo_actions.clear()

    def _log(self, message: str, level: str = "info") -> None:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1a_log\u3002"""
        cfg = self.ctx.runtime_config
        append_action_log(
            self.ctx.layout.root,
            enabled=bool(cfg.ui.enable_logs),
            message=message,
            level=level,
            keep=10,
        )

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1a_safe_int\u3002"""
        if isinstance(value, (list, tuple, dict, set)):
            return default
        try:
            return int(value or 0)
        except Exception:
            return default

    @staticmethod
    def _safe_nonneg_int(value, default: int = 0) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1a_safe_nonneg_int\u3002"""
        return max(0, MuseArcFacade._safe_int(value, default))

    def _stats_state_path(self) -> Path:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1a_stats_state_path\u3002"""
        base = self.ctx.layout.root / "manifests"
        base.mkdir(parents=True, exist_ok=True)
        return base / "stats_import_state.json"

    def _load_stats_state(self) -> dict:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1a_load_stats_state\u3002"""
        path = self._stats_state_path()
        if not path.exists():
            return {"history": [], "contributions": {}, "playlist_import_history": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"history": [], "contributions": {}, "playlist_import_history": []}
        if not isinstance(payload, dict):
            return {"history": [], "contributions": {}, "playlist_import_history": []}
        history = payload.get("history")
        contributions = payload.get("contributions")
        playlist_import_history = payload.get("playlist_import_history")
        if not isinstance(history, list):
            history = []
        if not isinstance(contributions, dict):
            contributions = {}
        if not isinstance(playlist_import_history, list):
            playlist_import_history = []
        return {
            "history": history,
            "contributions": contributions,
            "playlist_import_history": playlist_import_history,
        }

    def _save_stats_state(self, payload: dict) -> None:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1a_save_stats_state\u3002"""
        path = self._stats_state_path()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _compute_love_score(
        *,
        play_count: int,
        manual_play_count: int,
        play_seconds: int,
        early_skip_count: int,
        total_play_count_all: int,
        duration_sec: float,
    ) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1a_compute_love_score\u3002"""
        a = max(0, int(play_count))
        b = max(0, int(manual_play_count))
        c = max(0, int(play_seconds))
        d = max(0, int(early_skip_count))
        e = max(1, int(total_play_count_all))
        f = max(1.0, float(duration_sec or 0.0))
        a_safe = max(1, a)

        # 平均播放比例 0.4
        t1 = float(c) / f / float(a_safe)
        # 主动播放比例 0.5
        t2 = float(b) / float(a_safe)
        # 全库播放占比 0.1
        t3 = float(a) / float(e)
        # 跳过比例 -1
        t4 = float(d) / float(a_safe)
        t = 0.1 * t3 + 0.4 * t1 + 0.5 * t2 - t4
        return max(-100, min(100, int(round(t * 100.0))))
