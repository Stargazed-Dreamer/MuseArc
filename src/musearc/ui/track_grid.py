from __future__ import annotations

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from musearc.app.facade import MuseArcFacade
from musearc.ui.selection import SelectionController, SelectionMode
from musearc.ui.table_models import ColumnDef, DictTableModel
from musearc.ui.track_table_model import TrackTableModel
from musearc.ui.main_window_helpers import _apply_button_scale
from musearc.ui.long_task import run_modal_task


def _copy_selected_cells(table: QTableView) -> None:
    selection_model = table.selectionModel()
    if selection_model is None:
        return
    indexes = selection_model.selectedIndexes()
    if not indexes and hasattr(table, "controller") and table.model() is not None:
        controller = getattr(table, "controller", None)
        selected_rows = sorted(getattr(controller, "selected_rows", set())) if controller is not None else []
        if selected_rows:
            model = table.model()
            for row in selected_rows:
                for col in range(model.columnCount()):
                    idx = model.index(row, col)
                    if idx.isValid():
                        indexes.append(idx)
    if not indexes:
        return

    cells: dict[int, dict[int, str]] = {}
    max_col = 0
    for idx in indexes:
        row = idx.row()
        col = idx.column()
        max_col = max(max_col, col)
        cells.setdefault(row, {})[col] = str(idx.data() or "")

    lines: list[str] = []
    for row in sorted(cells.keys()):
        cols = cells[row]
        line = [cols.get(col, "") for col in range(max_col + 1)]
        lines.append("\t".join(line))

    QApplication.clipboard().setText("\n".join(lines))


def _install_copy_support(table: QTableView) -> None:
    shortcut = QShortcut(QKeySequence.StandardKey.Copy, table)
    shortcut.activated.connect(lambda: _copy_selected_cells(table))
    table._copy_shortcut = shortcut


def _next_sort_state(state: str) -> str:
    if state == "asc":
        return "desc"
    if state == "desc":
        return "off"
    return "asc"


def _safe_int(value, default: int = 0) -> int:
    if isinstance(value, (list, tuple, set, dict)):
        return default
    try:
        return int(value or 0)
    except Exception:
        return default


def _marker_for_state(state: str) -> str:
    if state == "asc":
        return "↑"
    if state == "desc":
        return "↓"
    return "·"


class TrackTableView(QTableView):
    context_menu_requested = Signal(object)
    ctrl_edit_requested = Signal(object)

    def __init__(self, controller: SelectionController):
        super().__init__()
        self.controller = controller
        self.edit_mode = False
        self._drag_origin: int | None = None
        self._drag_preview_base: set[int] | None = None
        self._dragging = False
        self._press_row: int | None = None

        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.setAlternatingRowColors(False)
        self.verticalHeader().setVisible(False)
        self.setTabKeyNavigation(True)

    def set_mode(self, mode: SelectionMode) -> None:
        self.controller.mode = mode
        if mode == SelectionMode.MULTI:
            self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            self.setStyleSheet(
                "QTableView{selection-background-color: transparent; selection-color: inherit;}"
                "QTableView::item:selected{background: transparent; color: inherit;}"
                "QTableView::item:selected:active{background: transparent; color: inherit;}"
                "QTableView::item:selected:!active{background: transparent; color: inherit;}"
                "QTableView::item:focus{border:1px solid #2f7dff;}"
            )
            self.clearSelection()
        else:
            self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            self.setStyleSheet("")
        self.apply_controller_selection()

    def set_edit_mode(self, enabled: bool) -> None:
        self.edit_mode = bool(enabled)

    def row_count(self) -> int:
        if self.model() is None:
            return 0
        return self.model().rowCount()

    def selected_rows(self) -> list[int]:
        if self.controller.mode == SelectionMode.MULTI:
            rows = sorted(self.controller.selected_rows)
            self._sync_visual_selection()
            return rows
        rows = sorted({idx.row() for idx in self.selectionModel().selectedRows()})
        if rows:
            self.controller.selected_rows = set(rows)
            self.controller.focus_row = rows[-1]
            self.controller.anchor_row = rows[0]
        self._sync_visual_selection()
        return rows

    def _sync_visual_selection(self) -> None:
        model = self.model()
        if model is None:
            return
        if not hasattr(model, "selected_track_ids_from_rows"):
            return
        if not hasattr(model, "set_visual_selected_track_ids"):
            return
        rows = sorted(self.controller.selected_rows)
        track_ids = set(model.selected_track_ids_from_rows(rows))
        model.set_visual_selected_track_ids(track_ids)

    def set_selected_rows(self, rows: list[int]) -> None:
        rows_set = {r for r in rows if 0 <= r < self.row_count()}
        self.controller.selected_rows = rows_set
        if rows_set:
            row = min(rows_set)
            self.controller.focus_row = row
            self.controller.anchor_row = row
        self.apply_controller_selection()

    def apply_controller_selection(self) -> None:
        if self.model() is None or self.selectionModel() is None:
            return
        self.blockSignals(True)
        self.selectionModel().clearSelection()
        if self.controller.mode == SelectionMode.NORMAL:
            rows = sorted(r for r in self.controller.selected_rows if 0 <= r < self.row_count())
            if rows:
                selection = QItemSelection()
                start = rows[0]
                end = rows[0]
                for row in rows[1:]:
                    if row == end + 1:
                        end = row
                        continue
                    selection.select(self.model().index(start, 0), self.model().index(end, 0))
                    start = row
                    end = row
                selection.select(self.model().index(start, 0), self.model().index(end, 0))
                self.selectionModel().select(
                    selection,
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )
        if self.controller.focus_row is not None and 0 <= self.controller.focus_row < self.row_count():
            self.setCurrentIndex(self.model().index(self.controller.focus_row, 0))
        self.blockSignals(False)
        self._sync_visual_selection()
        self.viewport().update()

    def _row_at_event(self, event: QMouseEvent) -> int:
        idx = self.indexAt(event.pos())
        return idx.row() if idx.isValid() else -1

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.controller.mode == SelectionMode.NORMAL:
            idx = self.indexAt(event.pos())
            if event.button() == Qt.MouseButton.RightButton:
                sm = self.selectionModel()
                selected_rows = {i.row() for i in sm.selectedRows()} if sm is not None else set()
                if idx.isValid() and idx.row() not in selected_rows and sm is not None:
                    sm.clearSelection()
                    sm.select(
                        idx,
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                    )
                    self.setCurrentIndex(idx)
                self.selected_rows()
                return
            super().mousePressEvent(event)
            self.selected_rows()
            if self.edit_mode and event.button() == Qt.MouseButton.LeftButton and idx.isValid():
                model = self.model()
                if model is not None and bool(model.flags(idx) & Qt.ItemFlag.ItemIsEditable):
                    self.edit(idx)
            return

        idx = self.indexAt(event.pos())
        if not idx.isValid():
            return
        row = idx.row()

        if event.button() == Qt.MouseButton.RightButton:
            if row not in self.controller.selected_rows:
                self.controller.selected_rows = {row}
                self.controller.anchor_row = row
                self.controller.focus_row = row
                self.apply_controller_selection()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        if bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.controller.focus_row = row
            self.setCurrentIndex(idx)
            self.ctrl_edit_requested.emit(idx)
            return

        use_anchor = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier) and self.controller.anchor_row is not None
        self._drag_origin = self.controller.anchor_row if use_anchor else row
        self._drag_preview_base = set(self.controller.selected_rows)
        self._dragging = False
        self._press_row = row
        self.setCurrentIndex(idx)
        if not use_anchor:
            self.controller.anchor_row = row
        self._apply_drag_preview(row)

    def _apply_drag_preview(self, end_row: int) -> None:
        if self._drag_origin is None or self._drag_preview_base is None:
            return
        start = min(self._drag_origin, end_row)
        end = max(self._drag_origin, end_row)
        range_set = set(range(start, end + 1))
        self.controller.selected_rows = self._drag_preview_base.symmetric_difference(range_set)
        self.controller.anchor_row = self._drag_origin
        self.controller.focus_row = end_row
        self.apply_controller_selection()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.controller.mode == SelectionMode.NORMAL:
            super().mouseMoveEvent(event)
            return
        if self._drag_origin is None or self._drag_preview_base is None:
            return
        row = self._row_at_event(event)
        if row >= 0:
            self._dragging = True
            self._apply_drag_preview(row)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.controller.mode == SelectionMode.NORMAL:
            super().mouseReleaseEvent(event)
            self.selected_rows()
            if event.button() == Qt.MouseButton.RightButton:
                self.context_menu_requested.emit(event.globalPosition().toPoint())
            return

        if event.button() == Qt.MouseButton.RightButton:
            self.context_menu_requested.emit(event.globalPosition().toPoint())
        elif event.button() == Qt.MouseButton.LeftButton and self._drag_origin is not None:
            # Selection preview is already applied on press/move; avoid re-applying on release,
            # otherwise single-click toggle can appear delayed with some event sequences.
            pass

        self._drag_origin = None
        self._drag_preview_base = None
        self._dragging = False
        self._press_row = None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        idx = self.indexAt(event.pos())
        if not idx.isValid():
            super().mouseDoubleClickEvent(event)
            return

        model = self.model()
        if model is None:
            super().mouseDoubleClickEvent(event)
            return
        if hasattr(model, "is_group_row") and model.is_group_row(idx.row()):
            return
        super().mouseDoubleClickEvent(event)

    def _move_cursor_and_edit(self, row_delta: int, col_delta: int) -> bool:
        current = self.currentIndex()
        model = self.model()
        if model is None or not current.isValid():
            return False
        row = max(0, min(model.rowCount() - 1, current.row() + row_delta))
        col = max(0, min(model.columnCount() - 1, current.column() + col_delta))
        idx = model.index(row, col)
        if not idx.isValid():
            return False
        self.setCurrentIndex(idx)
        if bool(model.flags(idx) & Qt.ItemFlag.ItemIsEditable):
            self.edit(idx)
        return True

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        mods = event.modifiers()
        if self.edit_mode:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if bool(mods & Qt.KeyboardModifier.ShiftModifier):
                    if self._move_cursor_and_edit(-1, 0):
                        return
                else:
                    if self._move_cursor_and_edit(1, 0):
                        return
            if key == Qt.Key.Key_Tab:
                if bool(mods & Qt.KeyboardModifier.ShiftModifier):
                    if self._move_cursor_and_edit(0, -1):
                        return
                else:
                    if self._move_cursor_and_edit(0, 1):
                        return

        if self.controller.mode == SelectionMode.NORMAL:
            super().keyPressEvent(event)
            self.selected_rows()
            return

        total = self.row_count()

        if key == Qt.Key.Key_Up:
            self.controller.move_focus(total, -1)
            self.apply_controller_selection()
            return
        if key == Qt.Key.Key_Down:
            self.controller.move_focus(total, 1)
            self.apply_controller_selection()
            return
        if key == Qt.Key.Key_Left:
            row_h = max(1, self.verticalHeader().defaultSectionSize())
            visible = max(1, self.viewport().height() // row_h)
            self.controller.page_focus(total, visible, -1)
            self.apply_controller_selection()
            return
        if key == Qt.Key.Key_Right:
            row_h = max(1, self.verticalHeader().defaultSectionSize())
            visible = max(1, self.viewport().height() // row_h)
            self.controller.page_focus(total, visible, 1)
            self.apply_controller_selection()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
            current = self.currentIndex()
            model = self.model()
            if self.edit_mode and current.isValid() and model is not None and bool(model.flags(current) & Qt.ItemFlag.ItemIsEditable):
                self.edit(current)
                return
            self.controller.keyboard_activate(shift=shift)
            self.apply_controller_selection()
            return

        super().keyPressEvent(event)


class TrackGridWidget(QWidget):
    track_field_edited = Signal(str, str, object)
    context_menu_requested = Signal(object, list)

    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade
        self.controller = SelectionController()
        self._base_status = "准备就绪"
        self._sort_states: dict[str, str] = {}
        self._bulk_edit_session: dict | None = None

        root = QVBoxLayout(self)

        ctrl = QHBoxLayout()
        self.lbl_group_mode = QLabel("分组模式")

        self.combo_group = QComboBox()
        self.combo_group.addItem("不分组", "none")
        self.chk_multi = QCheckBox("多选模式")
        self.chk_edit_mode = QCheckBox("编辑模式")
        self.btn_invert = QPushButton("反选")
        self.btn_save_selection = QPushButton("保存选中")
        self.btn_apply_snapshot = QPushButton("应用选中记录")
        self.snapshot_combo = QComboBox()
        self.snapshot_combo.setMinimumWidth(170)

        ctrl.addWidget(self.btn_invert)
        ctrl.addWidget(self.lbl_group_mode)
        ctrl.addWidget(self.combo_group)
        ctrl.addWidget(self.chk_multi)
        ctrl.addWidget(self.chk_edit_mode)
        ctrl.addWidget(self.btn_save_selection)
        ctrl.addWidget(self.snapshot_combo)
        ctrl.addWidget(self.btn_apply_snapshot)
        ctrl.addStretch(1)

        self.model = TrackTableModel()
        self.table = TrackTableView(self.controller)
        self.table.setModel(self.model)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionsMovable(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setVisible(True)

        _install_copy_support(self.table)

        self.status = QLabel("准备就绪")

        root.addLayout(ctrl)
        root.addWidget(self.table, 1)
        root.addWidget(self.status)

        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self.table.horizontalHeader().sectionMoved.connect(lambda *_args: self._sync_sort_from_header())
        self.combo_group.currentIndexChanged.connect(self._on_group_changed)
        self.chk_multi.toggled.connect(self._on_toggle_multi)
        self.chk_edit_mode.toggled.connect(self._on_toggle_edit_mode)
        self.btn_invert.clicked.connect(self._on_invert_selection)
        self.btn_save_selection.clicked.connect(self._on_save_snapshot)
        self.btn_apply_snapshot.clicked.connect(self._on_apply_snapshot)
        self.model.track_field_edited.connect(self._on_model_track_field_edited)
        self.table.clicked.connect(self._on_table_clicked)
        self.table.doubleClicked.connect(self._on_table_double_clicked)
        self.table.context_menu_requested.connect(self._on_context_menu_requested)
        self.table.ctrl_edit_requested.connect(self._on_ctrl_edit_requested)
        self.table.selectionModel().selectionChanged.connect(lambda *_args: self._refresh_status())
        self.model.set_confirm_empty_edit_callback(self._confirm_empty_edit)

        self.set_facade(facade)
        self._init_sort_states()
        self._sync_sort_from_header()

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade
        cfg = self.facade.get_runtime_config()
        self.force_save_threshold = int(cfg.ui.force_save_threshold)
        self.model.set_confirm_empty_edit_callback(self._confirm_empty_edit)
        self.refresh_tag_fields()

    def set_button_scale(self, scale: float) -> None:
        _apply_button_scale(self.btn_invert, scale)
        _apply_button_scale(self.btn_save_selection, scale)
        _apply_button_scale(self.btn_apply_snapshot, scale)

    def set_tracks(self, rows: list[dict], *, entry_editable: bool = False) -> None:
        keep_ids = list(self.model.visual_selected_track_ids)
        focus_track_id = self._focus_track_id()
        self.model.set_custom_order_enabled(bool(entry_editable))
        prepared = []
        for row in rows:
            item = dict(row)
            item["_entry_editable"] = bool(entry_editable and "entry" in item)
            prepared.append(item)
        self.model.set_tracks(prepared)
        if "custom_order" not in self._sort_states:
            self._init_sort_states()
        active_keys = [k for k, v in self._sort_states.items() if v in {"asc", "desc"}]
        if entry_editable and (not active_keys or active_keys == ["file_name"]):
            for key in list(self._sort_states.keys()):
                self._sort_states[key] = "off"
            self._sort_states["custom_order"] = "asc"
        if not entry_editable and active_keys == ["custom_order"]:
            self._sort_states["custom_order"] = "off"
            self._sort_states["file_name"] = "asc"
        self._sync_sort_from_header()
        self._restore_selection_by_ids(keep_ids, focus_track_id)
        self._base_status = f"已加载 {len(rows)} 条"
        self._refresh_status()

    def set_status(self, text: str) -> None:
        self._base_status = text
        self._refresh_status()

    def _refresh_status(self) -> None:
        selected = len(self.selected_track_ids())
        self.status.setText(f"{self._base_status} | 已选 {selected} 条")

    def selected_tracks(self) -> list[dict]:
        rows = self.table.selected_rows()
        out = []
        for row in rows:
            track = self.model.track_for_row(row)
            if track and track.get("track_id"):
                out.append(track)
        return out

    def selected_track_ids(self) -> list[str]:
        return [str(t.get("track_id", "")) for t in self.selected_tracks() if t.get("track_id")]

    def select_track_ids(self, track_ids: list[str]) -> None:
        row_indexes = self.model.row_indexes_for_track_ids(set(track_ids))
        self.table.set_selected_rows(row_indexes)
        self._refresh_status()

    def _focus_track_id(self) -> str | None:
        if self.controller.focus_row is None:
            return None
        track = self.model.track_for_row(self.controller.focus_row)
        if track and track.get("track_id"):
            return str(track.get("track_id"))
        return None

    def _restore_selection_by_ids(self, track_ids: list[str], focus_track_id: str | None = None) -> None:
        rows = self.model.row_indexes_for_track_ids(set(track_ids))
        self.controller.selected_rows = set(rows)
        if rows:
            if focus_track_id:
                focus_rows = self.model.row_indexes_for_track_ids({focus_track_id})
                if focus_rows:
                    self.controller.focus_row = focus_rows[0]
                    self.controller.anchor_row = focus_rows[0]
                else:
                    self.controller.focus_row = min(rows)
                    self.controller.anchor_row = min(rows)
            else:
                self.controller.focus_row = min(rows)
                self.controller.anchor_row = min(rows)
        else:
            if self.controller.mode == SelectionMode.NORMAL:
                self.controller._normalize_for_normal(self.model.rowCount())
            else:
                self.controller.selected_rows.clear()
                self.controller.anchor_row = None
                self.controller.focus_row = None
        self.table.apply_controller_selection()

    def _init_sort_states(self) -> None:
        keys = [self.model.column_key(i) for i in range(self.model.columnCount())]
        keep: dict[str, str] = {}
        for key in keys:
            keep[key] = self._sort_states.get(key, "off")
        if all(state == "off" for state in keep.values()):
            if "custom_order" in keep and bool(self.model.custom_order_enabled) and any(
                bool(r.get("_entry_editable")) for r in self.model.raw_tracks
            ):
                keep["custom_order"] = "asc"
            elif "file_name" in keep:
                keep["file_name"] = "asc"
        self._sort_states = keep
        self.model.set_header_sort_states(self._sort_states)

    def _sync_sort_from_header(self) -> None:
        selected_ids = list(self.model.visual_selected_track_ids)
        focus_track_id = self._focus_track_id()
        header = self.table.horizontalHeader()
        logical_indexes = sorted(range(self.model.columnCount()), key=lambda i: header.visualIndex(i))
        rules = []
        for logical in logical_indexes:
            key = self.model.column_key(logical)
            state = self._sort_states.get(key, "off")
            rules.append({"key": key, "state": state})
        self.model.set_header_sort_states(self._sort_states)
        self.model.set_sort_rules(rules)
        self._restore_selection_by_ids(selected_ids, focus_track_id)
        self._refresh_status()

    def _on_header_clicked(self, logical_section: int) -> None:
        key = self.model.column_key(logical_section)
        if not key:
            return
        self._sort_states[key] = _next_sort_state(self._sort_states.get(key, "off"))
        self._sync_sort_from_header()

    def _rebuild_group_combo(self) -> None:
        keep_key = str(self.combo_group.currentData() or "none")
        self.combo_group.blockSignals(True)
        self.combo_group.clear()
        self.combo_group.addItem("不分组", "none")
        for idx in range(self.model.columnCount()):
            key = self.model.column_key(idx)
            label = self.model.headerData(idx, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
            text = str(label).rsplit(" ", 1)[0]
            self.combo_group.addItem(text, key)
        target = self.combo_group.findData(keep_key)
        self.combo_group.setCurrentIndex(max(0, target))
        self.combo_group.blockSignals(False)

    def refresh_tag_fields(self) -> None:
        rows = self.facade.list_tag_fields()
        names = [str(r.get("tag_name", "")).strip() for r in rows if str(r.get("tag_name", "")).strip()]
        self.model.set_tag_fields(names)
        self._init_sort_states()
        self._sync_sort_from_header()
        self._rebuild_group_combo()

    def _on_group_changed(self) -> None:
        selected_ids = list(self.model.visual_selected_track_ids)
        focus_track_id = self._focus_track_id()
        self.model.set_group_by(str(self.combo_group.currentData()))
        self._restore_selection_by_ids(selected_ids, focus_track_id)
        self._refresh_status()

    def _on_toggle_multi(self, checked: bool) -> None:
        mode = SelectionMode.MULTI if checked else SelectionMode.NORMAL
        self.controller.set_mode(mode, self.model.rowCount(), self.force_save_threshold)
        self.table.set_mode(mode)
        self.refresh_snapshot_combo()
        self._refresh_status()

    def _on_toggle_edit_mode(self, checked: bool) -> None:
        self.table.set_edit_mode(bool(checked))

    def _on_invert_selection(self) -> None:
        visible_track_rows: list[int] = [idx for idx, row_obj in enumerate(self.model.display_rows) if row_obj.get("kind") == "track"]
        if not visible_track_rows:
            return
        visible_set = set(visible_track_rows)
        current = {r for r in self.controller.selected_rows if r in visible_set}

        target_rows: set[int]
        if len(visible_track_rows) >= 20000:
            snapshot_rows = list(visible_track_rows)
            snapshot_current = set(current)

            def _task(progress, is_cancelled):
                total = max(1, len(snapshot_rows))
                selected: list[int] = []
                step = max(1, total // 200)
                for idx, row in enumerate(snapshot_rows, 1):
                    if is_cancelled():
                        return {"rows": selected, "cancelled": True}
                    if row not in snapshot_current:
                        selected.append(row)
                    if idx == total or (idx % step == 0):
                        progress(idx, total, "正在计算反选")
                return {"rows": selected, "cancelled": False}

            outcome = run_modal_task(self, "反选", _task)
            if outcome.error is not None:
                QMessageBox.warning(self, "反选失败", f"反选失败\n{outcome.error}")
                return
            payload = outcome.result if isinstance(outcome.result, dict) else {}
            if bool(payload.get("cancelled")) and not payload.get("rows"):
                self.set_status("反选已取消")
                return
            target_rows = {int(v) for v in payload.get("rows", [])}
        else:
            target_rows = visible_set.difference(current)

        self.controller.selected_rows = target_rows
        if self.controller.selected_rows:
            focus = min(self.controller.selected_rows)
            self.controller.focus_row = focus
            self.controller.anchor_row = focus
        else:
            self.controller.focus_row = None
            self.controller.anchor_row = None
        self.table.apply_controller_selection()
        self._refresh_status()

    def _on_save_snapshot(self) -> None:
        self.controller.save_snapshot()
        self.refresh_snapshot_combo()

    def _on_apply_snapshot(self) -> None:
        index = self.snapshot_combo.currentIndex()
        if index < 0:
            return
        self.controller.load_snapshot(index)
        self.table.apply_controller_selection()
        self._refresh_status()

    def refresh_snapshot_combo(self) -> None:
        self.snapshot_combo.blockSignals(True)
        self.snapshot_combo.clear()
        for i, snap in enumerate(self.controller.saved_snapshots):
            self.snapshot_combo.addItem(f"记录{i + 1} ({len(snap)})")
        self.snapshot_combo.blockSignals(False)

    def _on_table_clicked(self, index: QModelIndex) -> None:
        row = index.row()
        if not self.model.is_group_row(row):
            if self.controller.mode == SelectionMode.NORMAL:
                self.table.selected_rows()
            self._refresh_status()
            return

        ids = self.model.group_track_ids(row)
        rows = self.model.row_indexes_for_track_ids(set(ids))
        if not rows:
            return

        self.controller.selected_rows = set(rows)
        self.controller.anchor_row = min(rows)
        self.controller.focus_row = min(rows)
        self.table.apply_controller_selection()
        self._refresh_status()

    def _on_table_double_clicked(self, index: QModelIndex) -> None:
        row = index.row()
        if self.model.is_group_row(row):
            self.model.toggle_group_row(row)
            self.table.apply_controller_selection()
            self._refresh_status()
            return
        if self.model.column_key(index.column()) == "lyrics_file_name":
            track = self.model.track_for_row(row) or {}
            track_id = str(track.get("track_id", "") or "")
            if track_id:
                self.track_field_edited.emit(track_id, "lyrics_file_name", "")
            return

    def _on_ctrl_edit_requested(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        if self.model.is_group_row(index.row()):
            return
        if self.model.column_key(index.column()) == "lyrics_file_name":
            track = self.model.track_for_row(index.row()) or {}
            track_id = str(track.get("track_id", "") or "")
            if track_id:
                self.track_field_edited.emit(track_id, "lyrics_file_name", "")
            return
        if not bool(self.model.flags(index) & Qt.ItemFlag.ItemIsEditable):
            return

        source = self.model.track_for_row(index.row()) or {}
        source_id = str(source.get("track_id", ""))
        key = self.model.column_key(index.column())
        targets = [str(t.get("track_id", "")) for t in self.selected_tracks() if t.get("track_id")]
        if source_id and source_id in targets and len(targets) > 1:
            self._bulk_edit_session = {"source_track_id": source_id, "key": key, "target_ids": targets}
        else:
            self._bulk_edit_session = None

        self.table.setCurrentIndex(index)
        self.table.edit(index)

    def _on_model_track_field_edited(self, track_id: str, key: str, value) -> None:
        keep_ids = list(self.model.visual_selected_track_ids)
        self.track_field_edited.emit(track_id, key, value)

        session = self._bulk_edit_session
        if not session:
            self._restore_selection_by_ids(keep_ids, track_id)
            return
        if str(session.get("source_track_id")) != str(track_id):
            self._restore_selection_by_ids(keep_ids, track_id)
            return
        if str(session.get("key")) != str(key):
            self._restore_selection_by_ids(keep_ids, track_id)
            return

        target_ids = [tid for tid in session.get("target_ids", []) if str(tid) and str(tid) != str(track_id)]
        if not target_ids:
            self._bulk_edit_session = None
            self._restore_selection_by_ids(keep_ids, track_id)
            return

        self.model.apply_value_to_tracks(set(target_ids), key, value)
        for tid in target_ids:
            self.track_field_edited.emit(str(tid), key, value)
        self._bulk_edit_session = None
        self._restore_selection_by_ids(keep_ids, track_id)

    def _on_context_menu_requested(self, global_pos) -> None:
        self.context_menu_requested.emit(global_pos, self.selected_tracks())

    def _confirm_empty_edit(self, _track_id: str, _key: str) -> bool:
        cfg = self.facade.get_runtime_config()
        if not bool(cfg.ui.prompt_empty_edit_confirm):
            return True
        box = QMessageBox(self)
        box.setWindowTitle("空值确认")
        box.setText("当前输入为空。请选择保留原值，或保存为空。")
        keep_btn = box.addButton("不修改", QMessageBox.ButtonRole.RejectRole)
        empty_btn = box.addButton("留空保存", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        return box.clickedButton() == empty_btn and box.clickedButton() != keep_btn

    def track_by_id(self, track_id: str) -> dict | None:
        target = str(track_id or "").strip()
        if not target:
            return None
        for row in self.model.raw_tracks:
            if str(row.get("track_id", "") or "") == target:
                return row
        return None


class LyricsTableModel(DictTableModel):
    lyrics_field_edited = Signal(str, str, object)

    _EDITABLE = {"file_name", "lyrics_title", "lyrics_artist", "lyrics_album", "lyrics_author"}

    def __init__(self, columns: list[ColumnDef], parent=None):
        super().__init__(columns, parent)
        self._sort_state_map: dict[str, str] = {}

    def set_header_sort_states(self, state_map: dict[str, str]) -> None:
        self._sort_state_map = dict(state_map)
        if self.columns:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self.columns) - 1)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.columns):
                col = self.columns[section]
                marker = _marker_for_state(self._sort_state_map.get(col.key, "off"))
                return f"{col.title} {marker}"
        return super().headerData(section, orientation, role)

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        key = self.columns[index.column()].key
        if key in self._EDITABLE:
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.EditRole:
            row = self.row_at(index.row()) or {}
            key = self.columns[index.column()].key
            value = row.get(key, "")
            return "" if value is None else str(value)
        return super().data(index, role)

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        key = self.columns[index.column()].key
        if key not in self._EDITABLE:
            return False
        row = self.row_at(index.row())
        if not row:
            return False
        old_value = str(row.get(key, "") or "")
        new_value = str(value).strip()
        if new_value == old_value:
            return False
        row[key] = new_value
        self.dataChanged.emit(index, index)
        lyrics_id = str(row.get("lyrics_id", "") or "")
        if lyrics_id:
            QTimer.singleShot(0, lambda lid=lyrics_id, k=key, v=new_value: self.lyrics_field_edited.emit(lid, k, v))
        return True


