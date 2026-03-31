from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QInputDialog, QLineEdit, QMenu, QMessageBox, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from musearc.app.facade import FAVORITES_PLAYLIST_ID, MuseArcFacade
from musearc.ui.track_grid import TrackGridWidget, _copy_selected_cells
from musearc.ui.main_window_helpers import (
    _apply_button_scale,
    _choose_or_create_playlist,
    _prompt_new_playlist,
    _resolve_delete_mode_and_maybe_save_default,
    _reveal_in_file_manager,
    _run_export_dialog,
    _show_track_details,
    _storage_path_for_track_row,
)
from musearc.ui.main_window_pages_common import _queue_play_tracks


# ?????
# 1) TracksPage ??????????????
# 2) PlaylistPage ????? + ????????
# 3) ???? TrackGridWidget?????????????????

class TracksPage(QWidget):
    library_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade
        self.all_rows: list[dict] = []

        root = QVBoxLayout(self)

        row1 = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索标题 / 艺人 / 专辑 / 文件名 / 路径")
        self.btn_search = QPushButton("搜索")
        row1.addWidget(self.search_input, 1)
        row1.addWidget(self.btn_search)

        row2 = QHBoxLayout()
        self.btn_play = QPushButton("播放")
        self.btn_export = QPushButton("导出选中")
        self.btn_add_playlist = QPushButton("加到歌单")
        self.btn_favorite = QPushButton("收藏")
        self.btn_unfavorite = QPushButton("取消收藏")
        self.btn_delete = QPushButton("从音乐库中删除")
        self.btn_delete.setStyleSheet("background-color:#b3261e;color:white;")
        for btn in [
            self.btn_play,
            self.btn_export,
            self.btn_add_playlist,
            self.btn_favorite,
            self.btn_unfavorite,
            self.btn_delete,
        ]:
            row2.addWidget(btn)
        row2.addStretch(1)

        self.grid = TrackGridWidget(self.facade)

        root.addLayout(row1)
        root.addLayout(row2)
        root.addWidget(self.grid, 1)

        self.btn_search.clicked.connect(self.apply_search_filter)
        self.search_input.returnPressed.connect(self.apply_search_filter)
        self.btn_play.clicked.connect(self.on_play)
        self.btn_export.clicked.connect(self.on_export)
        self.btn_add_playlist.clicked.connect(self.on_add_to_playlist)
        self.btn_favorite.clicked.connect(self.on_favorite)
        self.btn_unfavorite.clicked.connect(self.on_unfavorite)
        self.btn_delete.clicked.connect(self.on_delete)
        self.grid.track_field_edited.connect(self.on_track_field_edited)
        self.grid.context_menu_requested.connect(self._show_context_menu)

        self.reload_tracks_from_db()

    def apply_button_scale(self, scale: float) -> None:
        for btn in [
            self.btn_search,
            self.btn_play,
            self.btn_export,
            self.btn_add_playlist,
            self.btn_favorite,
            self.btn_unfavorite,
            self.btn_delete,
        ]:
            _apply_button_scale(btn, scale)
        self.grid.set_button_scale(scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade
        self.grid.set_facade(facade)
        self.reload_tracks_from_db()

    def refresh_page(self) -> None:
        self.reload_tracks_from_db()

    def reload_tracks_from_db(self) -> None:
        self.all_rows = self.facade.list_tracks(limit=2_000_000)
        self.apply_search_filter()

    def apply_search_filter(self) -> None:
        query = self.search_input.text().strip().casefold()
        if not query:
            rows = list(self.all_rows)
        else:
            rows = []
            for row in self.all_rows:
                text = " | ".join(
                    [
                        str(row.get("file_name", "")),
                        str(row.get("title", "")),
                        str(row.get("artist", "")),
                        str(row.get("album", "")),
                        str(row.get("source_relpath", "")),
                        str(row.get("source_fullpath", "")),
                        str(row.get("storage_relpath", "")),
                    ]
                ).casefold()
                if query in text:
                    rows.append(row)
        self.grid.set_tracks(rows)
        self.grid.set_status(f"已加载 {len(rows)} 条（源数据 {len(self.all_rows)} 条）")

    def selected_track_ids(self) -> list[str]:
        return self.grid.selected_track_ids()

    def _export_track_ids(self, track_ids: list[str], tracks: list[dict] | None = None) -> None:
        if not track_ids:
            QMessageBox.warning(self, "导出", "请先选择歌曲")
            return
        track_rows = list(tracks or [])
        if not track_rows:
            id_set = set(track_ids)
            track_rows = [row for row in self.all_rows if str(row.get("track_id", "")) in id_set]
        ok, target = _run_export_dialog(self, self.facade, track_rows, playlist_name="全部歌曲")
        if not ok:
            return
        self.grid.set_status(f"已导出 {len(track_ids)} 条到 {target}")

    def on_export(self) -> None:
        tracks = self.grid.selected_tracks()
        self._export_track_ids(
            [str(t.get("track_id", "")) for t in tracks if t.get("track_id")],
            tracks,
        )

    def on_play(self) -> None:
        tracks = self.grid.selected_tracks()
        if not tracks:
            return
        _queue_play_tracks(self, tracks)

    def _delete_track_ids(self, track_ids: list[str]) -> None:
        if not track_ids:
            return
        mode = _resolve_delete_mode_and_maybe_save_default(self, self.facade, len(track_ids))
        if mode == "cancel":
            return
        count = self.facade.delete_tracks(track_ids, mode=mode)
        self.grid.set_status(f"已移到回收站 {count} 条")
        self.reload_tracks_from_db()
        self.library_changed.emit()

    def on_delete(self) -> None:
        self._delete_track_ids(self.selected_track_ids())

    def _add_track_ids_to_playlist(self, track_ids: list[str], playlist_id: str) -> None:
        if not track_ids or not playlist_id:
            return
        count = self.facade.add_tracks_to_playlist(playlist_id, track_ids)
        self.grid.set_status(f"已添加 {count} 条到歌单")
        self.reload_tracks_from_db()
        self.library_changed.emit()

    def on_add_to_playlist(self) -> None:
        track_ids = self.selected_track_ids()
        if not track_ids:
            return
        playlist_id = _choose_or_create_playlist(self, self.facade, self.btn_add_playlist)
        if not playlist_id:
            return
        self._add_track_ids_to_playlist(track_ids, playlist_id)

    def on_favorite(self) -> None:
        tracks = self.grid.selected_tracks()
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id") and not bool(t.get("is_favorite"))]
        if not track_ids:
            return
        count = self.facade.add_to_favorites(track_ids)
        self.grid.set_status(f"已收藏 {count} 条")
        self.reload_tracks_from_db()
        self.library_changed.emit()

    def on_unfavorite(self) -> None:
        tracks = self.grid.selected_tracks()
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id") and bool(t.get("is_favorite"))]
        if not track_ids:
            return
        count = self.facade.remove_from_favorites(track_ids)
        self.grid.set_status(f"已取消收藏 {count} 条")
        self.reload_tracks_from_db()
        self.library_changed.emit()

    def on_track_field_edited(self, track_id: str, key: str, value) -> None:
        if not track_id:
            return
        if key == "custom_order":
            return
        try:
            if key.startswith("tag:"):
                tag_name = key.split(":", 1)[1]
                self.facade.update_track_tag_values([track_id], tag_name, str(value))
            else:
                self.facade.update_tracks_fields([track_id], {key: value})
        except Exception as exc:
            QMessageBox.warning(self, "编辑失败", f"edit: editing failed\n{exc}")
            QTimer.singleShot(0, self.reload_tracks_from_db)
            return
        for row in self.all_rows:
            if row.get("track_id") == track_id:
                if key.startswith("tag:"):
                    tags = dict(row.get("tags", {}) or {})
                    tag_name = key.split(":", 1)[1]
                    text = str(value).strip()
                    if text:
                        tags[tag_name] = text
                    else:
                        tags.pop(tag_name, None)
                    row["tags"] = tags
                    row[key] = text
                else:
                    row[key] = value
                break

    def _show_context_menu(self, pos, tracks: list[dict]) -> None:
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        if not track_ids:
            return

        can_favorite = any(not bool(t.get("is_favorite")) for t in tracks)
        can_unfavorite = any(bool(t.get("is_favorite")) for t in tracks)

        menu = QMenu(self)
        action_play = menu.addAction("播放")
        action_favorite = menu.addAction("收藏")
        action_unfavorite = menu.addAction("取消收藏")
        action_favorite.setEnabled(can_favorite)
        action_unfavorite.setEnabled(can_unfavorite)

        submenu_add = menu.addMenu("加到歌单")
        add_map: dict[QAction, str] = {}
        playlists = [p for p in self.facade.list_playlists() if str(p.get("playlist_id", "")) != FAVORITES_PLAYLIST_ID]
        for row in playlists:
            action = submenu_add.addAction(str(row.get("name", "")))
            add_map[action] = str(row.get("playlist_id", ""))
        if playlists:
            submenu_add.addSeparator()
        action_add_new = submenu_add.addAction("新建歌单...")

        menu.addSeparator()
        action_delete = menu.addAction("移到回收站")
        action_export = menu.addAction("导出")
        action_reveal = menu.addAction("使用文件管理器查看")
        action_copy = menu.addAction("复制行数据")
        action_detail = menu.addAction("查看详情")

        chosen = menu.exec(pos)
        if not chosen:
            return
        if chosen == action_play:
            _queue_play_tracks(self, tracks)
            return
        if chosen == action_favorite:
            self.on_favorite()
            return
        if chosen == action_unfavorite:
            self.on_unfavorite()
            return
        if chosen in add_map:
            self._add_track_ids_to_playlist(track_ids, add_map[chosen])
            return
        if chosen == action_add_new:
            playlist_id = _prompt_new_playlist(self, self.facade)
            if playlist_id:
                self._add_track_ids_to_playlist(track_ids, playlist_id)
            return
        if chosen == action_delete:
            self._delete_track_ids(track_ids)
            return
        if chosen == action_export:
            self._export_track_ids(track_ids, tracks)
            return
        if chosen == action_reveal:
            first = tracks[0] if tracks else {}
            _reveal_in_file_manager(self, _storage_path_for_track_row(self.facade, first))
            return
        if chosen == action_copy:
            _copy_selected_cells(self.grid.table)
            return
        if chosen == action_detail:
            _show_track_details(self, tracks[0])

class PlaylistPage(QWidget):
    library_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade
        self.current_playlist_id: str | None = None
        self.current_rows: list[dict] = []

        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self.btn_add = QPushButton("新建歌单")
        self.btn_del = QPushButton("删除歌单")
        self.btn_clear = QPushButton("清空歌单")
        self.btn_play_playlist = QPushButton("播放歌单")
        self.btn_export_playlist = QPushButton("导出选中歌单")
        top.addWidget(self.btn_add)
        top.addWidget(self.btn_del)
        top.addWidget(self.btn_clear)
        top.addWidget(self.btn_play_playlist)
        top.addWidget(self.btn_export_playlist)
        top.addStretch(1)

        splitter = QSplitter()

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["歌单", "曲目数"])
        self.tree.setAlternatingRowColors(True)
        left_layout.addWidget(self.tree)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        row = QHBoxLayout()
        self.btn_remove_tracks = QPushButton("从本歌单中移除")
        self.btn_copy_playlist = QPushButton("复制到歌单")
        self.btn_move_playlist = QPushButton("移动到歌单")
        self.btn_export = QPushButton("导出")
        self.btn_favorite = QPushButton("收藏")
        self.btn_unfavorite = QPushButton("取消收藏")
        self.btn_delete = QPushButton("从音乐库中删除")
        self.btn_delete.setStyleSheet("background-color:#b3261e;color:white;")
        for btn in [
            self.btn_remove_tracks,
            self.btn_copy_playlist,
            self.btn_move_playlist,
            self.btn_export,
            self.btn_favorite,
            self.btn_unfavorite,
            self.btn_delete,
        ]:
            row.addWidget(btn)
        row.addStretch(1)

        self.grid = TrackGridWidget(self.facade)

        right_layout.addLayout(row)
        right_layout.addWidget(self.grid, 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        root.addLayout(top)
        root.addWidget(splitter, 1)

        self.btn_add.clicked.connect(self.add_playlist)
        self.btn_del.clicked.connect(self.delete_playlist)
        self.btn_clear.clicked.connect(self.clear_playlist)
        self.btn_play_playlist.clicked.connect(self.play_current_playlist)
        self.btn_export_playlist.clicked.connect(self.export_current_playlist)
        self.btn_remove_tracks.clicked.connect(self.remove_selected_tracks)
        self.btn_copy_playlist.clicked.connect(self.copy_selected_tracks)
        self.btn_move_playlist.clicked.connect(self.move_selected_tracks)
        self.btn_export.clicked.connect(self.on_export)
        self.btn_favorite.clicked.connect(self.on_favorite)
        self.btn_unfavorite.clicked.connect(self.on_unfavorite)
        self.btn_delete.clicked.connect(self.on_delete_from_library)
        self.tree.currentItemChanged.connect(self.on_playlist_changed)
        self.grid.track_field_edited.connect(self.on_track_field_edited)
        self.grid.context_menu_requested.connect(self._show_context_menu)

        self.reload_playlists()

    def apply_button_scale(self, scale: float) -> None:
        for btn in [
            self.btn_add,
            self.btn_del,
            self.btn_clear,
            self.btn_play_playlist,
            self.btn_export_playlist,
            self.btn_remove_tracks,
            self.btn_copy_playlist,
            self.btn_move_playlist,
            self.btn_export,
            self.btn_favorite,
            self.btn_unfavorite,
            self.btn_delete,
        ]:
            _apply_button_scale(btn, scale)
        self.grid.set_button_scale(scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade
        self.grid.set_facade(facade)
        self.reload_playlists()

    def refresh_page(self) -> None:
        self.reload_playlists()
        self.reload_playlist_tracks()

    def reload_playlists(self) -> None:
        rows = self.facade.list_playlists()
        keep_id = self.current_playlist_id

        self.tree.clear()
        for row in rows:
            item = QTreeWidgetItem([str(row.get("name", "")), str(row.get("track_count", 0))])
            item.setData(0, Qt.ItemDataRole.UserRole, row.get("playlist_id"))
            self.tree.addTopLevelItem(item)

        target = None
        if keep_id:
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                if str(item.data(0, Qt.ItemDataRole.UserRole)) == keep_id:
                    target = item
                    break
        if target is None and self.tree.topLevelItemCount() > 0:
            target = self.tree.topLevelItem(0)

        if target is not None:
            self.tree.setCurrentItem(target)
        else:
            self.current_playlist_id = None
            self.current_rows = []
            self.grid.set_tracks([])

    def on_playlist_changed(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            self.current_playlist_id = None
            self.current_rows = []
            self.grid.set_tracks([])
            return
        self.current_playlist_id = str(item.data(0, Qt.ItemDataRole.UserRole))
        self.reload_playlist_tracks()

    def reload_playlist_tracks(self) -> None:
        if not self.current_playlist_id:
            self.current_rows = []
            self.grid.set_tracks([])
            return
        rows = self.facade.list_playlist_items(self.current_playlist_id)
        self.current_rows = rows
        self.grid.set_tracks(rows, entry_editable=True)
        self.grid.set_status(f"歌单包含 {len(rows)} 首")

    def selected_track_ids(self) -> list[str]:
        return self.grid.selected_track_ids()

    def add_playlist(self) -> None:
        name, ok = QInputDialog.getText(self, "新建歌单", "歌单名称")
        if not ok or not name.strip():
            return
        playlist_id = self.facade.create_playlist(name.strip())
        self.current_playlist_id = playlist_id
        self.reload_playlists()
        self.library_changed.emit()

    def delete_playlist(self) -> None:
        if not self.current_playlist_id:
            return
        if self.current_playlist_id == FAVORITES_PLAYLIST_ID:
            QMessageBox.warning(self, "删除歌单", "收藏歌单不可删除。")
            return
        answer = QMessageBox.question(self, "删除歌单", "确定删除当前歌单吗？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.facade.delete_playlist(self.current_playlist_id)
        self.current_playlist_id = None
        self.reload_playlists()
        self.library_changed.emit()

    def clear_playlist(self) -> None:
        if not self.current_playlist_id:
            return
        answer = QMessageBox.question(self, "清空歌单", "确定清空当前歌单吗？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        count = self.facade.clear_playlist(self.current_playlist_id)
        self.reload_playlist_tracks()
        self.reload_playlists()
        self.grid.set_status(f"已清空 {count} 首")
        self.library_changed.emit()

    def remove_selected_tracks(self) -> None:
        if not self.current_playlist_id:
            return
        track_ids = self.selected_track_ids()
        if not track_ids:
            return
        count = self.facade.remove_tracks_from_playlist(self.current_playlist_id, track_ids)
        self.reload_playlist_tracks()
        self.reload_playlists()
        self.grid.set_status(f"已移除 {count} 首")
        self.library_changed.emit()

    def _choose_target_playlist(self, anchor: QWidget, *, allow_create: bool = True) -> str | None:
        exclude = {self.current_playlist_id} if self.current_playlist_id else set()
        return _choose_or_create_playlist(self, self.facade, anchor, exclude_ids=exclude, allow_create=allow_create)

    def copy_selected_tracks(self) -> None:
        track_ids = self.selected_track_ids()
        if not track_ids:
            return
        target = self._choose_target_playlist(self.btn_copy_playlist, allow_create=True)
        if not target:
            return
        count = self.facade.add_tracks_to_playlist(target, track_ids)
        self.grid.set_status(f"已复制 {count} 首")
        self.reload_playlists()
        self.library_changed.emit()

    def move_selected_tracks(self) -> None:
        if not self.current_playlist_id:
            return
        track_ids = self.selected_track_ids()
        if not track_ids:
            return
        target = self._choose_target_playlist(self.btn_move_playlist, allow_create=True)
        if not target:
            return
        added = self.facade.add_tracks_to_playlist(target, track_ids)
        self.facade.remove_tracks_from_playlist(self.current_playlist_id, track_ids)
        self.reload_playlist_tracks()
        self.reload_playlists()
        self.grid.set_status(f"已移动 {added} 首")
        self.library_changed.emit()

    def on_export(self) -> None:
        tracks = self.grid.selected_tracks()
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        if not track_ids:
            return
        ok, target = _run_export_dialog(self, self.facade, tracks, playlist_name=self._current_playlist_name())
        if not ok:
            return
        self.grid.set_status(f"已导出 {len(track_ids)} 条到 {target}")

    def _current_playlist_name(self) -> str:
        item = self.tree.currentItem()
        if item is None:
            return "playlist"
        return str(item.text(0) or "playlist")

    def export_current_playlist(self) -> None:
        if not self.current_playlist_id:
            return
        tracks = list(self.current_rows)
        if not tracks:
            QMessageBox.information(self, "导出歌单", "当前歌单没有歌曲。")
            return
        ok, target = _run_export_dialog(self, self.facade, tracks, playlist_name=self._current_playlist_name())
        if not ok:
            return
        self.grid.set_status(f"已导出歌单 {len(tracks)} 首到 {target}")

    def play_current_playlist(self) -> None:
        tracks = list(self.current_rows)
        if not tracks:
            QMessageBox.information(self, "播放歌单", "当前歌单没有可播放歌曲。")
            return
        _queue_play_tracks(self, tracks)

    def on_favorite(self) -> None:
        tracks = self.grid.selected_tracks()
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id") and not bool(t.get("is_favorite"))]
        if not track_ids:
            return
        count = self.facade.add_to_favorites(track_ids)
        self.grid.set_status(f"已收藏 {count} 条")
        self.reload_playlist_tracks()
        self.reload_playlists()
        self.library_changed.emit()

    def on_unfavorite(self) -> None:
        tracks = self.grid.selected_tracks()
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id") and bool(t.get("is_favorite"))]
        if not track_ids:
            return
        count = self.facade.remove_from_favorites(track_ids)
        self.grid.set_status(f"已取消收藏 {count} 条")
        self.reload_playlist_tracks()
        self.reload_playlists()
        self.library_changed.emit()

    def on_delete_from_library(self) -> None:
        if not self.current_playlist_id:
            return
        track_ids = self.selected_track_ids()
        if not track_ids:
            return
        mode = _resolve_delete_mode_and_maybe_save_default(self, self.facade, len(track_ids))
        if mode == "cancel":
            return
        deleted = self.facade.delete_tracks(track_ids, mode=mode)
        self.reload_playlist_tracks()
        self.reload_playlists()
        self.grid.set_status(f"已移到回收站 {deleted} 条")
        self.library_changed.emit()

    def on_track_field_edited(self, track_id: str, key: str, value) -> None:
        if not track_id:
            return
        if key == "custom_order":
            if not self.current_playlist_id:
                return
            try:
                parsed = int(value)
            except Exception:
                return
            try:
                self.facade.update_playlist_entries(self.current_playlist_id, {track_id: parsed})
            except Exception as exc:
                QMessageBox.warning(self, "编辑失败", f"edit: editing failed\n{exc}")
                QTimer.singleShot(0, self.reload_playlist_tracks)
                return
            self.grid.select_track_ids([track_id])
            QTimer.singleShot(0, self.library_changed.emit)
            return
        try:
            if key.startswith("tag:"):
                tag_name = key.split(":", 1)[1]
                self.facade.update_track_tag_values([track_id], tag_name, str(value))
            else:
                self.facade.update_tracks_fields([track_id], {key: value})
        except Exception as exc:
            QMessageBox.warning(self, "编辑失败", f"edit: editing failed\n{exc}")
            QTimer.singleShot(0, self.reload_playlist_tracks)
            return
        QTimer.singleShot(0, self.library_changed.emit)

    def _show_context_menu(self, pos, tracks: list[dict]) -> None:
        if not self.current_playlist_id:
            return
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        if not track_ids:
            return
        can_favorite = any(not bool(t.get("is_favorite")) for t in tracks)
        can_unfavorite = any(bool(t.get("is_favorite")) for t in tracks)

        menu = QMenu(self)
        action_play = menu.addAction("播放")
        action_favorite = menu.addAction("收藏")
        action_unfavorite = menu.addAction("取消收藏")
        action_favorite.setEnabled(can_favorite)
        action_unfavorite.setEnabled(can_unfavorite)

        submenu_add = menu.addMenu("加到歌单")
        submenu_copy = menu.addMenu("复制到歌单")
        submenu_move = menu.addMenu("移动到歌单")
        action_map: dict[QAction, tuple[str, str | None]] = {}

        playlists = [p for p in self.facade.list_playlists() if str(p.get("playlist_id", "")) != FAVORITES_PLAYLIST_ID]
        for row in playlists:
            pid = str(row.get("playlist_id", ""))
            name = str(row.get("name", ""))
            action_map[submenu_add.addAction(name)] = ("add", pid)
            if pid != self.current_playlist_id:
                action_map[submenu_copy.addAction(name)] = ("copy", pid)
                action_map[submenu_move.addAction(name)] = ("move", pid)

        if playlists:
            submenu_add.addSeparator()
            submenu_copy.addSeparator()
            submenu_move.addSeparator()
        action_map[submenu_add.addAction("新建歌单...")] = ("add_new", None)
        action_map[submenu_copy.addAction("新建歌单...")] = ("copy_new", None)
        action_map[submenu_move.addAction("新建歌单...")] = ("move_new", None)

        menu.addSeparator()
        action_remove = menu.addAction("从本歌单中移除")
        action_delete = menu.addAction("移到回收站")
        action_export = menu.addAction("导出")
        action_reveal = menu.addAction("使用文件管理器查看")
        action_copy_data = menu.addAction("复制行数据")
        action_detail = menu.addAction("查看详情")

        chosen = menu.exec(pos)
        if not chosen:
            return
        if chosen == action_play:
            _queue_play_tracks(self, tracks)
            return
        if chosen == action_favorite:
            self.on_favorite()
            return
        if chosen == action_unfavorite:
            self.on_unfavorite()
            return
        if chosen in action_map:
            mode, pid = action_map[chosen]
            target = pid
            if mode.endswith("_new"):
                target = _prompt_new_playlist(self, self.facade)
            if not target:
                return
            if mode in {"add", "add_new", "copy", "copy_new"}:
                count = self.facade.add_tracks_to_playlist(target, track_ids)
                self.grid.set_status(f"已添加 {count} 首")
                self.reload_playlists()
                self.library_changed.emit()
                return
            if mode in {"move", "move_new"}:
                count = self.facade.add_tracks_to_playlist(target, track_ids)
                self.facade.remove_tracks_from_playlist(self.current_playlist_id, track_ids)
                self.reload_playlist_tracks()
                self.reload_playlists()
                self.grid.set_status(f"已移动 {count} 首")
                self.library_changed.emit()
                return
            return
        if chosen == action_remove:
            self.remove_selected_tracks()
            return
        if chosen == action_delete:
            self.on_delete_from_library()
            return
        if chosen == action_export:
            self.on_export()
            return
        if chosen == action_reveal:
            first = tracks[0] if tracks else {}
            _reveal_in_file_manager(self, _storage_path_for_track_row(self.facade, first))
            return
        if chosen == action_copy_data:
            _copy_selected_cells(self.grid.table)
            return
        if chosen == action_detail:
            _show_track_details(self, tracks[0])
