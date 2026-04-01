from __future__ import annotations

"""主窗口装配层。

仅负责：
1) 顶级布局与页面挂载；
2) 底部播放器栏挂载；
3) 对页面暴露统一播放队列入口。
"""

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QMessageBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from musearc.app.facade import MuseArcFacade
from musearc.ui.import_management_page import ImportManagementPage
from musearc.ui.main_window_components import (
    FullScanPage,
    LyricsManagementPage,
    PlaylistPage,
    TagManagementPage,
    TracksPage,
    TrashPage,
)
from musearc.ui.main_window_logic import MainWindowLogicMixin
from musearc.ui.player_bar import InlinePlayerBar
from musearc.ui.review_page import ReviewPage
from musearc.ui.settings_page import SettingsPage


class MainWindow(MainWindowLogicMixin, QMainWindow):
    def __init__(self, library_path: str | None = None):
        super().__init__()
        self.facade = MuseArcFacade(library_path)
        self.setWindowTitle("MuseArc")
        self.resize(1720, 980)
        self.setStyleSheet("QCheckBox::indicator{width:26px;height:26px;}")

        root = QWidget(self)
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        content_layout = QHBoxLayout()
        # 为主内容区保留统一留白，避免左侧贴边。
        content_layout.setContentsMargins(10, 8, 10, 0)

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

        content_layout.addWidget(left)
        content_layout.addWidget(self.stack, 1)
        root_layout.addLayout(content_layout, 1)

        self.player_bar = InlinePlayerBar(self)
        root_layout.addWidget(self.player_bar)

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
        if hasattr(self.page_review, "review_changed"):
            self.page_review.review_changed.connect(self._reload_related_pages)
        self.page_settings.settings_saved.connect(self._on_settings_saved)

        self._build_menu()
        self._save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self._save_shortcut.activated.connect(self._save_now)
        self._delete_shortcut = QShortcut(QKeySequence("Delete"), self)
        self._delete_shortcut.activated.connect(self._delete_selected_current_page)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._save_now)
        self._apply_button_scale_from_config()
        self._configure_autosave_timer()
        self._refresh_action_history()
        self._tool_windows: list[QWidget] = []

    def _resolve_track_play_path(self, row: dict) -> str:
        # 设计约束：优先使用库内归档路径，保证可重复播放与路径稳定性。
        rel = str(row.get("storage_relpath", "") or "").strip()
        if rel:
            path = (self.facade.library_root / rel).resolve()
            if path.exists():
                return str(path)
        source = str(row.get("source_fullpath", "") or "").strip()
        if source:
            path = Path(source)
            if path.exists():
                return str(path.resolve())
        return ""

    def queue_and_play_tracks(self, rows: list[dict], *, start_track_id: str | None = None) -> bool:
        if not rows:
            return False
        paths: list[str] = []
        labels: list[str] = []
        start_index = 0
        for idx, row in enumerate(rows):
            path = self._resolve_track_play_path(row if isinstance(row, dict) else {})
            if not path:
                continue
            paths.append(path)
            labels.append(str(row.get("file_name", "") or row.get("title", "") or Path(path).name))
            tid = str(row.get("track_id", "") or "")
            if start_track_id and tid and tid == start_track_id:
                start_index = len(paths) - 1
        if not paths:
            QMessageBox.information(self, "播放", "未找到可播放文件。")
            return False
        return self.player_bar.play_queue(paths, start_index=start_index, labels=labels)

    def queue_and_play_paths(self, paths: list[str], *, start_path: str | None = None, start_sec: int = 0) -> bool:
        cleaned: list[str] = []
        start_index = 0
        normalized_start = str(start_path or "").strip()
        if normalized_start:
            try:
                normalized_start = str(Path(normalized_start).resolve())
            except Exception:
                normalized_start = str(start_path or "").strip()
        for raw in paths:
            text = str(raw or "").strip()
            if not text:
                continue
            try:
                normalized_text = str(Path(text).resolve())
            except Exception:
                normalized_text = text
            cleaned.append(normalized_text)
            if normalized_start and normalized_text == normalized_start:
                start_index = len(cleaned) - 1
        if not cleaned:
            QMessageBox.information(self, "播放", "未找到可播放文件。")
            return False
        return self.player_bar.play_queue(cleaned, start_index=start_index, start_sec=start_sec)
