from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QItemSelectionModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableView, QVBoxLayout, QWidget

from musearc.app.facade import MuseArcFacade
from musearc.ui.table_models import ColumnDef
from musearc.ui.track_grid import LyricsTableModel, _copy_selected_cells, _install_copy_support, _safe_int
from musearc.ui.main_window_helpers import (
    TrackPickerDialog,
    _apply_button_scale,
    _install_row_function_shortcuts,
    _reveal_in_file_manager,
)
from musearc.ui.long_task import run_modal_task


# ?????
# LyricsManagementPage ???????????????
# - ?????
# - ??????????
# - ???????????

class LyricsManagementPage(QWidget):
    library_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade
        self._all_rows: list[dict] = []
        self._sort_states: dict[str, str] = {}

        root = QVBoxLayout(self)

        row_top = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索 文件名/标题/艺术家/专辑/歌词作者")
        self.btn_search = QPushButton("搜索")
        row_top.addWidget(self.search_input, 1)
        row_top.addWidget(self.btn_search)

        row_ctrl = QHBoxLayout()
        self.combo_group = QComboBox()
        self.combo_group.addItem("不分组", "none")
        self.combo_group.addItem("文件名", "file_name")
        self.combo_group.addItem("歌曲标题", "lyrics_title")
        self.combo_group.addItem("艺术家", "lyrics_artist")
        self.combo_group.addItem("专辑", "lyrics_album")
        self.combo_group.addItem("歌词文件作者", "lyrics_author")
        self.combo_group.addItem("语言", "lyrics_language")
        self.combo_group.addItem("对应歌曲", "mapped_track")
        self.btn_invert = QPushButton("反选")
        self.chk_multi = QCheckBox("多选模式")
        self.chk_multi.setChecked(False)
        self.chk_edit_mode = QCheckBox("编辑模式")
        row_ctrl.addWidget(self.btn_invert)
        row_ctrl.addWidget(QLabel("分组"))
        row_ctrl.addWidget(self.combo_group)
        row_ctrl.addWidget(self.chk_multi)
        row_ctrl.addWidget(self.chk_edit_mode)
        row_ctrl.addStretch(1)

        row_ops = QHBoxLayout()
        self.btn_map_track = QPushButton("映射到歌曲")
        self.btn_edit_author = QPushButton("批量改作者")
        self.btn_delete = QPushButton("删除歌词")
        self.btn_delete.setStyleSheet("background-color:#b3261e;color:white;")
        self.chk_preview = QCheckBox("预览歌词")
        row_ops.addWidget(self.btn_map_track)
        row_ops.addWidget(self.btn_edit_author)
        row_ops.addWidget(self.btn_delete)
        row_ops.addStretch(1)

        row_preview = QHBoxLayout()
        row_preview.addStretch(1)
        row_preview.addWidget(self.chk_preview)

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
                ColumnDef("lyrics_language", "语言"),
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
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setSectionsMovable(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
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
        root.addLayout(row_ctrl)
        root.addLayout(row_ops)
        root.addLayout(row_preview)
        root.addWidget(self.splitter, 1)

        self.btn_search.clicked.connect(self.apply_filter)
        self.btn_invert.clicked.connect(self._invert_selection)
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
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self.table.horizontalHeader().sectionMoved.connect(lambda *_args: self._sync_sort_from_header())
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.installEventFilter(self)
        self.model.lyrics_field_edited.connect(self._on_lyrics_field_edited)
        if self.table.selectionModel() is not None:
            self.table.selectionModel().selectionChanged.connect(lambda *_args: self._refresh_preview())
        _install_row_function_shortcuts(
            self,
            [
                self.btn_map_track,
                self.btn_edit_author,
                self.btn_delete,
            ],
            start_f=3,
        )

        self._on_toggle_multi(self.chk_multi.isChecked())
        self._on_toggle_edit_mode(self.chk_edit_mode.isChecked())
        self._init_sort_states()
        self.reload_lyrics()

    def apply_button_scale(self, scale: float) -> None:
        _apply_button_scale(self.btn_search, scale)
        _apply_button_scale(self.btn_invert, scale)
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
        for row in self._all_rows:
            row["lyrics_language"] = str(row.get("lyrics_language", "") or "unknown")
        self.apply_filter()

    def _init_sort_states(self) -> None:
        keys = [str(col.key) for col in self.model.columns]
        keep: dict[str, str] = {}
        for key in keys:
            keep[key] = self._sort_states.get(key, "off")
        if all(v == "off" for v in keep.values()) and "file_name" in keep:
            keep["file_name"] = "asc"
        self._sort_states = keep
        self.model.set_header_sort_states(self._sort_states)

    def _next_sort_state(self, state: str) -> str:
        if state == "asc":
            return "desc"
        if state == "desc":
            return "off"
        return "asc"

    def _sync_sort_from_header(self) -> None:
        self.model.set_header_sort_states(self._sort_states)
        self.apply_filter()

    @staticmethod
    def _lyrics_sort_value(row: dict, key: str):
        value = row.get(key, "")
        if key == "line_count":
            return _safe_int(value, 0)
        text = str(value or "")
        try:
            return float(text)
        except Exception:
            return text.casefold()

    def _sort_rows_by_rules(self, rows: list[dict]) -> list[dict]:
        out = list(rows)
        header = self.table.horizontalHeader()
        logical_indexes = sorted(range(len(self.model.columns)), key=lambda i: header.visualIndex(i))
        active: list[tuple[str, str]] = []
        for logical in logical_indexes:
            key = str(self.model.columns[logical].key)
            state = self._sort_states.get(key, "off")
            if state in {"asc", "desc"}:
                active.append((key, state))
        if not active:
            active = [("file_name", "asc")]
        for key, state in reversed(active):
            out.sort(key=lambda r, k=key: self._lyrics_sort_value(r, k), reverse=(state == "desc"))
        return out

    def _on_header_clicked(self, logical: int) -> None:
        if logical < 0 or logical >= len(self.model.columns):
            return
        key = str(self.model.columns[logical].key)
        self._sort_states[key] = self._next_sort_state(self._sort_states.get(key, "off"))
        self._sync_sort_from_header()

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
                        str(row.get("lyrics_language", "")),
                        str(row.get("mapped_track", "")),
                    ]
                ).casefold()
                if token in text:
                    rows.append(row)

        rows = self._sort_rows_by_rules(rows)
        if group_key and group_key != "none":
            rows.sort(key=lambda r: str(r.get(group_key, "")).casefold())
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

        total = len(lyrics_ids)

        def _task(progress, is_cancelled):
            deleted = 0
            chunk_size = 256
            processed = 0
            for start in range(0, total, chunk_size):
                if is_cancelled():
                    break
                chunk = lyrics_ids[start : start + chunk_size]
                deleted += int(self.facade.delete_lyrics(chunk) or 0)
                processed += len(chunk)
                progress(processed, total, "正在删除歌词")
            return {"deleted": deleted, "cancelled": bool(is_cancelled() and processed < total)}

        outcome = run_modal_task(self, "删除歌词", _task)
        if outcome.error is not None:
            QMessageBox.warning(self, "删除歌词", f"删除失败\n{outcome.error}")
            return
        payload = outcome.result if isinstance(outcome.result, dict) else {}
        deleted = int(payload.get("deleted", 0) or 0)
        cancelled = bool(payload.get("cancelled"))

        self.reload_lyrics()
        self.preview.clear()
        self.library_changed.emit()
        QMessageBox.information(self, "删除歌词", f"已删除 {deleted} 条歌词" + ("（已取消）" if cancelled else ""))

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
            if self.chk_multi.isChecked():
                return
            mods = QApplication.keyboardModifiers()
            if bool(mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)):
                return
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
            QTimer.singleShot(0, self.reload_lyrics)
            return
        for row in self._all_rows:
            if str(row.get("lyrics_id", "")) != lyrics_id:
                continue
            row[key] = value
            break
        QTimer.singleShot(0, self.library_changed.emit)

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
            QAbstractItemView.SelectionMode.MultiSelection
            if checked
            else QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setSelectionMode(mode)

    def _invert_selection(self) -> None:
        model = self.model
        if model is None or self.table.selectionModel() is None:
            return
        total = model.rowCount()
        if total <= 0:
            return
        sm = self.table.selectionModel()
        selected = {idx.row() for idx in sm.selectedRows()}

        def _compute_targets(progress, is_cancelled):
            out: list[int] = []
            step = max(1, total // 200)
            for row in range(total):
                if is_cancelled():
                    return {"rows": out, "cancelled": True}
                if row not in selected:
                    out.append(row)
                curr = row + 1
                if curr == total or curr % step == 0:
                    progress(curr, total, "正在计算反选")
            return {"rows": out, "cancelled": False}

        if total >= 10000:
            outcome = run_modal_task(self, "反选", _compute_targets)
            if outcome.error is not None:
                QMessageBox.warning(self, "反选失败", f"反选失败\n{outcome.error}")
                return
            payload = outcome.result if isinstance(outcome.result, dict) else {}
            rows = [int(v) for v in payload.get("rows", [])]
            if bool(payload.get("cancelled")) and not rows:
                return
        else:
            payload = _compute_targets(lambda *_args: None, lambda: False)
            rows = [int(v) for v in payload.get("rows", [])]

        sm.clearSelection()
        mode = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        for row in rows:
            idx = model.index(row, 0)
            sm.select(idx, mode)

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

    def focus_lyrics_id(self, lyrics_id: str) -> bool:
        target = str(lyrics_id or "").strip()
        if not target:
            return False
        self.search_input.clear()
        self.apply_filter()
        for row in range(self.model.rowCount()):
            payload = self.model.row_at(row) or {}
            if str(payload.get("lyrics_id", "") or "") != target:
                continue
            idx = self.model.index(row, 0)
            if not idx.isValid():
                continue
            self.table.setCurrentIndex(idx)
            self.table.selectRow(row)
            self.table.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
            self._refresh_preview()
            return True
        return False

