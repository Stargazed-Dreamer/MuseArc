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
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QSpinBox,
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
    stem = re.sub(r"\s*[\(\[（【].*?[\)\]）】]\s*$", "", stem)
    return stem.strip()


def _lyrics_file_bracket_count(file_name: str) -> int:
    stem = Path(str(file_name or "")).stem
    return len(re.findall(r"[\(\[（【].*?[\)\]）】]", stem))


def _derive_lyrics_group_title(group_key: str, source_rel: str) -> str:
    key = str(group_key or "").strip()
    if key and not key.startswith("lyr_grp_"):
        return key
    stem = Path(str(source_rel or "")).stem.strip()
    if not stem:
        return key or "未分组"
    cleaned = re.sub(r"\s*[\(\[（【].*?[\)\]）】]\s*$", "", stem).strip()
    return cleaned or stem


def _derive_song_group_title(group_key: str, source_path: str) -> str:
    key = str(group_key or "").strip()
    stem = Path(str(source_path or "")).stem.strip()
    if key and len(key) > 6 and not re.fullmatch(r"[0-9a-fA-F_]+", key):
        return key
    if stem:
        cleaned = re.sub(r"\s*[\(\[（【].*?[\)\]）】]\s*$", "", stem).strip()
        return cleaned or stem
    return key or "未分组"


class _ClickableFrame(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


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
        self._song_group_controls: dict[str, dict] = {}
        self._lyrics_group_controls: dict[str, dict] = {}

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
        self.song_scroll = QScrollArea()
        self.song_scroll.setWidgetResizable(True)
        self.song_groups_host = QWidget()
        self.song_groups_layout = QVBoxLayout(self.song_groups_host)
        self.song_groups_layout.setContentsMargins(8, 8, 8, 8)
        self.song_groups_layout.setSpacing(12)
        self.song_groups_layout.addStretch(1)
        self.song_scroll.setWidget(self.song_groups_host)
        root.addWidget(self.song_scroll, 1)

    def _build_lyrics_tab(self) -> None:
        root = QVBoxLayout(self.lyrics_tab)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.lyrics_scroll = QScrollArea()
        self.lyrics_scroll.setWidgetResizable(True)
        self.lyrics_groups_host = QWidget()
        self.lyrics_groups_layout = QVBoxLayout(self.lyrics_groups_host)
        self.lyrics_groups_layout.setContentsMargins(8, 8, 8, 8)
        self.lyrics_groups_layout.setSpacing(12)
        self.lyrics_groups_layout.addStretch(1)
        self.lyrics_scroll.setWidget(self.lyrics_groups_host)
        split.addWidget(self.lyrics_scroll)

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

    def _build_file_tab(self) -> None:
        root = QVBoxLayout(self.file_tab)
        row = QHBoxLayout()
        self.btn_file_invert = QPushButton("反选")
        self.btn_file_retry = QPushButton("重试导入选中路径")
        self.btn_file_save = QPushButton("保存勾选的文件")
        self.btn_file_ignore = QPushButton("忽略勾选")
        self._register_static_button(self.btn_file_invert)
        self._register_static_button(self.btn_file_retry)
        self._register_static_button(self.btn_file_save)
        self._register_static_button(self.btn_file_ignore)
        row.addWidget(self.btn_file_invert)
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

        self.btn_file_invert.clicked.connect(lambda: self._invert_check_state_tree(self.file_tree))
        self.btn_file_retry.clicked.connect(self._retry_selected_file_issues)
        self.btn_file_save.clicked.connect(lambda: self._resolve_checked_items(self.file_tree, "resolved"))
        self.btn_file_ignore.clicked.connect(lambda: self._resolve_checked_items(self.file_tree, "ignored"))

    def _build_other_tab(self) -> None:
        root = QVBoxLayout(self.other_tab)
        row = QHBoxLayout()
        self.btn_other_invert = QPushButton("反选")
        self.btn_other_save = QPushButton("保存勾选的文件")
        self.btn_other_ignore = QPushButton("忽略勾选")
        self._register_static_button(self.btn_other_invert)
        self._register_static_button(self.btn_other_save)
        self._register_static_button(self.btn_other_ignore)
        row.addWidget(self.btn_other_invert)
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

        self.btn_other_invert.clicked.connect(lambda: self._invert_check_state_tree(self.other_tree))
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
                        "group_title": _derive_song_group_title(
                            str(payload.get("group_key") or existing_track_id[:8] or ""),
                            source_path,
                        ),
                        "source_file": Path(source_path).name,
                        "source_path": source_path,
                        "candidate_track_id": existing_track_id,
                        "candidate_track": _track_label(track_meta) if track_meta else existing_track_id,
                        "candidate_file_name": str(track_meta.get("file_name", "") or ""),
                        "candidate_path": str(track_meta.get("source_fullpath", "") or ""),
                        "candidate_duration_sec": _safe_float(track_meta.get("duration_sec", 0), 0),
                        "score": _safe_float(payload.get("score", 0), 0.0),
                        "reason": str(payload.get("reason", "") or "疑似重复音频").replace("原因", ""),
                        "candidate_meta": dict(track_meta),
                        "restore_track_id": existing_track_id if review_title == "已删除歌曲重新导入" else "",
                        "deferred_import": bool(payload.get("deferred_import", False)),
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
                                "group_title": _derive_song_group_title(group_key, source_path),
                                "source_file": source_file,
                                "source_path": source_path,
                                "candidate_track_id": tid,
                                "candidate_track": _track_label(track_meta) if track_meta else str(sug.get("title", "") or tid),
                                "candidate_file_name": str(track_meta.get("file_name", "") or ""),
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
                            "group_title": _derive_song_group_title(group_key, source_path),
                            "source_file": source_file,
                            "source_path": source_path,
                            "candidate_track_id": "",
                            "candidate_track": "",
                            "candidate_file_name": "",
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
                        "group_title": _derive_lyrics_group_title(
                            str(payload.get("lyrics_group_title") or payload.get("lyrics_group_key") or payload.get("group_key") or ""),
                            source_rel,
                        ),
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

    @staticmethod
    def _clear_group_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if child_layout is not None:
                while child_layout.count():
                    sub = child_layout.takeAt(0)
                    sub_widget = sub.widget()
                    if sub_widget is not None:
                        sub_widget.deleteLater()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _iter_tree_leaf_items(tree: QTreeWidget) -> list[QTreeWidgetItem]:
        out: list[QTreeWidgetItem] = []
        stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
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

    def _group_parent_of(self, item: QTreeWidgetItem | None) -> QTreeWidgetItem | None:
        if item is None:
            return None
        node = item
        while node.parent() is not None:
            node = node.parent()
        return node

    def _iter_group_leaf_items(self, group: dict) -> list[QTreeWidgetItem]:
        tree = group.get("tree") if isinstance(group, dict) else None
        if not isinstance(tree, QTreeWidget):
            return []
        return self._iter_tree_leaf_items(tree)

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

    @staticmethod
    def _aggregate_song_group_rows(rows: list[dict]) -> list[dict]:
        merged: dict[tuple[str, str], dict] = {}
        order: list[tuple[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_path = str(row.get("source_path", "") or "")
            review_id = str(row.get("review_id", "") or "")
            key = (review_id or source_path, source_path)
            if key not in merged:
                base = dict(row)
                base["candidates"] = []
                merged[key] = base
                order.append(key)
            target = merged[key]
            candidate_track_id = str(row.get("candidate_track_id", "") or "")
            candidate_path = str(row.get("candidate_path", "") or "")
            candidate_file = str(row.get("candidate_file_name", "") or "")
            candidate_track = str(row.get("candidate_track", "") or "")
            has_candidate = any([candidate_track_id, candidate_path, candidate_file, candidate_track])
            if has_candidate:
                candidate = {
                    "candidate_track_id": candidate_track_id,
                    "candidate_path": candidate_path,
                    "candidate_file_name": candidate_file,
                    "candidate_track": candidate_track,
                    "candidate_duration_sec": _safe_float(row.get("candidate_duration_sec", 0), 0),
                    "score": _safe_float(row.get("score", 0), 0.0),
                    "candidate_meta": dict(row.get("candidate_meta") or {}),
                }
                exists = False
                for existing in target["candidates"]:
                    if (
                        str(existing.get("candidate_track_id", "") or "") == candidate_track_id
                        and str(existing.get("candidate_path", "") or "") == candidate_path
                    ):
                        exists = True
                        break
                if not exists:
                    target["candidates"].append(candidate)

        out: list[dict] = []
        for key in order:
            row = merged[key]
            candidates = row.get("candidates") if isinstance(row.get("candidates"), list) else []
            candidates.sort(key=lambda c: _safe_float(c.get("score", 0), 0.0), reverse=True)
            if candidates:
                best = candidates[0]
                row["candidate_track_id"] = str(best.get("candidate_track_id", "") or "")
                row["candidate_path"] = str(best.get("candidate_path", "") or "")
                row["candidate_file_name"] = str(best.get("candidate_file_name", "") or "")
                row["candidate_track"] = str(best.get("candidate_track", "") or "")
                row["candidate_duration_sec"] = _safe_float(best.get("candidate_duration_sec", 0), 0)
                row["candidate_meta"] = dict(best.get("candidate_meta") or {})
                row["score"] = _safe_float(best.get("score", row.get("score", 0)), 0.0)
            else:
                row["candidate_track_id"] = ""
                row["candidate_path"] = ""
                row["candidate_file_name"] = ""
                row["candidate_track"] = ""
                row["candidate_duration_sec"] = 0.0
                row["candidate_meta"] = {}
            out.append(row)
        return out

    def _fill_song_tree(self, rows: list[dict]) -> None:
        self._song_group_controls.clear()
        self._clear_group_layout(self.song_groups_layout)
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            group_title = str(row.get("group_title", "") or row.get("group_key", "") or "未分组")
            groups[group_title].append(row)

        if not groups:
            empty = QLabel("暂无歌曲待审查")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.song_groups_layout.addWidget(empty)
            self.song_groups_layout.addStretch(1)
            return

        for group_key in sorted(groups.keys(), key=lambda s: s.casefold()):
            group_rows = self._aggregate_song_group_rows(list(groups[group_key]))
            frame = QFrame()
            frame.setFrameShape(QFrame.Shape.StyledPanel)
            frame.setStyleSheet("QFrame{background:#f8fbff;border:1px solid #d7e4f4;border-radius:8px;}")
            host = QVBoxLayout(frame)
            host.setContentsMargins(10, 10, 10, 10)
            host.setSpacing(8)

            title = QLabel(group_key)
            tfont = title.font()
            tfont.setBold(True)
            tfont.setPointSize(max(tfont.pointSize() + 4, 14))
            title.setFont(tfont)
            host.addWidget(title)

            header = QWidget()
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(6, 0, 6, 0)
            header_layout.setSpacing(8)
            for text, stretch, fixed in [
                ("保留", 0, 44),
                ("播放", 0, 44),
                ("文件名", 3, 0),
                ("来源", 0, 84),
                ("相对相似度", 0, 96),
                ("审查原因", 2, 0),
            ]:
                lbl = QLabel(text)
                font = lbl.font()
                font.setBold(True)
                lbl.setFont(font)
                if fixed > 0:
                    lbl.setFixedWidth(fixed)
                header_layout.addWidget(lbl, stretch)
            host.addWidget(header)

            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 300)
            row_controls: list[dict] = []

            max_dur = 300
            for row in group_rows:
                row_ctrl = self._build_song_row_widget(row, slider)
                row_controls.append(row_ctrl)
                host.addWidget(row_ctrl["container"])
                max_dur = max(max_dur, _safe_int(row.get("candidate_duration_sec", 0), 0))
                candidates = row.get("candidates") if isinstance(row.get("candidates"), list) else []
                for candidate in candidates:
                    max_dur = max(max_dur, _safe_int(candidate.get("candidate_duration_sec", 0), 0))

            row_ops_host = QWidget()
            row_ops = QHBoxLayout(row_ops_host)
            row_ops.setContentsMargins(0, 2, 0, 0)
            row_ops.setSpacing(8)
            btn_invert = QPushButton("反选")
            btn_same = QPushButton("这是相同歌曲")
            btn_diff = QPushButton("这是不同歌曲")
            btn_save = QPushButton("保存勾选的文件")
            btn_cancel = QPushButton("取消导入")
            slider.setRange(0, max_dur)
            label_time = QLabel("00:00")
            row_ops.addWidget(btn_invert)
            row_ops.addWidget(btn_same)
            row_ops.addWidget(btn_diff)
            row_ops.addWidget(btn_save)
            row_ops.addWidget(btn_cancel)
            row_ops.addWidget(QLabel("组进度"))
            row_ops.addWidget(slider, 1)
            row_ops.addWidget(label_time)
            row_ops.addStretch(1)
            host.addWidget(row_ops_host)

            controls = {"group_key": group_key, "rows": row_controls, "slider": slider}
            self._song_group_controls[group_key] = controls
            slider.valueChanged.connect(lambda value, lbl=label_time: lbl.setText(_format_mmss(value)))
            btn_invert.clicked.connect(lambda _=False, g=controls: self._invert_song_group(g))
            btn_same.clicked.connect(lambda _=False, g=controls: self._apply_song_preset_same_for_group(g))
            btn_diff.clicked.connect(lambda _=False, g=controls: self._apply_song_preset_diff_for_group(g))
            btn_save.clicked.connect(lambda _=False, g=controls: self._save_song_group(g))
            btn_cancel.clicked.connect(lambda _=False, g=controls: self._cancel_song_group(g))
            self._register_dynamic_button(btn_invert)
            self._register_dynamic_button(btn_same)
            self._register_dynamic_button(btn_diff)
            self._register_dynamic_button(btn_save)
            self._register_dynamic_button(btn_cancel)

            self.song_groups_layout.addWidget(frame)
            self._apply_song_preset_same_for_group(controls)
        self.song_groups_layout.addStretch(1)

    def _build_song_row_widget(self, row: dict, slider: QSlider) -> dict:
        payload = dict(row)
        candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
        if not candidates:
            candidate_track_id = str(payload.get("candidate_track_id", "") or "")
            candidate_file = str(payload.get("candidate_file_name", "") or "")
            candidate_path = str(payload.get("candidate_path", "") or "")
            candidate_track = str(payload.get("candidate_track", "") or "")
            if any([candidate_track_id, candidate_file, candidate_path, candidate_track]):
                candidates = [
                    {
                        "candidate_track_id": candidate_track_id,
                        "candidate_file_name": candidate_file,
                        "candidate_path": candidate_path,
                        "candidate_track": candidate_track,
                        "candidate_duration_sec": _safe_float(payload.get("candidate_duration_sec", 0), 0),
                        "score": _safe_float(payload.get("score", 0), 0.0),
                    }
                ]
        payload["candidates"] = candidates
        container = QFrame()
        container.setFrameShape(QFrame.Shape.NoFrame)
        container.setStyleSheet("QFrame{background:#ffffff;border:1px solid #d8e2ef;border-radius:6px;}")
        outer = QVBoxLayout(container)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(3)

        top = _ClickableFrame()
        top.setFrameShape(QFrame.Shape.NoFrame)
        top.setStyleSheet("QFrame{background:transparent;border:none;}")
        row_layout = QHBoxLayout(top)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        checkbox = QCheckBox()
        checkbox.setChecked(True)
        checkbox.setStyleSheet("QCheckBox::indicator{width:28px;height:28px;}")

        btn_play = QPushButton("▶")
        btn_play.setFixedWidth(34)
        btn_play.setCursor(Qt.CursorShape.PointingHandCursor)

        lbl_file_name = _ClickableLabel(str(payload.get("source_file", "") or ""))
        lbl_file_name.setMinimumWidth(260)
        lbl_file_name.setToolTip(str(payload.get("source_path", "") or ""))

        lbl_source_kind = QLabel("待导入")
        lbl_source_kind.setFixedWidth(84)
        lbl_source_kind.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_source_kind.setStyleSheet("color:#2f7dff;")

        lbl_score = _ClickableLabel(f"{_safe_float(payload.get('score', 0.0), 0.0):.4f}")
        lbl_score.setFixedWidth(96)
        lbl_score.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        lbl_reason = _ClickableLabel(str(payload.get("reason", "")).replace("原因", ""))
        lbl_reason.setToolTip(str(payload.get("review_id", "") or ""))

        row_layout.addWidget(checkbox)
        row_layout.addWidget(btn_play)
        row_layout.addWidget(lbl_file_name, 3)
        row_layout.addWidget(lbl_source_kind)
        row_layout.addWidget(lbl_score)
        row_layout.addWidget(lbl_reason, 2)
        outer.addWidget(top)

        row_ctrl: dict[str, object] = {
            "row": payload,
            "container": container,
            "checkbox": checkbox,
            "source_checkbox": checkbox,
            "score": _safe_float(payload.get("score", 0), 0.0),
            "candidate_controls": [],
        }

        btn_play.clicked.connect(
            lambda _=False, r=payload, s=slider: self._play_with_external_player(
                str(r.get("source_path", "")).strip(),
                s.value(),
            )
        )
        for candidate in candidates:
            candidate_row = _ClickableFrame()
            candidate_row.setFrameShape(QFrame.Shape.NoFrame)
            candidate_row.setStyleSheet("QFrame{background:transparent;border:none;}")
            candidate_layout = QHBoxLayout(candidate_row)
            candidate_layout.setContentsMargins(0, 0, 0, 0)
            candidate_layout.setSpacing(8)

            candidate_checkbox = QCheckBox()
            candidate_checkbox.setChecked(False)
            candidate_checkbox.setStyleSheet("QCheckBox::indicator{width:28px;height:28px;}")
            btn_play_candidate = QPushButton("▶")
            btn_play_candidate.setFixedWidth(34)
            btn_play_candidate.setCursor(Qt.CursorShape.PointingHandCursor)

            candidate_file = str(candidate.get("candidate_file_name", "") or "").strip()
            candidate_track = str(candidate.get("candidate_track", "") or "").strip()
            candidate_text = candidate_file or candidate_track or "（无候选）"
            lbl_candidate_name = _ClickableLabel(candidate_text)
            lbl_candidate_name.setMinimumWidth(260)
            candidate_tip = str(candidate.get("candidate_path", "") or "")
            if candidate_track:
                candidate_tip = f"{candidate_tip}\n{candidate_track}" if candidate_tip else candidate_track
            lbl_candidate_name.setToolTip(candidate_tip)

            lbl_candidate_source = QLabel("库")
            lbl_candidate_source.setFixedWidth(84)
            lbl_candidate_source.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_candidate_source.setStyleSheet("color:#4f5f72;")
            lbl_candidate_score = QLabel("1.0000")
            lbl_candidate_score.setFixedWidth(96)
            lbl_candidate_score.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lbl_candidate_reason = _ClickableLabel(str(payload.get("reason", "")).replace("原因", ""))
            lbl_candidate_reason.setStyleSheet("color:#5d6f86;")

            candidate_layout.addWidget(candidate_checkbox)
            candidate_layout.addWidget(btn_play_candidate)
            candidate_layout.addWidget(lbl_candidate_name, 3)
            candidate_layout.addWidget(lbl_candidate_source)
            candidate_layout.addWidget(lbl_candidate_score)
            candidate_layout.addWidget(lbl_candidate_reason, 2)
            outer.addWidget(candidate_row)
            row_ctrl["candidate_controls"].append(
                {
                    "checkbox": candidate_checkbox,
                    "track_id": str(candidate.get("candidate_track_id", "") or ""),
                }
            )

            btn_play_candidate.clicked.connect(
                lambda _=False, c=dict(candidate), s=slider: self._play_with_external_player(
                    str(c.get("candidate_path", "")).strip(),
                    s.value(),
                )
            )
        return row_ctrl

    def _toggle_song_meta_panel(self, row_ctrl: dict) -> None:
        panel = row_ctrl.get("meta_panel")
        if not isinstance(panel, QWidget):
            return
        panel.setVisible(not panel.isVisible())

    def _refresh_song_row_candidate_label(self, row_ctrl: dict) -> None:
        row = row_ctrl.get("row")
        if not isinstance(row, dict):
            return
        track_id = str(row_ctrl.get("candidate_track_id", "") or row.get("candidate_track_id", "") or "")
        meta = row.get("candidate_meta") if isinstance(row.get("candidate_meta"), dict) else {}
        title = str(meta.get("title", "") or "").strip()
        artist = str(meta.get("artist", "") or "").strip()
        if track_id:
            detail = f"{artist or 'Unknown Artist'} - {title or 'Unknown Title'} ({track_id})"
        else:
            detail = str(row.get("candidate_track", "") or "")
        row["candidate_track"] = detail
        name_widget = row_ctrl.get("candidate_name_label")
        detail_widget = row_ctrl.get("candidate_detail_label")
        if isinstance(name_widget, QLabel):
            current_file = str(row.get("candidate_file_name", "") or "").strip()
            name_widget.setText(current_file or detail)
        if isinstance(detail_widget, QLabel):
            detail_widget.setText(detail)

    def _commit_song_meta_edit(self, row_ctrl: dict, field_key: str, value) -> None:
        track_id = str(row_ctrl.get("candidate_track_id", "") or "")
        if not track_id:
            return
        cache = row_ctrl.get("meta_cache")
        if not isinstance(cache, dict):
            cache = {}
            row_ctrl["meta_cache"] = cache
        old_value = cache.get(field_key)
        if field_key == "preference_level":
            new_value = max(1, min(10, _safe_int(value, 5)))
            old_comp = max(1, min(10, _safe_int(old_value, 5)))
            if new_value == old_comp:
                return
            payload_value = int(new_value)
        else:
            new_value = str(value or "").strip()
            old_comp = str(old_value or "").strip()
            if new_value == old_comp:
                return
            payload_value = new_value
        try:
            self.facade.update_tracks_fields([track_id], {field_key: payload_value})
        except Exception as exc:
            QMessageBox.warning(self, "编辑候选元数据", f"保存失败: {exc}")
            editor_map = row_ctrl.get("meta_widgets")
            editor = editor_map.get(field_key) if isinstance(editor_map, dict) else None
            if isinstance(editor, QLineEdit):
                editor.setText(str(old_value or ""))
            elif isinstance(editor, QSpinBox):
                editor.setValue(max(1, min(10, _safe_int(old_value, 5))))
            return

        cache[field_key] = payload_value
        row = row_ctrl.get("row")
        if isinstance(row, dict):
            meta = row.get("candidate_meta")
            if not isinstance(meta, dict):
                meta = {}
                row["candidate_meta"] = meta
            meta[field_key] = payload_value
        if field_key in {"title", "artist"}:
            self._refresh_song_row_candidate_label(row_ctrl)
        self.review_changed.emit()

    def _fill_lyrics_tree(self, rows: list[dict]) -> None:
        self._lyrics_group_controls.clear()
        self._clear_group_layout(self.lyrics_groups_layout)
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            groups[str(row.get("group_title", "") or row.get("group_key", "") or "未分组")].append(row)

        if not groups:
            empty = QLabel("暂无歌词待审查")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lyrics_groups_layout.addWidget(empty)
            self.lyrics_groups_layout.addStretch(1)
            return

        for group_key in sorted(groups.keys(), key=lambda s: s.casefold()):
            group_rows = list(groups[group_key])
            frame = QFrame()
            frame.setFrameShape(QFrame.Shape.StyledPanel)
            frame.setStyleSheet("QFrame{background:#f8fbff;border:1px solid #d7e4f4;border-radius:8px;}")
            host = QVBoxLayout(frame)
            host.setContentsMargins(10, 10, 10, 10)
            host.setSpacing(8)

            title = QLabel(group_key)
            tfont = title.font()
            tfont.setBold(True)
            tfont.setPointSize(max(tfont.pointSize() + 4, 14))
            title.setFont(tfont)
            host.addWidget(title)

            header = QWidget()
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(6, 0, 6, 0)
            header_layout.setSpacing(8)
            for text, stretch, fixed in [
                ("保留", 0, 44),
                ("歌词文件", 3, 0),
                ("相似度", 0, 88),
                ("说明", 2, 0),
            ]:
                lbl = QLabel(text)
                font = lbl.font()
                font.setBold(True)
                lbl.setFont(font)
                if fixed > 0:
                    lbl.setFixedWidth(fixed)
                header_layout.addWidget(lbl, stretch)
            host.addWidget(header)

            row_controls: list[dict] = []
            for row in group_rows:
                row_ctrl = self._build_lyrics_row_widget(row)
                row_controls.append(row_ctrl)
                host.addWidget(row_ctrl["container"])

            row_ops_host = QWidget()
            row_ops = QHBoxLayout(row_ops_host)
            row_ops.setContentsMargins(0, 2, 0, 0)
            row_ops.setSpacing(8)
            btn_invert = QPushButton("反选")
            btn_same = QPushButton("这是相同歌词")
            btn_diff = QPushButton("这是不同歌词")
            btn_save = QPushButton("保存勾选的文件")
            btn_cancel = QPushButton("取消导入")
            row_ops.addWidget(btn_invert)
            row_ops.addWidget(btn_same)
            row_ops.addWidget(btn_diff)
            row_ops.addWidget(btn_save)
            row_ops.addWidget(btn_cancel)
            row_ops.addStretch(1)
            host.addWidget(row_ops_host)

            controls = {"group_key": group_key, "rows": row_controls}
            self._lyrics_group_controls[group_key] = controls
            btn_invert.clicked.connect(lambda _=False, g=controls: self._invert_lyrics_group(g))
            btn_same.clicked.connect(lambda _=False, g=controls: self._apply_lyrics_preset_same_for_group(g))
            btn_diff.clicked.connect(lambda _=False, g=controls: self._apply_lyrics_preset_diff_for_group(g))
            btn_save.clicked.connect(lambda _=False, g=controls: self._save_lyrics_group(g))
            btn_cancel.clicked.connect(lambda _=False, g=controls: self._cancel_lyrics_group(g))
            self._register_dynamic_button(btn_invert)
            self._register_dynamic_button(btn_same)
            self._register_dynamic_button(btn_diff)
            self._register_dynamic_button(btn_save)
            self._register_dynamic_button(btn_cancel)

            self._apply_default_lyrics_checks(group_rows, row_controls)
            self.lyrics_groups_layout.addWidget(frame)
        self.lyrics_groups_layout.addStretch(1)

    def _build_lyrics_row_widget(self, row: dict) -> dict:
        payload = dict(row)
        container = QFrame()
        container.setFrameShape(QFrame.Shape.NoFrame)
        container.setStyleSheet("QFrame{background:#ffffff;border:1px solid #d8e2ef;border-radius:6px;}")
        outer = QVBoxLayout(container)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(2)

        top = _ClickableFrame()
        top.setFrameShape(QFrame.Shape.NoFrame)
        top.setStyleSheet("QFrame{background:transparent;border:none;}")
        row_layout = QHBoxLayout(top)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        checkbox = QCheckBox()
        checkbox.setChecked(False)
        checkbox.setStyleSheet("QCheckBox::indicator{width:28px;height:28px;}")
        lbl_file = _ClickableLabel(str(payload.get("lyrics_file", "") or ""))
        lbl_file.setMinimumWidth(260)
        lbl_file.setToolTip(str(payload.get("lyrics_source", "") or ""))
        lbl_file.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lbl_score = _ClickableLabel(f"{_safe_float(payload.get('score', 0.0), 0.0):.4f}")
        lbl_score.setFixedWidth(88)
        lbl_score.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl_score.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lbl_reason = _ClickableLabel(str(payload.get("reason", "")).replace("原因", ""))
        lbl_reason.setToolTip(str(payload.get("review_id", "") or ""))
        lbl_reason.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        row_layout.addWidget(checkbox)
        row_layout.addWidget(lbl_file, 3)
        row_layout.addWidget(lbl_score)
        row_layout.addWidget(lbl_reason, 2)
        outer.addWidget(top)

        link_bind_row = _ClickableFrame()
        link_bind_row.setFrameShape(QFrame.Shape.NoFrame)
        link_bind_row.setStyleSheet("QFrame{background:transparent;border:none;}")
        bind_layout = QHBoxLayout(link_bind_row)
        bind_layout.setContentsMargins(34, 0, 0, 0)
        bind_layout.setSpacing(6)
        bind_icon = QLabel("🔗")
        bind_text = QLabel("点击绑定数据库歌曲")
        bind_text.setStyleSheet("color:#5d6f86;")
        bind_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        bind_text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        bind_layout.addWidget(bind_icon)
        bind_layout.addWidget(bind_text, 1)
        bind_layout.addStretch(1)
        outer.addWidget(link_bind_row)

        suggest_text = str(payload.get("suggest_track", "") or "").strip()
        link_suggest_row: _ClickableFrame | None = None
        if suggest_text:
            link_suggest_row = _ClickableFrame()
            link_suggest_row.setFrameShape(QFrame.Shape.NoFrame)
            link_suggest_row.setStyleSheet("QFrame{background:transparent;border:none;}")
            suggest_layout = QHBoxLayout(link_suggest_row)
            suggest_layout.setContentsMargins(34, 0, 0, 0)
            suggest_layout.setSpacing(6)
            suggest_icon = QLabel("🔗")
            suggest_label = QLabel(f"建议：{suggest_text}")
            suggest_label.setStyleSheet("color:#425b7a;")
            suggest_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            suggest_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            suggest_layout.addWidget(suggest_icon)
            suggest_layout.addWidget(suggest_label, 1)
            suggest_layout.addStretch(1)
            outer.addWidget(link_suggest_row)

        row_ctrl: dict[str, object] = {
            "row": payload,
            "container": container,
            "checkbox": checkbox,
        }

        def _preview() -> None:
            self._on_lyrics_row_clicked(payload)

        def _edit_mapping() -> None:
            self._map_lyrics_row(payload)

        checkbox.clicked.connect(lambda _checked=False: _preview())
        top.clicked.connect(_preview)
        link_bind_row.clicked.connect(_edit_mapping)
        if link_suggest_row is not None:
            link_suggest_row.clicked.connect(_edit_mapping)
        return row_ctrl

    def _on_lyrics_row_clicked(self, row: dict) -> None:
        payload = dict(row or {})
        if not payload:
            return
        row_key = str(payload.get("lyrics_id", "") or payload.get("lyrics_source", "") or "")
        if self._preview_rows:
            prev = self._preview_rows[-1]
            prev_key = str(prev.get("lyrics_id", "") or prev.get("lyrics_source", "") or "")
            if row_key and row_key == prev_key:
                return
        self._preview_rows.append(payload)
        rows = list(self._preview_rows)
        if len(rows) == 1:
            rows = [rows[0], rows[0]]
        self.preview_left.setPlainText(self._read_lyrics_text(rows[-2]))
        self.preview_right.setPlainText(self._read_lyrics_text(rows[-1]))

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

    def _apply_default_lyrics_checks(self, rows: list[dict], row_controls: list[dict]) -> None:
        if not rows or not row_controls:
            return
        for row_ctrl in row_controls:
            checkbox = row_ctrl.get("checkbox")
            if isinstance(checkbox, QCheckBox):
                checkbox.setChecked(False)

        pairs = list(zip(rows, row_controls))
        if self._lyrics_group_same_file(rows):
            target = max(
                pairs,
                key=lambda p: (
                    self._lyrics_line_count(p[0]),
                    -_lyrics_file_bracket_count(str(p[0].get("lyrics_file", "") or "")),
                    -len(str(p[0].get("lyrics_file", "") or "")),
                    str(p[0].get("lyrics_source", "") or ""),
                ),
            )
        else:
            target = max(
                pairs,
                key=lambda p: (
                    self._lyrics_source_mtime(p[0]),
                    self._lyrics_imported_at(p[0]),
                    self._lyrics_line_count(p[0]),
                    str(p[0].get("lyrics_source", "") or ""),
                ),
            )
        target_ctrl = target[1]
        checkbox = target_ctrl.get("checkbox")
        if isinstance(checkbox, QCheckBox):
            checkbox.setChecked(True)

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

    def _invert_check_state_tree(self, tree: QTreeWidget) -> None:
        stack: list[QTreeWidgetItem] = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            for i in range(item.childCount()):
                stack.append(item.child(i))
            row = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if not row or row.get("_meta_row") or row.get("_link_row"):
                continue
            item.setCheckState(
                0,
                Qt.CheckState.Unchecked if item.checkState(0) == Qt.CheckState.Checked else Qt.CheckState.Checked,
            )

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

    def _song_controls_for_tree(self, tree: QTreeWidget) -> dict | None:
        for controls in self._song_group_controls.values():
            if controls.get("tree") is tree:
                return controls
        return None

    def _on_song_item_clicked(self, item: QTreeWidgetItem, col: int, tree: QTreeWidget | None = None) -> None:
        row = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if not row or row.get("_footer"):
            return
        if row.get("_meta_row"):
            self._edit_song_meta_row(item, row)
            return
        if col == 1:
            start = 0
            if tree is None:
                tree = item.treeWidget()
            if isinstance(tree, QTreeWidget):
                controls = self._song_controls_for_tree(tree) or {}
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

    def _on_lyrics_item_clicked(self, item: QTreeWidgetItem, _col: int, _tree: QTreeWidget | None = None) -> None:
        row = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if not row or row.get("_footer") or row.get("_meta_row"):
            return
        if row.get("_link_row"):
            self._map_lyrics_row(row)
            return
        row_key = str(row.get("lyrics_id", "") or row.get("lyrics_source", "") or "")
        if self._preview_rows:
            prev = self._preview_rows[-1]
            prev_key = str(prev.get("lyrics_id", "") or prev.get("lyrics_source", "") or "")
            if row_key and row_key == prev_key:
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

    def _review_ids_for_group(self, group: dict) -> list[str]:
        ids: set[str] = set()
        rows = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in rows if isinstance(rows, list) else []:
            row = row_ctrl.get("row") if isinstance(row_ctrl, dict) else {}
            if not isinstance(row, dict):
                continue
            rid = str(row.get("review_id", "") or "")
            if rid:
                ids.add(rid)
        return sorted(ids)

    def _apply_song_preset_same_for_group(self, group: dict) -> None:
        best_row: dict | None = None
        best_score = float("-inf")
        all_rows: list[dict] = []
        rows = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in rows if isinstance(rows, list) else []:
            row = row_ctrl.get("row") if isinstance(row_ctrl, dict) else {}
            if not isinstance(row, dict):
                continue
            score = _safe_float(row.get("score", 0), 0.0)
            all_rows.append(row_ctrl)
            if score > best_score:
                best_score = score
                best_row = row_ctrl
        keep = {id(best_row)} if best_row is not None else set()
        for row_ctrl in all_rows:
            checkbox = row_ctrl.get("checkbox") if isinstance(row_ctrl, dict) else None
            if isinstance(checkbox, QCheckBox):
                checkbox.setChecked(id(row_ctrl) in keep)
            candidate_controls = row_ctrl.get("candidate_controls") if isinstance(row_ctrl, dict) else []
            for candidate in candidate_controls if isinstance(candidate_controls, list) else []:
                candidate_checkbox = candidate.get("checkbox") if isinstance(candidate, dict) else None
                if isinstance(candidate_checkbox, QCheckBox):
                    candidate_checkbox.setChecked(False)

    def _invert_song_group(self, group: dict) -> None:
        rows = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in rows if isinstance(rows, list) else []:
            checkbox = row_ctrl.get("checkbox") if isinstance(row_ctrl, dict) else None
            if isinstance(checkbox, QCheckBox):
                checkbox.setChecked(not checkbox.isChecked())
            candidate_controls = row_ctrl.get("candidate_controls") if isinstance(row_ctrl, dict) else []
            for candidate in candidate_controls if isinstance(candidate_controls, list) else []:
                candidate_checkbox = candidate.get("checkbox") if isinstance(candidate, dict) else None
                if isinstance(candidate_checkbox, QCheckBox):
                    candidate_checkbox.setChecked(not candidate_checkbox.isChecked())

    def _apply_song_preset_diff_for_group(self, group: dict) -> None:
        rows = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in rows if isinstance(rows, list) else []:
            checkbox = row_ctrl.get("checkbox") if isinstance(row_ctrl, dict) else None
            if isinstance(checkbox, QCheckBox):
                checkbox.setChecked(True)
            candidate_controls = row_ctrl.get("candidate_controls") if isinstance(row_ctrl, dict) else []
            for candidate in candidate_controls if isinstance(candidate_controls, list) else []:
                candidate_checkbox = candidate.get("checkbox") if isinstance(candidate, dict) else None
                if isinstance(candidate_checkbox, QCheckBox):
                    candidate_checkbox.setChecked(True)

    def _save_song_group(self, group: dict) -> None:
        status_by_review: dict[str, bool] = {}
        restore_track_ids: set[str] = set()
        deferred_rows: dict[str, dict] = {}
        rows = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in rows if isinstance(rows, list) else []:
            row = row_ctrl.get("row") if isinstance(row_ctrl, dict) else {}
            if not isinstance(row, dict):
                continue
            rid = str(row.get("review_id", "") or "")
            if not rid:
                continue
            checkbox = row_ctrl.get("checkbox") if isinstance(row_ctrl, dict) else None
            keep_source = bool(checkbox.isChecked()) if isinstance(checkbox, QCheckBox) else False
            keep_library = False
            selected_library_track_ids: list[str] = []
            candidate_controls = row_ctrl.get("candidate_controls") if isinstance(row_ctrl, dict) else []
            for candidate in candidate_controls if isinstance(candidate_controls, list) else []:
                if not isinstance(candidate, dict):
                    continue
                candidate_checkbox = candidate.get("checkbox")
                if not isinstance(candidate_checkbox, QCheckBox):
                    continue
                if not candidate_checkbox.isChecked():
                    continue
                tid = str(candidate.get("track_id", "") or "")
                if tid:
                    keep_library = True
                    selected_library_track_ids.append(tid)

            status_by_review[rid] = bool(status_by_review.get(rid, False) or keep_source)
            if keep_source:
                restore_id = str(row.get("restore_track_id", "") or "")
                if restore_id:
                    restore_track_ids.add(restore_id)
                if bool(row.get("deferred_import", False)):
                    payload = dict(row)
                    payload["_keep_library"] = keep_library
                    payload["_selected_library_track_ids"] = selected_library_track_ids
                    deferred_rows[rid] = payload
        if not status_by_review:
            return
        if restore_track_ids:
            self.facade.restore_tracks(sorted(restore_track_ids))

        failed_imports: list[tuple[str, str]] = []
        for rid, row in deferred_rows.items():
            source_path = str(row.get("source_path", "") or "").strip()
            existing_track_id = str(row.get("candidate_track_id", "") or "").strip() or None
            keep_library = bool(row.get("_keep_library", False))
            if not source_path:
                failed_imports.append((rid, "缺少源路径"))
                continue
            try:
                result = self.facade.import_track_from_review(
                    source_path,
                    existing_track_id=existing_track_id,
                    replace_existing=not keep_library,
                )
            except Exception as exc:
                failed_imports.append((rid, str(exc)))
                continue
            if str(result.get("status", "")) != "imported":
                failed_imports.append((rid, str(result)))
                continue
        failed_ids = {rid for rid, _ in failed_imports}
        resolved_ids = [rid for rid, keep in status_by_review.items() if keep]
        ignored_ids = [rid for rid, keep in status_by_review.items() if not keep]
        resolved_ids = [rid for rid in resolved_ids if rid not in failed_ids]
        if resolved_ids:
            self.facade.resolve_reviews(resolved_ids, status="resolved")
        if ignored_ids:
            self.facade.resolve_reviews(ignored_ids, status="ignored")
        if failed_imports:
            preview = "\n".join(f"{rid}: {reason}" for rid, reason in failed_imports[:8])
            QMessageBox.warning(
                self,
                "审查导入",
                f"有 {len(failed_imports)} 项导入失败，已保留为待审查。\n{preview}",
            )
        self.reload_reviews()
        self.review_changed.emit()

    def _cancel_song_group(self, group: dict) -> None:
        ids = self._review_ids_for_group(group)
        if not ids:
            return
        self.facade.resolve_reviews(ids, status="ignored")
        self.reload_reviews()
        self.review_changed.emit()

    def _apply_lyrics_preset_same_for_group(self, group: dict) -> None:
        rows: list[dict] = []
        row_controls: list[dict] = []
        entries = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in entries if isinstance(entries, list) else []:
            row = row_ctrl.get("row") if isinstance(row_ctrl, dict) else {}
            if not isinstance(row, dict):
                continue
            rows.append(dict(row))
            row_controls.append(row_ctrl)
        self._apply_default_lyrics_checks(rows, row_controls)

    def _apply_lyrics_preset_diff_for_group(self, group: dict) -> None:
        entries = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in entries if isinstance(entries, list) else []:
            checkbox = row_ctrl.get("checkbox") if isinstance(row_ctrl, dict) else None
            if isinstance(checkbox, QCheckBox):
                checkbox.setChecked(True)

    def _invert_lyrics_group(self, group: dict) -> None:
        entries = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in entries if isinstance(entries, list) else []:
            checkbox = row_ctrl.get("checkbox") if isinstance(row_ctrl, dict) else None
            if isinstance(checkbox, QCheckBox):
                checkbox.setChecked(not checkbox.isChecked())

    def _save_lyrics_group(self, group: dict) -> None:
        status_by_review: dict[str, bool] = {}
        restore_lyrics_ids: set[str] = set()
        entries = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in entries if isinstance(entries, list) else []:
            row = row_ctrl.get("row") if isinstance(row_ctrl, dict) else {}
            if not isinstance(row, dict):
                continue
            rid = str(row.get("review_id", "") or "")
            if not rid:
                continue
            checkbox = row_ctrl.get("checkbox") if isinstance(row_ctrl, dict) else None
            checked = bool(checkbox.isChecked()) if isinstance(checkbox, QCheckBox) else False
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

    def _cancel_lyrics_group(self, group: dict) -> None:
        ids = self._review_ids_for_group(group)
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
