"""播放器联动管理页面。"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from musearc.app.facade import FAVORITES_PLAYLIST_ID, MuseArcFacade, PlayerClient, PlayerClientError
from musearc.ui.table_models import ColumnDef, DictTableModel

logger = logging.getLogger(__name__)


# ── 异步工作线程 ────────────────────────────────────────────


class _ConnectWorker(QObject):
    finished = Signal(bool, str)  # success, message

    def __init__(self, client: PlayerClient):
        """
        初始化方法。

        功能：初始化实例，设置PlayerClient客户端。
        参数：
            self: 实例自身。
            client: PlayerClient类型的客户端对象。
        返回值：无。
        """
        super().__init__()  # 调用父类的初始化方法
        self.client = client  # 将client存储为实例属性

    def run(self) -> None:
        """
        尝试连接到服务器，并在成功或失败时通过finished信号通知结果。

        功能：执行连接服务器的操作，并根据连接结果发送相应的信号。
        参数：无（self 为实例引用）。
        返回值：无直接返回值。连接成功或失败的信息将通过 self.finished 信号发射。
        """
        logger.info("[PlayerLink] 连接中 host=%s port=%d", self.client.host, self.client.port)
        try:
            # 调用客户端的 connect 方法尝试建立连接，设置5秒超时
            self.client.connect(timeout=5.0)
            # 连接成功，记录信息日志
            logger.info("[PlayerLink] 连接成功")
            # 发射成功信号，携带状态True和成功消息
            self.finished.emit(True, "已连接")
        except PlayerClientError as exc:
            # 捕获特定于PlayerClient的错误（如认证失败、协议错误等）
            logger.warning("[PlayerLink] 连接失败: %s", exc)
            # 发射失败信号，携带状态False和异常信息
            self.finished.emit(False, str(exc))
        except Exception as exc:
            # 捕获其他所有未预料的通用异常
            logger.warning("[PlayerLink] 连接异常: %s", exc)
            # 发射失败信号，携带状态False和格式化的错误信息
            self.finished.emit(False, f"连接失败: {exc}")


class _FetchPlaylistWorker(QObject):
    finished = Signal(list, list, str, str)  # matched, external, playlist_name, playlist_id

    def __init__(self, client: PlayerClient, arc_rows: list[dict]):
        """初始化对象的实例。

        功能：
            初始化客户端和弧形行数据属性。

        参数：
            client (PlayerClient): 玩家客户端对象。
            arc_rows (list[dict]): 弧形行数据的字典列表。

        返回值：
            无（构造函数）。
        """
        super().__init__()  # 调用父类的构造函数进行初始化
        self.client = client  # 将传入的客户端参数赋值给实例属性
        self.arc_rows = arc_rows  # 将传入的弧形行数据参数赋值给实例属性

    def run(self) -> None:
        logger.info("[PlayerLink] 开始同步播放歌单")
        try:
            # 获取播放器状态，拿到当前歌单 ID
            state = self.client.state()
            player_info = state.get("player") or {}
            playlist_id = str(player_info.get("playlist_id", "") or "")
            playlist_name = str(player_info.get("playlist_name", "") or "")
            logger.info("[PlayerLink] state 返回: playlist_id=%s playlist_name=%s",
                        playlist_id, playlist_name)

            if not playlist_id:
                logger.info("[PlayerLink] 播放器无当前歌单")
                self.finished.emit([], [], "", "")
                return

            # 用 get_playlist 只读获取歌单（不影响当前播放，曲目含 source_sha256）
            playlist = None
            try:
                playlist = self.client.get_playlist(playlist_id)
                logger.info("[PlayerLink] get_playlist(%s) 成功", playlist_id)
            except PlayerClientError as exc:
                logger.warning("[PlayerLink] get_playlist 失败: %s, 回退到 current_playlist", exc)
                try:
                    playlist = self.client.current_playlist()
                    logger.info("[PlayerLink] current_playlist 回退成功")
                except PlayerClientError as exc2:
                    logger.warning("[PlayerLink] current_playlist 也失败: %s", exc2)

            if not playlist or not isinstance(playlist, dict):
                logger.info("[PlayerLink] 无法获取歌单详情")
                self.finished.emit([], [], playlist_name or playlist_id, playlist_id)
                return

            playlist_name = str(playlist.get("name", "") or playlist_name or "当前播放歌单")
            tracks = playlist.get("tracks") or []
            if not isinstance(tracks, list):
                logger.info("[PlayerLink] 歌单 tracks 不是列表")
                self.finished.emit([], [], playlist_name, playlist_id)
                return

            logger.info("[PlayerLink] 播放器返回 %d 首曲目, 曲库索引 %d 条",
                        len(tracks), len(self.arc_rows))

            # 构建 sha256 -> track_row 索引（使用主线程传入的数据，避免跨线程 DB 访问）
            by_sha: dict[str, dict] = {}
            for row in self.arc_rows:
                sha = str(row.get("source_sha256", "") or "").strip().lower()
                if sha:
                    by_sha[sha] = row

            matched: list[dict] = []
            external: list[dict] = []
            for trk in tracks:
                if not isinstance(trk, dict):
                    continue
                sha = str(trk.get("source_sha256", "") or "").strip().lower()
                arc_row = by_sha.get(sha) if sha else None
                if arc_row:
                    dur = float(trk.get("duration_sec", 0) or 0)
                    dur_min, dur_sec = divmod(int(dur), 60)
                    matched.append({
                        "player_track_id": str(trk.get("id", "") or ""),
                        "track_id": str(arc_row.get("track_id", "") or ""),
                        "title": str(arc_row.get("title", "") or trk.get("title", "")),
                        "artist": str(arc_row.get("artist", "") or trk.get("artist", "")),
                        "album": str(arc_row.get("album", "") or trk.get("album", "")),
                        "duration_sec": f"{dur_min}分{dur_sec}秒",
                        "storage_relpath": str(arc_row.get("storage_relpath", "") or ""),
                        "source_sha256": sha,
                        "file_name": str(arc_row.get("file_name", "") or ""),
                    })
                else:
                    external.append({
                        "player_track_id": str(trk.get("id", "") or ""),
                        "title": str(trk.get("title", "") or "未知标题"),
                        "artist": str(trk.get("artist", "") or "未知歌手"),
                        "path": str(trk.get("path", "") or ""),
                    })

            logger.info("[PlayerLink] 同步完成: %d 曲库匹配, %d 外部",
                        len(matched), len(external))
            self.finished.emit(matched, external, playlist_name, playlist_id)
        except Exception as exc:
            logger.exception("[PlayerLink] 同步歌单异常")
            self.finished.emit([], [], f"错误: {exc}", "")


class _FetchFavoritesWorker(QObject):
    finished = Signal(list, str)  # matched_rows, message

    def __init__(self, client: PlayerClient, arc_rows: list[dict]):
        super().__init__()
        self.client = client
        self.arc_rows = arc_rows

    def run(self) -> None:
        logger.info("[PlayerLink] 开始导入红心")
        try:
            # 用 get_playlist 只读获取红心歌单（不影响当前播放）
            playlist = None
            try:
                playlist = self.client.get_playlist("favorites")
                logger.info("[PlayerLink] get_playlist(favorites) 成功")
            except PlayerClientError as exc:
                logger.warning("[PlayerLink] get_playlist(favorites) 失败: %s, 回退到 load_playlist", exc)
                try:
                    self.client.load_playlist("favorites")
                    playlist = self.client.current_playlist()
                    logger.info("[PlayerLink] load_playlist 回退成功")
                except PlayerClientError as exc2:
                    logger.warning("[PlayerLink] 回退也失败: %s", exc2)

            if not playlist or not isinstance(playlist, dict):
                self.finished.emit([], "播放器中无红心歌单或获取失败")
                return
            tracks = playlist.get("tracks") or []
            if not isinstance(tracks, list):
                self.finished.emit([], "红心歌单为空")
                return

            by_sha: dict[str, dict] = {}
            for row in self.arc_rows:
                sha = str(row.get("source_sha256", "") or "").strip().lower()
                if sha:
                    by_sha[sha] = row

            matched: list[dict] = []
            for trk in tracks:
                if not isinstance(trk, dict):
                    continue
                sha = str(trk.get("source_sha256", "") or "").strip().lower()
                arc_row = by_sha.get(sha) if sha else None
                if arc_row:
                    matched.append(arc_row)

            logger.info("[PlayerLink] 红心导入完成: %d 首匹配", len(matched))
            self.finished.emit(matched, f"匹配到 {len(matched)} 首曲库歌曲")
        except Exception as exc:
            logger.exception("[PlayerLink] 导入红心异常")
            self.finished.emit([], f"导入红心失败: {exc}")


class _ImportPlayerPlaylistWorker(QObject):
    """从播放器拉取指定歌单，匹配曲库后返回可加入 MuseArc 歌单的 track_id 列表。

    数据库写操作（create_playlist / add_tracks_to_playlist）由主线程在回调中执行，
    Worker 只做网络 IO 和内存匹配，避免跨线程 DB 访问。
    """

    finished = Signal(list, int, str, str)
    # matched_track_ids, external_count, playlist_name, message

    def __init__(self, client: PlayerClient, arc_rows: list[dict], playlist_id: str):
        super().__init__()
        self.client = client
        self.arc_rows = arc_rows
        self.playlist_id = playlist_id

    def run(self) -> None:
        logger.info("[PlayerLink] 开始从播放器导入歌单 id=%s", self.playlist_id)
        try:
            playlist = None
            try:
                playlist = self.client.get_playlist(self.playlist_id)
                logger.info("[PlayerLink] get_playlist(%s) 成功", self.playlist_id)
            except PlayerClientError as exc:
                logger.warning("[PlayerLink] get_playlist 失败: %s, 回退到 current_playlist", exc)
                try:
                    self.client.load_playlist(self.playlist_id)
                    playlist = self.client.current_playlist()
                    logger.info("[PlayerLink] load_playlist 回退成功")
                except PlayerClientError as exc2:
                    logger.warning("[PlayerLink] 回退也失败: %s", exc2)

            if not playlist or not isinstance(playlist, dict):
                self.finished.emit([], 0, "", "无法获取该歌单内容")
                return

            playlist_name = str(playlist.get("name", "") or self.playlist_id)
            tracks = playlist.get("tracks") or []
            if not isinstance(tracks, list) or not tracks:
                self.finished.emit([], 0, playlist_name, "该歌单为空")
                return

            # 构建 sha256 -> track_id 索引
            sha_to_track_id: dict[str, str] = {}
            for row in self.arc_rows:
                sha = str(row.get("source_sha256", "") or "").strip().lower()
                if sha:
                    sha_to_track_id[sha] = str(row.get("track_id", "") or "")

            matched_track_ids: list[str] = []
            external = 0
            for trk in tracks:
                if not isinstance(trk, dict):
                    continue
                sha = str(trk.get("source_sha256", "") or "").strip().lower()
                track_id = sha_to_track_id.get(sha) if sha else None
                if track_id:
                    matched_track_ids.append(track_id)
                else:
                    external += 1

            logger.info(
                "[PlayerLink] 歌单 %s 导入匹配完成: %d 命中, %d 外部",
                playlist_name, len(matched_track_ids), external,
            )
            msg = f"歌单「{playlist_name}」共 {len(tracks)} 首，曲库命中 {len(matched_track_ids)} 首"
            self.finished.emit(matched_track_ids, external, playlist_name, msg)
        except Exception as exc:
            logger.exception("[PlayerLink] 从播放器导入歌单异常")
            self.finished.emit([], 0, "", f"导入失败: {exc}")


# ── 主页面 ──────────────────────────────────────────────────


class PlayerLinkPage(QWidget):
    library_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        """
        初始化播放器控制界面。

        该方法负责构建与外部播放器进行交互的用户界面，包括连接管理、工具栏、歌单信息显示以及曲库和外部歌曲的展示表格。

        Args:
            facade (MuseArcFacade): 一个核心的外观对象，用于获取应用程序的运行时配置等资源。

        Returns:
            None
        """
        super().__init__()
        self.facade = facade
        self._client = PlayerClient()
        self._connected = False
        self._current_playlist_id: str = ""
        self._thread: QThread | None = None
        self._worker: QObject | None = None

        root = QVBoxLayout(self)

        # ── 连接区 ─────────────────────────────────────
        # 创建一个分组框，用于播放器连接相关的控件
        conn_box = QGroupBox("播放器连接")
        conn_layout = QHBoxLayout(conn_box)
        conn_layout.addWidget(QLabel("端口:"))
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1024, 65535)
        # 从运行时配置获取端口号，如果配置无效则使用默认端口
        cfg_port = facade.get_runtime_config().ui.player_link_port
        self.spin_port.setValue(cfg_port if 1024 <= cfg_port <= 65535 else PlayerClient.DEFAULT_PORT)
        self.spin_port.setFixedWidth(80)
        conn_layout.addWidget(self.spin_port)
        self.btn_connect = QPushButton("连接播放器")
        self.btn_disconnect = QPushButton("断开")
        self.btn_disconnect.setEnabled(False)
        conn_layout.addWidget(self.btn_connect)
        conn_layout.addWidget(self.btn_disconnect)
        self.label_status = QLabel("未连接")
        conn_layout.addWidget(self.label_status)
        conn_layout.addStretch(1)
        root.addWidget(conn_box)

        # ── 工具栏 ─────────────────────────────────────
        # 创建一个水平布局的工具栏
        toolbar = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新当前播放歌单")
        self.btn_locate = QPushButton("定位到当前播放歌曲")
        self.btn_play_selected = QPushButton("播放选中歌曲")
        self.btn_delete = QPushButton("联动删除歌曲")
        self.btn_import_fav = QPushButton("从播放器导入红心")
        self.btn_import_playlist = QPushButton("从播放器导入歌单")
        # 初始化时禁用所有工具栏按钮，直到连接成功
        for btn in (self.btn_refresh, self.btn_locate, self.btn_play_selected,
                     self.btn_delete, self.btn_import_fav, self.btn_import_playlist):
            btn.setEnabled(False)
            toolbar.addWidget(btn)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

        # ── 歌单信息 ───────────────────────────────────
        # 用于显示当前正在播放的歌单名称
        self.label_playlist = QLabel("歌单: -")
        root.addWidget(self.label_playlist)

        # ── 曲库歌曲表格 ───────────────────────────────
        # 定义并创建用于显示“曲库歌曲”的表格及其数据模型
        self.matched_model = DictTableModel([
            ColumnDef("title", "标题"),
            ColumnDef("artist", "艺术家"),
            ColumnDef("album", "专辑"),
            ColumnDef("duration_sec", "时长"),
        ])
        self.matched_table = QTableView()
        self.matched_table.setModel(self.matched_model)
        self.matched_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.matched_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.matched_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.matched_table.setAlternatingRowColors(True)
        self.matched_table.horizontalHeader().setStretchLastSection(True)
        self.matched_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        # 为表格连接右键菜单信号
        self.matched_table.customContextMenuRequested.connect(self._show_matched_context_menu)

        # ── 外部歌曲表格 ───────────────────────────────
        # 定义并创建用于显示“外部歌曲”的表格及其数据模型
        self.external_model = DictTableModel([
            ColumnDef("title", "歌曲"),
            ColumnDef("artist", "歌手"),
            ColumnDef("path", "路径"),
        ])
        self.external_table = QTableView()
        self.external_table.setModel(self.external_model)
        self.external_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.external_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.external_table.setAlternatingRowColors(True)
        self.external_table.horizontalHeader().setStretchLastSection(True)
        self.external_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        # 为表格连接右键菜单信号
        self.external_table.customContextMenuRequested.connect(self._show_external_context_menu)
        self.external_table.setMaximumHeight(200)

        # ── 分割布局 ───────────────────────────────────
        # 创建垂直分割器，用于调整上下两个表格区域的高度比例
        splitter = QSplitter(Qt.Orientation.Vertical)
        matched_group = QWidget()
        matched_layout = QVBoxLayout(matched_group)
        matched_layout.setContentsMargins(0, 0, 0, 0)
        self.label_matched = QLabel("曲库歌曲 (0)")
        matched_layout.addWidget(self.label_matched)
        matched_layout.addWidget(self.matched_table, 1)

        external_group = QWidget()
        external_layout = QVBoxLayout(external_group)
        external_layout.setContentsMargins(0, 0, 0, 0)
        self.label_external = QLabel("外部歌曲 (0) — 不在曲库中，无法操作")
        external_layout.addWidget(self.label_external)
        external_layout.addWidget(self.external_table, 1)

        splitter.addWidget(matched_group)
        splitter.addWidget(external_group)
        # 设置初始分割比例，上部区域占3份，下部占1份
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        # ── 信号连接 ───────────────────────────────────
        # 将各个按钮的clicked信号连接到对应的处理函数
        self.btn_connect.clicked.connect(self._on_connect)
        self.btn_disconnect.clicked.connect(self._on_disconnect)
        self.btn_refresh.clicked.connect(self._on_refresh)
        self.btn_locate.clicked.connect(self._on_locate)
        self.btn_play_selected.clicked.connect(self._on_play_selected)
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_import_fav.clicked.connect(self._on_import_favorites)
        self.btn_import_playlist.clicked.connect(self._on_import_playlist)

    # ── 公共接口 ──────────────────────────────────────────

    def apply_button_scale(self, scale: float) -> None:
        """应用按钮缩放系数，统一调整关键操作按钮的最小高度。

        Args:
            scale (float): 缩放系数，用于调整按钮基础高度。
        """
        for btn in (
            self.btn_connect, self.btn_disconnect, self.btn_refresh,
            self.btn_locate, self.btn_play_selected, self.btn_delete,
            self.btn_import_fav, self.btn_import_playlist,
        ):
            # 遍历一组功能按钮，统一设置它们的最小高度
            btn.setMinimumHeight(max(30, int(28 * scale)))
            # 计算缩放后的高度（基础高度28 * 系数），并确保最小高度不低于30像素

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade

    def refresh_page(self) -> None:
        """刷新页面。参数：无。返回值：无。"""
        if self._connected:  # 检查是否已连接
            self._on_refresh()  # 调用刷新回调

    # ── 连接管理 ──────────────────────────────────────────

    def _set_connected_ui(self, connected: bool) -> None:
        self._connected = connected
        self.btn_connect.setEnabled(not connected)
        self.btn_disconnect.setEnabled(connected)
        self.spin_port.setEnabled(not connected)
        for btn in (self.btn_refresh, self.btn_locate, self.btn_play_selected,
                     self.btn_delete, self.btn_import_fav, self.btn_import_playlist):
            btn.setEnabled(connected)

    def _on_connect(self) -> None:
        port = self.spin_port.value()
        self._client = PlayerClient(port=port)
        self.label_status.setText("连接中...")
        self._run_worker(_ConnectWorker(self._client), self._on_connect_done)

    def _on_connect_done(self, success: bool, message: str) -> None:
        logger.info("[PlayerLink] 连接回调 success=%s msg=%s", success, message)
        if success:
            self._set_connected_ui(True)
            self.label_status.setText(f"已连接 (端口 {self._client.port})")
            # 延迟刷新，确保线程清理完毕
            from PySide6.QtCore import QTimer
            QTimer.singleShot(100, self._on_refresh)
        else:
            self._set_connected_ui(False)
            self.label_status.setText(f"连接失败: {message}")

    def _on_disconnect(self) -> None:
        """断开连接后的回调方法。

        功能：处理客户端断开连接后的UI状态重置和相关数据清理。
        参数：无。
        返回值：无。
        """
        self._client.disconnect()  # 断开与服务器的连接
        self._set_connected_ui(False)  # 将UI界面设置为“未连接”状态
        self.label_status.setText("未连接")  # 更新状态标签显示为“未连接”
        self.matched_model.set_rows([])  # 清空匹配模型（本地歌单）的数据
        self.external_model.set_rows([])  # 清空外部模型（网络歌单）的数据
        self.label_playlist.setText("歌单: -")  # 重置播放列表标签显示为默认值
        self._current_playlist_id = ""  # 清空当前正在显示的播放列表ID

    # ── 刷新歌单 ──────────────────────────────────────────

    def _on_refresh(self) -> None:
        if not self._connected:
            return
        self.label_status.setText("同步中...")
        # 在主线程中读取曲库数据，传入子线程（避免跨线程 DB 访问）
        arc_rows = self.facade.list_tracks(limit=2_000_000)
        logger.info("[PlayerLink] 读取曲库 %d 条，开始同步", len(arc_rows))
        self._run_worker(
            _FetchPlaylistWorker(self._client, arc_rows),
            self._on_refresh_done,
        )

    def _on_refresh_done(self, matched: list, external: list, playlist_name: str, playlist_id: str) -> None:
        """刷新完成后的回调函数，用于更新界面显示。

        功能：
            当外部数据刷新完成后，此方法被调用，用于更新匹配歌曲列表、外部歌曲列表、
            当前歌单信息以及相关的UI显示。

        参数：
            matched (list): 已匹配的歌曲列表，这些歌曲在本地曲库中存在。
            external (list): 未匹配的歌曲列表，这些歌曲不在本地曲库中。
            playlist_name (str): 当前歌单的名称。
            playlist_id (str): 当前歌单的唯一标识符。

        返回值：
            None: 此方法不返回任何值，仅用于更新UI状态。
        """
        # 记录日志信息，显示匹配数量、外部数量、歌单名称和歌单ID
        logger.info("[PlayerLink] 同步回调: matched=%d external=%d playlist=%s id=%s",
                    len(matched), len(external), playlist_name, playlist_id)

        # 更新状态标签，显示已连接状态及使用的端口
        self.label_status.setText(f"已连接 (端口 {self._client.port})")

        # 更新已匹配歌曲模型，传入已匹配的歌曲列表
        self.matched_model.set_rows(matched)

        # 更新外部歌曲模型，传入外部歌曲列表
        self.external_model.set_rows(external)

        # 更新已匹配歌曲数量标签显示
        self.label_matched.setText(f"曲库歌曲 ({len(matched)})")

        # 更新外部歌曲数量标签显示，说明这些歌曲不在曲库中无法操作
        self.label_external.setText(f"外部歌曲 ({len(external)}) — 不在曲库中，无法操作")

        # 检查歌单名称是否有效（不为空且不以"错误"开头）
        if playlist_name and not playlist_name.startswith("错误"):
            # 有效歌单名称，显示歌单名称
            self.label_playlist.setText(f"歌单: {playlist_name}")
        else:
            # 无效歌单名称或以"错误"开头，显示默认占位符
            self.label_playlist.setText("歌单: -")

        # 更新当前歌单ID为传入的playlist_id
        self._current_playlist_id = playlist_id

    # ── 定位当前播放歌曲 ──────────────────────────────────

    def _on_locate(self) -> None:
        """定位当前播放歌曲到匹配列表中。

        功能：检查播放器连接状态，获取当前播放歌曲，通过SHA256匹配找到对应行，并选中滚动到该行。

        参数：无显式参数。

        返回值：无。
        """
        if not self._connected:
            # 如果未连接到播放器，则直接返回
            return
        try:
            # 尝试获取当前播放歌曲
            track = self._client.current_track()
        except PlayerClientError as exc:
            # 获取失败时显示警告消息
            QMessageBox.warning(self, "定位", f"获取当前播放歌曲失败: {exc}")
            return
        if not track or not isinstance(track, dict):
            # 如果没有歌曲或歌曲不是字典格式，显示信息消息
            QMessageBox.information(self, "定位", "播放器当前未播放歌曲。")
            return
        # 提取歌曲的SHA256哈希值
        sha = str(track.get("source_sha256", "") or "").strip().lower()
        if not sha:
            # 如果SHA为空，表示歌曲不在曲库中
            QMessageBox.information(self, "定位", "当前播放歌曲不在曲库中。")
            return
        # 遍历匹配模型中的所有行
        for i in range(len(self.matched_model.rows or [])):
            row = self.matched_model.rows[i]
            # 比较SHA256哈希值
            if str(row.get("source_sha256", "") or "").lower() == sha:
                # 找到匹配行，获取索引并选中滚动
                idx = self.matched_model.index(i, 0)
                self.matched_table.clearSelection()
                self.matched_table.selectRow(i)
                self.matched_table.scrollTo(idx)
                return
        # 如果没有找到匹配行，显示信息消息
        QMessageBox.information(self, "定位", "当前播放歌曲不在同步列表中，请刷新歌单。")

    # ── 播放选中歌曲 ──────────────────────────────────────

    def _on_play_selected(self) -> None:
        """处理用户选择播放项目的事件。

        获取当前选中的匹配行，并在主窗口执行播放队列操作。

        Args:
            self: 类实例对象。

        Returns:
            None
        """
        rows = self._selected_matched_rows()  # 获取当前选中的匹配行数据
        if not rows:  # 若没有选中任何行，则直接返回
            return
        main_win = self.window()  # 获取所属的主窗口实例
        if hasattr(main_win, "queue_and_play_tracks"):  # 检查主窗口是否支持队列播放功能
            main_win.queue_and_play_tracks(rows)  # 调用主窗口方法播放选中的行

    # ── 联动删除 ──────────────────────────────────────────

    def _on_delete(self) -> None:
        """处理联动删除歌曲的操作

        功能：
            根据用户选择，同步删除MuseArc曲库和播放器中的歌曲，并提供删除模式选择。
            支持多种删除模式（如：仅删除歌曲、同时删除关联歌词等）。

        参数：
            无（除了self实例本身）

        返回值：
            None
        """
        rows = self._selected_matched_rows()  # 获取当前选中的匹配歌曲行
        if not rows:
            # 如果没有选中任何歌曲，提示用户并提前返回
            QMessageBox.information(self, "联动删除", "请先选择要删除的歌曲。")
            return

        # 导入删除对话框助手函数
        from musearc.ui.main_window_helpers import _ask_delete_tracks_with_lyrics
        cfg = self.facade.get_runtime_config()  # 获取运行时配置
        # 从配置中读取默认删除模式，若未设置则使用默认值"move_linked_lyrics"
        default_mode = str(cfg.ui.delete_tracks_mode_default or "move_linked_lyrics")
        # 弹出对话框让用户确认删除并选择删除模式
        mode, remember = _ask_delete_tracks_with_lyrics(self, len(rows), default_mode)
        if mode == "cancel":  # 如果用户选择取消，则返回
            return

        # 提取歌曲ID列表，用于从MuseArc曲库删除
        track_ids = [str(r.get("track_id", "") or "") for r in rows if r.get("track_id")]
        # 提取播放器歌曲ID列表，用于从播放器删除
        player_track_ids = [str(r.get("player_track_id", "") or "") for r in rows if r.get("player_track_id")]

        # 记录操作日志，包含ID数量和删除模式
        logger.info("[PlayerLink] 联动删除: arc_ids=%d player_ids=%d mode=%s",
                    len(track_ids), len(player_track_ids), mode)

        # 1. 在 MuseArc 中删除歌曲
        try:
            count = self.facade.delete_tracks(track_ids, mode=mode)  # 调用facade删除歌曲
        except Exception as exc:
            # 删除失败时记录异常并提示用户，然后返回
            logger.exception("[PlayerLink] 曲库删除失败")
            QMessageBox.warning(self, "联动删除", f"曲库删除失败: {exc}")
            return

        # 2. 在播放器中删除歌曲
        playlist_id = self._current_playlist_id or "all_songs"  # 获取当前播放列表ID，默认为"all_songs"
        failed_player = []  # 记录在播放器端删除失败的歌曲ID
        last_error_msg = ""  # 记录最后一次播放器端错误信息
        for ptid in player_track_ids:
            if not ptid:  # 跳过空ID
                continue
            try:
                # 调用客户端从播放列表移除歌曲
                self._client.remove_track_from_playlist(playlist_id, ptid)
                logger.info("[PlayerLink] 播放器删除成功: playlist=%s track=%s", playlist_id, ptid)
            except PlayerClientError as exc:
                # 记录播放器删除失败，但不中断整个删除过程
                logger.warning("[PlayerLink] 播放器删除失败: %s", exc)
                failed_player.append(ptid)
                last_error_msg = str(exc)

        # 构建操作结果消息
        msg = f"已从曲库删除 {count} 首歌曲。"
        if failed_player:
            msg += f"\n{len(failed_player)} 首在播放器端删除失败，最后错误: {last_error_msg}"
        # 显示最终操作结果
        QMessageBox.information(self, "联动删除", msg)
        # 发射信号通知其他部件曲库已更改
        self.library_changed.emit()
        # 刷新当前视图
        self._on_refresh()

    # ── 导入红心 ──────────────────────────────────────────

    def _on_import_favorites(self) -> None:
        """导入用户收藏的歌曲（红心标记的歌曲）到本地。

        该方法在用户触发导入收藏夹操作时被调用，主要用于将云端的“红心”标记歌曲同步到本地数据库。
        执行流程包括检查连接状态、更新界面提示、获取现有歌曲列表，然后启动后台工作线程来实际处理导入任务。

        Args:
            self: 实例对象本身，用于访问实例属性和方法。

        Returns:
            None: 该方法不返回任何值，结果通过回调方法处理。
        """
        # 检查与音乐服务的连接状态，如果未连接则直接返回，避免后续操作失败
        if not self._connected:
            return
        # 更新界面状态标签，提示用户导入操作正在进行中
        self.label_status.setText("导入红心中...")
        # 从数据库中获取所有现有歌曲行，用于后续比对和更新
        # 注意：limit设置为2,000,000，表示获取近全部歌曲记录
        arc_rows = self.facade.list_tracks(limit=2_000_000)
        # 启动后台工作线程执行实际的收藏夹导入任务
        # 工作线程会调用客户端获取收藏夹数据，并与现有歌曲列表进行比对
        # 完成后会通过回调函数_on_import_fav_done处理结果
        self._run_worker(
            _FetchFavoritesWorker(self._client, arc_rows),
            self._on_import_fav_done,
        )

    def _on_import_fav_done(self, matched: list, message: str) -> None:
        """导入红心歌曲完成的回调函数。

        根据导入匹配的结果，将匹配到的歌曲添加到收藏歌单，并显示相应的提示信息。

        Args:
            matched: 匹配到的歌曲信息列表，每个元素是一个包含歌曲信息的字典。
            message: 导入过程的状态或结果消息。

        Returns:
            None: 此方法无返回值。
        """
        logger.info("[PlayerLink] 红心导入回调: %d 首, %s", len(matched), message)
        self.label_status.setText(f"已连接 (端口 {self._client.port})")
        # 如果没有匹配到任何歌曲，则直接显示消息并返回
        if not matched:
            QMessageBox.information(self, "导入红心", message)
            return
        # 从匹配列表中提取所有有效歌曲ID（非空），并添加到收藏夹
        count = self.facade.add_to_favorites(
            [str(r.get("track_id", "")) for r in matched if r.get("track_id")]
        )
        # 显示成功添加的歌曲数量
        QMessageBox.information(self, "导入红心", f"已将 {count} 首歌曲加入收藏歌单。")
        # 发射信号，通知其他组件库已更新
        self.library_changed.emit()

    # ── 从播放器导入歌单 ──────────────────────────────────

    def _on_import_playlist(self) -> None:
        """从播放器选择一个已有歌单并导入到 MuseArc。

        流程：
        1. 调用 client.state() 拿到播放器所有歌单列表（轻量调用，主线程执行）。
        2. 弹出对话框让用户选择一个歌单。
        3. 启动 _ImportPlayerPlaylistWorker 在子线程拉取歌单内容并匹配曲库。
        4. 回调中在 MuseArc 创建/复用同名歌单并写入 track_ids。
        """
        if not self._connected:
            return

        try:
            state = self._get_player_playlists()
        except PlayerClientError as exc:
            QMessageBox.warning(self, "从播放器导入歌单", f"获取播放器歌单列表失败: {exc}")
            return

        if not state:
            QMessageBox.information(self, "从播放器导入歌单", "播放器中暂无可用歌单。")
            return

        # 过滤掉 all_songs（与 MuseArc 整个曲库等价）和 favorites（已有"导入红心"按钮处理）
        candidates = [
            p for p in state
            if str(p.get("id", "")) not in {"all_songs", "favorites"}
        ]
        if not candidates:
            QMessageBox.information(self, "从播放器导入歌单", "播放器中暂无可用歌单。")
            return

        playlist_id, playlist_name = self._pick_player_playlist(candidates)
        if not playlist_id:
            return

        self.label_status.setText(f"导入歌单「{playlist_name}」中...")
        arc_rows = self.facade.list_tracks(limit=2_000_000)
        logger.info("[PlayerLink] 读取曲库 %d 条，开始导入播放器歌单 %s", len(arc_rows), playlist_id)
        self._run_worker(
            _ImportPlayerPlaylistWorker(self._client, arc_rows, playlist_id),
            self._on_import_playlist_done,
        )

    def _get_player_playlists(self) -> list[dict]:
        """获取播放器端所有歌单摘要（id/name/count）。"""
        state = self._client.state()
        playlists = state.get("playlists") or []
        if not isinstance(playlists, list):
            return []
        return [p for p in playlists if isinstance(p, dict)]

    def _pick_player_playlist(self, candidates: list[dict]) -> tuple[str, str]:
        """弹出对话框让用户从播放器歌单中选择一个。

        Returns:
            (playlist_id, playlist_name) — 用户取消时返回 ("", "")。
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("从播放器导入歌单")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel("请选择要导入的播放器歌单："))

        list_widget = QListWidget(dialog)
        list_widget.setMinimumWidth(360)
        list_widget.setMinimumHeight(280)
        for pl in candidates:
            pid = str(pl.get("id", "") or "")
            name = str(pl.get("name", "") or pid)
            count = int(pl.get("count", 0) or 0)
            item = QListWidgetItem(f"{name}  ({count} 首)")
            item.setData(Qt.UserRole, pid)
            item.setData(Qt.UserRole + 1, name)
            list_widget.addItem(item)
        list_widget.setCurrentRow(0)
        layout.addWidget(list_widget, 1)

        # 双击直接确认
        list_widget.itemDoubleClicked.connect(dialog.accept)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return "", ""

        item = list_widget.currentItem()
        if item is None:
            return "", ""
        return str(item.data(Qt.UserRole) or ""), str(item.data(Qt.UserRole + 1) or "")

    def _on_import_playlist_done(
        self,
        matched_track_ids: list,
        external_count: int,
        playlist_name: str,
        message: str,
    ) -> None:
        """Worker 完成后，在主线程创建 MuseArc 歌单并写入 track_ids。"""
        logger.info(
            "[PlayerLink] 播放器歌单导入回调: matched=%d external=%d name=%s",
            len(matched_track_ids), external_count, playlist_name,
        )
        self.label_status.setText(f"已连接 (端口 {self._client.port})")

        if not matched_track_ids:
            QMessageBox.information(self, "从播放器导入歌单", message or "未匹配到任何曲库歌曲。")
            return

        # 复用同名 MuseArc 歌单；若不存在则新建
        target_playlist_id = ""
        existing = [
            p for p in self.facade.list_playlists()
            if str(p.get("name", "")).strip() == playlist_name.strip()
            and str(p.get("playlist_id", "")) != FAVORITES_PLAYLIST_ID
        ]
        if existing:
            target_playlist_id = str(existing[0].get("playlist_id", "") or "")
            logger.info("[PlayerLink] 复用已有 MuseArc 歌单: %s", target_playlist_id)
        else:
            try:
                target_playlist_id = self.facade.create_playlist(playlist_name)
                logger.info("[PlayerLink] 新建 MuseArc 歌单: %s (%s)", playlist_name, target_playlist_id)
            except Exception as exc:
                logger.exception("[PlayerLink] 创建歌单失败")
                QMessageBox.warning(self, "从播放器导入歌单", f"创建歌单失败: {exc}")
                return

        try:
            added = self.facade.add_tracks_to_playlist(target_playlist_id, matched_track_ids)
        except Exception as exc:
            logger.exception("[PlayerLink] 写入歌单失败")
            QMessageBox.warning(self, "从播放器导入歌单", f"写入歌单失败: {exc}")
            return

        summary = (
            f"已导入歌单「{playlist_name}」：新增 {added} 首到 MuseArc 歌单。"
            f"\n曲库未命中 {external_count} 首（已跳过）。"
        )
        QMessageBox.information(self, "从播放器导入歌单", summary)
        self.library_changed.emit()

    # ── 右键菜单 ──────────────────────────────────────────

    def _show_matched_context_menu(self, pos) -> None:
        """显示匹配结果列表的右键菜单。

        在鼠标右键点击匹配结果表格时调用，根据用户选择执行相应操作。

        参数:
            pos (QPoint): 鼠标点击在表格视口中的局部坐标。

        返回值:
            None: 无返回值。
        """
        rows = self._selected_matched_rows()  # 获取当前选中的匹配歌曲行索引列表
        if not rows:  # 如果没有选中任何行，则直接返回，不显示菜单
            return
        menu = QMenu(self)  # 创建一个右键菜单实例
        act_play = menu.addAction("播放选中歌曲")  # 添加“播放选中歌曲”菜单项
        act_delete = menu.addAction("联动删除歌曲")  # 添加“联动删除歌曲”菜单项
        act_add_playlist = menu.addAction("加到歌单...")  # 添加“加到歌单...”菜单项
        menu.addSeparator()  # 添加分隔线
        act_play_ext = menu.addAction("在播放器中播放")  # 添加“在播放器中播放”菜单项

        # 显示菜单并获取用户选择的动作，将局部坐标转换为全局坐标以定位菜单
        chosen = menu.exec(self.matched_table.viewport().mapToGlobal(pos))
        if chosen == act_play:  # 如果用户选择了“播放选中歌曲”
            self._on_play_selected()
        elif chosen == act_delete:  # 如果用户选择了“联动删除歌曲”
            self._on_delete()
        elif chosen == act_add_playlist:  # 如果用户选择了“加到歌单...”
            self._add_to_playlist(rows)
        elif chosen == act_play_ext:  # 如果用户选择了“在播放器中播放”
            self._play_in_player(rows)

    def _show_external_context_menu(self, pos) -> None:
        """显示外部上下文菜单。

        此方法在外部表格上显示一个右键菜单，允许用户播放文件或打开文件位置。
        根据用户选择，调用相应功能或忽略操作。

        参数:
            pos (QPoint): 鼠标右键点击的位置，通常由事件传递。

        返回值:
            None
        """
        idx = self.external_table.indexAt(pos)  # 获取点击位置在表格中的索引
        row = self.external_model.row_at(idx.row()) if idx.isValid() else None  # 如果索引有效，获取对应行的数据；否则设为None
        if not row:  # 如果没有有效行数据，提前返回
            return
        menu = QMenu(self)  # 创建上下文菜单，父控件为self
        path = str(row.get("path", "") or "").strip()  # 从行数据中获取路径，确保为字符串并去除空白字符
        act_play = menu.addAction("播放")  # 添加"播放"菜单动作
        act_play.setEnabled(bool(path) and Path(path).exists())  # 仅当路径非空且文件存在时启用"播放"选项
        act_locate = menu.addAction("打开文件位置")  # 添加"打开文件位置"菜单动作
        act_locate.setEnabled(bool(path) and Path(path).exists())  # 仅当路径非空且文件存在时启用"打开文件位置"选项

        chosen = menu.exec(self.external_table.viewport().mapToGlobal(pos))  # 显示菜单并获取用户选择的动作
        if chosen == act_play and path:  # 如果用户选择"播放"且路径有效
            try:
                self._client.play_file(path)  # 尝试调用客户端播放文件
            except PlayerClientError:  # 捕获播放错误
                pass  # 忽略错误，避免中断程序
        elif chosen == act_locate and path:  # 如果用户选择"打开文件位置"且路径有效
            self._reveal_in_explorer(path)  # 在文件资源管理器中显示路径

    # ── 辅助方法 ──────────────────────────────────────────

    def _selected_matched_rows(self) -> list[dict]:
        rows: list[dict] = []
        for idx in self.matched_table.selectionModel().selectedRows():
            row = self.matched_model.row_at(idx.row())
            if row:
                rows.append(row)
        return rows

    def _add_to_playlist(self, rows: list[dict]) -> None:
        """将指定歌曲添加到用户选择的歌单中。

        Args:
            rows (list[dict]): 包含歌曲信息的字典列表，每个字典需包含 "track_id" 键。

        Returns:
            None: 此方法不返回任何值，但会修改歌单数据并发射信号。
        """
        # 从输入行中提取所有有效的歌曲ID，如果“track_id”存在且非空则转换为字符串
        track_ids = [str(r.get("track_id", "")) for r in rows if r.get("track_id")]
        # 如果没有提取到任何有效的歌曲ID，则直接返回，不做任何操作
        if not track_ids:
            return
        # 获取所有歌单，并过滤掉“我的收藏”这个特殊歌单
        playlists = [p for p in self.facade.list_playlists()
                     if str(p.get("playlist_id", "")) != FAVORITES_PLAYLIST_ID]
        # 如果过滤后没有可供选择的歌单，则提示用户并返回
        if not playlists:
            QMessageBox.information(self, "加到歌单", "暂无歌单，请先在歌单管理中创建。")
            return
        # 创建一个右键菜单，用于让用户选择目标歌单
        menu = QMenu(self)
        action_map: dict = {}
        # 遍历所有歌单，将每个歌单的名称作为菜单项添加到菜单中，并建立菜单项到歌单ID的映射
        for pl in playlists:
            act = menu.addAction(str(pl.get("name", "")))
            action_map[act] = str(pl.get("playlist_id", ""))
        # 在鼠标光标当前位置显示菜单，并等待用户选择
        from PySide6.QtGui import QCursor
        chosen = menu.exec(QCursor.pos())
        # 如果用户选择了某个歌单（且该选择存在于映射中），则执行添加操作
        if chosen and chosen in action_map:
            self.facade.add_tracks_to_playlist(action_map[chosen], track_ids)
            # 发送库已变更的信号，通知其他部件刷新数据
            self.library_changed.emit()

    def _play_in_player(self, rows: list[dict]) -> None:
        """
        遍历文件信息列表，尝试在媒体播放器中播放第一个存在的文件。

        参数:
            rows (list[dict]): 包含文件信息的字典列表，每个字典应包含'storage_relpath'键。

        返回:
            None: 该方法不返回任何值。
        """
        for row in rows:
            # 从字典中安全地获取文件的相对存储路径，若不存在或为空则处理为空字符串
            rel = str(row.get("storage_relpath", "") or "").strip()
            # 如果处理后的相对路径为空，则跳过当前文件
            if not rel:
                continue
            # 根据配置的库根目录与相对路径，拼接出文件的完整路径
            path = self.facade.library_root / rel
            # 检查拼接出的完整路径对应的文件是否存在
            if path.exists():
                try:
                    # 尝试使用媒体播放器客户端播放该文件
                    self._client.play_file(str(path))
                except PlayerClientError:
                    # 捕获播放器客户端错误（如文件格式不支持等），但不做额外处理
                    pass
                # 无论是成功播放还是遇到错误，都结束方法（即只尝试播放第一个找到的文件）
                return

    @staticmethod
    def _reveal_in_explorer(file_path: str) -> None:
        target = Path(file_path)
        if not target.exists():
            target = target.parent
        try:
            if target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                subprocess.Popen(["explorer", str(target)])
        except Exception:
            pass

    def _run_worker(self, worker: QObject, callback) -> None:
        """在子线程中运行 worker，完成后调用 callback。

        关键：确保旧线程完全结束后才启动新线程，避免 QThread destroyed while running。
        """
        # 等待旧线程结束
        if self._thread is not None:
            if self._thread.isRunning():
                logger.info("[PlayerLink] 等待旧线程结束...")
                self._thread.quit()
                self._thread.wait(5000)
            # 清理旧 worker/thread 引用
            if self._worker is not None:
                self._worker.deleteLater()
                self._worker = None
            self._thread.deleteLater()
            self._thread = None

        thread = QThread(self)
        # worker 不设 parent，moveToThread 后由 thread 事件循环管理
        worker.moveToThread(thread)

        # 保持引用防止 GC
        self._thread = thread
        self._worker = worker

        thread.started.connect(worker.run)
        worker.finished.connect(callback)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._cleanup_worker)

        logger.info("[PlayerLink] 启动工作线程 %s", type(worker).__name__)
        thread.start()

    def _cleanup_worker(self) -> None:
        """清理工作线程资源。

        该方法负责安全地销毁并清理与工作线程相关的对象（self._worker 和 self._thread），
        防止内存泄漏。清理后，相关属性将被设置为 None。

        参数:
            无（除 self）。

        返回:
            无返回值 (None)。
        """
        logger.info("[PlayerLink] 清理工作线程")
        # 检查 worker 对象是否存在，如果存在则安排其延迟删除并解除引用
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        # 检查 thread 对象是否存在，如果存在则安排其延迟删除并解除引用
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None
