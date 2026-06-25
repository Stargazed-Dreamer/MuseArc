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
    """功能：释放文件操作相关的播放器。
参数：
    parent (QWidget): 父部件，用于获取顶层窗口。
返回值：
    None
"""
    top = parent.window()  # 获取父部件的顶层窗口
    handler = getattr(top, "release_player_for_file_ops", None)  # 从顶层窗口安全获取属性，如果不存在则返回None
    if callable(handler):  # 检查handler是否可调用
        handler()  # 调用释放播放器的方法
