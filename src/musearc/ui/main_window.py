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
from musearc.ui.player_link_page import PlayerLinkPage
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
                "播放器联动",
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
        self.page_player_link = PlayerLinkPage(self.facade)
        self.page_trash = TrashPage(self.facade)
        self.page_settings = SettingsPage(self.facade)

        self.stack.addWidget(self.page_tracks)
        self.stack.addWidget(self.page_imports)
        self.stack.addWidget(self.page_review)
        self.stack.addWidget(self.page_fullscan)
        self.stack.addWidget(self.page_playlist)
        self.stack.addWidget(self.page_tags)
        self.stack.addWidget(self.page_lyrics)
        self.stack.addWidget(self.page_player_link)
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
        self.page_player_link.library_changed.connect(self._reload_related_pages)
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
        """解析曲目播放路径。

        功能：根据提供的行字典，解析并返回曲目的播放路径。优先使用库内归档路径，如果不存在则使用源路径。

        参数：
            row (dict): 包含曲目信息的字典，应有键'storage_relpath'和'source_fullpath'。

        返回值：
            str: 解析后的路径字符串，如果路径不存在则返回空字符串。
        """
        # 设计约束：优先使用库内归档路径，保证可重复播放与路径稳定性。
        rel = str(row.get("storage_relpath", "") or "").strip()  # 获取存储的相对路径，并清理空格
        if rel:  # 如果相对路径存在
            path = (self.facade.library_root / rel).resolve()  # 构建完整路径并解析符号链接
            if path.exists():  # 检查路径是否存在
                return str(path)  # 返回路径字符串
        source = str(row.get("source_fullpath", "") or "").strip()  # 获取源文件的完整路径，并清理空格
        if source:  # 如果源路径存在
            path = Path(source)  # 创建路径对象
            if path.exists():  # 检查路径是否存在
                return str(path.resolve())  # 解析并返回路径字符串
        return ""  # 如果都没有找到，返回空字符串

    def queue_and_play_tracks(self, rows: list[dict], *, start_track_id: str | None = None) -> bool:
        """功能：将给定的行列表排队并播放轨道，可选择从特定轨道开始播放。

        参数：
            rows (list[dict]): 字典列表，每个字典包含轨道的元数据，如文件名、标题等。
            start_track_id (str | None, optional): 指定开始播放的轨道ID。如果为None，则从第一个轨道开始。

        返回值：
            bool: 如果成功开始播放，则返回True；否则返回False。
        """
        if not rows:  # 检查输入行列表是否为空
            return False
        paths: list[str] = []  # 存储可播放文件的路径列表
        labels: list[str] = []  # 存储每个轨道的显示标签
        start_index = 0  # 初始化起始播放索引
        for idx, row in enumerate(rows):  # 遍历每一行
            path = self._resolve_track_play_path(row if isinstance(row, dict) else {})  # 解析轨道的播放路径
            if not path:  # 如果路径无效，跳过此行
                continue
            paths.append(path)  # 添加有效路径
            # 获取标签：优先使用文件名，其次标题，最后使用路径的文件名部分
            labels.append(str(row.get("file_name", "") or row.get("title", "") or Path(path).name))
            tid = str(row.get("track_id", "") or "")  # 获取轨道ID
            # 检查是否匹配指定的起始轨道ID
            if start_track_id and tid and tid == start_track_id:
                start_index = len(paths) - 1  # 更新起始索引为当前路径的索引
        if not paths:  # 如果没有可播放路径
            QMessageBox.information(self, "播放", "未找到可播放文件。")  # 显示提示信息
            return False  # 返回False表示失败
        return self.player_bar.play_queue(paths, start_index=start_index, labels=labels)  # 调用播放器排队并播放，返回结果

    def queue_and_play_paths(self, paths: list[str], *, start_path: str | None = None, start_sec: int = 0) -> bool:
        """处理路径列表并播放队列。

        参数：
            paths (list[str]): 要播放的路径列表。
            start_path (str | None, 可选): 指定开始播放的路径。默认为None，从第一个路径开始。
            start_sec (int, 可选): 指定开始播放的秒数。默认为0。

        返回值：
            bool: 如果成功开始播放，返回True；否则返回False。
        """
        cleaned: list[str] = []  # 初始化一个空列表，用于存储规范化的路径
        start_index = 0  # 初始化起始索引为0
        normalized_start = str(start_path or "").strip()  # 规范化起始路径，去除空白
        if normalized_start:  # 如果起始路径非空
            try:
                normalized_start = str(Path(normalized_start).resolve())  # 尝试将起始路径转换为绝对路径
            except Exception:
                normalized_start = str(start_path or "").strip()  # 如果解析失败，保持原样
        for raw in paths:  # 遍历原始路径列表
            text = str(raw or "").strip()  # 规范化每个路径，去除空白
            if not text:  # 如果路径为空，跳过
                continue
            try:
                normalized_text = str(Path(text).resolve())  # 尝试将路径转换为绝对路径
            except Exception:
                normalized_text = text  # 如果解析失败，使用原始文本
            cleaned.append(normalized_text)  # 将规范化后的路径添加到cleaned列表
            if normalized_start and normalized_text == normalized_start:  # 如果当前路径匹配起始路径
                start_index = len(cleaned) - 1  # 更新起始索引为当前索引
        if not cleaned:  # 如果cleaned列表为空
            QMessageBox.information(self, "播放", "未找到可播放文件。")  # 显示错误消息
            return False  # 返回False表示失败
        return self.player_bar.play_queue(cleaned, start_index=start_index, start_sec=start_sec)  # 调用player_bar播放队列

    def release_player_for_file_ops(self) -> None:
        if getattr(self, "player_bar", None) is None:
            return
        try:
            self.player_bar.release_for_file_ops()
        except Exception:
            try:
                self.player_bar.stop_and_hide()
            except Exception:
                pass
