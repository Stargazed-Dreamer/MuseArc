from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget


def _queue_play_tracks(parent: QWidget, tracks: list[dict], *, start_track_id: str | None = None) -> bool:
    """??????????????"""
    top = parent.window()
    handler = getattr(top, "queue_and_play_tracks", None)
    if callable(handler):
        return bool(handler(list(tracks), start_track_id=start_track_id))
    QMessageBox.information(parent, "??", "?????????????")
    return False


def _release_player_for_file_ops(parent: QWidget) -> None:
    top = parent.window()
    handler = getattr(top, "release_player_for_file_ops", None)
    if callable(handler):
        handler()
