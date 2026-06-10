"""播放器联动管理页面。"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from PySide6.QtCore import QModelIndex, QObject, Qt, QThread, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from musearc.app.facade import MuseArcFacade, FAVORITES_PLAYLIST_ID
from musearc.infra.player.client import PlayerClient, PlayerClientError
from musearc.ui.table_models import ColumnDef, DictTableModel

logger = logging.getLogger(__name__)


# ── 异步工作线程 ────────────────────────────────────────────


class _ConnectWorker(QObject):
    finished = Signal(bool, str)  # success, message

    def __init__(self, client: PlayerClient):
        super().__init__()
        self.client = client

    def run(self) -> None:
        logger.info("[PlayerLink] 连接中 host=%s port=%d", self.client.host, self.client.port)
        try:
            self.client.connect(timeout=5.0)
            logger.info("[PlayerLink] 连接成功")
            self.finished.emit(True, "已连接")
        except PlayerClientError as exc:
            logger.warning("[PlayerLink] 连接失败: %s", exc)
            self.finished.emit(False, str(exc))
        except Exception as exc:
            logger.warning("[PlayerLink] 连接异常: %s", exc)
            self.finished.emit(False, f"连接失败: {exc}")


class _FetchPlaylistWorker(QObject):
    finished = Signal(list, list, str, str)  # matched, external, playlist_name, playlist_id

    def __init__(self, client: PlayerClient, arc_rows: list[dict]):
        super().__init__()
        self.client = client
        self.arc_rows = arc_rows

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


# ── 主页面 ──────────────────────────────────────────────────


class PlayerLinkPage(QWidget):
    library_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade
        self._client = PlayerClient()
        self._connected = False
        self._current_playlist_id: str = ""
        self._thread: QThread | None = None
        self._worker: QObject | None = None

        root = QVBoxLayout(self)

        # ── 连接区 ─────────────────────────────────────
        conn_box = QGroupBox("播放器连接")
        conn_layout = QHBoxLayout(conn_box)
        conn_layout.addWidget(QLabel("端口:"))
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1024, 65535)
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
        toolbar = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新当前播放歌单")
        self.btn_locate = QPushButton("定位到当前播放歌曲")
        self.btn_play_selected = QPushButton("播放选中歌曲")
        self.btn_delete = QPushButton("联动删除歌曲")
        self.btn_import_fav = QPushButton("从播放器导入红心")
        for btn in (self.btn_refresh, self.btn_locate, self.btn_play_selected,
                     self.btn_delete, self.btn_import_fav):
            btn.setEnabled(False)
            toolbar.addWidget(btn)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

        # ── 歌单信息 ───────────────────────────────────
        self.label_playlist = QLabel("歌单: -")
        root.addWidget(self.label_playlist)

        # ── 曲库歌曲表格 ───────────────────────────────
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
        self.matched_table.customContextMenuRequested.connect(self._show_matched_context_menu)

        # ── 外部歌曲表格 ───────────────────────────────
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
        self.external_table.customContextMenuRequested.connect(self._show_external_context_menu)
        self.external_table.setMaximumHeight(200)

        # ── 分割布局 ───────────────────────────────────
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
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        # ── 信号连接 ───────────────────────────────────
        self.btn_connect.clicked.connect(self._on_connect)
        self.btn_disconnect.clicked.connect(self._on_disconnect)
        self.btn_refresh.clicked.connect(self._on_refresh)
        self.btn_locate.clicked.connect(self._on_locate)
        self.btn_play_selected.clicked.connect(self._on_play_selected)
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_import_fav.clicked.connect(self._on_import_favorites)

    # ── 公共接口 ──────────────────────────────────────────

    def apply_button_scale(self, scale: float) -> None:
        for btn in (
            self.btn_connect, self.btn_disconnect, self.btn_refresh,
            self.btn_locate, self.btn_play_selected, self.btn_delete,
            self.btn_import_fav,
        ):
            btn.setMinimumHeight(max(30, int(28 * scale)))

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade

    def refresh_page(self) -> None:
        if self._connected:
            self._on_refresh()

    # ── 连接管理 ──────────────────────────────────────────

    def _set_connected_ui(self, connected: bool) -> None:
        self._connected = connected
        self.btn_connect.setEnabled(not connected)
        self.btn_disconnect.setEnabled(connected)
        self.spin_port.setEnabled(not connected)
        for btn in (self.btn_refresh, self.btn_locate, self.btn_play_selected,
                     self.btn_delete, self.btn_import_fav):
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
        self._client.disconnect()
        self._set_connected_ui(False)
        self.label_status.setText("未连接")
        self.matched_model.set_rows([])
        self.external_model.set_rows([])
        self.label_playlist.setText("歌单: -")
        self._current_playlist_id = ""

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
        logger.info("[PlayerLink] 同步回调: matched=%d external=%d playlist=%s id=%s",
                    len(matched), len(external), playlist_name, playlist_id)
        self.label_status.setText(f"已连接 (端口 {self._client.port})")
        self.matched_model.set_rows(matched)
        self.external_model.set_rows(external)
        self.label_matched.setText(f"曲库歌曲 ({len(matched)})")
        self.label_external.setText(f"外部歌曲 ({len(external)}) — 不在曲库中，无法操作")
        if playlist_name and not playlist_name.startswith("错误"):
            self.label_playlist.setText(f"歌单: {playlist_name}")
        else:
            self.label_playlist.setText("歌单: -")
        self._current_playlist_id = playlist_id

    # ── 定位当前播放歌曲 ──────────────────────────────────

    def _on_locate(self) -> None:
        if not self._connected:
            return
        try:
            track = self._client.current_track()
        except PlayerClientError as exc:
            QMessageBox.warning(self, "定位", f"获取当前播放歌曲失败: {exc}")
            return
        if not track or not isinstance(track, dict):
            QMessageBox.information(self, "定位", "播放器当前未播放歌曲。")
            return
        sha = str(track.get("source_sha256", "") or "").strip().lower()
        if not sha:
            QMessageBox.information(self, "定位", "当前播放歌曲不在曲库中。")
            return
        for i in range(len(self.matched_model.rows or [])):
            row = self.matched_model.rows[i]
            if str(row.get("source_sha256", "") or "").lower() == sha:
                idx = self.matched_model.index(i, 0)
                self.matched_table.clearSelection()
                self.matched_table.selectRow(i)
                self.matched_table.scrollTo(idx)
                return
        QMessageBox.information(self, "定位", "当前播放歌曲不在同步列表中，请刷新歌单。")

    # ── 播放选中歌曲 ──────────────────────────────────────

    def _on_play_selected(self) -> None:
        rows = self._selected_matched_rows()
        if not rows:
            return
        main_win = self.window()
        if hasattr(main_win, "queue_and_play_tracks"):
            main_win.queue_and_play_tracks(rows)

    # ── 联动删除 ──────────────────────────────────────────

    def _on_delete(self) -> None:
        rows = self._selected_matched_rows()
        if not rows:
            QMessageBox.information(self, "联动删除", "请先选择要删除的歌曲。")
            return

        from musearc.ui.main_window_helpers import _ask_delete_tracks_with_lyrics
        cfg = self.facade.get_runtime_config()
        default_mode = str(cfg.ui.delete_tracks_mode_default or "move_linked_lyrics")
        mode, remember = _ask_delete_tracks_with_lyrics(self, len(rows), default_mode)
        if mode == "cancel":
            return

        track_ids = [str(r.get("track_id", "") or "") for r in rows if r.get("track_id")]
        player_track_ids = [str(r.get("player_track_id", "") or "") for r in rows if r.get("player_track_id")]

        logger.info("[PlayerLink] 联动删除: arc_ids=%d player_ids=%d mode=%s",
                    len(track_ids), len(player_track_ids), mode)

        # 1. 在 MuseArc 中删除
        try:
            count = self.facade.delete_tracks(track_ids, mode=mode)
        except Exception as exc:
            logger.exception("[PlayerLink] 曲库删除失败")
            QMessageBox.warning(self, "联动删除", f"曲库删除失败: {exc}")
            return

        # 2. 在播放器中删除
        playlist_id = self._current_playlist_id or "all_songs"
        failed_player = []
        for ptid in player_track_ids:
            if not ptid:
                continue
            try:
                self._client.remove_track_from_playlist(playlist_id, ptid)
                logger.info("[PlayerLink] 播放器删除成功: playlist=%s track=%s", playlist_id, ptid)
            except PlayerClientError as exc:
                logger.warning("[PlayerLink] 播放器删除失败: %s", exc)
                failed_player.append(ptid)

        msg = f"已从曲库删除 {count} 首歌曲。"
        if failed_player:
            msg += f"\n{len(failed_player)} 首在播放器端删除失败: {exc}"
        QMessageBox.information(self, "联动删除", msg)
        self.library_changed.emit()
        self._on_refresh()

    # ── 导入红心 ──────────────────────────────────────────

    def _on_import_favorites(self) -> None:
        if not self._connected:
            return
        self.label_status.setText("导入红心中...")
        arc_rows = self.facade.list_tracks(limit=2_000_000)
        self._run_worker(
            _FetchFavoritesWorker(self._client, arc_rows),
            self._on_import_fav_done,
        )

    def _on_import_fav_done(self, matched: list, message: str) -> None:
        logger.info("[PlayerLink] 红心导入回调: %d 首, %s", len(matched), message)
        self.label_status.setText(f"已连接 (端口 {self._client.port})")
        if not matched:
            QMessageBox.information(self, "导入红心", message)
            return
        count = self.facade.add_to_favorites(
            [str(r.get("track_id", "")) for r in matched if r.get("track_id")]
        )
        QMessageBox.information(self, "导入红心", f"已将 {count} 首歌曲加入收藏歌单。")
        self.library_changed.emit()

    # ── 右键菜单 ──────────────────────────────────────────

    def _show_matched_context_menu(self, pos) -> None:
        rows = self._selected_matched_rows()
        if not rows:
            return
        menu = QMenu(self)
        act_play = menu.addAction("播放选中歌曲")
        act_delete = menu.addAction("联动删除歌曲")
        act_add_playlist = menu.addAction("加到歌单...")
        menu.addSeparator()
        act_play_ext = menu.addAction("在播放器中播放")

        chosen = menu.exec(self.matched_table.viewport().mapToGlobal(pos))
        if chosen == act_play:
            self._on_play_selected()
        elif chosen == act_delete:
            self._on_delete()
        elif chosen == act_add_playlist:
            self._add_to_playlist(rows)
        elif chosen == act_play_ext:
            self._play_in_player(rows)

    def _show_external_context_menu(self, pos) -> None:
        idx = self.external_table.indexAt(pos)
        row = self.external_model.row_at(idx.row()) if idx.isValid() else None
        if not row:
            return
        menu = QMenu(self)
        path = str(row.get("path", "") or "").strip()
        act_play = menu.addAction("播放")
        act_play.setEnabled(bool(path) and Path(path).exists())
        act_locate = menu.addAction("打开文件位置")
        act_locate.setEnabled(bool(path) and Path(path).exists())

        chosen = menu.exec(self.external_table.viewport().mapToGlobal(pos))
        if chosen == act_play and path:
            try:
                self._client.play_file(path)
            except PlayerClientError:
                pass
        elif chosen == act_locate and path:
            self._reveal_in_explorer(path)

    # ── 辅助方法 ──────────────────────────────────────────

    def _selected_matched_rows(self) -> list[dict]:
        rows: list[dict] = []
        for idx in self.matched_table.selectionModel().selectedRows():
            row = self.matched_model.row_at(idx.row())
            if row:
                rows.append(row)
        return rows

    def _add_to_playlist(self, rows: list[dict]) -> None:
        track_ids = [str(r.get("track_id", "")) for r in rows if r.get("track_id")]
        if not track_ids:
            return
        playlists = [p for p in self.facade.list_playlists()
                     if str(p.get("playlist_id", "")) != FAVORITES_PLAYLIST_ID]
        if not playlists:
            QMessageBox.information(self, "加到歌单", "暂无歌单，请先在歌单管理中创建。")
            return
        menu = QMenu(self)
        action_map: dict = {}
        for pl in playlists:
            act = menu.addAction(str(pl.get("name", "")))
            action_map[act] = str(pl.get("playlist_id", ""))
        from PySide6.QtGui import QCursor
        chosen = menu.exec(QCursor.pos())
        if chosen and chosen in action_map:
            self.facade.add_tracks_to_playlist(action_map[chosen], track_ids)
            self.library_changed.emit()

    def _play_in_player(self, rows: list[dict]) -> None:
        for row in rows:
            rel = str(row.get("storage_relpath", "") or "").strip()
            if not rel:
                continue
            path = self.facade.library_root / rel
            if path.exists():
                try:
                    self._client.play_file(str(path))
                except PlayerClientError:
                    pass
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
        logger.info("[PlayerLink] 清理工作线程")
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None
