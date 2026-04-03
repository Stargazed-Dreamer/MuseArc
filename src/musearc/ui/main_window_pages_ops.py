from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QRadioButton,
    QButtonGroup,
    QLineEdit,
)
from PySide6.QtGui import QAction

from musearc.app.facade import FAVORITES_PLAYLIST_ID, MuseArcFacade
from musearc.ui.table_models import ColumnDef, DictTableModel
from musearc.ui.track_grid import TrackGridWidget, _copy_selected_cells, _install_copy_support
from musearc.ui.main_window_helpers import (
    _apply_button_scale,
    _choose_or_create_playlist,
    _handle_track_lyrics_cell_action,
    _install_row_function_shortcuts,
    _prompt_new_playlist,
    _resolve_delete_mode_and_maybe_save_default,
    _reveal_in_file_manager,
    _run_export_dialog,
    _show_track_details,
    _storage_path_for_track_row,
)
from musearc.ui.main_window_pages_common import _queue_play_tracks
from musearc.ui.long_task import make_chunked_task, run_modal_task


# ?????
# 1) FullScanPage???????????????????/???
# 2) TrashPage???????????? / ??????
# 3) TagManagementPage????? + ?????????????


def _run_chunked_ids_modal(
    parent: QWidget,
    *,
    title: str,
    message: str,
    ids: list[str],
    step,
    chunk_size: int = 512,
) -> tuple[dict, bool]:
    task = make_chunked_task(ids, chunk_size=chunk_size, message=message, step=step)
    outcome = run_modal_task(parent, title, task)
    if outcome.error is not None:
        raise outcome.error
    result = outcome.result if isinstance(outcome.result, dict) else {"processed": 0, "affected": 0, "cancelled": outcome.cancelled}
    return result, bool(outcome.cancelled)

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
        _install_row_function_shortcuts(
            self,
            [
                self.btn_pass,
                self.btn_add_playlist,
                self.btn_favorite,
                self.btn_unfavorite,
                self.btn_export,
                self.btn_delete,
            ],
            start_f=3,
        )

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
        dialog = QDialog(self)
        dialog.setWindowTitle("新建全量筛选工作")
        layout = QVBoxLayout(dialog)

        group = QButtonGroup(dialog)
        opt_all = QRadioButton("全部歌曲")
        opt_meta = QRadioButton("筛选名称高相似歌曲")
        opt_fp = QRadioButton("按新阈值筛选相似歌曲")
        opt_all.setChecked(True)
        group.addButton(opt_all, 0)
        group.addButton(opt_meta, 1)
        group.addButton(opt_fp, 2)
        layout.addWidget(opt_all)
        layout.addWidget(opt_meta)
        layout.addWidget(opt_fp)

        row_name = QHBoxLayout()
        row_name.addWidget(QLabel("工作名称"))
        edit_name = QLineEdit("全量歌曲筛选")
        edit_name.setPlaceholderText("请输入工作名称")
        row_name.addWidget(edit_name, 1)
        layout.addLayout(row_name)

        row_threshold = QHBoxLayout()
        row_threshold.addWidget(QLabel("相似度区间"))
        spin_low = QDoubleSpinBox()
        spin_low.setRange(0.0, 1.0)
        spin_low.setSingleStep(0.01)
        spin_low.setDecimals(3)
        spin_low.setValue(0.88)
        spin_high = QDoubleSpinBox()
        spin_high.setRange(0.0, 1.0)
        spin_high.setSingleStep(0.01)
        spin_high.setDecimals(3)
        spin_high.setValue(0.96)
        row_threshold.addWidget(spin_low)
        row_threshold.addWidget(QLabel("~"))
        row_threshold.addWidget(spin_high)
        row_threshold.addStretch(1)
        layout.addLayout(row_threshold)

        warn = QLabel("提示：区间过低会包含大量歌曲。")
        warn.setStyleSheet("color:#b3261e;")
        warn.hide()
        layout.addWidget(warn)

        default_name_map = {
            0: "全量歌曲筛选",
            1: "元数据高相似歌曲",
            2: "指纹高相似歌曲",
        }
        name_touched = {"value": False}
        last_default = {"value": "全量歌曲筛选"}

        def _set_default_name() -> None:
            selected_id = group.checkedId()
            default_name = default_name_map.get(selected_id, "全量歌曲筛选")
            current = edit_name.text().strip()
            if (not name_touched["value"]) or current == last_default["value"]:
                edit_name.setText(default_name)
            last_default["value"] = default_name

        def _on_name_changed(_text: str) -> None:
            name_touched["value"] = True

        edit_name.textChanged.connect(_on_name_changed)

        def _refresh_ui() -> None:
            is_fp = opt_fp.isChecked()
            spin_low.setEnabled(is_fp)
            spin_high.setEnabled(is_fp)
            low = float(spin_low.value())
            warn.setVisible(bool(is_fp and low < 0.60))
            _set_default_name()

        opt_all.toggled.connect(_refresh_ui)
        opt_meta.toggled.connect(_refresh_ui)
        opt_fp.toggled.connect(_refresh_ui)
        spin_low.valueChanged.connect(lambda _v: _refresh_ui())
        _refresh_ui()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        _set_default_name()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selected = group.checkedId()
        name = edit_name.text().strip() or default_name_map.get(selected, "全量歌曲筛选")
        work_id = ""
        if selected == 0:
            def _task_all(progress, _is_cancelled):
                progress(0, 1, "正在创建工作")
                wid = self.facade.create_fullscan_work_all(name)
                progress(1, 1, "正在创建工作")
                return {"work_id": wid}
            outcome = run_modal_task(self, "创建全量筛选工作", _task_all)
            if outcome.error is not None:
                QMessageBox.warning(self, "创建失败", f"创建工作失败\n{outcome.error}")
                return
            payload = outcome.result if isinstance(outcome.result, dict) else {}
            work_id = str(payload.get("work_id", "") or "")
        elif selected == 1:
            def _task_meta(progress, is_cancelled):
                wid = self.facade.create_fullscan_work_metadata_similar(
                    name,
                    progress_callback=progress,
                    is_cancelled=is_cancelled,
                )
                return {"work_id": wid}
            outcome = run_modal_task(self, "创建元数据高相似工作", _task_meta)
            if outcome.error is not None:
                QMessageBox.warning(self, "创建失败", f"创建工作失败\n{outcome.error}")
                return
            if outcome.cancelled:
                self.grid.set_status("创建工作已取消")
                return
            payload = outcome.result if isinstance(outcome.result, dict) else {}
            work_id = str(payload.get("work_id", "") or "")
        else:
            lower = float(spin_low.value())
            upper = float(spin_high.value())
            if min(lower, upper) < 0.60:
                answer = QMessageBox.question(
                    self,
                    "阈值较低",
                    "当前阈值可能包含大量歌曲，是否继续创建？",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            def _task_fp(progress, is_cancelled):
                wid = self.facade.create_fullscan_work_fingerprint_similar(
                    min_score=lower,
                    max_score=upper,
                    base_name=name,
                    progress_callback=progress,
                    is_cancelled=is_cancelled,
                )
                return {"work_id": wid}
            outcome = run_modal_task(self, "创建指纹高相似工作", _task_fp)
            if outcome.error is not None:
                QMessageBox.warning(self, "创建失败", f"创建工作失败\n{outcome.error}")
                return
            if outcome.cancelled:
                self.grid.set_status("创建工作已取消")
                return
            payload = outcome.result if isinstance(outcome.result, dict) else {}
            work_id = str(payload.get("work_id", "") or "")
        if not work_id:
            return
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
        try:
            result, cancelled = _run_chunked_ids_modal(
                self,
                title="从工作移除",
                message="正在从工作移除",
                ids=track_ids,
                step=lambda chunk: self.facade.remove_fullscan_items(self.current_work_id, chunk),
                chunk_size=512,
            )
        except Exception as exc:
            QMessageBox.warning(self, "操作失败", f"从工作移除失败\n{exc}")
            return
        count = int(result.get("affected", 0) or 0)
        self.on_work_changed()
        self.grid.set_status(f"已从工作移除 {count} 条" + ("（已取消）" if cancelled else ""))

    def add_selected_to_playlist(self) -> None:
        track_ids = self.selected_track_ids()
        if not track_ids:
            return
        playlist_id = _choose_or_create_playlist(self, self.facade, self.btn_add_playlist)
        if not playlist_id:
            return
        try:
            result, cancelled = _run_chunked_ids_modal(
                self,
                title="加到歌单",
                message="正在写入歌单",
                ids=track_ids,
                step=lambda chunk: self.facade.add_tracks_to_playlist(playlist_id, chunk),
                chunk_size=512,
            )
        except Exception as exc:
            QMessageBox.warning(self, "操作失败", f"加到歌单失败\n{exc}")
            return
        count = int(result.get("affected", 0) or 0)
        self.grid.set_status(f"已添加 {count} 条到歌单" + ("（已取消）" if cancelled else ""))
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
        ok, target = _run_export_dialog(self, self.facade, tracks, playlist_name="全量筛选")
        if not ok:
            return
        self.grid.set_status(f"已导出 {len(track_ids)} 条到 {target}")

    def on_delete(self) -> None:
        if not self.current_work_id:
            return
        track_ids = self.selected_track_ids()
        if not track_ids:
            return
        mode = _resolve_delete_mode_and_maybe_save_default(self, self.facade, len(track_ids), track_ids)
        if mode == "cancel":
            return
        def _step(chunk: list[str]) -> int:
            deleted = int(self.facade.delete_tracks(chunk, mode=mode) or 0)
            self.facade.remove_fullscan_items(self.current_work_id, chunk)
            return deleted
        try:
            result, cancelled = _run_chunked_ids_modal(
                self,
                title="移到回收站",
                message="正在移到回收站",
                ids=track_ids,
                step=_step,
                chunk_size=256,
            )
        except Exception as exc:
            QMessageBox.warning(self, "操作失败", f"移到回收站失败\n{exc}")
            return
        count = int(result.get("affected", 0) or 0)
        self.on_work_changed()
        self.grid.set_status(f"已移到回收站 {count} 条" + ("（已取消）" if cancelled else ""))
        self.library_changed.emit()

    def on_track_field_edited(self, track_id: str, key: str, value) -> None:
        if not track_id or key == "custom_order":
            return
        if key == "lyrics_file_name":
            row = self.grid.track_by_id(track_id)
            if row and _handle_track_lyrics_cell_action(self, self.facade, [row], action=None):
                QTimer.singleShot(0, self.on_work_changed)
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
            QTimer.singleShot(0, self.on_work_changed)
            return
        QTimer.singleShot(0, self.library_changed.emit)

    def _show_context_menu(self, pos, tracks: list[dict]) -> None:
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        if not track_ids:
            return
        can_favorite = any(not bool(t.get("is_favorite")) for t in tracks)
        can_unfavorite = any(bool(t.get("is_favorite")) for t in tracks)

        menu = QMenu(self)
        action_play = menu.addAction("播放")
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
        action_change_lyrics = menu.addAction("更改歌词绑定")
        action_jump_lyrics = menu.addAction("跳转到歌词")
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
            try:
                result, cancelled = _run_chunked_ids_modal(
                    self,
                    title="加到歌单",
                    message="正在写入歌单",
                    ids=track_ids,
                    step=lambda chunk: self.facade.add_tracks_to_playlist(add_map[chosen], chunk),
                    chunk_size=512,
                )
            except Exception as exc:
                QMessageBox.warning(self, "操作失败", f"加到歌单失败\n{exc}")
                return
            count = int(result.get("affected", 0) or 0)
            self.grid.set_status(f"已添加 {count} 条到歌单" + ("（已取消）" if cancelled else ""))
            self.library_changed.emit()
            return
        if chosen == action_add_new:
            target = _prompt_new_playlist(self, self.facade)
            if target:
                try:
                    result, cancelled = _run_chunked_ids_modal(
                        self,
                        title="加到歌单",
                        message="正在写入歌单",
                        ids=track_ids,
                        step=lambda chunk: self.facade.add_tracks_to_playlist(target, chunk),
                        chunk_size=512,
                    )
                except Exception as exc:
                    QMessageBox.warning(self, "操作失败", f"加到歌单失败\n{exc}")
                    return
                count = int(result.get("affected", 0) or 0)
                self.grid.set_status(f"已添加 {count} 条到歌单" + ("（已取消）" if cancelled else ""))
                self.library_changed.emit()
            return
        if chosen == action_change_lyrics:
            if _handle_track_lyrics_cell_action(self, self.facade, tracks, action="change_mapping"):
                QTimer.singleShot(0, self.on_work_changed)
                QTimer.singleShot(0, self.library_changed.emit)
            return
        if chosen == action_jump_lyrics:
            _handle_track_lyrics_cell_action(self, self.facade, tracks, action="jump_to_lyrics")
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
        self.btn_delete_file = QPushButton("删除文件（保留元数据）")
        self.btn_delete_file.setStyleSheet("background-color:#b3261e;color:white;")
        self.btn_delete_meta = QPushButton("删除元数据")
        self.btn_delete_meta.setStyleSheet("background-color:#8b1e1e;color:white;")
        row.addWidget(self.btn_restore)
        row.addWidget(self.btn_delete_file)
        row.addWidget(self.btn_delete_meta)
        row.addStretch(1)

        split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("文件仍在（可恢复 / 可彻底删文件）"))
        self.left_model = DictTableModel(
            [
                ColumnDef("item_type_label", "类型"),
                ColumnDef("file_name", "文件名"),
                ColumnDef("title", "标题"),
                ColumnDef("artist", "艺术家"),
                ColumnDef("deleted_at", "删除时间"),
                ColumnDef("item_id", "ID"),
            ]
        )
        self.left_table = QTableView()
        self.left_table.setModel(self.left_model)
        self.left_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.left_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.left_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.left_table.setAlternatingRowColors(True)
        self.left_table.setSortingEnabled(True)
        self.left_table.horizontalHeader().setStretchLastSection(True)
        _install_copy_support(self.left_table)
        self.left_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.left_table.customContextMenuRequested.connect(lambda pos: self._show_context_menu(self.left_table, pos))
        left_layout.addWidget(self.left_table, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("仅元数据（文件已删）"))
        self.right_model = DictTableModel(
            [
                ColumnDef("item_type_label", "类型"),
                ColumnDef("file_name", "文件名"),
                ColumnDef("title", "标题"),
                ColumnDef("artist", "艺术家"),
                ColumnDef("deleted_at", "删除时间"),
                ColumnDef("item_id", "ID"),
            ]
        )
        self.right_table = QTableView()
        self.right_table.setModel(self.right_model)
        self.right_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.right_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.right_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.right_table.setAlternatingRowColors(True)
        self.right_table.setSortingEnabled(True)
        self.right_table.horizontalHeader().setStretchLastSection(True)
        _install_copy_support(self.right_table)
        self.right_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.right_table.customContextMenuRequested.connect(lambda pos: self._show_context_menu(self.right_table, pos))
        right_layout.addWidget(self.right_table, 1)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)

        root.addLayout(row)
        root.addWidget(split, 1)
        self.status = QLabel("-")
        root.addWidget(self.status)

        self.btn_restore.clicked.connect(self.restore_selected)
        self.btn_delete_file.clicked.connect(self.delete_selected_files)
        self.btn_delete_meta.clicked.connect(self.delete_selected_metadata)

        self.reload_trash()

    def apply_button_scale(self, scale: float) -> None:
        _apply_button_scale(self.btn_restore, scale)
        _apply_button_scale(self.btn_delete_file, scale)
        _apply_button_scale(self.btn_delete_meta, scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade
        self.reload_trash()

    def refresh_page(self) -> None:
        self.reload_trash()

    def reload_trash(self) -> None:
        rows = self.facade.list_deleted_items(limit=2_000_000)
        left_rows = [r for r in rows if bool(r.get("file_exists"))]
        right_rows = [r for r in rows if not bool(r.get("file_exists"))]
        self.left_model.set_rows(left_rows)
        self.right_model.set_rows(right_rows)
        self.status.setText(f"回收站 共 {len(rows)} 条 | 文件仍在 {len(left_rows)} | 仅元数据 {len(right_rows)}")

    def _selected_rows_from(self, table: QTableView, model: DictTableModel) -> list[dict]:
        sm = table.selectionModel()
        if sm is None:
            return []
        rows: list[dict] = []
        for idx in sm.selectedRows():
            row = model.row_at(idx.row())
            if row:
                rows.append(row)
        return rows

    def _selected_items(self) -> list[dict]:
        picked: dict[tuple[str, str], dict] = {}
        for row in self._selected_rows_from(self.left_table, self.left_model) + self._selected_rows_from(self.right_table, self.right_model):
            key = (str(row.get("item_type", "")), str(row.get("item_id", "")))
            if key[0] and key[1]:
                picked[key] = row
        return list(picked.values())

    def restore_selected(self) -> None:
        items = [r for r in self._selected_items() if bool(r.get("file_exists"))]
        if not items:
            QMessageBox.information(self, "恢复", "仅“文件仍在”列表中的项目可恢复。")
            return
        total = len(items)
        def _task(progress, is_cancelled):
            tracks = 0
            lyrics = 0
            chunk_size = 128
            processed = 0
            for start in range(0, total, chunk_size):
                if is_cancelled():
                    break
                chunk = items[start : start + chunk_size]
                part = self.facade.restore_deleted_items(chunk)
                tracks += int(part.get("tracks", 0) or 0)
                lyrics += int(part.get("lyrics", 0) or 0)
                processed += len(chunk)
                progress(processed, total, "正在恢复")
            return {"tracks": tracks, "lyrics": lyrics, "cancelled": bool(is_cancelled() and processed < total)}
        outcome = run_modal_task(self, "恢复项目", _task)
        if outcome.error is not None:
            QMessageBox.warning(self, "恢复失败", f"恢复失败\n{outcome.error}")
            return
        restored = outcome.result if isinstance(outcome.result, dict) else {"tracks": 0, "lyrics": 0}
        self.reload_trash()
        self.status.setText(
            f"已恢复 歌曲 {restored.get('tracks',0)} 条，歌词 {restored.get('lyrics',0)} 条"
            + ("（已取消）" if bool(restored.get("cancelled")) else "")
        )
        self.library_changed.emit()

    def delete_selected_files(self) -> None:
        items = self._selected_items()
        if not items:
            return
        answer = QMessageBox.question(self, "删除文件", f"仅删除 {len(items)} 条对应文件（保留元数据）？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        total = len(items)
        def _task(progress, is_cancelled):
            tracks = 0
            lyrics = 0
            chunk_size = 128
            processed = 0
            for start in range(0, total, chunk_size):
                if is_cancelled():
                    break
                chunk = items[start : start + chunk_size]
                part = self.facade.purge_deleted_item_files(chunk)
                tracks += int(part.get("tracks", 0) or 0)
                lyrics += int(part.get("lyrics", 0) or 0)
                processed += len(chunk)
                progress(processed, total, "正在删除文件")
            return {"tracks": tracks, "lyrics": lyrics, "cancelled": bool(is_cancelled() and processed < total)}
        outcome = run_modal_task(self, "删除文件", _task)
        if outcome.error is not None:
            QMessageBox.warning(self, "删除失败", f"删除文件失败\n{outcome.error}")
            return
        removed = outcome.result if isinstance(outcome.result, dict) else {"tracks": 0, "lyrics": 0}
        self.status.setText(
            f"已删除文件 歌曲 {removed.get('tracks',0)} 个，歌词 {removed.get('lyrics',0)} 个（元数据保留）"
            + ("（已取消）" if bool(removed.get("cancelled")) else "")
        )
        self.reload_trash()

    def delete_selected_metadata(self) -> None:
        items = self._selected_items()
        if not items:
            return
        answer1 = QMessageBox.warning(
            self,
            "删除元数据",
            f"将永久删除 {len(items)} 条回收站元数据，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer1 != QMessageBox.StandardButton.Yes:
            return
        answer2 = QMessageBox.warning(
            self,
            "再次确认",
            "该操作不可撤销，确认永久删除元数据？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer2 != QMessageBox.StandardButton.Yes:
            return
        total = len(items)
        def _task(progress, is_cancelled):
            tracks = 0
            lyrics = 0
            chunk_size = 128
            processed = 0
            for start in range(0, total, chunk_size):
                if is_cancelled():
                    break
                chunk = items[start : start + chunk_size]
                part = self.facade.delete_deleted_items_metadata(chunk)
                tracks += int(part.get("tracks", 0) or 0)
                lyrics += int(part.get("lyrics", 0) or 0)
                processed += len(chunk)
                progress(processed, total, "正在删除元数据")
            return {"tracks": tracks, "lyrics": lyrics, "cancelled": bool(is_cancelled() and processed < total)}
        outcome = run_modal_task(self, "删除元数据", _task)
        if outcome.error is not None:
            QMessageBox.warning(self, "删除失败", f"删除元数据失败\n{outcome.error}")
            return
        removed = outcome.result if isinstance(outcome.result, dict) else {"tracks": 0, "lyrics": 0}
        self.status.setText(
            f"已删除元数据 歌曲 {removed.get('tracks',0)} 条，歌词 {removed.get('lyrics',0)} 条"
            + ("（已取消）" if bool(removed.get("cancelled")) else "")
        )
        self.reload_trash()
        self.library_changed.emit()

    def _show_context_menu(self, table: QTableView, pos) -> None:
        model = self.left_model if table is self.left_table else self.right_model
        rows = self._selected_rows_from(table, model)
        if not rows:
            return
        first = rows[0]
        menu = QMenu(self)
        action_restore = menu.addAction("恢复")
        action_delete_file = menu.addAction("删除文件（保留元数据）")
        action_delete_meta = menu.addAction("删除元数据")
        action_reveal = menu.addAction("使用文件管理器查看")
        action_copy = menu.addAction("复制行数据")
        action_detail = menu.addAction("查看详情")
        global_pos = table.viewport().mapToGlobal(pos)
        chosen = menu.exec(global_pos)
        if not chosen:
            return
        if chosen == action_restore:
            self.restore_selected()
            return
        if chosen == action_delete_file:
            self.delete_selected_files()
            return
        if chosen == action_delete_meta:
            self.delete_selected_metadata()
            return
        if chosen == action_reveal:
            rel = str(first.get("storage_relpath", "") or "").strip()
            path_text = str(Path(self.facade.library_root) / rel) if rel else ""
            _reveal_in_file_manager(self, path_text)
            return
        if chosen == action_copy:
            _copy_selected_cells(table)
            return
        if chosen == action_detail:
            lines = [
                f"类型: {first.get('item_type_label','')}",
                f"ID: {first.get('item_id','')}",
                f"文件名: {first.get('file_name','')}",
                f"标题: {first.get('title','')}",
                f"艺术家: {first.get('artist','')}",
                f"专辑: {first.get('album','')}",
                f"Storage: {first.get('storage_relpath','')}",
                f"Deleted At: {first.get('deleted_at','')}",
            ]
            QMessageBox.information(self, "回收站详情", "\n".join(lines))

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
        self.btn_tools = QPushButton("小工具")
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_delete)
        row.addWidget(self.btn_tools)
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
        self.btn_tools.clicked.connect(self._open_tools_menu)
        self.btn_remove_from_tag.clicked.connect(self._remove_selected_from_tag)
        self.btn_export.clicked.connect(self._on_export)
        self.btn_favorite.clicked.connect(self._on_favorite)
        self.btn_unfavorite.clicked.connect(self._on_unfavorite)
        self.btn_delete_from_library.clicked.connect(self._on_delete_from_library)
        self.tree.currentItemChanged.connect(self._on_tag_changed)
        self.grid.track_field_edited.connect(self._on_track_field_edited)
        self.grid.context_menu_requested.connect(self._show_context_menu)
        _install_row_function_shortcuts(
            self,
            [
                self.btn_remove_from_tag,
                self.btn_export,
                self.btn_favorite,
                self.btn_unfavorite,
                self.btn_delete_from_library,
            ],
            start_f=3,
        )

        self.reload_tags()

    def apply_button_scale(self, scale: float) -> None:
        _apply_button_scale(self.btn_add, scale)
        _apply_button_scale(self.btn_delete, scale)
        _apply_button_scale(self.btn_tools, scale)
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
        try:
            result, cancelled = _run_chunked_ids_modal(
                self,
                title="从标签移除",
                message="正在清理标签",
                ids=track_ids,
                step=lambda chunk: self.facade.update_track_tag_values(chunk, tag_name, ""),
                chunk_size=512,
            )
        except Exception as exc:
            QMessageBox.warning(self, "操作失败", f"从标签移除失败\n{exc}")
            return
        count = int(result.get("affected", 0) or 0)
        self.grid.set_status(f"已从标签“{tag_name}”移除 {count} 首" + ("（已取消）" if cancelled else ""))
        self._reload_tracks_for_current_tag()
        self.reload_tags()
        self.library_changed.emit()

    def _on_export(self) -> None:
        tracks = self._selected_tracks()
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        if not track_ids:
            return
        ok, target = _run_export_dialog(self, self.facade, tracks, playlist_name=f"标签_{self.current_tag_name or 'tracks'}")
        if not ok:
            return
        self.grid.set_status(f"已导出 {len(track_ids)} 条到 {target}")

    def _open_tools_menu(self) -> None:
        menu = QMenu(self)
        action_love = menu.addAction("计算喜爱程度")
        action_sync_preference = menu.addAction("喜好同步")
        chosen = menu.exec(self.btn_tools.mapToGlobal(self.btn_tools.rect().bottomLeft()))
        if chosen == action_love:
            count = self.facade.recompute_love_score_tag()
            self.grid.set_status(f"已更新 {count} 首的喜爱程度")
            self.reload_tags()
            self._reload_tracks_for_current_tag()
            self.library_changed.emit()
            return
        if chosen == action_sync_preference:
            answer = QMessageBox.question(
                self,
                "喜好同步",
                "将标签【喜爱程度】同步到【喜好(1-10)】（除以10并四舍五入）？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            count = self.facade.sync_preference_from_love_tag()
            self.grid.set_status(f"已同步 {count} 首的喜好")
            self._reload_tracks_for_current_tag()
            self.library_changed.emit()
            return

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
        mode = _resolve_delete_mode_and_maybe_save_default(self, self.facade, len(track_ids), track_ids)
        if mode == "cancel":
            return
        try:
            result, cancelled = _run_chunked_ids_modal(
                self,
                title="移到回收站",
                message="正在移到回收站",
                ids=track_ids,
                step=lambda chunk: self.facade.delete_tracks(chunk, mode=mode),
                chunk_size=256,
            )
        except Exception as exc:
            QMessageBox.warning(self, "操作失败", f"移到回收站失败\n{exc}")
            return
        deleted = int(result.get("affected", 0) or 0)
        self.grid.set_status(f"已移到回收站 {deleted} 条" + ("（已取消）" if cancelled else ""))
        self._reload_tracks_for_current_tag()
        self.reload_tags()
        self.library_changed.emit()

    def _on_track_field_edited(self, track_id: str, key: str, value) -> None:
        if not track_id or key == "custom_order":
            return
        if key == "lyrics_file_name":
            row = self.grid.track_by_id(track_id)
            if row and _handle_track_lyrics_cell_action(self, self.facade, [row], action=None):
                QTimer.singleShot(0, self._reload_tracks_for_current_tag)
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
            QTimer.singleShot(0, self._reload_tracks_for_current_tag)
            QTimer.singleShot(0, self.reload_tags)
            return
        if key == f"tag:{self.current_tag_name or ''}":
            QTimer.singleShot(0, self._reload_tracks_for_current_tag)
            QTimer.singleShot(0, self.reload_tags)
        QTimer.singleShot(0, self.library_changed.emit)

    def _show_context_menu(self, pos, tracks: list[dict]) -> None:
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        if not track_ids:
            return
        can_favorite = any(not bool(t.get("is_favorite")) for t in tracks)
        can_unfavorite = any(bool(t.get("is_favorite")) for t in tracks)
        menu = QMenu(self)
        action_play = menu.addAction("播放")
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
        action_change_lyrics = menu.addAction("更改歌词绑定")
        action_jump_lyrics = menu.addAction("跳转到歌词")
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
            try:
                result, cancelled = _run_chunked_ids_modal(
                    self,
                    title="加到歌单",
                    message="正在写入歌单",
                    ids=track_ids,
                    step=lambda chunk: self.facade.add_tracks_to_playlist(add_map[chosen], chunk),
                    chunk_size=512,
                )
            except Exception as exc:
                QMessageBox.warning(self, "操作失败", f"加到歌单失败\n{exc}")
                return
            count = int(result.get("affected", 0) or 0)
            self.grid.set_status(f"已添加 {count} 条到歌单" + ("（已取消）" if cancelled else ""))
            self.library_changed.emit()
            return
        if chosen == action_add_new:
            target = _prompt_new_playlist(self, self.facade)
            if target:
                try:
                    result, cancelled = _run_chunked_ids_modal(
                        self,
                        title="加到歌单",
                        message="正在写入歌单",
                        ids=track_ids,
                        step=lambda chunk: self.facade.add_tracks_to_playlist(target, chunk),
                        chunk_size=512,
                    )
                except Exception as exc:
                    QMessageBox.warning(self, "操作失败", f"加到歌单失败\n{exc}")
                    return
                count = int(result.get("affected", 0) or 0)
                self.grid.set_status(f"已添加 {count} 条到歌单" + ("（已取消）" if cancelled else ""))
                self.library_changed.emit()
            return
        if chosen == action_change_lyrics:
            if _handle_track_lyrics_cell_action(self, self.facade, tracks, action="change_mapping"):
                QTimer.singleShot(0, self._reload_tracks_for_current_tag)
                QTimer.singleShot(0, self.library_changed.emit)
            return
        if chosen == action_jump_lyrics:
            _handle_track_lyrics_cell_action(self, self.facade, tracks, action="jump_to_lyrics")
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

