from __future__ import annotations

from pathlib import Path
import subprocess

from PySide6.QtCore import QEvent, QItemSelectionModel, QModelIndex, Qt, QThread, QTimer, Signal
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
from musearc.ui.review_page import ReviewPage
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


def _safe_int(value, default: int = 0) -> int:
    if isinstance(value, (list, tuple, set, dict)):
        return default
    try:
        return int(value or 0)
    except Exception:
        return default


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


def _storage_path_for_track_row(facade: MuseArcFacade, row: dict) -> str:
    rel = str(row.get("storage_relpath", "") or "").strip()
    if rel:
        return str(Path(facade.library_root) / rel)
    return str(row.get("source_fullpath", "") or "").strip()


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
        "update_lyrics_fields": "编辑歌词字段",
        "set_primary_lyrics_for_track": "修改歌曲歌词映射",
        "set_primary_track_for_lyrics": "修改歌词歌曲映射",
        "resolve_reviews": "处理审查项",
        "delete_lyrics": "删除歌词",
        "restore_lyrics": "恢复歌词",
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
        return _prompt_new_playlist(parent, facade)
    return action_map.get(chosen)


def _prompt_new_playlist(parent: QWidget, facade: MuseArcFacade, *, title: str = "新建歌单") -> str | None:
    name, ok = QInputDialog.getText(parent, title, "歌单名称")
    if not ok or not name.strip():
        return None
    return facade.create_playlist(name.strip())


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


class ExportConfigDialog(QDialog):
    def __init__(self, parent: QWidget, tracks: list[dict], *, default_name: str = "playlist"):
        super().__init__(parent)
        self.setWindowTitle("导出配置")
        self.resize(980, 700)
        self._combo_by_track_id: dict[str, QComboBox] = {}
        self._tracks = list(tracks)
        self._default_name = default_name.strip() or "playlist"

        root = QVBoxLayout(self)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("导出路径"))
        self.path_input = QLineEdit()
        self.btn_browse = QPushButton("浏览...")
        path_row.addWidget(self.path_input, 1)
        path_row.addWidget(self.btn_browse)

        mode_row = QHBoxLayout()
        self.chk_files = QCheckBox("导出为多个音频文件")
        self.chk_playlist = QCheckBox("导出为歌单清单(JSON)")
        self.chk_files.setChecked(True)
        self.chk_playlist.setChecked(True)
        mode_row.addWidget(self.chk_files)
        mode_row.addWidget(self.chk_playlist)
        mode_row.addStretch(1)

        self.playlist_hint = QLabel("歌单清单将包含数据库路径、歌词路径、统计占位字段与歌单唯一哈希。")
        self.playlist_hint.setStyleSheet("color:#5d6f86;")
        self.playlist_hint.setVisible(False)

        row_set = QHBoxLayout()
        self.btn_all_original = QPushButton("整列设为源格式")
        self.btn_all_mp3 = QPushButton("整列设为mp3")
        self.btn_all_opus = QPushButton("整列设为opus")
        self.btn_all_flac = QPushButton("整列设为flac")
        self.btn_all_wav = QPushButton("整列设为wav")
        self.btn_all_ogg = QPushButton("整列设为ogg")
        for btn in [
            self.btn_all_original,
            self.btn_all_mp3,
            self.btn_all_opus,
            self.btn_all_flac,
            self.btn_all_wav,
            self.btn_all_ogg,
        ]:
            row_set.addWidget(btn)
        row_set.addStretch(1)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["歌曲", "导出格式", "track_id"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(False)
        for row in self._tracks:
            track_id = str(row.get("track_id", "") or "")
            label = f"{row.get('artist', '')} - {row.get('title', '')} ({row.get('file_name', '')})"
            item = QTreeWidgetItem([label, "", track_id])
            self.tree.addTopLevelItem(item)
            combo = QComboBox()
            combo.addItems(["源格式", "mp3", "opus", "flac", "wav", "ogg"])
            combo.setCurrentText("源格式")
            self.tree.setItemWidget(item, 1, combo)
            if track_id:
                self._combo_by_track_id[track_id] = combo
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)

        root.addLayout(path_row)
        root.addLayout(mode_row)
        root.addWidget(self.playlist_hint)
        root.addLayout(row_set)
        root.addWidget(self.tree, 1)
        root.addWidget(self.buttons)

        self.btn_browse.clicked.connect(self._choose_folder)
        self.chk_files.toggled.connect(self._apply_mode_visibility)
        self.chk_playlist.toggled.connect(self._apply_mode_visibility)
        self.btn_all_original.clicked.connect(lambda: self._apply_all("源格式"))
        self.btn_all_mp3.clicked.connect(lambda: self._apply_all("mp3"))
        self.btn_all_opus.clicked.connect(lambda: self._apply_all("opus"))
        self.btn_all_flac.clicked.connect(lambda: self._apply_all("flac"))
        self.btn_all_wav.clicked.connect(lambda: self._apply_all("wav"))
        self.btn_all_ogg.clicked.connect(lambda: self._apply_all("ogg"))
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)

        self.path_input.setText(str(Path.cwd()))
        self._apply_mode_visibility()

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择导出目录", self.path_input.text().strip() or str(Path.cwd()))
        if folder:
            self.path_input.setText(folder)

    def _apply_mode_visibility(self) -> None:
        files_mode = bool(self.chk_files.isChecked())
        playlist_mode = bool(self.chk_playlist.isChecked())
        self.tree.setVisible(files_mode)
        self.playlist_hint.setVisible(playlist_mode)
        for btn in [
            self.btn_all_original,
            self.btn_all_mp3,
            self.btn_all_opus,
            self.btn_all_flac,
            self.btn_all_wav,
            self.btn_all_ogg,
        ]:
            btn.setVisible(files_mode)

    def _apply_all(self, text: str) -> None:
        for combo in self._combo_by_track_id.values():
            combo.setCurrentText(text)

    def _on_accept(self) -> None:
        out_dir = self.output_dir()
        if not out_dir:
            QMessageBox.warning(self, "导出配置", "请选择导出目录。")
            return
        if not self.export_files_enabled() and not self.export_playlist_enabled():
            QMessageBox.warning(self, "导出配置", "请至少勾选一种导出方式。")
            return
        self.accept()

    def output_dir(self) -> str:
        return str(self.path_input.text().strip())

    def export_files_enabled(self) -> bool:
        return bool(self.chk_files.isChecked())

    def export_playlist_enabled(self) -> bool:
        return bool(self.chk_playlist.isChecked())

    def export_plan(self) -> dict[str, str]:
        mapping = {
            "源格式": "original",
            "mp3": "mp3",
            "opus": "opus",
            "flac": "flac",
            "wav": "wav",
            "ogg": "ogg",
        }
        out: dict[str, str] = {}
        for track_id, combo in self._combo_by_track_id.items():
            text = str(combo.currentText() or "源格式")
            out[track_id] = mapping.get(text, "original")
        return out


def _run_export_dialog(parent: QWidget, facade: MuseArcFacade, tracks: list[dict], *, playlist_name: str = "") -> tuple[bool, str]:
    track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
    if not track_ids:
        QMessageBox.warning(parent, "导出", "请先选择歌曲")
        return False, ""
    dlg = ExportConfigDialog(parent, tracks, default_name=playlist_name or "playlist")
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False, ""
    out_dir = dlg.output_dir()
    outputs: list[str] = []
    if dlg.export_playlist_enabled():
        file_path = facade.export_playlist_package(track_ids, out_dir, playlist_name=playlist_name or "playlist")
        outputs.append(file_path)
    if dlg.export_files_enabled():
        facade.export_with_plan(track_ids, out_dir, dlg.export_plan(), bitrate="320k")
        outputs.append(out_dir)
    return True, " ; ".join(outputs) if outputs else out_dir


