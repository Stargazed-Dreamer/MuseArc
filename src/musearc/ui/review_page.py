from __future__ import annotations

"""???????

????????????????????????
??????????????????
"""

from collections import deque
from pathlib import Path
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
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
from musearc.ui.review_page_mixins_song import ReviewPageSongMixin
from musearc.ui.review_page_mixins_lyrics import ReviewPageLyricsMixin


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


class ReviewPage(ReviewPageSongMixin, ReviewPageLyricsMixin, QWidget):
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
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
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
