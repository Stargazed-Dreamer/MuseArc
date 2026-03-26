from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QModelIndex, Qt, Signal
from PySide6.QtGui import QAction, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from musearc.app.facade import FAVORITES_PLAYLIST_ID, MuseArcFacade
from musearc.ui.table_models import ColumnDef
from musearc.ui.track_grid import (
    LyricsTableModel,
    TrackGridWidget,
    _copy_selected_cells,
    _install_copy_support,
    _safe_int,
)
from musearc.ui.main_window_helpers import (
    ExportPlanDialog,
    TrackPickerDialog,
    _apply_button_scale,
    _ask_export_format,
    _choose_or_create_playlist,
    _resolve_delete_mode_and_maybe_save_default,
    _reveal_in_file_manager,
    _show_track_details,
    _storage_path_for_track_row,
)


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
        self.btn_play = QPushButton("播放（预留）")
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
        out_dir = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not out_dir:
            return
        fmt, ok = _ask_export_format(self, self.btn_export)
        if not ok:
            return
        if fmt == "__plan__":
            track_rows = list(tracks or [])
            if not track_rows:
                id_set = set(track_ids)
                track_rows = [row for row in self.all_rows if str(row.get("track_id", "")) in id_set]
            dlg = ExportPlanDialog(self, track_rows)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            self.facade.export_with_plan(track_ids, out_dir, dlg.export_plan(), bitrate="320k")
        else:
            self.facade.export(track_ids, out_dir, fmt=fmt, bitrate="320k")
        self.grid.set_status(f"已导出 {len(track_ids)} 条到 {out_dir}")

    def on_export(self) -> None:
        tracks = self.grid.selected_tracks()
        self._export_track_ids(
            [str(t.get("track_id", "")) for t in tracks if t.get("track_id")],
            tracks,
        )

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
            self.reload_tracks_from_db()
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
        action_play = menu.addAction("播放（预留）")
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
            QMessageBox.information(self, "播放", "播放功能预留。")
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
            playlist_id = _choose_or_create_playlist(self, self.facade, self.btn_add_playlist, allow_create=True)
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
        top.addWidget(self.btn_add)
        top.addWidget(self.btn_del)
        top.addWidget(self.btn_clear)
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
        out_dir = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not out_dir:
            return
        fmt, ok = _ask_export_format(self, self.btn_export)
        if not ok:
            return
        if fmt == "__plan__":
            dlg = ExportPlanDialog(self, tracks)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            self.facade.export_with_plan(track_ids, out_dir, dlg.export_plan(), bitrate="320k")
        else:
            self.facade.export(track_ids, out_dir, fmt=fmt, bitrate="320k")
        self.grid.set_status(f"已导出 {len(track_ids)} 条到 {out_dir}")

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
                self.reload_playlist_tracks()
                return
            self.reload_playlist_tracks()
            self.grid.select_track_ids([track_id])
            self.library_changed.emit()
            return
        try:
            if key.startswith("tag:"):
                tag_name = key.split(":", 1)[1]
                self.facade.update_track_tag_values([track_id], tag_name, str(value))
            else:
                self.facade.update_tracks_fields([track_id], {key: value})
        except Exception as exc:
            QMessageBox.warning(self, "编辑失败", f"edit: editing failed\n{exc}")
            self.reload_playlist_tracks()
            return
        self.library_changed.emit()

    def _show_context_menu(self, pos, tracks: list[dict]) -> None:
        if not self.current_playlist_id:
            return
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        if not track_ids:
            return
        can_favorite = any(not bool(t.get("is_favorite")) for t in tracks)
        can_unfavorite = any(bool(t.get("is_favorite")) for t in tracks)

        menu = QMenu(self)
        action_play = menu.addAction("播放（预留）")
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
            QMessageBox.information(self, "播放", "播放功能预留。")
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
                target = _choose_or_create_playlist(
                    self,
                    self.facade,
                    self.btn_copy_playlist,
                    exclude_ids={self.current_playlist_id},
                    allow_create=True,
                )
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


class FullScanPage(QWidget):
    library_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade
        self.current_work_id: str | None = None

        root = QVBoxLayout(self)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("工作"))
        self.combo_work = QComboBox()
        self.combo_work.setMinimumWidth(920)
        self.btn_new_work = QPushButton("新建工作")
        self.btn_delete_work = QPushButton("删除工作")
        row1.addWidget(self.combo_work, 1)
        row1.addWidget(self.btn_new_work)
        row1.addWidget(self.btn_delete_work)

        row2 = QHBoxLayout()
        self.btn_pass = QPushButton("过（从当前工作移除）")
        self.btn_add_playlist = QPushButton("添加到歌单")
        self.btn_favorite = QPushButton("收藏")
        self.btn_unfavorite = QPushButton("取消收藏")
        self.btn_export = QPushButton("导出")
        self.btn_delete = QPushButton("从音乐库中删除")
        self.btn_delete.setStyleSheet("background-color:#b3261e;color:white;")
        for btn in [
            self.btn_pass,
            self.btn_add_playlist,
            self.btn_favorite,
            self.btn_unfavorite,
            self.btn_export,
            self.btn_delete,
        ]:
            row2.addWidget(btn)
        row2.addStretch(1)

        self.grid = TrackGridWidget(self.facade)

        root.addLayout(row1)
        root.addLayout(row2)
        root.addWidget(self.grid, 1)

        self.btn_new_work.clicked.connect(self.create_work)
        self.btn_delete_work.clicked.connect(self.delete_work)
        self.combo_work.currentIndexChanged.connect(self.on_work_changed)
        self.btn_pass.clicked.connect(self.pass_selected)
        self.btn_add_playlist.clicked.connect(self.add_selected_to_playlist)
        self.btn_favorite.clicked.connect(self.on_favorite)
        self.btn_unfavorite.clicked.connect(self.on_unfavorite)
        self.btn_export.clicked.connect(self.on_export)
        self.btn_delete.clicked.connect(self.on_delete)
        self.grid.track_field_edited.connect(self.on_track_field_edited)
        self.grid.context_menu_requested.connect(self._show_context_menu)

        self.reload_works()

    def apply_button_scale(self, scale: float) -> None:
        for btn in [
            self.btn_new_work,
            self.btn_delete_work,
            self.btn_pass,
            self.btn_add_playlist,
            self.btn_favorite,
            self.btn_unfavorite,
            self.btn_export,
            self.btn_delete,
        ]:
            _apply_button_scale(btn, scale)
        self.grid.set_button_scale(scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade
        self.grid.set_facade(facade)
        self.reload_works()

    def refresh_page(self) -> None:
        self.reload_works()

    def reload_works(self) -> None:
        rows = self.facade.list_fullscan_works()
        keep = self.current_work_id

        self.combo_work.blockSignals(True)
        self.combo_work.clear()
        for row in rows:
            label = f"{row.get('name', '')} (待处理 {row.get('todo_items', 0)} / 总计 {row.get('total_items', 0)})"
            self.combo_work.addItem(label, row.get("work_id"))
        self.combo_work.blockSignals(False)

        if self.combo_work.count() == 0:
            self.current_work_id = None
            self.grid.set_tracks([])
            self.grid.set_status("暂无全量筛选工作")
            return

        idx = 0
        if keep:
            for i in range(self.combo_work.count()):
                if str(self.combo_work.itemData(i)) == keep:
                    idx = i
                    break
        self.combo_work.setCurrentIndex(idx)
        self.on_work_changed()

    def on_work_changed(self) -> None:
        if self.combo_work.currentIndex() < 0:
            self.current_work_id = None
            self.grid.set_tracks([])
            return
        self.current_work_id = str(self.combo_work.currentData())
        rows = self.facade.get_fullscan_work_items(self.current_work_id, limit=2_000_000)
        self.grid.set_tracks(rows)
        self.grid.set_status(f"工作项目 {len(rows)} 条")

    def create_work(self) -> None:
        name, ok = QInputDialog.getText(self, "新建全量筛选工作", "工作名称")
        if not ok or not name.strip():
            return
        work_id = self.facade.create_fullscan_work(name.strip())
        self.current_work_id = work_id
        self.reload_works()
        self.library_changed.emit()

    def delete_work(self) -> None:
        if not self.current_work_id:
            return
        answer = QMessageBox.question(self, "删除工作", "确定删除当前工作吗？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.facade.delete_fullscan_work(self.current_work_id)
        self.current_work_id = None
        self.reload_works()
        self.library_changed.emit()

    def selected_track_ids(self) -> list[str]:
        return self.grid.selected_track_ids()

    def pass_selected(self) -> None:
        if not self.current_work_id:
            return
        track_ids = self.selected_track_ids()
        if not track_ids:
            return
        count = self.facade.remove_fullscan_items(self.current_work_id, track_ids)
        self.on_work_changed()
        self.grid.set_status(f"已从工作移除 {count} 条")

    def add_selected_to_playlist(self) -> None:
        track_ids = self.selected_track_ids()
        if not track_ids:
            return
        playlist_id = _choose_or_create_playlist(self, self.facade, self.btn_add_playlist)
        if not playlist_id:
            return
        count = self.facade.add_tracks_to_playlist(playlist_id, track_ids)
        self.grid.set_status(f"已添加 {count} 条到歌单")
        self.library_changed.emit()

    def on_favorite(self) -> None:
        tracks = self.grid.selected_tracks()
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id") and not bool(t.get("is_favorite"))]
        if not track_ids:
            return
        count = self.facade.add_to_favorites(track_ids)
        self.on_work_changed()
        self.grid.set_status(f"已收藏 {count} 条")
        self.library_changed.emit()

    def on_unfavorite(self) -> None:
        tracks = self.grid.selected_tracks()
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id") and bool(t.get("is_favorite"))]
        if not track_ids:
            return
        count = self.facade.remove_from_favorites(track_ids)
        self.on_work_changed()
        self.grid.set_status(f"已取消收藏 {count} 条")
        self.library_changed.emit()

    def on_export(self) -> None:
        tracks = self.grid.selected_tracks()
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        if not track_ids:
            return
        out_dir = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not out_dir:
            return
        fmt, ok = _ask_export_format(self, self.btn_export)
        if not ok:
            return
        if fmt == "__plan__":
            dlg = ExportPlanDialog(self, tracks)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            self.facade.export_with_plan(track_ids, out_dir, dlg.export_plan(), bitrate="320k")
        else:
            self.facade.export(track_ids, out_dir, fmt=fmt, bitrate="320k")
        self.grid.set_status(f"已导出 {len(track_ids)} 条到 {out_dir}")

    def on_delete(self) -> None:
        if not self.current_work_id:
            return
        track_ids = self.selected_track_ids()
        if not track_ids:
            return
        mode = _resolve_delete_mode_and_maybe_save_default(self, self.facade, len(track_ids))
        if mode == "cancel":
            return
        count = self.facade.delete_tracks(track_ids, mode=mode)
        self.facade.remove_fullscan_items(self.current_work_id, track_ids)
        self.on_work_changed()
        self.grid.set_status(f"已移到回收站 {count} 条")
        self.library_changed.emit()

    def on_track_field_edited(self, track_id: str, key: str, value) -> None:
        if not track_id or key == "custom_order":
            return
        try:
            if key.startswith("tag:"):
                tag_name = key.split(":", 1)[1]
                self.facade.update_track_tag_values([track_id], tag_name, str(value))
            else:
                self.facade.update_tracks_fields([track_id], {key: value})
        except Exception as exc:
            QMessageBox.warning(self, "编辑失败", f"edit: editing failed\n{exc}")
            self.on_work_changed()
            return
        self.library_changed.emit()

    def _show_context_menu(self, pos, tracks: list[dict]) -> None:
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        if not track_ids:
            return
        can_favorite = any(not bool(t.get("is_favorite")) for t in tracks)
        can_unfavorite = any(bool(t.get("is_favorite")) for t in tracks)

        menu = QMenu(self)
        action_play = menu.addAction("播放（预留）")
        action_pass = menu.addAction("从当前工作移除")
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
            QMessageBox.information(self, "播放", "播放功能预留。")
            return
        if chosen == action_pass:
            self.pass_selected()
            return
        if chosen == action_favorite:
            self.on_favorite()
            return
        if chosen == action_unfavorite:
            self.on_unfavorite()
            return
        if chosen in add_map:
            count = self.facade.add_tracks_to_playlist(add_map[chosen], track_ids)
            self.grid.set_status(f"已添加 {count} 条到歌单")
            self.library_changed.emit()
            return
        if chosen == action_add_new:
            target = _choose_or_create_playlist(self, self.facade, self.btn_add_playlist, allow_create=True)
            if target:
                count = self.facade.add_tracks_to_playlist(target, track_ids)
                self.grid.set_status(f"已添加 {count} 条到歌单")
                self.library_changed.emit()
            return
        if chosen == action_delete:
            self.on_delete()
            return
        if chosen == action_export:
            self.on_export()
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


class TrashPage(QWidget):
    library_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade

        root = QVBoxLayout(self)
        row = QHBoxLayout()
        self.btn_restore = QPushButton("恢复选中")
        row.addWidget(self.btn_restore)
        row.addStretch(1)

        self.grid = TrackGridWidget(self.facade)

        root.addLayout(row)
        root.addWidget(self.grid, 1)

        self.btn_restore.clicked.connect(self.restore_selected)
        self.grid.track_field_edited.connect(self.on_track_field_edited)
        self.grid.context_menu_requested.connect(self._show_context_menu)

        self.reload_trash()

    def apply_button_scale(self, scale: float) -> None:
        _apply_button_scale(self.btn_restore, scale)
        self.grid.set_button_scale(scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade
        self.grid.set_facade(facade)
        self.reload_trash()

    def refresh_page(self) -> None:
        self.reload_trash()

    def reload_trash(self) -> None:
        rows = self.facade.list_deleted_tracks(limit=2_000_000)
        self.grid.set_tracks(rows)
        self.grid.set_status(f"回收站 {len(rows)} 条")

    def restore_selected(self) -> None:
        track_ids = self.grid.selected_track_ids()
        if not track_ids:
            return
        restored = self.facade.restore_tracks(track_ids)
        self.reload_trash()
        self.grid.set_status(f"已恢复 {restored} 条")
        self.library_changed.emit()

    def on_track_field_edited(self, track_id: str, key: str, value) -> None:
        if not track_id or key == "custom_order":
            return
        try:
            if key.startswith("tag:"):
                tag_name = key.split(":", 1)[1]
                self.facade.update_track_tag_values([track_id], tag_name, str(value))
            else:
                self.facade.update_tracks_fields([track_id], {key: value})
        except Exception as exc:
            QMessageBox.warning(self, "编辑失败", f"edit: editing failed\n{exc}")
            self.reload_trash()
            return

    def _show_context_menu(self, pos, tracks: list[dict]) -> None:
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        if not track_ids:
            return
        menu = QMenu(self)
        action_restore = menu.addAction("恢复")
        action_reveal = menu.addAction("使用文件管理器查看")
        action_copy = menu.addAction("复制行数据")
        action_detail = menu.addAction("查看详情")
        chosen = menu.exec(pos)
        if not chosen:
            return
        if chosen == action_restore:
            self.restore_selected()
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


class TagManagementPage(QWidget):
    tags_changed = Signal()
    library_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade
        self.current_tag_name: str | None = None

        root = QVBoxLayout(self)

        row = QHBoxLayout()
        self.btn_add = QPushButton("新增标签")
        self.btn_delete = QPushButton("删除标签")
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_delete)
        row.addStretch(1)

        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["标签", "歌曲数"])
        self.tree.setAlternatingRowColors(True)
        left_layout.addWidget(self.tree)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        row_ops = QHBoxLayout()
        self.btn_remove_from_tag = QPushButton("从本标签中移除")
        self.btn_export = QPushButton("导出")
        self.btn_favorite = QPushButton("收藏")
        self.btn_unfavorite = QPushButton("取消收藏")
        self.btn_delete_from_library = QPushButton("从音乐库中删除")
        self.btn_delete_from_library.setStyleSheet("background-color:#b3261e;color:white;")
        for btn in [
            self.btn_remove_from_tag,
            self.btn_export,
            self.btn_favorite,
            self.btn_unfavorite,
            self.btn_delete_from_library,
        ]:
            row_ops.addWidget(btn)
        row_ops.addStretch(1)

        self.grid = TrackGridWidget(self.facade)
        right_layout.addLayout(row_ops)
        right_layout.addWidget(self.grid, 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        root.addLayout(row)
        root.addWidget(splitter, 1)

        self.btn_add.clicked.connect(self._on_add)
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_remove_from_tag.clicked.connect(self._remove_selected_from_tag)
        self.btn_export.clicked.connect(self._on_export)
        self.btn_favorite.clicked.connect(self._on_favorite)
        self.btn_unfavorite.clicked.connect(self._on_unfavorite)
        self.btn_delete_from_library.clicked.connect(self._on_delete_from_library)
        self.tree.currentItemChanged.connect(self._on_tag_changed)
        self.grid.track_field_edited.connect(self._on_track_field_edited)
        self.grid.context_menu_requested.connect(self._show_context_menu)

        self.reload_tags()

    def apply_button_scale(self, scale: float) -> None:
        _apply_button_scale(self.btn_add, scale)
        _apply_button_scale(self.btn_delete, scale)
        _apply_button_scale(self.btn_remove_from_tag, scale)
        _apply_button_scale(self.btn_export, scale)
        _apply_button_scale(self.btn_favorite, scale)
        _apply_button_scale(self.btn_unfavorite, scale)
        _apply_button_scale(self.btn_delete_from_library, scale)
        self.grid.set_button_scale(scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade
        self.grid.set_facade(facade)
        self.reload_tags()

    def refresh_page(self) -> None:
        self.reload_tags()
        self._reload_tracks_for_current_tag()

    def reload_tags(self) -> None:
        keep = self.current_tag_name
        rows = self.facade.list_tag_fields()
        self.tree.clear()
        target_item: QTreeWidgetItem | None = None
        for row in rows:
            tag_name = str(row.get("tag_name", ""))
            item = QTreeWidgetItem([tag_name, str(row.get("track_count", 0))])
            item.setData(0, Qt.ItemDataRole.UserRole, tag_name)
            self.tree.addTopLevelItem(item)
            if keep and keep == tag_name:
                target_item = item
        if target_item is None and self.tree.topLevelItemCount() > 0:
            target_item = self.tree.topLevelItem(0)
        if target_item is not None:
            self.tree.setCurrentItem(target_item)
            self.current_tag_name = str(target_item.data(0, Qt.ItemDataRole.UserRole) or "")
        else:
            self.current_tag_name = None
            self.grid.set_tracks([])
            self.grid.set_status("暂无标签")

    def _reload_tracks_for_current_tag(self) -> None:
        if not self.current_tag_name:
            self.grid.set_tracks([])
            self.grid.set_status("未选择标签")
            return
        rows = self.facade.list_tracks(limit=2_000_000)
        key = f"tag:{self.current_tag_name}"
        filtered = [r for r in rows if str((r.get("tags", {}) or {}).get(self.current_tag_name, "")).strip() or str(r.get(key, "")).strip()]
        self.grid.set_tracks(filtered)
        self.grid.set_status(f"标签“{self.current_tag_name}”共 {len(filtered)} 首")

    def _selected_tag_name(self) -> str | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        name = str(item.data(0, Qt.ItemDataRole.UserRole) or "").strip()
        return name or None

    def _on_tag_changed(self) -> None:
        self.current_tag_name = self._selected_tag_name()
        self._reload_tracks_for_current_tag()

    def _selected_tracks(self) -> list[dict]:
        return self.grid.selected_tracks()

    def _selected_track_ids(self) -> list[str]:
        return [str(t.get("track_id", "")) for t in self._selected_tracks() if t.get("track_id")]

    def _on_add(self) -> None:
        name, ok = QInputDialog.getText(self, "新增标签", "标签名称")
        if not ok:
            return
        text = str(name).strip()
        if not text:
            return
        if not self.facade.create_tag_field(text):
            QMessageBox.warning(self, "新增标签", "标签可能已存在或名称无效。")
            return
        self.current_tag_name = text
        self.reload_tags()
        self.grid.refresh_tag_fields()
        self.tags_changed.emit()

    def _on_delete(self) -> None:
        name = self._selected_tag_name()
        if not name:
            return
        answer = QMessageBox.question(self, "删除标签", f"确定删除标签“{name}”吗？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        count = self.facade.delete_tag_field(name)
        if count <= 0:
            QMessageBox.warning(self, "删除标签", "默认标签不可删除，或标签不存在。")
            return
        self.current_tag_name = None
        self.reload_tags()
        self.grid.refresh_tag_fields()
        self.tags_changed.emit()

    def _remove_selected_from_tag(self) -> None:
        tag_name = self._selected_tag_name()
        if not tag_name:
            return
        track_ids = self._selected_track_ids()
        if not track_ids:
            return
        count = self.facade.update_track_tag_values(track_ids, tag_name, "")
        self.grid.set_status(f"已从标签“{tag_name}”移除 {count} 首")
        self._reload_tracks_for_current_tag()
        self.reload_tags()
        self.library_changed.emit()

    def _on_export(self) -> None:
        tracks = self._selected_tracks()
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        if not track_ids:
            return
        out_dir = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not out_dir:
            return
        fmt, ok = _ask_export_format(self, self.btn_export)
        if not ok:
            return
        if fmt == "__plan__":
            dlg = ExportPlanDialog(self, tracks)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            self.facade.export_with_plan(track_ids, out_dir, dlg.export_plan(), bitrate="320k")
        else:
            self.facade.export(track_ids, out_dir, fmt=fmt, bitrate="320k")
        self.grid.set_status(f"已导出 {len(track_ids)} 条到 {out_dir}")

    def _on_favorite(self) -> None:
        tracks = self._selected_tracks()
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id") and not bool(t.get("is_favorite"))]
        if not track_ids:
            return
        count = self.facade.add_to_favorites(track_ids)
        self.grid.set_status(f"已收藏 {count} 条")
        self._reload_tracks_for_current_tag()
        self.library_changed.emit()

    def _on_unfavorite(self) -> None:
        tracks = self._selected_tracks()
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id") and bool(t.get("is_favorite"))]
        if not track_ids:
            return
        count = self.facade.remove_from_favorites(track_ids)
        self.grid.set_status(f"已取消收藏 {count} 条")
        self._reload_tracks_for_current_tag()
        self.library_changed.emit()

    def _on_delete_from_library(self) -> None:
        track_ids = self._selected_track_ids()
        if not track_ids:
            return
        mode = _resolve_delete_mode_and_maybe_save_default(self, self.facade, len(track_ids))
        if mode == "cancel":
            return
        deleted = self.facade.delete_tracks(track_ids, mode=mode)
        self.grid.set_status(f"已移到回收站 {deleted} 条")
        self._reload_tracks_for_current_tag()
        self.reload_tags()
        self.library_changed.emit()

    def _on_track_field_edited(self, track_id: str, key: str, value) -> None:
        if not track_id or key == "custom_order":
            return
        try:
            if key.startswith("tag:"):
                tag_name = key.split(":", 1)[1]
                self.facade.update_track_tag_values([track_id], tag_name, str(value))
            else:
                self.facade.update_tracks_fields([track_id], {key: value})
        except Exception as exc:
            QMessageBox.warning(self, "编辑失败", f"edit: editing failed\n{exc}")
            self._reload_tracks_for_current_tag()
            self.reload_tags()
            return
        self._reload_tracks_for_current_tag()
        self.reload_tags()
        self.library_changed.emit()

    def _show_context_menu(self, pos, tracks: list[dict]) -> None:
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        if not track_ids:
            return
        can_favorite = any(not bool(t.get("is_favorite")) for t in tracks)
        can_unfavorite = any(bool(t.get("is_favorite")) for t in tracks)
        menu = QMenu(self)
        action_play = menu.addAction("播放（预留）")
        action_remove_tag = menu.addAction("从本标签中移除")
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
            QMessageBox.information(self, "播放", "播放功能预留。")
            return
        if chosen == action_remove_tag:
            self._remove_selected_from_tag()
            return
        if chosen == action_favorite:
            self._on_favorite()
            return
        if chosen == action_unfavorite:
            self._on_unfavorite()
            return
        if chosen in add_map:
            count = self.facade.add_tracks_to_playlist(add_map[chosen], track_ids)
            self.grid.set_status(f"已添加 {count} 条到歌单")
            self.library_changed.emit()
            return
        if chosen == action_add_new:
            target = _choose_or_create_playlist(self, self.facade, self.btn_export, allow_create=True)
            if target:
                count = self.facade.add_tracks_to_playlist(target, track_ids)
                self.grid.set_status(f"已添加 {count} 条到歌单")
                self.library_changed.emit()
            return
        if chosen == action_delete:
            self._on_delete_from_library()
            return
        if chosen == action_export:
            self._on_export()
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


class LyricsManagementPage(QWidget):
    library_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade
        self._all_rows: list[dict] = []

        root = QVBoxLayout(self)

        row_top = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索 文件名/标题/艺术家/专辑/歌词作者")
        self.btn_search = QPushButton("搜索")
        self.combo_group = QComboBox()
        self.combo_group.addItem("不分组", "none")
        self.combo_group.addItem("文件名", "file_name")
        self.combo_group.addItem("歌曲标题", "lyrics_title")
        self.combo_group.addItem("艺术家", "lyrics_artist")
        self.combo_group.addItem("专辑", "lyrics_album")
        self.combo_group.addItem("歌词文件作者", "lyrics_author")
        self.combo_group.addItem("对应歌曲", "mapped_track")
        self.chk_multi = QCheckBox("多选模式")
        self.chk_multi.setChecked(True)
        self.chk_edit_mode = QCheckBox("编辑模式")
        self.chk_preview = QCheckBox("预览歌词")
        row_top.addWidget(self.search_input, 1)
        row_top.addWidget(self.btn_search)
        row_top.addWidget(QLabel("分组"))
        row_top.addWidget(self.combo_group)
        row_top.addWidget(self.chk_multi)
        row_top.addWidget(self.chk_edit_mode)
        row_top.addWidget(self.chk_preview)

        row_ops = QHBoxLayout()
        self.btn_map_track = QPushButton("映射到歌曲")
        self.btn_edit_author = QPushButton("批量改作者")
        self.btn_delete = QPushButton("删除歌词")
        self.btn_delete.setStyleSheet("background-color:#b3261e;color:white;")
        row_ops.addWidget(self.btn_map_track)
        row_ops.addWidget(self.btn_edit_author)
        row_ops.addWidget(self.btn_delete)
        row_ops.addStretch(1)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.model = LyricsTableModel(
            [
                ColumnDef("file_name", "文件名"),
                ColumnDef("lyrics_title", "歌曲标题"),
                ColumnDef("lyrics_artist", "艺术家"),
                ColumnDef("lyrics_album", "专辑"),
                ColumnDef("lyrics_author", "歌词文件作者"),
                ColumnDef("line_count", "歌词行数"),
                ColumnDef("mapped_track", "对应歌曲"),
                ColumnDef("lyrics_id", "歌词ID"),
            ]
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        _install_copy_support(self.table)
        left_layout.addWidget(self.table)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("歌词预览")

        self.splitter.addWidget(left)
        self.splitter.addWidget(self.preview)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.preview.hide()

        root.addLayout(row_top)
        root.addLayout(row_ops)
        root.addWidget(self.splitter, 1)

        self.btn_search.clicked.connect(self.apply_filter)
        self.search_input.returnPressed.connect(self.apply_filter)
        self.combo_group.currentIndexChanged.connect(self.apply_filter)
        self.chk_multi.toggled.connect(self._on_toggle_multi)
        self.chk_edit_mode.toggled.connect(self._on_toggle_edit_mode)
        self.btn_map_track.clicked.connect(self._map_selected_to_track)
        self.btn_edit_author.clicked.connect(self._edit_author_for_selected)
        self.btn_delete.clicked.connect(self._delete_selected_lyrics)
        self.chk_preview.toggled.connect(self._on_toggle_preview)
        self.table.clicked.connect(self._on_click_cell)
        self.table.doubleClicked.connect(self._on_double_click_cell)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.installEventFilter(self)
        self.model.lyrics_field_edited.connect(self._on_lyrics_field_edited)
        if self.table.selectionModel() is not None:
            self.table.selectionModel().selectionChanged.connect(lambda *_args: self._refresh_preview())

        self._on_toggle_multi(self.chk_multi.isChecked())
        self._on_toggle_edit_mode(self.chk_edit_mode.isChecked())
        self.reload_lyrics()

    def apply_button_scale(self, scale: float) -> None:
        _apply_button_scale(self.btn_search, scale)
        _apply_button_scale(self.btn_map_track, scale)
        _apply_button_scale(self.btn_edit_author, scale)
        _apply_button_scale(self.btn_delete, scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade
        self.reload_lyrics()

    def refresh_page(self) -> None:
        self.reload_lyrics()

    def reload_lyrics(self) -> None:
        self._all_rows = self.facade.list_lyrics(limit=200_000)
        self.apply_filter()

    def apply_filter(self) -> None:
        token = self.search_input.text().strip().casefold()
        group_key = str(self.combo_group.currentData() or "none")
        if not token:
            rows = list(self._all_rows)
        else:
            rows = []
            for row in self._all_rows:
                text = " | ".join(
                    [
                        str(row.get("file_name", "")),
                        str(row.get("lyrics_title", "")),
                        str(row.get("lyrics_artist", "")),
                        str(row.get("lyrics_album", "")),
                        str(row.get("lyrics_author", "")),
                        str(row.get("mapped_track", "")),
                    ]
                ).casefold()
                if token in text:
                    rows.append(row)

        rows.sort(
            key=lambda r: (
                str(r.get(group_key, "")).casefold() if group_key and group_key != "none" else "",
                str(r.get("file_name", "")).casefold(),
                str(r.get("lyrics_title", "")).casefold(),
                str(r.get("lyrics_artist", "")).casefold(),
                str(r.get("lyrics_album", "")).casefold(),
                str(r.get("lyrics_author", "")).casefold(),
                _safe_int(r.get("line_count", 0), 0),
                str(r.get("mapped_track", "")).casefold(),
            )
        )
        self.model.set_rows(rows)
        self._refresh_preview()

    def _selected_rows(self) -> list[dict]:
        if self.table.selectionModel() is None:
            return []
        selected = self.table.selectionModel().selectedRows()
        out: list[dict] = []
        for idx in selected:
            row = self.model.row_at(idx.row())
            if row:
                out.append(row)
        return out

    def _selected_lyrics_ids(self) -> list[str]:
        return [str(r.get("lyrics_id", "")) for r in self._selected_rows() if r.get("lyrics_id")]

    def _column_key_at(self, index: QModelIndex) -> str:
        if not index.isValid():
            return ""
        if not hasattr(self.model, "columns"):
            return ""
        if index.column() < 0 or index.column() >= len(self.model.columns):
            return ""
        return str(self.model.columns[index.column()].key)

    def _row_at_index(self, index: QModelIndex) -> dict | None:
        if not index.isValid():
            return None
        return self.model.row_at(index.row())

    def _on_toggle_preview(self, checked: bool) -> None:
        self.preview.setVisible(bool(checked))
        if checked:
            self.splitter.setSizes([750, 650])
        self._refresh_preview()

    def _selected_lyrics_row(self) -> dict | None:
        if self.table.selectionModel() is None:
            return None
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return None
        return self.model.row_at(selected[0].row())

    def _refresh_preview(self) -> None:
        if not self.chk_preview.isChecked():
            self.preview.clear()
            return
        row = self._selected_lyrics_row()
        if not row:
            self.preview.clear()
            return
        rel = str(row.get("storage_relpath", "") or "")
        if not rel:
            self.preview.setPlainText("")
            return
        target = Path(self.facade.library_root) / rel
        try:
            text = target.read_text(encoding="utf-8")
        except Exception as exc:
            text = f"无法读取歌词文件: {exc}"
        self.preview.setPlainText(text)

    def _map_selected_to_track(self) -> None:
        lyrics_ids = self._selected_lyrics_ids()
        if not lyrics_ids:
            return
        dlg = TrackPickerDialog(self, self.facade, allow_clear=True)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for lyrics_id in lyrics_ids:
            self.facade.set_primary_track_for_lyrics(lyrics_id, dlg.selected_track_id)
        self.reload_lyrics()
        self.library_changed.emit()

    def _map_single_row(self, row: dict) -> bool:
        lyrics_id = str((row or {}).get("lyrics_id", "") or "")
        if not lyrics_id:
            return False
        dlg = TrackPickerDialog(self, self.facade, allow_clear=True)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        self.facade.set_primary_track_for_lyrics(lyrics_id, dlg.selected_track_id)
        self.reload_lyrics()
        self.library_changed.emit()
        return True

    def _map_single_row_by_index(self, index: QModelIndex) -> bool:
        row = self._row_at_index(index)
        if not row:
            return False
        return self._map_single_row(row)

    def _edit_author_for_selected(self) -> None:
        lyrics_ids = self._selected_lyrics_ids()
        if not lyrics_ids:
            return
        value, ok = QInputDialog.getText(self, "批量改作者", "歌词文件作者")
        if not ok:
            return
        self.facade.update_lyrics_author(lyrics_ids, str(value))
        self.reload_lyrics()
        self.library_changed.emit()

    def _edit_author_for_row(self, row: dict) -> bool:
        lyrics_id = str((row or {}).get("lyrics_id", "") or "")
        if not lyrics_id:
            return False
        current_author = str((row or {}).get("lyrics_author", "") or "")
        value, ok = QInputDialog.getText(self, "修改作者", "歌词文件作者", text=current_author)
        if not ok:
            return False
        self.facade.update_lyrics_author([lyrics_id], str(value))
        self.reload_lyrics()
        self.library_changed.emit()
        return True

    def _delete_selected_lyrics(self) -> None:
        lyrics_ids = self._selected_lyrics_ids()
        if not lyrics_ids:
            return
        answer = QMessageBox.question(self, "删除歌词", f"确定删除 {len(lyrics_ids)} 条歌词吗？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        deleted = self.facade.delete_lyrics(lyrics_ids)
        self.reload_lyrics()
        self.preview.clear()
        self.library_changed.emit()
        QMessageBox.information(self, "删除歌词", f"已删除 {deleted} 条歌词文件。")

    def _on_double_click_cell(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        key = self._column_key_at(index)
        if key == "mapped_track":
            self._map_single_row_by_index(index)
            return
        if self.chk_edit_mode.isChecked() and key in {"file_name", "lyrics_title", "lyrics_artist", "lyrics_album", "lyrics_author"}:
            self.table.edit(index)
            return

    def _on_click_cell(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        key = self._column_key_at(index)
        if key == "mapped_track":
            self._map_single_row_by_index(index)
            return
        if self.chk_edit_mode.isChecked() and key in {"file_name", "lyrics_title", "lyrics_artist", "lyrics_album", "lyrics_author"}:
            self.table.edit(index)

    def _on_lyrics_field_edited(self, lyrics_id: str, key: str, value: object) -> None:
        if not lyrics_id:
            return
        try:
            self.facade.update_lyrics_fields([lyrics_id], {key: value})
        except Exception as exc:
            QMessageBox.warning(self, "编辑失败", f"edit: editing failed\n{exc}")
            self.reload_lyrics()
            return
        for row in self._all_rows:
            if str(row.get("lyrics_id", "")) != lyrics_id:
                continue
            row[key] = value
            break
        self.library_changed.emit()

    def _map_next_row_from(self, row_index: int) -> None:
        if row_index < 0:
            return
        row_count = self.model.rowCount()
        if row_count <= 0:
            return
        mapped_col = 0
        if hasattr(self.model, "columns"):
            for idx, col in enumerate(self.model.columns):
                if str(getattr(col, "key", "")) == "mapped_track":
                    mapped_col = idx
                    break
        current_row = row_index
        while current_row < row_count:
            idx = self.model.index(current_row, mapped_col)
            self.table.setCurrentIndex(idx)
            applied = self._map_single_row_by_index(idx)
            if not applied:
                break
            current_row += 1
            row_count = self.model.rowCount()

    def eventFilter(self, obj, event):
        if obj is self.table and event.type() == QEvent.Type.KeyPress:
            key_event = event if isinstance(event, QKeyEvent) else None
            if key_event is not None and key_event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                idx = self.table.currentIndex()
                if idx.isValid() and self._column_key_at(idx) == "mapped_track":
                    self._map_next_row_from(idx.row())
                    return True
        return super().eventFilter(obj, event)

    def _on_toggle_multi(self, checked: bool) -> None:
        mode = (
            QAbstractItemView.SelectionMode.ExtendedSelection
            if checked
            else QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setSelectionMode(mode)

    def _on_toggle_edit_mode(self, checked: bool) -> None:
        if checked:
            self.table.setEditTriggers(
                QAbstractItemView.EditTrigger.SelectedClicked
                | QAbstractItemView.EditTrigger.DoubleClicked
                | QAbstractItemView.EditTrigger.EditKeyPressed
            )
        else:
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    def _show_context_menu(self, pos) -> None:
        global_pos = self.table.viewport().mapToGlobal(pos)
        lyrics_ids = self._selected_lyrics_ids()
        if not lyrics_ids:
            return
        menu = QMenu(self)
        action_map_track = menu.addAction("映射到歌曲")
        action_edit_author = menu.addAction("批量改作者")
        action_delete = menu.addAction("删除歌词")
        action_reveal = menu.addAction("使用文件管理器查看")
        action_copy = menu.addAction("复制行数据")

        chosen = menu.exec(global_pos)
        if not chosen:
            return
        if chosen == action_map_track:
            self._map_selected_to_track()
            return
        if chosen == action_edit_author:
            self._edit_author_for_selected()
            return
        if chosen == action_delete:
            self._delete_selected_lyrics()
            return
        if chosen == action_reveal:
            row = self._selected_lyrics_row() or {}
            rel = str(row.get("storage_relpath", "") or "")
            path_text = str(Path(self.facade.library_root) / rel) if rel else ""
            _reveal_in_file_manager(self, path_text)
            return
        if chosen == action_copy:
            _copy_selected_cells(self.table)


