from __future__ import annotations

from pathlib import Path
import subprocess

from PySide6.QtCore import QItemSelectionModel, QModelIndex, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeyEvent, QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableView,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from musearc.app.facade import FAVORITES_PLAYLIST_ID, MuseArcFacade
from musearc.config.store import save_runtime_config
from musearc.ui.import_worker import ImportWorker
from musearc.ui.import_management_page import ImportManagementPage
from musearc.ui.settings_page import SettingsPage
from musearc.ui.selection import SelectionController, SelectionMode
from musearc.ui.table_models import ColumnDef, DictTableModel
from musearc.ui.track_table_model import TrackTableModel


def _apply_button_scale(button: QPushButton, scale: float) -> None:
    h = max(30, int(28 * scale))
    button.setMinimumHeight(h)


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


def _ask_export_format(parent: QWidget, anchor: QWidget) -> tuple[str, bool]:
    menu = QMenu(parent)
    action_original = menu.addAction("原格式")
    action_plan = menu.addAction("逐首配置...")
    menu.addSeparator()
    action_mp3 = menu.addAction("mp3")
    action_flac = menu.addAction("flac")
    action_opus = menu.addAction("opus")
    chosen = menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
    if chosen == action_original:
        return "original", True
    if chosen == action_plan:
        return "__plan__", True
    if chosen == action_mp3:
        return "mp3", True
    if chosen == action_flac:
        return "flac", True
    if chosen == action_opus:
        return "opus", True
    return "", False


def _next_sort_state(state: str) -> str:
    if state == "asc":
        return "desc"
    if state == "desc":
        return "off"
    return "asc"


def _show_track_details(parent: QWidget, track: dict) -> None:
    lines = [
        f"Track ID: {track.get('track_id', '')}",
        f"文件名: {track.get('file_name', '')}",
        f"标题: {track.get('title', '')}",
        f"艺术家: {track.get('artist', '')}",
        f"专辑: {track.get('album', '')}",
        f"语言: {track.get('language_kind', '')}",
        f"喜好: {track.get('preference_level', '')}",
        f"Source: {track.get('source_fullpath', '')}",
        f"Storage: {track.get('storage_relpath', '')}",
    ]
    QMessageBox.information(parent, "详情（待设计）", "\n".join(lines))


def _reveal_in_file_manager(parent: QWidget, path_text: str) -> None:
    text = str(path_text or "").strip()
    if not text:
        QMessageBox.information(parent, "文件管理器", "当前项没有可定位的文件路径。")
        return
    path = Path(text)
    target = path
    if not target.exists():
        parent_dir = target.parent
        if parent_dir.exists():
            target = parent_dir
    try:
        if target.is_file():
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            subprocess.Popen(["explorer", str(target)])
    except Exception as exc:
        QMessageBox.critical(parent, "文件管理器", str(exc))


def _ask_delete_tracks_with_lyrics(parent: QWidget, count: int, default_mode: str) -> tuple[str, bool]:
    default_is_move = default_mode != "unlink_only"
    box = QMessageBox(parent)
    box.setWindowTitle("从音乐库中删除")
    box.setText(f"确定将 {count} 条移到回收站吗？")
    move_btn = box.addButton("绑定歌词一起移动到回收站", QMessageBox.ButtonRole.AcceptRole)
    unlink_btn = box.addButton("仅删除歌曲并解开映射关系", QMessageBox.ButtonRole.DestructiveRole)
    cancel_btn = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    remember = QCheckBox("设为默认")
    box.setCheckBox(remember)
    box.setDefaultButton(move_btn if default_is_move else unlink_btn)
    box.exec()
    clicked = box.clickedButton()
    if clicked == move_btn:
        return "move_linked_lyrics", bool(remember.isChecked())
    if clicked == unlink_btn:
        return "unlink_only", bool(remember.isChecked())
    if clicked == cancel_btn:
        return "cancel", False
    return "cancel", False


def _resolve_delete_mode_and_maybe_save_default(parent: QWidget, facade: MuseArcFacade, count: int) -> str:
    cfg = facade.get_runtime_config()
    default_mode = str(cfg.ui.delete_tracks_mode_default or "move_linked_lyrics")
    mode, remember = _ask_delete_tracks_with_lyrics(parent, count, default_mode)
    if remember and mode in {"move_linked_lyrics", "unlink_only"}:
        cfg.ui.delete_tracks_mode_default = mode
        save_runtime_config(cfg)
    return mode


def _history_action_label(action_type: str) -> str:
    mapping = {
        "soft_delete_tracks": "移到回收站",
        "restore_tracks": "恢复歌曲",
        "update_tracks_fields": "编辑字段",
        "create_playlist": "新建歌单",
        "delete_playlist": "删除歌单",
        "add_tracks_to_playlist": "加到歌单",
        "remove_tracks_from_playlist": "从歌单移除",
        "clear_playlist": "清空歌单",
        "reorder_playlist": "重排歌单",
        "update_playlist_entries": "修改自定义排序",
        "create_fullscan_work": "新建全量筛选工作",
    }
    return mapping.get(action_type, action_type)


def _choose_or_create_playlist(
    parent: QWidget,
    facade: MuseArcFacade,
    anchor: QWidget,
    *,
    exclude_ids: set[str] | None = None,
    allow_create: bool = True,
) -> str | None:
    exclude = exclude_ids or set()
    playlists = [p for p in facade.list_playlists() if str(p.get("playlist_id", "")) not in exclude]
    menu = QMenu(parent)
    action_map: dict[QAction, str] = {}
    for row in playlists:
        playlist_id = str(row.get("playlist_id", ""))
        title = str(row.get("name", ""))
        action_map[menu.addAction(title)] = playlist_id

    action_new = None
    if allow_create:
        if playlists:
            menu.addSeparator()
        action_new = menu.addAction("新建歌单...")

    chosen = menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
    if not chosen:
        return None
    if action_new is not None and chosen == action_new:
        name, ok = QInputDialog.getText(parent, "新建歌单", "歌单名称")
        if not ok or not name.strip():
            return None
        return facade.create_playlist(name.strip())
    return action_map.get(chosen)


class TrackPickerDialog(QDialog):
    def __init__(self, parent: QWidget, facade: MuseArcFacade, *, allow_clear: bool = True):
        super().__init__(parent)
        self.facade = facade
        self.setWindowTitle("选择歌曲")
        self.resize(980, 620)
        self.selected_track_id: str | None = None

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索 标题/艺术家/专辑/文件名")
        self.btn_search = QPushButton("搜索")
        top.addWidget(self.search_input, 1)
        top.addWidget(self.btn_search)

        self.model = DictTableModel(
            [
                ColumnDef("file_name", "文件名"),
                ColumnDef("title", "标题"),
                ColumnDef("artist", "艺术家"),
                ColumnDef("album", "专辑"),
                ColumnDef("track_id", "数据库ID"),
            ]
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)

        self.buttons = QDialogButtonBox()
        self.btn_ok = self.buttons.addButton("确定", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_cancel = self.buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_clear = self.buttons.addButton("清空映射", QDialogButtonBox.ButtonRole.DestructiveRole) if allow_clear else None

        root.addLayout(top)
        root.addWidget(self.table, 1)
        root.addWidget(self.buttons)

        self._all_rows = self.facade.list_tracks(limit=200_000)
        self._apply_filter()

        self.btn_search.clicked.connect(self._apply_filter)
        self.search_input.returnPressed.connect(self._apply_filter)
        self.table.doubleClicked.connect(lambda _idx: self._accept_current())
        self.btn_ok.clicked.connect(self._accept_current)
        self.btn_cancel.clicked.connect(self.reject)
        if self.btn_clear is not None:
            self.btn_clear.clicked.connect(self._accept_clear)

    def _apply_filter(self) -> None:
        token = self.search_input.text().strip().casefold()
        if not token:
            rows = list(self._all_rows)
        else:
            rows = []
            for row in self._all_rows:
                text = " | ".join(
                    [
                        str(row.get("file_name", "")),
                        str(row.get("title", "")),
                        str(row.get("artist", "")),
                        str(row.get("album", "")),
                    ]
                ).casefold()
                if token in text:
                    rows.append(row)
        self.model.set_rows(rows)

    def _accept_current(self) -> None:
        indexes = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not indexes:
            QMessageBox.warning(self, "选择歌曲", "请先选择一首歌曲。")
            return
        row = self.model.row_at(indexes[0].row())
        self.selected_track_id = str(row.get("track_id", "")) if row else None
        if not self.selected_track_id:
            QMessageBox.warning(self, "选择歌曲", "当前行没有有效 track_id。")
            return
        self.accept()

    def _accept_clear(self) -> None:
        self.selected_track_id = None
        self.accept()


class ExportPlanDialog(QDialog):
    def __init__(self, parent: QWidget, tracks: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("逐首导出格式")
        self.resize(860, 560)
        self._combo_by_track_id: dict[str, QComboBox] = {}

        root = QVBoxLayout(self)
        row_set = QHBoxLayout()
        self.btn_all_original = QPushButton("全部原格式")
        self.btn_all_mp3 = QPushButton("全部 mp3")
        self.btn_all_flac = QPushButton("全部 flac")
        self.btn_all_opus = QPushButton("全部 opus")
        row_set.addWidget(self.btn_all_original)
        row_set.addWidget(self.btn_all_mp3)
        row_set.addWidget(self.btn_all_flac)
        row_set.addWidget(self.btn_all_opus)
        row_set.addStretch(1)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["歌曲", "导出格式", "track_id"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(False)

        for row in tracks:
            track_id = str(row.get("track_id", ""))
            label = f"{row.get('artist', '')} - {row.get('title', '')} ({row.get('file_name', '')})"
            item = QTreeWidgetItem([label, "", track_id])
            self.tree.addTopLevelItem(item)
            combo = QComboBox()
            combo.addItems(["original", "mp3", "flac", "opus"])
            combo.setCurrentText("original")
            self.tree.setItemWidget(item, 1, combo)
            if track_id:
                self._combo_by_track_id[track_id] = combo

        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)

        root.addLayout(row_set)
        root.addWidget(self.tree, 1)
        root.addWidget(self.buttons)

        self.btn_all_original.clicked.connect(lambda: self._apply_all("original"))
        self.btn_all_mp3.clicked.connect(lambda: self._apply_all("mp3"))
        self.btn_all_flac.clicked.connect(lambda: self._apply_all("flac"))
        self.btn_all_opus.clicked.connect(lambda: self._apply_all("opus"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

    def _apply_all(self, fmt: str) -> None:
        for combo in self._combo_by_track_id.values():
            combo.setCurrentText(fmt)

    def export_plan(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for track_id, combo in self._combo_by_track_id.items():
            out[track_id] = str(combo.currentText() or "original")
        return out


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
            for row in sorted(self.controller.selected_rows):
                if 0 <= row < self.row_count():
                    index = self.model().index(row, 0)
                    self.selectionModel().select(
                        index,
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
            self.ctrl_edit_requested.emit(idx)
            return

        self._drag_origin = self.controller.anchor_row if bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier) and self.controller.anchor_row is not None else row
        self._drag_preview_base = set(self.controller.selected_rows)
        self._dragging = False
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

        self._drag_origin = None
        self._drag_preview_base = None
        self._dragging = False

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        idx = self.indexAt(event.pos())
        if not idx.isValid():
            super().mouseDoubleClickEvent(event)
            return

        self.doubleClicked.emit(idx)
        model = self.model()
        if model is None:
            return
        if hasattr(model, "is_group_row") and model.is_group_row(idx.row()):
            return
        if bool(model.flags(idx) & Qt.ItemFlag.ItemIsEditable):
            self.edit(idx)
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
        ctrl.addWidget(QLabel("分组模式"))

        self.combo_group = QComboBox()
        self.combo_group.addItem("不分组", "none")
        self.chk_multi = QCheckBox("多选模式")
        self.chk_edit_mode = QCheckBox("编辑模式")
        self.btn_save_selection = QPushButton("保存选中")
        self.btn_apply_snapshot = QPushButton("应用选中记录")
        self.snapshot_combo = QComboBox()
        self.snapshot_combo.setMinimumWidth(170)

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

        if self.controller.mode == SelectionMode.MULTI:
            self.controller.selected_rows.update(rows)
        else:
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
        if bool(self.model.flags(index) & Qt.ItemFlag.ItemIsEditable):
            self.table.edit(index)

    def _on_ctrl_edit_requested(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        if self.model.is_group_row(index.row()):
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
        if key.startswith("tag:"):
            tag_name = key.split(":", 1)[1]
            self.facade.update_track_tag_values([track_id], tag_name, str(value))
        else:
            self.facade.update_tracks_fields([track_id], {key: value})
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
            source_path = str(first.get("source_fullpath", "") or "")
            if not source_path:
                storage_rel = str(first.get("storage_relpath", "") or "")
                source_path = str((Path(self.facade.library_root) / storage_rel)) if storage_rel else ""
            _reveal_in_file_manager(self, source_path)
            return
        if chosen == action_copy:
            _copy_selected_cells(self.grid.table)
            return
        if chosen == action_detail:
            _show_track_details(self, tracks[0])


class ReviewPage(QWidget):
    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade
        self._track_map: dict[str, dict] = {}

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()

        (
            self.uncertain_model,
            self.uncertain_table,
            self.uncertain_host,
        ) = self._make_table(
            [
                ColumnDef("priority", "优先级"),
                ColumnDef("group_key", "分组"),
                ColumnDef("source_file", "源文件"),
                ColumnDef("candidate_track", "候选歌曲"),
                ColumnDef("score", "相似分"),
                ColumnDef("reason", "原因"),
                ColumnDef("review_id", "审查ID"),
            ]
        )
        uncertain_top = QHBoxLayout()
        self.btn_play_source = QPushButton("播放源文件")
        self.btn_play_candidate = QPushButton("播放候选歌曲")
        uncertain_top.addWidget(self.btn_play_source)
        uncertain_top.addWidget(self.btn_play_candidate)
        uncertain_top.addStretch(1)
        self.uncertain_host.layout().insertLayout(0, uncertain_top)

        (
            self.lyrics_model,
            self.lyrics_table,
            self.lyrics_host,
        ) = self._make_table(
            [
                ColumnDef("priority", "优先级"),
                ColumnDef("group_key", "分组"),
                ColumnDef("lyrics_source", "歌词来源"),
                ColumnDef("suggest_track", "建议歌曲"),
                ColumnDef("score", "匹配分"),
                ColumnDef("reason", "原因"),
                ColumnDef("review_id", "审查ID"),
            ]
        )
        lyrics_top = QHBoxLayout()
        self.btn_compare_lyrics = QPushButton("查看歌词对比")
        self.btn_retry_lyrics_match = QPushButton("重试歌词自动匹配（设计版）")
        lyrics_top.addWidget(self.btn_compare_lyrics)
        lyrics_top.addWidget(self.btn_retry_lyrics_match)
        lyrics_top.addStretch(1)
        self.lyrics_host.layout().insertLayout(0, lyrics_top)

        self.file_model, self.file_table, self.file_host = self._make_table(
            [
                ColumnDef("priority", "优先级"),
                ColumnDef("title", "标题"),
                ColumnDef("path", "来源"),
                ColumnDef("detail", "详情"),
                ColumnDef("duration_sec", "时长(s)"),
                ColumnDef("review_id", "审查ID"),
            ]
        )
        self.other_model, self.other_table, self.other_host = self._make_table(
            [
                ColumnDef("priority", "优先级"),
                ColumnDef("kind", "类型"),
                ColumnDef("title", "标题"),
                ColumnDef("payload", "数据"),
                ColumnDef("review_id", "审查ID"),
            ]
        )

        self.tabs.addTab(self.uncertain_host, "不确定歌曲")
        self.tabs.addTab(self.lyrics_host, "歌词待审查")
        self.tabs.addTab(self.file_host, "文件异常")
        self.tabs.addTab(self.other_host, "其它")
        root.addWidget(self.tabs, 1)

        row_actions = QHBoxLayout()
        self.btn_resolve_selected = QPushButton("保存勾选的文件（标记已处理）")
        self.btn_ignore_selected = QPushButton("忽略选中项")
        self.btn_reload_reviews = QPushButton("刷新审查")
        row_actions.addWidget(self.btn_resolve_selected)
        row_actions.addWidget(self.btn_ignore_selected)
        row_actions.addWidget(self.btn_reload_reviews)
        row_actions.addStretch(1)
        root.addLayout(row_actions)

        self.btn_play_source.clicked.connect(self._play_uncertain_source)
        self.btn_play_candidate.clicked.connect(self._play_uncertain_candidate)
        self.btn_compare_lyrics.clicked.connect(self._compare_lyrics_rows)
        self.btn_retry_lyrics_match.clicked.connect(
            lambda: QMessageBox.information(self, "重试歌词匹配", "设计版入口已预留，下一步将接入批量重算并自动回填。")
        )
        self.btn_resolve_selected.clicked.connect(lambda: self._resolve_selected_reviews("resolved"))
        self.btn_ignore_selected.clicked.connect(lambda: self._resolve_selected_reviews("ignored"))
        self.btn_reload_reviews.clicked.connect(self.reload_reviews)

        self.reload_reviews()

    def _make_table(self, columns: list[ColumnDef]) -> tuple[DictTableModel, QTableView, QWidget]:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)

        model = DictTableModel(columns)
        table = QTableView()
        table.setModel(model)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        table.horizontalHeader().setStretchLastSection(True)
        _install_copy_support(table)
        layout.addWidget(table)
        return model, table, host

    def apply_button_scale(self, _scale: float) -> None:
        _apply_button_scale(self.btn_play_source, _scale)
        _apply_button_scale(self.btn_play_candidate, _scale)
        _apply_button_scale(self.btn_compare_lyrics, _scale)
        _apply_button_scale(self.btn_retry_lyrics_match, _scale)
        _apply_button_scale(self.btn_resolve_selected, _scale)
        _apply_button_scale(self.btn_ignore_selected, _scale)
        _apply_button_scale(self.btn_reload_reviews, _scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade
        self.reload_reviews()

    def refresh_page(self) -> None:
        self.reload_reviews()

    def reload_reviews(self) -> None:
        rows = self.facade.pending_reviews(limit=5000)
        track_rows = self.facade.list_tracks(limit=200000)
        self._track_map = {str(r.get("track_id", "")): r for r in track_rows if r.get("track_id")}

        def track_label(track_id: str) -> str:
            row = self._track_map.get(track_id)
            if not row:
                return track_id
            return f"{row.get('artist', '')} - {row.get('title', '')} ({track_id})"

        uncertain_rows: list[dict] = []
        lyrics_rows: list[dict] = []
        file_rows: list[dict] = []
        other_rows: list[dict] = []

        for row in rows:
            kind = str(row.get("kind", ""))
            payload = row.get("payload") or {}

            if kind == "duplicate":
                track_id = str(payload.get("existing_track_id") or "")
                track_meta = self._track_map.get(track_id) or {}
                uncertain_rows.append(
                    {
                        "priority": row.get("priority"),
                        "group_key": track_id[:8] if track_id else "未分组",
                        "source_file": Path(str(payload.get("path", "") or "")).name,
                        "source_path": payload.get("path", ""),
                        "candidate_track": track_label(track_id),
                        "candidate_path": track_meta.get("source_fullpath", ""),
                        "score": payload.get("score", ""),
                        "reason": payload.get("reason", ""),
                        "review_id": row.get("review_id"),
                    }
                )
                continue

            if kind == "file_issue" and str(row.get("title", "")) == "指纹提取失败":
                source_path = str(payload.get("path", "") or "")
                source_file = Path(source_path).name
                suggestions = payload.get("suggest_candidates") or []
                if isinstance(suggestions, list) and suggestions:
                    for sug in suggestions:
                        if not isinstance(sug, dict):
                            continue
                        track_id = str(sug.get("track_id", "") or "")
                        track_meta = self._track_map.get(track_id) or {}
                        uncertain_rows.append(
                            {
                                "priority": row.get("priority"),
                                "group_key": str(payload.get("group_key", "") or track_id[:8] or "未分组"),
                                "source_file": source_file,
                                "source_path": source_path,
                                "candidate_track": track_label(track_id),
                                "candidate_path": track_meta.get("source_fullpath", ""),
                                "score": sug.get("score", ""),
                                "reason": f"指纹失败/名称相近 ({payload.get('title_hint', '')})",
                                "review_id": row.get("review_id"),
                            }
                        )
                else:
                    uncertain_rows.append(
                        {
                            "priority": row.get("priority"),
                            "group_key": "未分组",
                            "source_file": source_file,
                            "source_path": source_path,
                            "candidate_track": "",
                            "candidate_path": "",
                            "score": "",
                            "reason": "指纹失败，暂无候选",
                            "review_id": row.get("review_id"),
                        }
                    )
                continue

            if kind == "lyrics_match":
                suggest_id = str(payload.get("suggest_track_id") or "")
                lyrics_rows.append(
                    {
                        "priority": row.get("priority"),
                        "group_key": str(payload.get("group_key", "") or suggest_id[:8] or "未分组"),
                        "lyrics_source": payload.get("lyrics_source", ""),
                        "suggest_track": track_label(suggest_id),
                        "score": payload.get("score", ""),
                        "reason": payload.get("reason", ""),
                        "lyrics_preview": "\\n".join((payload.get("lyrics_preview") or [])[:12]),
                        "review_id": row.get("review_id"),
                    }
                )
                continue

            if kind == "file_issue":
                file_rows.append(
                    {
                        "priority": row.get("priority"),
                        "title": row.get("title"),
                        "path": payload.get("path", ""),
                        "detail": payload.get("error", ""),
                        "duration_sec": payload.get("duration_sec", ""),
                        "review_id": row.get("review_id"),
                    }
                )
                continue
            other_rows.append(
                {
                    "priority": row.get("priority"),
                    "kind": kind,
                    "title": row.get("title"),
                    "payload": str(payload),
                    "review_id": row.get("review_id"),
                }
            )

        uncertain_rows.sort(
            key=lambda r: (
                -int(r.get("priority", 0) or 0),
                str(r.get("group_key", "")).casefold(),
                str(r.get("source_file", "")).casefold(),
            )
        )
        lyrics_rows.sort(
            key=lambda r: (
                -int(r.get("priority", 0) or 0),
                str(r.get("group_key", "")).casefold(),
                str(r.get("lyrics_source", "")).casefold(),
            )
        )

        self.uncertain_model.set_rows(uncertain_rows)
        self.lyrics_model.set_rows(lyrics_rows)
        self.file_model.set_rows(file_rows)
        self.other_model.set_rows(other_rows)

    @staticmethod
    def _selected_row(model: DictTableModel, table: QTableView) -> dict | None:
        selection = table.selectionModel().selectedRows() if table.selectionModel() else []
        if not selection:
            return None
        return model.row_at(selection[0].row())

    def _play_with_external_player(self, path_text: str) -> None:
        target = str(path_text or "").strip()
        if not target:
            QMessageBox.information(self, "播放", "当前行没有可播放路径。")
            return

        cfg = self.facade.get_runtime_config()
        mode = str(cfg.ui.player_mode or "external")
        if mode == "builtin":
            QMessageBox.information(self, "播放", "内置播放器暂未实现，请切换外部播放器。")
            return

        exe = str(cfg.ui.external_player_path or "").strip()
        if not exe:
            QMessageBox.warning(self, "播放", "请先在设置中配置外部播放器可执行文件路径。")
            return
        try:
            subprocess.Popen([exe, target])
        except Exception as exc:
            QMessageBox.critical(self, "播放失败", str(exc))

    def _play_uncertain_source(self) -> None:
        row = self._selected_row(self.uncertain_model, self.uncertain_table)
        if not row:
            return
        self._play_with_external_player(str(row.get("source_path", "")))

    def _play_uncertain_candidate(self) -> None:
        row = self._selected_row(self.uncertain_model, self.uncertain_table)
        if not row:
            return
        self._play_with_external_player(str(row.get("candidate_path", "")))

    def _compare_lyrics_rows(self) -> None:
        row = self._selected_row(self.lyrics_model, self.lyrics_table)
        if not row:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("歌词对比")
        dialog.resize(980, 640)

        root = QVBoxLayout(dialog)
        info = QLabel(
            f"来源: {row.get('lyrics_source', '')}\n"
            f"建议: {row.get('suggest_track', '')}\n"
            f"原因: {row.get('reason', '')}"
        )
        left = QPlainTextEdit()
        left.setReadOnly(True)
        left.setPlainText(str(row.get("lyrics_preview", "")))
        right = QPlainTextEdit()
        right.setReadOnly(True)
        right.setPlainText("此处预留候选歌曲歌词内容对比。")
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        root.addWidget(info)
        root.addWidget(split, 1)
        dialog.exec()

    @staticmethod
    def _selected_review_ids(model: DictTableModel, table: QTableView) -> list[str]:
        if table.selectionModel() is None:
            return []
        ids: list[str] = []
        for idx in table.selectionModel().selectedRows():
            row = model.row_at(idx.row()) or {}
            rid = str(row.get("review_id", "") or "")
            if rid:
                ids.append(rid)
        return ids

    def _resolve_selected_reviews(self, status: str) -> None:
        tab_index = self.tabs.currentIndex()
        if tab_index == 0:
            ids = self._selected_review_ids(self.uncertain_model, self.uncertain_table)
        elif tab_index == 1:
            ids = self._selected_review_ids(self.lyrics_model, self.lyrics_table)
        elif tab_index == 2:
            ids = self._selected_review_ids(self.file_model, self.file_table)
        else:
            ids = self._selected_review_ids(self.other_model, self.other_table)
        if not ids:
            QMessageBox.information(self, "审查处理", "请先选择要处理的项。")
            return
        count = self.facade.resolve_reviews(ids, status=status)
        self.reload_reviews()
        QMessageBox.information(self, "审查处理", f"已处理 {count} 项。")


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
            self.facade.update_playlist_entries(self.current_playlist_id, {track_id: parsed})
            self.reload_playlist_tracks()
            self.grid.select_track_ids([track_id])
            self.library_changed.emit()
            return
        if key.startswith("tag:"):
            tag_name = key.split(":", 1)[1]
            self.facade.update_track_tag_values([track_id], tag_name, str(value))
        else:
            self.facade.update_tracks_fields([track_id], {key: value})
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
            source_path = str(first.get("source_fullpath", "") or "")
            if not source_path:
                storage_rel = str(first.get("storage_relpath", "") or "")
                source_path = str((Path(self.facade.library_root) / storage_rel)) if storage_rel else ""
            _reveal_in_file_manager(self, source_path)
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
        if key.startswith("tag:"):
            tag_name = key.split(":", 1)[1]
            self.facade.update_track_tag_values([track_id], tag_name, str(value))
        else:
            self.facade.update_tracks_fields([track_id], {key: value})
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
            source_path = str(first.get("source_fullpath", "") or "")
            if not source_path:
                storage_rel = str(first.get("storage_relpath", "") or "")
                source_path = str((Path(self.facade.library_root) / storage_rel)) if storage_rel else ""
            _reveal_in_file_manager(self, source_path)
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
        if key.startswith("tag:"):
            tag_name = key.split(":", 1)[1]
            self.facade.update_track_tag_values([track_id], tag_name, str(value))
        else:
            self.facade.update_tracks_fields([track_id], {key: value})

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
            source_path = str(first.get("source_fullpath", "") or "")
            if not source_path:
                storage_rel = str(first.get("storage_relpath", "") or "")
                source_path = str((Path(self.facade.library_root) / storage_rel)) if storage_rel else ""
            _reveal_in_file_manager(self, source_path)
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
        if key.startswith("tag:"):
            tag_name = key.split(":", 1)[1]
            self.facade.update_track_tag_values([track_id], tag_name, str(value))
        else:
            self.facade.update_tracks_fields([track_id], {key: value})
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
            source_path = str(first.get("source_fullpath", "") or "")
            if not source_path:
                storage_rel = str(first.get("storage_relpath", "") or "")
                source_path = str((Path(self.facade.library_root) / storage_rel)) if storage_rel else ""
            _reveal_in_file_manager(self, source_path)
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

        self.model = DictTableModel(
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
        self.table.doubleClicked.connect(self._on_double_click_cell)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
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
                int(r.get("line_count", 0) or 0),
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
        if not self.chk_edit_mode.isChecked():
            return
        key = self.model.columns[index.column()].key if hasattr(self.model, "columns") else ""
        if str(key) == "mapped_track":
            self._map_selected_to_track()
            return
        if str(key) == "lyrics_author":
            self._edit_author_for_selected()
            return

    def _on_toggle_multi(self, checked: bool) -> None:
        mode = (
            QAbstractItemView.SelectionMode.ExtendedSelection
            if checked
            else QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setSelectionMode(mode)

    def _on_toggle_edit_mode(self, checked: bool) -> None:
        if checked:
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
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


class MainWindow(QMainWindow):
    def __init__(self, library_path: str | None = None):
        super().__init__()
        self.facade = MuseArcFacade(library_path)
        self.setWindowTitle("MuseArc")
        self.resize(1720, 980)

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.sidebar = QListWidget()
        self.sidebar.addItems(
            [
                "全部歌曲",
                "导入管理",
                "待人工审查",
                "全量筛选",
                "歌单管理",
                "标签管理",
                "歌词管理",
                "回收站",
                "设置",
            ]
        )
        self.sidebar.setMaximumWidth(260)

        left_layout.addWidget(self.sidebar, 3)
        left_layout.addWidget(QLabel("历史可撤回操作"))

        row_hist = QHBoxLayout()
        self.btn_undo = QPushButton("撤回")
        self.btn_redo = QPushButton("重做")
        row_hist.addWidget(self.btn_undo)
        row_hist.addWidget(self.btn_redo)
        left_layout.addLayout(row_hist)

        self.list_history = QListWidget()
        left_layout.addWidget(self.list_history, 2)

        self.stack = QStackedWidget()
        self.page_tracks = TracksPage(self.facade)
        self.page_imports = ImportManagementPage(self.facade)
        self.page_review = ReviewPage(self.facade)
        self.page_fullscan = FullScanPage(self.facade)
        self.page_playlist = PlaylistPage(self.facade)
        self.page_tags = TagManagementPage(self.facade)
        self.page_lyrics = LyricsManagementPage(self.facade)
        self.page_trash = TrashPage(self.facade)
        self.page_settings = SettingsPage(self.facade)

        self.stack.addWidget(self.page_tracks)
        self.stack.addWidget(self.page_imports)
        self.stack.addWidget(self.page_review)
        self.stack.addWidget(self.page_fullscan)
        self.stack.addWidget(self.page_playlist)
        self.stack.addWidget(self.page_tags)
        self.stack.addWidget(self.page_lyrics)
        self.stack.addWidget(self.page_trash)
        self.stack.addWidget(self.page_settings)

        layout.addWidget(left)
        layout.addWidget(self.stack, 1)

        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.currentRowChanged.connect(self._on_page_changed)
        self.sidebar.setCurrentRow(0)

        self.btn_undo.clicked.connect(self._undo_one)
        self.btn_redo.clicked.connect(self._redo_one)
        self.list_history.itemClicked.connect(self._jump_to_history_item)

        self.page_tracks.library_changed.connect(self._reload_related_pages)
        self.page_imports.library_changed.connect(self._reload_related_pages)
        self.page_fullscan.library_changed.connect(self._reload_related_pages)
        self.page_playlist.library_changed.connect(self._reload_related_pages)
        self.page_tags.tags_changed.connect(self._on_tags_changed)
        self.page_tags.library_changed.connect(self._reload_related_pages)
        self.page_lyrics.library_changed.connect(self._reload_related_pages)
        self.page_trash.library_changed.connect(self._reload_related_pages)
        self.page_settings.settings_saved.connect(self._on_settings_saved)

        self._build_menu()
        self._save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self._save_shortcut.activated.connect(self._save_now)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._save_now)
        self._apply_button_scale_from_config()
        self._configure_autosave_timer()
        self._refresh_action_history()

    def _build_menu(self) -> None:
        menu_file = self.menuBar().addMenu("文件")
        action_open = QAction("打开音乐库", self)
        action_open.triggered.connect(self._open_library)
        menu_file.addAction(action_open)
        action_save = QAction("保存当前更改", self)
        action_save.setShortcut(QKeySequence.StandardKey.Save)
        action_save.triggered.connect(self._save_now)
        menu_file.addAction(action_save)

        menu_view = self.menuBar().addMenu("页面")
        action_refresh = QAction("刷新当前页面", self)
        action_refresh.triggered.connect(self._refresh_current_page)
        menu_view.addAction(action_refresh)

    def _save_now(self) -> None:
        self.facade.save_now()
        self.statusBar().showMessage("已保存更改", 1800)

    def _configure_autosave_timer(self) -> None:
        minutes = max(1, int(self.facade.get_runtime_config().ui.db_autosave_minutes))
        self._autosave_timer.setInterval(minutes * 60 * 1000)
        self._autosave_timer.start()

    def _refresh_current_page(self) -> None:
        page = self.stack.currentWidget()
        if page is None:
            return
        if hasattr(page, "refresh_page"):
            page.refresh_page()

    def _undo_one(self) -> None:
        result = self.facade.undo_last_action()
        if result == "no_action":
            QMessageBox.information(self, "撤回", "没有可撤回操作")
            return
        self._reload_all_pages()
        self._refresh_action_history()

    def _redo_one(self) -> None:
        result = self.facade.redo_last_action()
        if result == "no_action":
            QMessageBox.information(self, "重做", "没有可重做操作")
            return
        self._reload_all_pages()
        self._refresh_action_history()

    def _jump_to_history_item(self, item: QListWidgetItem) -> None:
        target = int(item.data(Qt.ItemDataRole.UserRole))
        timeline = self.facade.list_action_timeline(limit=500)
        current = int(timeline.get("current_index", -1))
        if target == current:
            return

        if target < current:
            for _ in range(current - target):
                if self.facade.undo_last_action() == "no_action":
                    break
        else:
            for _ in range(target - current):
                if self.facade.redo_last_action() == "no_action":
                    break

        self._reload_all_pages()
        self._refresh_action_history(select_current=True)

    def _refresh_action_history(self, select_current: bool = True) -> None:
        timeline = self.facade.list_action_timeline(limit=500)
        history = list(timeline.get("history", []))
        current_index = int(timeline.get("current_index", -1))

        self.list_history.blockSignals(True)
        self.list_history.clear()
        for idx, row in enumerate(history):
            action_type = str(row.get("action_type", ""))
            created_at = str(row.get("created_at", ""))[:19].replace("T", " ")
            marker = "●" if idx <= current_index else "○"
            text = f"{marker} {_history_action_label(action_type)}  {created_at}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            if idx == current_index:
                item.setBackground(QColor(225, 240, 255))
            self.list_history.addItem(item)
        self.list_history.blockSignals(False)

        self.btn_undo.setEnabled(current_index >= 0)
        self.btn_redo.setEnabled(current_index < len(history) - 1)

        if select_current and 0 <= current_index < self.list_history.count():
            self.list_history.setCurrentRow(current_index)

    def _reload_related_pages(self) -> None:
        self.page_review.reload_reviews()
        self.page_playlist.reload_playlists()
        self.page_fullscan.reload_works()
        self.page_imports.reload_history()
        self.page_tags.reload_tags()
        self.page_lyrics.reload_lyrics()
        self.page_trash.reload_trash()
        self.page_tracks.reload_tracks_from_db()
        self._refresh_action_history()

    def _reload_all_pages(self) -> None:
        self.page_tracks.reload_tracks_from_db()
        self.page_imports.reload_history()
        self.page_review.reload_reviews()
        self.page_fullscan.reload_works()
        self.page_playlist.reload_playlists()
        self.page_tags.reload_tags()
        self.page_lyrics.reload_lyrics()
        self.page_trash.reload_trash()
        self.page_settings.refresh_page()

    def _on_tags_changed(self) -> None:
        self.page_tracks.grid.refresh_tag_fields()
        self.page_fullscan.grid.refresh_tag_fields()
        self.page_playlist.grid.refresh_tag_fields()
        self.page_trash.grid.refresh_tag_fields()
        self._reload_all_pages()

    def _on_settings_saved(self) -> None:
        self._apply_button_scale_from_config()
        self._configure_autosave_timer()
        self.page_tracks.set_facade(self.facade)
        self.page_playlist.set_facade(self.facade)
        self.page_fullscan.set_facade(self.facade)
        self.page_tags.set_facade(self.facade)
        self.page_lyrics.set_facade(self.facade)
        self.page_trash.set_facade(self.facade)

    def _apply_button_scale_from_config(self) -> None:
        scale = float(self.facade.get_runtime_config().ui.button_scale)
        self.page_tracks.apply_button_scale(scale)
        self.page_imports.apply_button_scale(scale)
        self.page_review.apply_button_scale(scale)
        self.page_fullscan.apply_button_scale(scale)
        self.page_playlist.apply_button_scale(scale)
        self.page_tags.apply_button_scale(scale)
        self.page_lyrics.apply_button_scale(scale)
        self.page_trash.apply_button_scale(scale)
        self.page_settings.apply_button_scale(scale)
        _apply_button_scale(self.btn_undo, scale)
        _apply_button_scale(self.btn_redo, scale)

    def _on_page_changed(self, index: int) -> None:
        if index == 1:
            self.page_imports.reload_history()
        elif index == 2:
            self.page_review.reload_reviews()
        elif index == 3:
            self.page_fullscan.reload_works()
        elif index == 4:
            self.page_playlist.reload_playlists()
        elif index == 5:
            self.page_tags.reload_tags()
        elif index == 6:
            self.page_lyrics.reload_lyrics()
        elif index == 7:
            self.page_trash.reload_trash()
        self._refresh_action_history(select_current=False)

    def _open_library(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择音乐库路径")
        if not folder:
            return
        self.facade = MuseArcFacade(str(Path(folder).resolve()))

        self.page_tracks.set_facade(self.facade)
        self.page_imports.set_facade(self.facade)
        self.page_review.set_facade(self.facade)
        self.page_fullscan.set_facade(self.facade)
        self.page_playlist.set_facade(self.facade)
        self.page_tags.set_facade(self.facade)
        self.page_lyrics.set_facade(self.facade)
        self.page_trash.set_facade(self.facade)
        self.page_settings.set_facade(self.facade)

        self._apply_button_scale_from_config()
        self._configure_autosave_timer()
        self._reload_all_pages()
        self._refresh_action_history()
