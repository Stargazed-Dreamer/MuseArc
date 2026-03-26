from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
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
    _apply_button_scale,
    _history_action_label,
)
from musearc.ui.review_page import ReviewPage
from musearc.ui.settings_page import SettingsPage


def _safe_int(value, default: int = 0) -> int:
    if isinstance(value, (list, tuple, set, dict)):
        return default
    try:
        return int(value or 0)
    except Exception:
        return default


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
        if hasattr(self.page_review, "review_changed"):
            self.page_review.review_changed.connect(self._reload_related_pages)
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
        minutes = max(1, _safe_int(self.facade.get_runtime_config().ui.db_autosave_minutes, 5))
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
        target = _safe_int(item.data(Qt.ItemDataRole.UserRole), -1)
        if target < 0:
            return
        timeline = self.facade.list_action_timeline(limit=500)
        current = _safe_int(timeline.get("current_index", -1), -1)
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
        current_index = _safe_int(timeline.get("current_index", -1), -1)

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
