from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
import re
import subprocess

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSplitter,
    QTabWidget,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from musearc.app.facade import MuseArcFacade
from musearc.ui.table_models import ColumnDef, DictTableModel


def _apply_button_scale(button: QPushButton, scale: float) -> None:
    button.setMinimumHeight(max(30, int(28 * scale)))


def _install_tree_copy_shortcut(tree: QTreeWidget) -> None:
    def _copy_rows() -> None:
        selected = tree.selectedItems()
        if not selected:
            return
        lines = []
        col_count = tree.columnCount()
        for item in selected:
            lines.append("\t".join(item.text(i) for i in range(col_count)))
        QApplication.clipboard().setText("\n".join(lines))

    shortcut = QShortcut(QKeySequence.StandardKey.Copy, tree)
    shortcut.activated.connect(_copy_rows)
    tree._copy_shortcut = shortcut


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value, default: int = 0) -> int:
    if isinstance(value, (list, tuple, dict, set)):
        return default
    try:
        return int(value or 0)
    except Exception:
        return default


def _format_mmss(seconds: int) -> str:
    sec = max(0, _safe_int(seconds, 0))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _track_label(track: dict) -> str:
    return f"{track.get('artist', '')} - {track.get('title', '')} ({track.get('track_id', '')})"


def _canonical_lyrics_name(file_name: str) -> str:
    stem = Path(str(file_name or "")).stem.casefold().strip()
    stem = re.sub(r"[\s._-]+", " ", stem)
    stem = re.sub(r"\s*[\(\[（]\s*\d+\s*[\)\]）]\s*$", "", stem)
    return stem.strip()


class _TrackPickerDialog(QDialog):
    def __init__(self, parent: QWidget, facade: MuseArcFacade):
        super().__init__(parent)
        self.facade = facade
        self.selected_track_id: str | None = None
        self.setWindowTitle("选择映射歌曲")
        self.resize(920, 620)

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索 文件名/标题/艺术家/专辑")
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
        self.btn_clear = self.buttons.addButton("清空映射", QDialogButtonBox.ButtonRole.DestructiveRole)

        root.addLayout(top)
        root.addWidget(self.table, 1)
        root.addWidget(self.buttons)

        self._all_rows = self.facade.list_tracks(limit=200_000)
        self._apply_filter()

        self.btn_search.clicked.connect(self._apply_filter)
        self.search_input.returnPressed.connect(self._apply_filter)
        self.table.doubleClicked.connect(lambda _idx: self._accept_selected())
        self.btn_ok.clicked.connect(self._accept_selected)
        self.btn_cancel.clicked.connect(self.reject)
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

    def _accept_selected(self) -> None:
        sm = self.table.selectionModel()
        selected = sm.selectedRows() if sm is not None else []
        if not selected:
            QMessageBox.warning(self, "选择映射歌曲", "请先选择一首歌曲。")
            return
        row = self.model.row_at(selected[0].row()) or {}
        track_id = str(row.get("track_id", "") or "")
        if not track_id:
            QMessageBox.warning(self, "选择映射歌曲", "当前行没有有效 track_id。")
            return
        self.selected_track_id = track_id
        self.accept()

    def _accept_clear(self) -> None:
        self.selected_track_id = None
        self.accept()


class ReviewPage(QWidget):
    review_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade
        self._track_map: dict[str, dict] = {}
        self._lyrics_by_source: dict[str, dict] = {}
        self._preview_rows: deque[dict] = deque(maxlen=2)
        self._sync_preview_scroll = False
        self._button_scale = 1.0
        self._static_buttons: list[QPushButton] = []
        self._dynamic_buttons: list[QPushButton] = []
        self._song_group_controls: dict[int, dict] = {}
        self._lyrics_group_controls: dict[int, dict] = {}

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        self.song_tab = QWidget()
        self.lyrics_tab = QWidget()
        self.file_tab = QWidget()
        self.other_tab = QWidget()
        self.tabs.addTab(self.song_tab, "歌曲待审查")
        self.tabs.addTab(self.lyrics_tab, "歌词待审查")
        self.tabs.addTab(self.file_tab, "文件异常")
        self.tabs.addTab(self.other_tab, "其它")

        self._build_song_tab()
        self._build_lyrics_tab()
        self._build_file_tab()
        self._build_other_tab()

        row_bottom = QHBoxLayout()
        self.btn_reload = QPushButton("刷新审查")
        self._register_static_button(self.btn_reload)
        row_bottom.addWidget(self.btn_reload)
        row_bottom.addStretch(1)
        root.addLayout(row_bottom)

        self.btn_reload.clicked.connect(self.reload_reviews)
        self.reload_reviews()

    def _register_static_button(self, button: QPushButton) -> None:
        self._static_buttons.append(button)
        _apply_button_scale(button, self._button_scale)

    def _register_dynamic_button(self, button: QPushButton) -> None:
        self._dynamic_buttons.append(button)
        _apply_button_scale(button, self._button_scale)

    def _build_song_tab(self) -> None:
        root = QVBoxLayout(self.song_tab)
        self.song_tree = QTreeWidget()
        self.song_tree.setHeaderLabels(["保留", "播放", "源文件", "候选歌曲", "相似度", "说明", "审查ID"])
        self.song_tree.setAlternatingRowColors(True)
        self.song_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.song_tree.setRootIsDecorated(True)
        self.song_tree.setStyleSheet("QTreeWidget::indicator{width:30px;height:30px;}")
        _install_tree_copy_shortcut(self.song_tree)
        root.addWidget(self.song_tree, 1)

        self.song_tree.itemClicked.connect(self._on_song_item_clicked)

    def _build_lyrics_tab(self) -> None:
        root = QVBoxLayout(self.lyrics_tab)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.lyrics_tree = QTreeWidget()
        self.lyrics_tree.setHeaderLabels(["保留", "歌词文件", "相似度", "说明", "审查ID", ""])
        self.lyrics_tree.setAlternatingRowColors(True)
        self.lyrics_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.lyrics_tree.setStyleSheet("QTreeWidget::indicator{width:30px;height:30px;}")
        _install_tree_copy_shortcut(self.lyrics_tree)
        split.addWidget(self.lyrics_tree)

        preview_host = QWidget()
        preview_layout = QVBoxLayout(preview_host)
        preview_layout.addWidget(QLabel("歌词对比预览（最近点击的两个文件）"))
        preview_split = QSplitter(Qt.Orientation.Horizontal)
        self.preview_left = QPlainTextEdit()
        self.preview_left.setReadOnly(True)
        self.preview_right = QPlainTextEdit()
        self.preview_right.setReadOnly(True)
        preview_split.addWidget(self.preview_left)
        preview_split.addWidget(self.preview_right)
        preview_split.setStretchFactor(0, 1)
        preview_split.setStretchFactor(1, 1)
        preview_layout.addWidget(preview_split, 1)
        split.addWidget(preview_host)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)

        self.preview_left.verticalScrollBar().valueChanged.connect(
            lambda v: self._sync_preview_scrollbars(from_left=True, value=v)
        )
        self.preview_right.verticalScrollBar().valueChanged.connect(
            lambda v: self._sync_preview_scrollbars(from_left=False, value=v)
        )
        self.lyrics_tree.itemClicked.connect(self._on_lyrics_item_clicked)

    def _build_file_tab(self) -> None:
        root = QVBoxLayout(self.file_tab)
        row = QHBoxLayout()
        self.btn_file_retry = QPushButton("重试导入选中路径")
        self.btn_file_save = QPushButton("保存勾选的文件")
        self.btn_file_ignore = QPushButton("忽略勾选")
        self._register_static_button(self.btn_file_retry)
        self._register_static_button(self.btn_file_save)
        self._register_static_button(self.btn_file_ignore)
        row.addWidget(self.btn_file_retry)
        row.addWidget(self.btn_file_save)
        row.addWidget(self.btn_file_ignore)
        row.addStretch(1)

        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["保留", "标题", "来源", "详情", "审查ID"])
        self.file_tree.setAlternatingRowColors(True)
        self.file_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_tree.setStyleSheet("QTreeWidget::indicator{width:30px;height:30px;}")
        _install_tree_copy_shortcut(self.file_tree)
        root.addLayout(row)
        root.addWidget(self.file_tree, 1)

        self.btn_file_retry.clicked.connect(self._retry_selected_file_issues)
        self.btn_file_save.clicked.connect(lambda: self._resolve_checked_items(self.file_tree, "resolved"))
        self.btn_file_ignore.clicked.connect(lambda: self._resolve_checked_items(self.file_tree, "ignored"))

    def _build_other_tab(self) -> None:
        root = QVBoxLayout(self.other_tab)
        row = QHBoxLayout()
        self.btn_other_save = QPushButton("保存勾选的文件")
        self.btn_other_ignore = QPushButton("忽略勾选")
        self._register_static_button(self.btn_other_save)
        self._register_static_button(self.btn_other_ignore)
        row.addWidget(self.btn_other_save)
        row.addWidget(self.btn_other_ignore)
        row.addStretch(1)

        self.other_tree = QTreeWidget()
        self.other_tree.setHeaderLabels(["保留", "类型", "标题", "数据", "审查ID"])
        self.other_tree.setAlternatingRowColors(True)
        self.other_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.other_tree.setStyleSheet("QTreeWidget::indicator{width:30px;height:30px;}")
        _install_tree_copy_shortcut(self.other_tree)
        root.addLayout(row)
        root.addWidget(self.other_tree, 1)

        self.btn_other_save.clicked.connect(lambda: self._resolve_checked_items(self.other_tree, "resolved"))
        self.btn_other_ignore.clicked.connect(lambda: self._resolve_checked_items(self.other_tree, "ignored"))

    def apply_button_scale(self, scale: float) -> None:
        self._button_scale = max(1.0, float(scale))
        for btn in self._static_buttons:
            _apply_button_scale(btn, self._button_scale)
        for btn in self._dynamic_buttons:
            _apply_button_scale(btn, self._button_scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade
        self.reload_reviews()

    def refresh_page(self) -> None:
        self.reload_reviews()

    def reload_reviews(self) -> None:
        rows = self.facade.pending_reviews(limit=5000)
        tracks = self.facade.list_tracks(limit=200_000)
        self._track_map = {str(r.get("track_id", "")): r for r in tracks if r.get("track_id")}
        lyrics_rows = self.facade.list_lyrics(limit=300_000)
        self._lyrics_by_source = {
            str(r.get("source_relpath", "")).replace("\\", "/"): r
            for r in lyrics_rows
            if str(r.get("source_relpath", "")).strip()
        }

        song_rows: list[dict] = []
        lyrics_rows_out: list[dict] = []
        file_rows: list[dict] = []
        other_rows: list[dict] = []

        for row in rows:
            review_id = str(row.get("review_id", "") or "")
            kind = str(row.get("kind", "") or "")
            payload = row.get("payload") or {}

            if kind == "duplicate":
                review_title = str(row.get("title", "") or "")
                existing_track_id = str(payload.get("existing_track_id") or "")
                track_meta = self._track_map.get(existing_track_id) or {}
                source_path = str(payload.get("path", "") or "")
                song_rows.append(
                    {
                        "review_id": review_id,
                        "group_key": str(payload.get("group_key") or existing_track_id[:8] or Path(source_path).stem or "未分组"),
                        "source_file": Path(source_path).name,
                        "source_path": source_path,
                        "candidate_track_id": existing_track_id,
                        "candidate_track": _track_label(track_meta) if track_meta else existing_track_id,
                        "candidate_path": str(track_meta.get("source_fullpath", "") or ""),
                        "candidate_duration_sec": _safe_float(track_meta.get("duration_sec", 0), 0),
                        "score": _safe_float(payload.get("score", 0), 0.0),
                        "reason": str(payload.get("reason", "") or "疑似重复音频").replace("原因", ""),
                        "candidate_meta": dict(track_meta),
                        "restore_track_id": existing_track_id if review_title == "已删除歌曲重新导入" else "",
                    }
                )
                continue

            if kind == "file_issue" and str(row.get("title", "") or "") in {"指纹提取失败", "响度归一不可用"}:
                source_path = str(payload.get("path", "") or "")
                source_file = Path(source_path).name
                title_hint = str(payload.get("title_hint", "") or "")
                group_key = str(payload.get("group_key", "") or Path(source_path).stem or "未分组")
                suggestions = payload.get("suggest_candidates") or []
                if isinstance(suggestions, list) and suggestions:
                    for sug in suggestions:
                        if not isinstance(sug, dict):
                            continue
                        tid = str(sug.get("track_id", "") or "")
                        track_meta = self._track_map.get(tid) or {}
                        song_rows.append(
                            {
                                "review_id": review_id,
                                "group_key": group_key,
                                "source_file": source_file,
                                "source_path": source_path,
                                "candidate_track_id": tid,
                                "candidate_track": _track_label(track_meta) if track_meta else str(sug.get("title", "") or tid),
                                "candidate_path": str(track_meta.get("source_fullpath", "") or ""),
                                "candidate_duration_sec": _safe_float(track_meta.get("duration_sec", 0), 0),
                                "score": _safe_float(sug.get("score", 0), 0.0),
                                "reason": f"指纹失败/名称相近 {title_hint}",
                                "candidate_meta": dict(track_meta),
                            }
                        )
                else:
                    song_rows.append(
                        {
                            "review_id": review_id,
                            "group_key": group_key,
                            "source_file": source_file,
                            "source_path": source_path,
                            "candidate_track_id": "",
                            "candidate_track": "",
                            "candidate_path": "",
                            "candidate_duration_sec": 0,
                            "score": 0.0,
                            "reason": "指纹失败，暂无候选",
                            "candidate_meta": {},
                        }
                    )
                continue

            if kind == "lyrics_match":
                review_title = str(row.get("title", "") or "")
                source_rel = str(payload.get("lyrics_source", "") or "").replace("\\", "/")
                suggest_id = str(payload.get("suggest_track_id") or "")
                suggest_track = self._track_map.get(suggest_id) or {}
                matched = self._lyrics_by_source.get(source_rel) or {}
                storage_relpath = str(matched.get("storage_relpath", "") or "")
                source_mtime = 0.0
                if storage_relpath:
                    storage_abs = Path(self.facade.library_root) / storage_relpath
                    try:
                        source_mtime = float(storage_abs.stat().st_mtime)
                    except Exception:
                        source_mtime = 0.0
                lyrics_rows_out.append(
                    {
                        "review_id": review_id,
                        "group_key": str(payload.get("lyrics_group_key") or payload.get("group_key") or Path(source_rel).stem or "未分组"),
                        "lyrics_source": source_rel,
                        "lyrics_file": Path(source_rel).name,
                        "lyrics_id": str(payload.get("lyrics_id") or matched.get("lyrics_id") or ""),
                        "storage_relpath": storage_relpath,
                        "suggest_track": _track_label(suggest_track) if suggest_track else suggest_id,
                        "score": _safe_float(payload.get("score", 0), 0.0),
                        "reason": str(payload.get("reason", "") or "匹配置信度不足").replace("原因", ""),
                        "line_count": _safe_int(matched.get("line_count", 0), 0),
                        "imported_at": str(matched.get("imported_at", "") or ""),
                        "source_mtime": source_mtime,
                        "preview": "\n".join(payload.get("lyrics_preview") or []),
                        "restore_lyrics_id": str(payload.get("lyrics_id", "") or "") if review_title == "已删除歌词重新导入" else "",
                    }
                )
                continue

            if kind == "file_issue":
                file_rows.append(
                    {
                        "review_id": review_id,
                        "title": str(row.get("title", "") or ""),
                        "path": str(payload.get("path", "") or ""),
                        "detail": str(payload.get("error", "") or payload.get("duration_sec", "") or "").replace("原因", ""),
                    }
                )
                continue

            other_rows.append(
                {
                    "review_id": review_id,
                    "kind": kind,
                    "title": str(row.get("title", "") or ""),
                    "payload": str(payload),
                }
            )

        self._dynamic_buttons.clear()
        self._fill_song_tree(song_rows)
        self._fill_lyrics_tree(lyrics_rows_out)
        self._fill_file_tree(file_rows)
        self._fill_other_tree(other_rows)
        self.apply_button_scale(self._button_scale)

    def _style_group_header(self, item: QTreeWidgetItem) -> None:
        font = item.font(0)
        font.setBold(True)
        font.setPointSize(max(font.pointSize() + 4, 13))
        item.setFont(0, font)

    def _group_parent_of(self, item: QTreeWidgetItem | None) -> QTreeWidgetItem | None:
        if item is None:
            return None
        node = item
        while node.parent() is not None:
            node = node.parent()
        return node

    def _iter_group_leaf_items(self, parent: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        out: list[QTreeWidgetItem] = []
        stack: list[QTreeWidgetItem] = [parent.child(i) for i in range(parent.childCount())]
        while stack:
            node = stack.pop()
            for i in range(node.childCount()):
                stack.append(node.child(i))
            row = node.data(0, Qt.ItemDataRole.UserRole) or {}
            if not row:
                continue
            if row.get("_meta_row") or row.get("_link_row") or row.get("_footer"):
                continue
            out.append(node)
        return out

    def _find_meta_child(self, item: QTreeWidgetItem) -> QTreeWidgetItem | None:
        for i in range(item.childCount()):
            child = item.child(i)
            row = child.data(0, Qt.ItemDataRole.UserRole) or {}
            if row.get("_meta_row"):
                return child
        return None

    def _iter_meta_children(self, item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        out: list[QTreeWidgetItem] = []
        for i in range(item.childCount()):
            child = item.child(i)
            row = child.data(0, Qt.ItemDataRole.UserRole) or {}
            if row.get("_meta_row"):
                out.append(child)
        return out

    def _fill_song_tree(self, rows: list[dict]) -> None:
        self.song_tree.clear()
        self._song_group_controls.clear()
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            groups[str(row.get("group_key", "") or "未分组")].append(row)

        for group_key in sorted(groups.keys(), key=lambda s: s.casefold()):
            parent = QTreeWidgetItem([group_key, "", "", "", "", "", ""])
            parent.setFirstColumnSpanned(True)
            parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            parent.setData(0, Qt.ItemDataRole.UserRole, {"_group_header": True, "group_key": group_key})
            self._style_group_header(parent)
            self.song_tree.addTopLevelItem(parent)

            max_dur = 300
            for row in groups[group_key]:
                item = QTreeWidgetItem(
                    [
                        "",
                        "▶",
                        str(row.get("source_file", "")),
                        str(row.get("candidate_track", "")),
                        f"{_safe_float(row.get('score', 0.0), 0.0):.4f}",
                        str(row.get("reason", "")).replace("原因", ""),
                        str(row.get("review_id", "")),
                    ]
                )
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked)
                item.setData(0, Qt.ItemDataRole.UserRole, dict(row))
                parent.addChild(item)
                max_dur = max(max_dur, _safe_int(row.get("candidate_duration_sec", 0), 0))

                meta = row.get("candidate_meta") if isinstance(row.get("candidate_meta"), dict) else {}
                track_id = str(row.get("candidate_track_id", "") or "")
                editable_fields = [
                    ("title", "标题"),
                    ("artist", "艺术家"),
                    ("album", "专辑"),
                    ("language_kind", "语言"),
                    ("preference_level", "喜好"),
                ]
                for field_key, field_label in editable_fields:
                    field_value = str(meta.get(field_key, "") or "")
                    meta_item = QTreeWidgetItem(["", "", field_label, field_value, "", "", ""])
                    meta_item.setData(
                        0,
                        Qt.ItemDataRole.UserRole,
                        {
                            "_meta_row": True,
                            "track_id": track_id,
                            "field_key": field_key,
                            "field_label": field_label,
                        },
                    )
                    meta_item.setHidden(True)
                    item.addChild(meta_item)

            footer = QTreeWidgetItem(["", "", "", "", "", "", ""])
            footer.setFirstColumnSpanned(True)
            footer.setFlags(footer.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            footer.setData(0, Qt.ItemDataRole.UserRole, {"_footer": True, "group_key": group_key})
            parent.addChild(footer)

            host = QWidget(self.song_tree)
            row_ops = QHBoxLayout(host)
            row_ops.setContentsMargins(8, 6, 8, 6)
            row_ops.setSpacing(8)
            btn_same = QPushButton("这是相同歌曲")
            btn_diff = QPushButton("这是不同歌曲")
            btn_save = QPushButton("保存勾选的文件")
            btn_cancel = QPushButton("取消导入")
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, max_dur)
            label_time = QLabel("00:00")
            row_ops.addWidget(btn_same)
            row_ops.addWidget(btn_diff)
            row_ops.addWidget(btn_save)
            row_ops.addWidget(btn_cancel)
            row_ops.addWidget(QLabel("组进度"))
            row_ops.addWidget(slider, 1)
            row_ops.addWidget(label_time)
            row_ops.addStretch(1)
            self.song_tree.setItemWidget(footer, 0, host)

            self._song_group_controls[id(parent)] = {"parent": parent, "slider": slider}
            slider.valueChanged.connect(lambda value, lbl=label_time: lbl.setText(_format_mmss(value)))
            btn_same.clicked.connect(lambda _=False, p=parent: self._apply_song_preset_same_for_group(p))
            btn_diff.clicked.connect(lambda _=False, p=parent: self._apply_song_preset_diff_for_group(p))
            btn_save.clicked.connect(lambda _=False, p=parent: self._save_song_group(p))
            btn_cancel.clicked.connect(lambda _=False, p=parent: self._cancel_song_group(p))
            self._register_dynamic_button(btn_same)
            self._register_dynamic_button(btn_diff)
            self._register_dynamic_button(btn_save)
            self._register_dynamic_button(btn_cancel)

            parent.setExpanded(True)
            self._apply_song_preset_same_for_group(parent)

    def _fill_lyrics_tree(self, rows: list[dict]) -> None:
        self.lyrics_tree.clear()
        self._lyrics_group_controls.clear()
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            groups[str(row.get("group_key", "") or "未分组")].append(row)

        for group_key in sorted(groups.keys(), key=lambda s: s.casefold()):
            group_rows = list(groups[group_key])
            parent = QTreeWidgetItem([group_key, "", "", "", "", ""])
            parent.setFirstColumnSpanned(True)
            parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            parent.setData(0, Qt.ItemDataRole.UserRole, {"_group_header": True, "group_key": group_key})
            self._style_group_header(parent)
            self.lyrics_tree.addTopLevelItem(parent)

            group_items: list[QTreeWidgetItem] = []
            for row in group_rows:
                item = QTreeWidgetItem(
                    [
                        "",
                        str(row.get("lyrics_file", "")),
                        f"{_safe_float(row.get('score', 0.0), 0.0):.4f}",
                        str(row.get("reason", "")).replace("原因", ""),
                        str(row.get("review_id", "")),
                        "",
                    ]
                )
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Unchecked)
                item.setData(0, Qt.ItemDataRole.UserRole, dict(row))
                parent.addChild(item)
                group_items.append(item)

                suggest_text = str(row.get("suggest_track", "") or "").strip()
                link_label = f"建议：{suggest_text}" if suggest_text else ""
                link_item = QTreeWidgetItem(["", "🔗", "", link_label, "", "点击编辑绑定"])
                link_item.setFlags((link_item.flags() | Qt.ItemFlag.ItemIsSelectable) & ~Qt.ItemFlag.ItemIsUserCheckable)
                link_payload = dict(row)
                link_payload["_link_row"] = True
                link_item.setData(0, Qt.ItemDataRole.UserRole, link_payload)
                item.addChild(link_item)
                item.setExpanded(True)

            footer = QTreeWidgetItem(["", "", "", "", "", ""])
            footer.setFirstColumnSpanned(True)
            footer.setFlags(footer.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            footer.setData(0, Qt.ItemDataRole.UserRole, {"_footer": True, "group_key": group_key})
            parent.addChild(footer)

            host = QWidget(self.lyrics_tree)
            row_ops = QHBoxLayout(host)
            row_ops.setContentsMargins(8, 6, 8, 6)
            row_ops.setSpacing(8)
            btn_same = QPushButton("这是相同歌词")
            btn_diff = QPushButton("这是不同歌词")
            btn_save = QPushButton("保存勾选的文件")
            btn_cancel = QPushButton("取消导入")
            row_ops.addWidget(btn_same)
            row_ops.addWidget(btn_diff)
            row_ops.addWidget(btn_save)
            row_ops.addWidget(btn_cancel)
            row_ops.addStretch(1)
            self.lyrics_tree.setItemWidget(footer, 0, host)

            self._lyrics_group_controls[id(parent)] = {"parent": parent}
            btn_same.clicked.connect(lambda _=False, p=parent: self._apply_lyrics_preset_same_for_group(p))
            btn_diff.clicked.connect(lambda _=False, p=parent: self._apply_lyrics_preset_diff_for_group(p))
            btn_save.clicked.connect(lambda _=False, p=parent: self._save_lyrics_group(p))
            btn_cancel.clicked.connect(lambda _=False, p=parent: self._cancel_lyrics_group(p))
            self._register_dynamic_button(btn_same)
            self._register_dynamic_button(btn_diff)
            self._register_dynamic_button(btn_save)
            self._register_dynamic_button(btn_cancel)

            self._apply_default_lyrics_checks(group_rows, group_items)
            parent.setExpanded(True)

    @staticmethod
    def _lyrics_line_count(row: dict) -> int:
        return _safe_int(row.get("line_count", 0), 0)

    @staticmethod
    def _lyrics_imported_at(row: dict) -> str:
        return str(row.get("imported_at", "") or "")

    @staticmethod
    def _lyrics_source_mtime(row: dict) -> float:
        return _safe_float(row.get("source_mtime", 0.0), 0.0)

    @staticmethod
    def _lyrics_group_same_file(rows: list[dict]) -> bool:
        keys = {_canonical_lyrics_name(str(r.get("lyrics_file", "") or "")) for r in rows}
        keys.discard("")
        return len(keys) <= 1

    def _apply_default_lyrics_checks(self, rows: list[dict], items: list[QTreeWidgetItem]) -> None:
        if not rows or not items:
            return
        pairs = list(zip(rows, items))
        if self._lyrics_group_same_file(rows):
            target = max(
                pairs,
                key=lambda p: (
                    self._lyrics_line_count(p[0]),
                    self._lyrics_source_mtime(p[0]),
                    self._lyrics_imported_at(p[0]),
                    str(p[0].get("lyrics_source", "") or ""),
                ),
            )[1]
        else:
            target = max(
                pairs,
                key=lambda p: (
                    self._lyrics_source_mtime(p[0]),
                    self._lyrics_imported_at(p[0]),
                    self._lyrics_line_count(p[0]),
                    str(p[0].get("lyrics_source", "") or ""),
                ),
            )[1]
        target.setCheckState(0, Qt.CheckState.Checked)

    def _fill_file_tree(self, rows: list[dict]) -> None:
        self.file_tree.clear()
        for row in rows:
            item = QTreeWidgetItem(
                [
                    "",
                    str(row.get("title", "")),
                    str(row.get("path", "")),
                    str(row.get("detail", "")).replace("原因", ""),
                    str(row.get("review_id", "")),
                ]
            )
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked)
            item.setData(0, Qt.ItemDataRole.UserRole, dict(row))
            self.file_tree.addTopLevelItem(item)

    def _fill_other_tree(self, rows: list[dict]) -> None:
        self.other_tree.clear()
        for row in rows:
            item = QTreeWidgetItem(
                [
                    "",
                    str(row.get("kind", "")),
                    str(row.get("title", "")),
                    str(row.get("payload", "")),
                    str(row.get("review_id", "")),
                ]
            )
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked)
            item.setData(0, Qt.ItemDataRole.UserRole, dict(row))
            self.other_tree.addTopLevelItem(item)

    def _iter_checked_review_ids(self, tree: QTreeWidget) -> list[str]:
        ids: list[str] = []
        stack: list[QTreeWidgetItem] = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            for i in range(item.childCount()):
                stack.append(item.child(i))
            row = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if not row:
                continue
            if item.checkState(0) != Qt.CheckState.Checked:
                continue
            rid = str(row.get("review_id", "") or "")
            if rid:
                ids.append(rid)
        return ids

    def _resolve_checked_items(self, tree: QTreeWidget, status: str) -> None:
        ids = self._iter_checked_review_ids(tree)
        if not ids:
            QMessageBox.information(self, "审查处理", "请先勾选要处理的项。")
            return
        count = self.facade.resolve_reviews(ids, status=status)
        self.reload_reviews()
        self.review_changed.emit()
        QMessageBox.information(self, "审查处理", f"已处理 {count} 项。")

    def _play_with_external_player(self, path_text: str, start_sec: int = 0) -> None:
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
        cmd = [exe, target]
        start = max(0, int(start_sec))
        low_name = Path(exe).name.casefold()
        if start > 0:
            if "ffplay" in low_name:
                cmd = [exe, "-ss", str(start), target]
            elif "vlc" in low_name:
                cmd = [exe, f"--start-time={start}", target]
            elif "mpv" in low_name:
                cmd = [exe, f"--start={start}", target]
        try:
            subprocess.Popen(cmd)
        except Exception as exc:
            QMessageBox.critical(self, "播放失败", str(exc))

    def _on_song_item_clicked(self, item: QTreeWidgetItem, col: int) -> None:
        row = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if not row or row.get("_footer"):
            return
        if row.get("_meta_row"):
            self._edit_song_meta_row(item, row)
            return
        if col == 1:
            parent = self._group_parent_of(item)
            start = 0
            if parent is not None:
                controls = self._song_group_controls.get(id(parent), {})
                slider = controls.get("slider")
                if isinstance(slider, QSlider):
                    start = slider.value()
            path = str(row.get("source_path", "") or row.get("candidate_path", "")).strip()
            self._play_with_external_player(path, start)
            return
        self._toggle_song_meta(item)

    def _toggle_song_meta(self, item: QTreeWidgetItem) -> None:
        meta_items = self._iter_meta_children(item)
        if not meta_items:
            return
        show = bool(meta_items[0].isHidden())
        for meta_item in meta_items:
            meta_item.setHidden(not show)
        item.setExpanded(show)

    def _edit_song_meta_row(self, item: QTreeWidgetItem, row: dict) -> None:
        track_id = str(row.get("track_id", "") or "")
        field_key = str(row.get("field_key", "") or "")
        field_label = str(row.get("field_label", "") or field_key)
        if not track_id or not field_key:
            return

        old_value = str(item.text(3) or "")
        if field_key == "preference_level":
            try:
                start = max(1, min(10, int(old_value or "5")))
            except Exception:
                start = 5
            value, ok = QInputDialog.getInt(self, "编辑候选元数据", field_label, value=start, minValue=1, maxValue=10)
            if not ok:
                return
            new_value = int(value)
            display = str(new_value)
        else:
            value, ok = QInputDialog.getText(self, "编辑候选元数据", field_label, text=old_value)
            if not ok:
                return
            new_value = str(value).strip()
            display = new_value

        if str(old_value) == str(display):
            return
        self.facade.update_tracks_fields([track_id], {field_key: new_value})
        item.setText(3, display)
        self.reload_reviews()

    def _on_song_item_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        row = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if not row or row.get("_meta_row") or row.get("_footer"):
            return
        self._edit_song_candidate_from_row(row)

    def _edit_song_candidate_from_row(self, row: dict) -> None:
        track_id = str(row.get("candidate_track_id", "") or "")
        if not track_id:
            return
        track = self._track_map.get(track_id) or {}
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑候选歌曲信息")
        form = QFormLayout(dialog)
        input_title = QLineEdit(str(track.get("title", "") or ""))
        input_artist = QLineEdit(str(track.get("artist", "") or ""))
        input_album = QLineEdit(str(track.get("album", "") or ""))
        form.addRow("标题", input_title)
        form.addRow("艺术家", input_artist)
        form.addRow("专辑", input_album)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        form.addWidget(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.facade.update_tracks_fields(
            [track_id],
            {
                "title": input_title.text().strip(),
                "artist": input_artist.text().strip(),
                "album": input_album.text().strip(),
            },
        )
        self.reload_reviews()

    def _on_lyrics_item_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        row = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if not row or row.get("_footer") or row.get("_meta_row"):
            return
        if row.get("_link_row"):
            self._map_lyrics_row(row)
            return
        self._preview_rows.append(dict(row))
        rows = list(self._preview_rows)
        if len(rows) == 1:
            rows = [rows[0], rows[0]]
        self.preview_left.setPlainText(self._read_lyrics_text(rows[-2]))
        self.preview_right.setPlainText(self._read_lyrics_text(rows[-1]))

    def _map_lyrics_row(self, row: dict) -> None:
        lyrics_id = str(row.get("lyrics_id", "") or "")
        if not lyrics_id:
            QMessageBox.warning(self, "修改建议歌曲", "当前行没有有效 lyrics_id。")
            return
        picker = _TrackPickerDialog(self, self.facade)
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        self.facade.set_primary_track_for_lyrics(lyrics_id, picker.selected_track_id)
        self.reload_reviews()

    def _read_lyrics_text(self, row: dict) -> str:
        storage_rel = str(row.get("storage_relpath", "") or "")
        if storage_rel:
            target = Path(self.facade.library_root) / storage_rel
            if target.exists():
                try:
                    return target.read_text(encoding="utf-8")
                except Exception as exc:
                    return f"无法读取歌词文件: {exc}"
        preview = str(row.get("preview", "") or "").strip()
        return preview or "（无可用歌词内容）"

    def _sync_preview_scrollbars(self, *, from_left: bool, value: int) -> None:
        if self._sync_preview_scroll:
            return
        self._sync_preview_scroll = True
        try:
            if from_left:
                self.preview_right.verticalScrollBar().setValue(value)
            else:
                self.preview_left.verticalScrollBar().setValue(value)
        finally:
            self._sync_preview_scroll = False

    def _review_ids_for_group(self, parent: QTreeWidgetItem) -> list[str]:
        ids: set[str] = set()
        for item in self._iter_group_leaf_items(parent):
            row = item.data(0, Qt.ItemDataRole.UserRole) or {}
            rid = str(row.get("review_id", "") or "")
            if rid:
                ids.add(rid)
        return sorted(ids)

    def _apply_song_preset_same_for_group(self, parent: QTreeWidgetItem) -> None:
        best_by_source: dict[str, tuple[float, QTreeWidgetItem]] = {}
        all_leaf: list[QTreeWidgetItem] = []
        for child in self._iter_group_leaf_items(parent):
            row = child.data(0, Qt.ItemDataRole.UserRole) or {}
            source = str(row.get("source_file", "") or "")
            score = _safe_float(row.get("score", 0), 0)
            all_leaf.append(child)
            current = best_by_source.get(source)
            if current is None or score > current[0]:
                best_by_source[source] = (score, child)
        keep = {id(v[1]) for v in best_by_source.values()}
        for child in all_leaf:
            child.setCheckState(0, Qt.CheckState.Checked if id(child) in keep else Qt.CheckState.Unchecked)

    def _apply_song_preset_diff_for_group(self, parent: QTreeWidgetItem) -> None:
        for child in self._iter_group_leaf_items(parent):
            child.setCheckState(0, Qt.CheckState.Checked)

    def _save_song_group(self, parent: QTreeWidgetItem) -> None:
        status_by_review: dict[str, bool] = {}
        restore_track_ids: set[str] = set()
        for child in self._iter_group_leaf_items(parent):
            row = child.data(0, Qt.ItemDataRole.UserRole) or {}
            rid = str(row.get("review_id", "") or "")
            if not rid:
                continue
            checked = child.checkState(0) == Qt.CheckState.Checked
            status_by_review[rid] = bool(status_by_review.get(rid, False) or checked)
            if checked:
                restore_id = str(row.get("restore_track_id", "") or "")
                if restore_id:
                    restore_track_ids.add(restore_id)
        if not status_by_review:
            return
        if restore_track_ids:
            self.facade.restore_tracks(sorted(restore_track_ids))
        resolved_ids = [rid for rid, keep in status_by_review.items() if keep]
        ignored_ids = [rid for rid, keep in status_by_review.items() if not keep]
        if resolved_ids:
            self.facade.resolve_reviews(resolved_ids, status="resolved")
        if ignored_ids:
            self.facade.resolve_reviews(ignored_ids, status="ignored")
        self.reload_reviews()
        self.review_changed.emit()

    def _cancel_song_group(self, parent: QTreeWidgetItem) -> None:
        ids = self._review_ids_for_group(parent)
        if not ids:
            return
        self.facade.resolve_reviews(ids, status="ignored")
        self.reload_reviews()
        self.review_changed.emit()

    def _apply_lyrics_preset_same_for_group(self, parent: QTreeWidgetItem) -> None:
        rows: list[dict] = []
        items: list[QTreeWidgetItem] = []
        for child in self._iter_group_leaf_items(parent):
            child.setCheckState(0, Qt.CheckState.Unchecked)
            row = child.data(0, Qt.ItemDataRole.UserRole) or {}
            rows.append(dict(row))
            items.append(child)
        self._apply_default_lyrics_checks(rows, items)

    def _apply_lyrics_preset_diff_for_group(self, parent: QTreeWidgetItem) -> None:
        for child in self._iter_group_leaf_items(parent):
            child.setCheckState(0, Qt.CheckState.Checked)

    def _save_lyrics_group(self, parent: QTreeWidgetItem) -> None:
        status_by_review: dict[str, bool] = {}
        restore_lyrics_ids: set[str] = set()
        for child in self._iter_group_leaf_items(parent):
            row = child.data(0, Qt.ItemDataRole.UserRole) or {}
            rid = str(row.get("review_id", "") or "")
            if not rid:
                continue
            checked = child.checkState(0) == Qt.CheckState.Checked
            status_by_review[rid] = bool(status_by_review.get(rid, False) or checked)
            if checked:
                restore_id = str(row.get("restore_lyrics_id", "") or "")
                if restore_id:
                    restore_lyrics_ids.add(restore_id)
        if not status_by_review:
            return
        if restore_lyrics_ids:
            self.facade.restore_lyrics(sorted(restore_lyrics_ids))
        resolved_ids = [rid for rid, keep in status_by_review.items() if keep]
        ignored_ids = [rid for rid, keep in status_by_review.items() if not keep]
        if resolved_ids:
            self.facade.resolve_reviews(resolved_ids, status="resolved")
        if ignored_ids:
            self.facade.resolve_reviews(ignored_ids, status="ignored")
        self.reload_reviews()
        self.review_changed.emit()

    def _cancel_lyrics_group(self, parent: QTreeWidgetItem) -> None:
        ids = self._review_ids_for_group(parent)
        if not ids:
            return
        self.facade.resolve_reviews(ids, status="ignored")
        self.reload_reviews()
        self.review_changed.emit()

    def _retry_selected_file_issues(self) -> None:
        stack = [self.file_tree.topLevelItem(i) for i in range(self.file_tree.topLevelItemCount())]
        pairs: list[tuple[str, str]] = []
        while stack:
            item = stack.pop()
            for i in range(item.childCount()):
                stack.append(item.child(i))
            row = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if not row:
                continue
            if item.checkState(0) != Qt.CheckState.Checked:
                continue
            review_id = str(row.get("review_id", "") or "")
            path = str(row.get("path", "") or "").strip()
            if review_id and path:
                pairs.append((review_id, path))
        if not pairs:
            QMessageBox.information(self, "重试导入", "请先勾选包含有效路径的异常项。")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        ok_count = 0
        fail_count = 0
        for _review_id, path in pairs:
            try:
                self.facade.import_from(path)
                ok_count += 1
            except Exception:
                fail_count += 1
        QApplication.restoreOverrideCursor()
        self.facade.resolve_reviews([rid for rid, _path in pairs], status="resolved")
        self.reload_reviews()
        self.review_changed.emit()
        QMessageBox.information(self, "重试导入", f"已重试 {len(pairs)} 项，成功 {ok_count}，失败 {fail_count}。")
