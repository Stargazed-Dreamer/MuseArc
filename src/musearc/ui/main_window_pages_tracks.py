from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from musearc.app.facade import FAVORITES_PLAYLIST_ID, MuseArcFacade
from musearc.ui.long_task import make_chunked_task, run_modal_task
from musearc.ui.main_window_helpers import (
    _apply_button_scale,
    _choose_or_create_playlist,
    _clear_line_edit_with_undo,
    _handle_track_lyrics_cell_action,
    _install_inline_clear_button,
    _install_row_function_shortcuts,
    _prompt_new_playlist,
    _resolve_delete_mode_and_maybe_save_default,
    _reveal_in_file_manager,
    _run_export_dialog,
    _show_track_details,
    _storage_path_for_track_row,
)
from musearc.ui.main_window_pages_common import _queue_play_tracks, _release_player_for_file_ops
from musearc.ui.track_grid import TrackGridWidget, _copy_selected_cells

logger = logging.getLogger(__name__)


# ?????
# 1) TracksPage ??????????????
# 2) PlaylistPage ????? + ????????
# 3) ???? TrackGridWidget?????????????????


def _run_chunked_ids_modal(
    parent: QWidget,
    *,
    title: str,
    message: str,
    ids: list[str],
    step,
    chunk_size: int = 512,
) -> tuple[dict, bool]:
    """运行一个分块处理ID列表的任务，并通过模态对话框展示进度和状态。

    Args:
        parent: 对话框的父窗口部件。
        title: 对话框的标题。
        message: 对话框中显示的任务描述信息。
        ids: 需要处理的ID列表。
        step: 用于处理每个ID块的回调函数或方法。
        chunk_size: 每次处理的ID数量，默认为512。

    Returns:
        tuple[dict, bool]: 一个元组。第一个元素是包含任务结果信息的字典（包含'processed'和'affected'计数）；第二个元素是布尔值，指示任务是否被用户取消。
    """
    # 创建一个分块任务，将大的ID列表拆分为多个小块来处理
    task = make_chunked_task(ids, chunk_size=chunk_size, message=message, step=step)
    # 在模态对话框中运行创建的任务，并获取其结果状态
    outcome = run_modal_task(parent, title, task)
    # 如果任务执行过程中发生错误，则向上层抛出该异常
    if outcome.error is not None:
        raise outcome.error
    # 确保结果是一个字典，如果不是（例如为None），则构造一个默认的结果字典
    result = outcome.result if isinstance(outcome.result, dict) else {"processed": 0, "affected": 0, "cancelled": outcome.cancelled}
    # 返回结果字典和任务是否被取消的布尔状态
    return result, bool(outcome.cancelled)

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
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self.apply_search_filter)
        _install_inline_clear_button(self.search_input, on_cleared=self.apply_search_filter)

        row2 = QHBoxLayout()
        self.btn_play = QPushButton("播放")
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
        self.search_input.textChanged.connect(self._on_search_text_changed)
        self.btn_play.clicked.connect(self.on_play)
        self.btn_export.clicked.connect(self.on_export)
        self.btn_add_playlist.clicked.connect(self.on_add_to_playlist)
        self.btn_favorite.clicked.connect(self.on_favorite)
        self.btn_unfavorite.clicked.connect(self.on_unfavorite)
        self.btn_delete.clicked.connect(self.on_delete)
        self.grid.track_field_edited.connect(self.on_track_field_edited)
        self.grid.context_menu_requested.connect(self._show_context_menu)
        _install_row_function_shortcuts(
            self,
            [
                self.btn_play,
                self.btn_export,
                self.btn_add_playlist,
                self.btn_favorite,
                self.btn_unfavorite,
                self.btn_delete,
            ],
            start_f=3,
        )

        self.reload_tracks_from_db()

    def apply_button_scale(self, scale: float) -> None:
        """
        应用按钮缩放到指定的按钮列表和网格。

        参数:
            scale (float): 缩放比例。

        返回值:
            None
        """
        # 遍历所有按钮并应用缩放比例
        for btn in [
            self.btn_search,
            self.btn_play,
            self.btn_export,
            self.btn_add_playlist,
            self.btn_favorite,
            self.btn_unfavorite,
            self.btn_delete,
        ]:
            # 应用缩放到单个按钮
            _apply_button_scale(btn, scale)
        # 设置网格的按钮缩放比例
        self.grid.set_button_scale(scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade
        self.grid.set_facade(facade)
        self.reload_tracks_from_db()

    def refresh_page(self) -> None:
        self.reload_tracks_from_db()

    def reload_tracks_from_db(self) -> None:
        """从数据库重新加载所有轨道记录。无参数，返回None。"""
        self.all_rows = self.facade.list_tracks(limit=2_000_000)  # 从外观层获取最多200万条轨道记录并存储到all_rows
        self.apply_search_filter()  # 应用搜索过滤条件

    def _is_realtime_search_enabled(self) -> bool:
        """
        检查实时搜索是否启用。

        参数:
            无

        返回:
            bool: 如果实时搜索启用则返回 True，否则返回 False。
        """
        cfg = self.facade.get_runtime_config()  # 获取运行时配置
        return bool(getattr(cfg.ui, "realtime_search_enabled", True))  # 从配置的UI部分获取实时搜索启用状态，如果属性不存在则默认为True，并转换为布尔值

    def _on_search_text_changed(self, _text: str) -> None:
        """当搜索文本改变时调用。如果实时搜索已启用，则启动搜索计时器。
        参数：
            _text (str): 搜索文本。
        返回值：
            None
        """
        if not self._is_realtime_search_enabled():  # 检查实时搜索是否未启用
            return  # 如果未启用，直接返回
        self._search_timer.start()  # 启动搜索计时器

    def clear_search_with_undo(self) -> None:
        """
        清除搜索输入框的内容，支持撤销操作。
        如果启用了实时搜索，则启动搜索计时器；否则，直接应用搜索过滤器。
        参数：无。
        返回值：无。
        """
        _clear_line_edit_with_undo(self.search_input)  # 清除搜索输入框内容
        if self._is_realtime_search_enabled():  # 检查是否启用实时搜索
            self._search_timer.start()  # 启动搜索计时器，延迟执行搜索
        else:
            self.apply_search_filter()  # 直接应用搜索过滤器

    def apply_search_filter(self) -> None:
        """执行搜索过滤，根据输入框内容筛选显示的项目行。

        功能：获取搜索框的文本，过滤 `self.all_rows` 中匹配的行，并更新网格显示内容。
        参数：无额外参数，使用实例属性 self.search_input 和 self.all_rows。
        返回值：无 (None)。
        """
        # 获取搜索文本，去除首尾空白并转换为小写，以便进行不区分大小写的比较
        query = self.search_input.text().strip().casefold()

        # 如果查询字符串为空，则显示所有行
        if not query:
            rows = list(self.all_rows)
        else:
            # 查询不为空，遍历所有行进行过滤
            rows = []
            for row in self.all_rows:
                # 将该行多个关键字段的值连接成一个字符串，用于搜索
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
                ).casefold()  # 将连接后的字符串也转为小写
                # 如果查询字符串出现在该行的拼接文本中，则保留该行
                if query in text:
                    rows.append(row)

        # 更新网格控件显示筛选后的行数据
        self.grid.set_tracks(rows)
        # 在状态栏显示当前加载的行数和总源数据行数
        self.grid.set_status(f"已加载 {len(rows)} 条（源数据 {len(self.all_rows)} 条）")

    def selected_track_ids(self) -> list[str]:
        return self.grid.selected_track_ids()

    def _export_track_ids(self, track_ids: list[str], tracks: list[dict] | None = None) -> None:
        """导出指定歌曲ID列表对应的歌曲信息。

        功能：
            根据提供的歌曲ID列表，将对应的歌曲数据（如从播放列表中获取或从所有曲目中筛选）导出到用户选择的目标位置（如文件）。
            如果未提供具体的歌曲列表数据，则根据ID从所有曲目中筛选。

        参数：
            track_ids (list[str]): 需要导出的歌曲ID列表。
            tracks (list[dict] | None): 可选的、与ID对应的歌曲信息字典列表。默认为None。

        返回值：
            None: 此方法没有返回值。
        """
        if not track_ids:
            # 如果没有选择任何歌曲ID，则弹出警告对话框并提前返回
            QMessageBox.warning(self, "导出", "请先选择歌曲")
            return

        # 确保 track_rows 是一个列表；如果传入的 tracks 为 None，则使用空列表
        track_rows = list(tracks or [])

        if not track_rows:
            # 如果没有提供具体的歌曲数据（track_rows为空），则根据 track_ids 从所有曲目中筛选
            id_set = set(track_ids)  # 转换为集合以提高查找效率
            # 从所有曲目中筛选出 track_id 在 id_set 中的行
            track_rows = [row for row in self.all_rows if str(row.get("track_id", "")) in id_set]

        # 调用导出对话框，获取用户确认和目标路径
        ok, target = _run_export_dialog(self, self.facade, track_rows, playlist_name="全部歌曲")
        if not ok:
            # 如果用户在对话框中取消了操作，则直接返回
            return

        # 导出成功，更新界面状态栏信息，显示导出数量和目标位置
        self.grid.set_status(f"已导出 {len(track_ids)} 条到 {target}")

    def on_export(self) -> None:
        tracks = self.grid.selected_tracks()
        self._export_track_ids(
            [str(t.get("track_id", "")) for t in tracks if t.get("track_id")],
            tracks,
        )

    def on_play(self) -> None:
        tracks = self.grid.selected_tracks()
        if not tracks:
            return
        _queue_play_tracks(self, tracks)

    def _delete_track_ids(self, track_ids: list[str]) -> None:
        """
        根据给定的音轨ID列表，将对应的音轨移动到回收站。

        该方法处理删除音轨的完整流程，包括：验证输入、释放播放器资源、
        确认删除模式、分块执行删除操作、更新界面状态和触发状态变化信号。

        参数:
            track_ids (list[str]): 需要删除的音轨ID列表。

        返回值:
            None: 该方法不返回任何值，但会修改实例状态并更新界面。
        """
        if not track_ids:
            return  # 如果传入的音轨ID列表为空，则直接返回，不执行任何操作。

        _release_player_for_file_ops(self)  # 释放播放器资源，以便进行文件操作。

        # 确定删除模式，如果有多个音轨或默认模式已保存，则可能显示对话框让用户选择。
        mode = _resolve_delete_mode_and_maybe_save_default(self, self.facade, len(track_ids), track_ids)
        if mode == "cancel":
            return  # 如果用户选择取消操作，则直接返回。

        try:
            # 以模态对话框的形式分块执行删除操作，每块最多处理256个音轨。
            result, cancelled = _run_chunked_ids_modal(
                self,
                title="移到回收站",  # 对话框标题。
                message="正在移到回收站",  # 对话框中的处理信息。
                ids=track_ids,  # 待删除的音轨ID列表。
                step=lambda chunk: self.facade.delete_tracks(chunk, mode=mode),  # 每个分块的删除操作。
                chunk_size=256,  # 每个分块的大小。
            )
        except Exception as exc:
            # 如果删除过程中发生异常，显示警告信息并返回。
            QMessageBox.warning(self, "操作失败", f"移到回收站失败\n{exc}")
            return

        # 获取实际受影响的音轨数量，处理可能为None或0的情况。
        count = int(result.get("affected", 0) or 0)

        # 将已删除的音轨ID转换为集合，以便快速查找。
        removed = set(track_ids)

        # 更新本地音轨列表，移除所有已删除的音轨。
        # 通过比较音轨ID（转换为字符串）来过滤掉已删除的行。
        self.all_rows = [row for row in self.all_rows if str(row.get("track_id", "")) not in removed]

        self.apply_search_filter()  # 重新应用搜索过滤器，更新界面显示。

        # 更新状态栏信息，显示已删除的数量，并指示是否操作被部分取消。
        self.grid.set_status(f"已移到回收站 {count} 条" + ("（已取消）" if cancelled else ""))

        self.library_changed.emit()  # 发出库状态变化信号，通知其他部分进行更新。

    def on_delete(self) -> None:
        self._delete_track_ids(self.selected_track_ids())

    def _add_track_ids_to_playlist(self, track_ids: list[str], playlist_id: str) -> None:
        """将给定的曲目ID列表添加到指定的歌单中。

        Args:
            track_ids (list[str]): 要添加的曲目ID列表。
            playlist_id (str): 目标歌单的ID。

        Returns:
            None
        """
        if not track_ids or not playlist_id:  # 如果曲目ID列表或歌单ID为空，则直接返回
            return
        try:  # 尝试分块添加曲目到歌单
            result, cancelled = _run_chunked_ids_modal(  # 调用分块模态对话框函数
                self,
                title="加到歌单",
                message="正在写入歌单",
                ids=track_ids,
                step=lambda chunk: self.facade.add_tracks_to_playlist(playlist_id, chunk),  # 分块添加曲目
                chunk_size=512,  # 每个块的大小为512
            )
        except Exception as exc:  # 捕获异常并显示警告消息
            QMessageBox.warning(self, "操作失败", f"加到歌单失败\n{exc}")
            return
        count = int(result.get("affected", 0) or 0)  # 获取受影响的曲目数
        self.grid.set_status(f"已添加 {count} 条到歌单" + ("（已取消）" if cancelled else ""))  # 更新状态显示
        self.reload_tracks_from_db()  # 重新加载曲目列表
        self.library_changed.emit()  # 发射库变化信号

    def on_add_to_playlist(self) -> None:
        """将选中的歌曲添加到一个新的或已有的播放列表中。

        此方法用于处理“添加到播放列表”的用户操作。它会先获取用户界面上选中的歌曲，
        然后弹出界面让用户选择或创建一个目标播放列表，最后将选中的歌曲添加进去。

        Args:
            self: 类实例，代表当前视图或窗口。

        Returns:
            None: 此方法不返回任何值，其作用是执行UI操作和数据更新。
        """
        # 获取当前界面上用户选中的所有歌曲ID
        track_ids = self.selected_track_ids()
        # 如果没有选中任何歌曲（列表为空），则直接返回，不执行后续操作
        if not track_ids:
            return
        # 弹出界面，让用户选择一个已有的播放列表或创建一个新的，并返回其ID
        playlist_id = _choose_or_create_playlist(self, self.facade, self.btn_add_playlist)
        # 如果用户取消了选择或创建播放列表（返回的ID为空），则直接返回
        if not playlist_id:
            return
        # 将选中的歌曲ID列表，添加到指定的播放列表中
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
        """当轨道字段被编辑时的回调函数。

        此方法处理UI中对轨道数据字段的编辑操作，包括更新标签、歌词文件名等。
        根据编辑的键名(key)执行不同的更新逻辑，并将更改同步到数据库和内存数据中。

        参数:
            track_id (str): 被编辑轨道的唯一标识符。
            key (str): 被编辑的字段名。
            value: 新字段值，类型取决于具体字段。

        返回:
            None: 此方法没有返回值。
        """
        # 检查轨道ID是否为空，如果为空则记录调试日志并返回，忽略此次编辑
        if not track_id:
            logger.debug("[TracksPage] on_track_field_edited: track_id 为空，忽略")
            return
        # 记录编辑操作的详细信息，包含轨道ID、键名和值
        logger.info("[TracksPage] on_track_field_edited: tid=%s key=%s value=%r", track_id, key, value)
        print(f"[edit] TracksPage 收到: tid={track_id} key={key} value={value!r}")
        # 如果键名是"custom_order"，则忽略，因为自定义排序可能需要特殊处理，此处直接返回
        if key == "custom_order":
            return
        # 如果编辑的是歌词文件名字段
        if key == "lyrics_file_name":
            # 通过轨道ID在网格视图中查找对应的行数据，如果找不到则在所有行数据中搜索
            row = self.grid.track_by_id(track_id) or next(
                (r for r in self.all_rows if str(r.get("track_id", "")) == str(track_id)),
                None,
            )
            # 如果找到了行数据，并且成功处理了歌词单元格操作（可能是弹出对话框或更新文件）
            if row and _handle_track_lyrics_cell_action(self, self.facade, [row]):
                # 使用定时器异步重新从数据库加载轨道数据，确保UI更新在主线程中安全执行
                QTimer.singleShot(0, self.reload_tracks_from_db)
                # 使用定时器异步发射库改变信号，通知其他部分库数据已更新
                QTimer.singleShot(0, self.library_changed.emit)
            # 歌词文件名编辑的处理到此结束，直接返回
            return
        # 尝试执行字段更新操作
        try:
            # 如果键名以"tag:"开头，表示编辑的是自定义标签
            if key.startswith("tag:"):
                # 从键名中提取标签名称（例如"tag:风格" -> "风格"）
                tag_name = key.split(":", 1)[1]
                # 记录调用facade方法更新标签值的日志
                logger.info("[TracksPage] 调用 facade.update_track_tag_values: tid=%s tag=%s val=%r", track_id, tag_name, value)
                # 调用facade方法更新指定轨道的标签值
                self.facade.update_track_tag_values([track_id], tag_name, str(value))
            else:
                # 对于非标签字段，记录调用facade方法更新普通字段的日志
                logger.info("[TracksPage] 调用 facade.update_tracks_fields: tid=%s key=%s val=%r", track_id, key, value)
                # 调用facade方法更新指定轨道的普通字段
                self.facade.update_tracks_fields([track_id], {key: value})
            # 记录编辑成功的信息日志和控制台输出
            logger.info("[TracksPage] 编辑成功: tid=%s key=%s", track_id, key)
            print(f"[edit] TracksPage 成功: tid={track_id} key={key}")
        # 捕获可能出现的异常
        except Exception as exc:
            # 记录错误日志和控制台输出
            logger.error("[TracksPage] 编辑失败: tid=%s key=%s exc=%s", track_id, key, exc)
            print(f"[edit] TracksPage 失败: tid={track_id} key={key} exc={exc}")
            # 弹出警告消息框，显示编辑失败信息
            QMessageBox.warning(self, "编辑失败", f"edit: editing failed\n{exc}")
            # 使用定时器异步重新从数据库加载轨道数据，以恢复可能的不一致状态
            QTimer.singleShot(0, self.reload_tracks_from_db)
            # 异常处理完成后返回
            return
        # 更新内存中的所有行数据，以反映刚刚的编辑操作
        for row in self.all_rows:
            # 查找与当前编辑轨道ID匹配的行
            if str(row.get("track_id", "")) == str(track_id):
                # 如果编辑的是标签字段
                if key.startswith("tag:"):
                    # 复制现有标签字典，避免直接修改可能为空的原始数据
                    tags = dict(row.get("tags", {}) or {})
                    # 再次提取标签名称
                    tag_name = key.split(":", 1)[1]
                    # 将值转换为字符串并去除首尾空格
                    text = str(value).strip()
                    # 如果文本非空，则更新或添加标签；如果为空，则从字典中移除该标签
                    if text:
                        tags[tag_name] = text
                    else:
                        tags.pop(tag_name, None)
                    # 更新行数据中的标签字典和对应的键值对
                    row["tags"] = tags
                    row[key] = text
                else:
                    # 对于非标签字段，直接更新行数据中对应的键值对
                    row[key] = value
                # 找到并更新后，跳出循环
                break

    def _show_context_menu(self, pos, tracks: list[dict]) -> None:
        """
        显示上下文菜单。
        功能：在指定位置显示一个上下文菜单，提供对所选轨道的各种操作，如播放、收藏、添加到歌单等。
        参数：
            pos: 上下文菜单显示的位置，通常是一个QPoint对象。
            tracks: 一个列表，包含所选轨道的字典数据。每个字典代表一个轨道，包含track_id等信息。
        返回值：None（该方法不返回任何值）。
        """
        # 从轨道列表中提取所有有效的track_id，转换为字符串
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        # 如果没有有效的track_id，则直接返回
        if not track_ids:
            return

        # 检查是否可以收藏（至少有一个未收藏的轨道）
        can_favorite = any(not bool(t.get("is_favorite")) for t in tracks)
        # 检查是否可以取消收藏（至少有一个已收藏的轨道）
        can_unfavorite = any(bool(t.get("is_favorite")) for t in tracks)

        # 创建上下文菜单
        menu = QMenu(self)
        # 添加播放动作
        action_play = menu.addAction("播放")
        # 添加收藏动作
        action_favorite = menu.addAction("收藏")
        # 添加取消收藏动作
        action_unfavorite = menu.addAction("取消收藏")
        # 根据条件启用或禁用收藏和取消收藏动作
        action_favorite.setEnabled(can_favorite)
        action_unfavorite.setEnabled(can_unfavorite)

        # 创建子菜单"加到歌单"
        submenu_add = menu.addMenu("加到歌单")
        # 用于映射动作到歌单ID的字典
        add_map: dict[QAction, str] = {}
        # 获取所有歌单，排除收藏歌单
        playlists = [p for p in self.facade.list_playlists() if str(p.get("playlist_id", "")) != FAVORITES_PLAYLIST_ID]
        # 为每个歌单添加动作到子菜单
        for row in playlists:
            action = submenu_add.addAction(str(row.get("name", "")))
            add_map[action] = str(row.get("playlist_id", ""))
        # 如果有歌单，添加分隔符
        if playlists:
            submenu_add.addSeparator()
        # 添加新建歌单动作
        action_add_new = submenu_add.addAction("新建歌单...")

        # 添加分隔符
        menu.addSeparator()
        # 添加其他动作
        action_change_lyrics = menu.addAction("更改歌词绑定")
        action_jump_lyrics = menu.addAction("跳转到歌词")
        action_delete = menu.addAction("移到回收站")
        action_export = menu.addAction("导出")
        action_reveal = menu.addAction("使用文件管理器查看")
        action_copy = menu.addAction("复制行数据")
        action_detail = menu.addAction("查看详情")

        # 在指定位置显示菜单并等待用户选择
        chosen = menu.exec(pos)
        # 如果没有选择，则返回
        if not chosen:
            return
        # 处理播放动作
        if chosen == action_play:
            _queue_play_tracks(self, tracks)
            return
        # 处理收藏动作
        if chosen == action_favorite:
            self.on_favorite()
            return
        # 处理取消收藏动作
        if chosen == action_unfavorite:
            self.on_unfavorite()
            return
        # 处理添加到歌单动作（从映射中）
        if chosen in add_map:
            self._add_track_ids_to_playlist(track_ids, add_map[chosen])
            return
        # 处理新建歌单并添加轨道
        if chosen == action_add_new:
            # 提示用户创建新歌单
            playlist_id = _prompt_new_playlist(self, self.facade)
            if playlist_id:
                self._add_track_ids_to_playlist(track_ids, playlist_id)
            return
        # 处理更改歌词绑定动作
        if chosen == action_change_lyrics:
            # 调用歌词处理函数，如果成功，重新加载轨道并发出信号
            if _handle_track_lyrics_cell_action(self, self.facade, tracks, action="change_mapping"):
                QTimer.singleShot(0, self.reload_tracks_from_db)
                QTimer.singleShot(0, self.library_changed.emit)
            return
        # 处理跳转到歌词动作
        if chosen == action_jump_lyrics:
            _handle_track_lyrics_cell_action(self, self.facade, tracks, action="jump_to_lyrics")
            return
        # 处理删除轨道动作
        if chosen == action_delete:
            self._delete_track_ids(track_ids)
            return
        # 处理导出轨道动作
        if chosen == action_export:
            self._export_track_ids(track_ids, tracks)
            return
        # 处理使用文件管理器查看动作
        if chosen == action_reveal:
            # 获取第一个轨道，如果存在
            first = tracks[0] if tracks else {}
            _reveal_in_file_manager(self, _storage_path_for_track_row(self.facade, first))
            return
        # 处理复制行数据动作
        if chosen == action_copy:
            _copy_selected_cells(self.grid.table)
            return
        # 处理查看详情动作
        if chosen == action_detail:
            _show_track_details(self, tracks[0])

class PlaylistPage(QWidget):
    library_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        """
        初始化播放列表管理界面，设置UI元素和事件连接。

        功能：构建播放列表管理界面，包括顶部操作按钮、左侧歌单树和右侧曲目网格，并绑定所有按钮事件。
        参数：
            facade (MuseArcFacade): 提供数据访问的facade对象，用于获取和操作播放列表数据。
        返回值：无（__init__方法不返回值）。
        """
        super().__init__()  # 调用父类构造函数
        self.facade = facade  # 存储facade实例，用于后续数据操作
        self.current_playlist_id: str | None = None  # 当前选中的歌单ID，初始为None
        self.current_rows: list[dict] = []  # 当前歌单的曲目数据列表

        # 创建主垂直布局
        root = QVBoxLayout(self)

        # 顶部水平布局，用于放置歌单操作按钮
        top = QHBoxLayout()
        self.btn_add = QPushButton("新建歌单")  # 新建歌单按钮
        self.btn_del = QPushButton("删除歌单")  # 删除歌单按钮
        self.btn_clear = QPushButton("清空歌单")  # 清空歌单按钮
        self.btn_play_playlist = QPushButton("播放歌单")  # 播放当前歌单按钮
        self.btn_export_playlist = QPushButton("导出选中歌单")  # 导出选中歌单按钮
        # 将按钮添加到顶部布局
        top.addWidget(self.btn_add)
        top.addWidget(self.btn_del)
        top.addWidget(self.btn_clear)
        top.addWidget(self.btn_play_playlist)
        top.addWidget(self.btn_export_playlist)
        top.addStretch(1)  # 添加伸缩项，使按钮靠左对齐

        # 创建分割器，用于左右分栏显示
        splitter = QSplitter()

        # 左侧区域：歌单树控件
        left = QWidget()
        left_layout = QVBoxLayout(left)  # 左侧垂直布局
        self.tree = QTreeWidget()  # 创建树状控件，用于显示歌单列表
        self.tree.setHeaderLabels(["歌单", "曲目数"])  # 设置树状控件的列标题
        self.tree.setAlternatingRowColors(True)  # 启用交替行颜色，提高可读性
        left_layout.addWidget(self.tree)  # 将树状控件添加到左侧布局

        # 右侧区域：曲目操作按钮和网格控件
        right = QWidget()
        right_layout = QVBoxLayout(right)  # 右侧垂直布局
        row = QHBoxLayout()  # 水平布局，用于放置曲目操作按钮
        self.btn_remove_tracks = QPushButton("从本歌单中移除")  # 从当前歌单移除选中曲目按钮
        self.btn_copy_playlist = QPushButton("复制到歌单")  # 复制选中曲目到其他歌单按钮
        self.btn_move_playlist = QPushButton("移动到歌单")  # 移动选中曲目到其他歌单按钮
        self.btn_export = QPushButton("导出")  # 导出选中曲目按钮
        self.btn_favorite = QPushButton("收藏")  # 收藏选中曲目按钮
        self.btn_unfavorite = QPushButton("取消收藏")  # 取消收藏选中曲目按钮
        self.btn_delete = QPushButton("从音乐库中删除")  # 从音乐库中删除选中曲目按钮
        self.btn_delete.setStyleSheet("background-color:#b3261e;color:white;")  # 设置删除按钮的样式，红色背景白色文字
        # 将曲目操作按钮添加到水平布局
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
        row.addStretch(1)  # 添加伸缩项，使按钮靠左对齐

        # 创建曲目网格控件，用于显示和编辑曲目列表
        self.grid = TrackGridWidget(self.facade)

        # 将按钮行和网格控件添加到右侧布局
        right_layout.addLayout(row)
        right_layout.addWidget(self.grid, 1)  # 网格控件占据大部分空间

        # 将左右区域添加到分割器，并设置伸缩因子，使右侧区域更宽
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)  # 左侧区域不伸缩
        splitter.setStretchFactor(1, 1)  # 右侧区域伸缩

        # 将顶部布局和分割器添加到主布局
        root.addLayout(top)
        root.addWidget(splitter, 1)  # 分割器占据大部分空间

        # 绑定按钮点击事件到相应方法
        self.btn_add.clicked.connect(self.add_playlist)  # 新建歌单事件
        self.btn_del.clicked.connect(self.delete_playlist)  # 删除歌单事件
        self.btn_clear.clicked.connect(self.clear_playlist)  # 清空歌单事件
        self.btn_play_playlist.clicked.connect(self.play_current_playlist)  # 播放歌单事件
        self.btn_export_playlist.clicked.connect(self.export_current_playlist)  # 导出歌单事件
        self.btn_remove_tracks.clicked.connect(self.remove_selected_tracks)  # 移除曲目事件
        self.btn_copy_playlist.clicked.connect(self.copy_selected_tracks)  # 复制曲目事件
        self.btn_move_playlist.clicked.connect(self.move_selected_tracks)  # 移动曲目事件
        self.btn_export.clicked.connect(self.on_export)  # 导出曲目事件
        self.btn_favorite.clicked.connect(self.on_favorite)  # 收藏曲目事件
        self.btn_unfavorite.clicked.connect(self.on_unfavorite)  # 取消收藏事件
        self.btn_delete.clicked.connect(self.on_delete_from_library)  # 从音乐库删除事件
        self.tree.currentItemChanged.connect(self.on_playlist_changed)  # 歌单选择变化事件
        self.grid.track_field_edited.connect(self.on_track_field_edited)  # 曲目字段编辑事件
        self.grid.context_menu_requested.connect(self._show_context_menu)  # 网格右键菜单请求事件
        # 安装行功能快捷键，为曲目操作按钮设置快捷键，从F3开始分配
        _install_row_function_shortcuts(
            self,
            [
                self.btn_remove_tracks,
                self.btn_copy_playlist,
                self.btn_move_playlist,
                self.btn_export,
                self.btn_favorite,
                self.btn_unfavorite,
                self.btn_delete,
            ],
            start_f=3,
        )

        # 初始加载歌单列表
        self.reload_playlists()

    def apply_button_scale(self, scale: float) -> None:
        """
        功能：应用按钮缩放，调整指定按钮的缩放比例。
        参数：
            scale (float): 缩放比例，用于控制按钮大小。
        返回值：无
        """
        # 遍历按钮列表，为每个按钮应用缩放
        for btn in [
            self.btn_add,          # 添加按钮
            self.btn_del,          # 删除按钮
            self.btn_clear,        # 清除按钮
            self.btn_play_playlist,# 播放列表按钮
            self.btn_export_playlist,# 导出列表按钮
            self.btn_remove_tracks,# 移除曲目按钮
            self.btn_copy_playlist,# 复制列表按钮
            self.btn_move_playlist,# 移动列表按钮
            self.btn_export,       # 导出按钮
            self.btn_favorite,     # 收藏按钮
            self.btn_unfavorite,   # 取消收藏按钮
            self.btn_delete,       # 删除按钮
        ]:
            _apply_button_scale(btn, scale)  # 应用缩放给当前按钮
        self.grid.set_button_scale(scale)    # 设置网格的按钮缩放比例

    def set_facade(self, facade: MuseArcFacade) -> None:
        """设置外观对象并更新相关状态。

        该方法将传入的外观对象设置给当前实例及其内部网格组件，
        并随后触发播放列表的重新加载以应用新的外观样式。

        Args:
            self: 类实例。
            facade (MuseArcFacade): 需要设置的新外观对象。

        Returns:
            None
        """
        self.facade = facade  # 设置实例的外观属性
        self.grid.set_facade(facade)  # 让内部网格组件也应用此外观
        self.reload_playlists()  # 外观变更后，重新加载播放列表以应用新样式

    def refresh_page(self) -> None:
        self.reload_playlists()
        self.reload_playlist_tracks()

    def reload_playlists(self) -> None:
        """重新加载所有播放列表到树形控件中。

        功能：
        1. 从后端获取所有播放列表数据
        2. 清空当前树形控件并重新填充播放列表项
        3. 保持或重置当前选中的播放列表状态

        参数：
            无（方法通过self访问实例状态）

        返回值：
            无返回值（方法执行完成后树形控件状态已更新）
        """
        # 从门面层获取所有播放列表的原始数据行
        rows = self.facade.list_playlists()
        # 记住当前正在查看的播放列表ID，用于后续恢复选中状态
        keep_id = self.current_playlist_id

        # 清空树形控件中的所有现有项目
        self.tree.clear()
        # 遍历每个播放列表数据行
        for row in rows:
            # 创建新的树形项目，显示播放列表名称和曲目数量
            item = QTreeWidgetItem([str(row.get("name", "")), str(row.get("track_count", 0))])
            # 将播放列表ID存储为项目的用户数据，便于后续识别
            item.setData(0, Qt.ItemDataRole.UserRole, row.get("playlist_id"))
            # 将项目添加到树形控件的顶层
            self.tree.addTopLevelItem(item)

        # 初始化目标项目变量，用于记录要选中的播放列表项
        target = None
        # 如果存在之前选中的播放列表ID，则尝试在树形控件中找到对应项
        if keep_id:
            # 遍历树形控件的所有顶层项目
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                # 比较当前项的用户数据与之前保存的播放列表ID
                if str(item.data(0, Qt.ItemDataRole.UserRole)) == keep_id:
                    target = item
                    break
        # 如果没有找到之前的选中项，但树形控件中有项目，则默认选中第一个项目
        if target is None and self.tree.topLevelItemCount() > 0:
            target = self.tree.topLevelItem(0)

        # 如果找到了要选中的目标项目（无论是之前的还是默认的第一个）
        if target is not None:
            # 在树形控件中设置当前选中项
            self.tree.setCurrentItem(target)
        # 如果树形控件中没有任何播放列表项目
        else:
            # 重置当前播放列表ID和相关数据
            self.current_playlist_id = None
            self.current_rows = []
            # 清空网格视图中的曲目显示
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
        """重新加载当前播放列表的曲目到界面中。

        本方法会根据 `self.current_playlist_id` 的值，决定是从数据源获取最新歌单内容并刷新网格，还是清空当前网格显示。

        参数:
            无参数。

        返回值:
            无返回值。
        """
        if not self.current_playlist_id:  # 如果当前没有选中的播放列表（ID为空或不存在）
            self.current_rows = []  # 清空当前存储的曲目行数据
            self.grid.set_tracks([])  # 清空网格控件的显示
            return  # 提前结束方法，不执行后续加载操作
        rows = self.facade.list_playlist_items(self.current_playlist_id)  # 通过外观层（Facade）获取指定播放列表的所有曲目项
        self.current_rows = rows  # 将获取到的曲目列表保存到实例变量中
        self.grid.set_tracks(rows, entry_editable=True)  # 将曲目列表设置到网格控件，并启用每行的编辑功能
        self.grid.set_status(f"歌单包含 {len(rows)} 首")  # 在网格的状态栏显示曲目总数

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
        """清空当前播放列表。

        功能：
            弹出确认对话框，清空当前选中的播放列表，并更新UI显示。

        参数：
            self: 实例对象本身。

        返回值：
            None
        """
        # 检查是否已选中播放列表
        if not self.current_playlist_id:
            return
        # 弹出确认对话框，询问用户是否清空
        answer = QMessageBox.question(self, "清空歌单", "确定清空当前歌单吗？")
        # 如果用户选择"否"，则直接返回
        if answer != QMessageBox.StandardButton.Yes:
            return

        # 定义后台任务函数，用于实际执行清空操作
        def _task(progress, _is_cancelled):
            # 通知进度开始
            progress(0, 1, "正在清空歌单")
            # 调用门面方法清空播放列表
            count = self.facade.clear_playlist(self.current_playlist_id)
            # 通知进度完成
            progress(1, 1, "正在清空歌单")
            # 返回清空的歌曲数量
            return {"count": int(count or 0)}

        # 以模态对话框形式运行后台任务
        outcome = run_modal_task(self, "清空歌单", _task)
        # 检查任务是否执行出错
        if outcome.error is not None:
            QMessageBox.warning(self, "操作失败", f"清空歌单失败\n{outcome.error}")
            return
        # 从任务结果中提取清空数量
        payload = outcome.result if isinstance(outcome.result, dict) else {}
        count = int(payload.get("count", 0) or 0)
        # 重新加载播放列表中的曲目
        self.reload_playlist_tracks()
        # 重新加载所有播放列表信息
        self.reload_playlists()
        # 在状态栏显示清空的歌曲数量
        self.grid.set_status(f"已清空 {count} 首")
        # 发射信号通知其他组件库已发生变化
        self.library_changed.emit()

    def remove_selected_tracks(self) -> None:
        if not self.current_playlist_id:
            return
        track_ids = self.selected_track_ids()
        if not track_ids:
            return
        try:
            result, cancelled = _run_chunked_ids_modal(
                self,
                title="从歌单移除",
                message="正在从歌单移除",
                ids=track_ids,
                step=lambda chunk: self.facade.remove_tracks_from_playlist(self.current_playlist_id, chunk),
                chunk_size=512,
            )
        except Exception as exc:
            QMessageBox.warning(self, "操作失败", f"从歌单移除失败\n{exc}")
            return
        count = int(result.get("affected", 0) or 0)
        self.reload_playlist_tracks()
        self.reload_playlists()
        self.grid.set_status(f"已移除 {count} 首" + ("（已取消）" if cancelled else ""))
        self.library_changed.emit()

    def _choose_target_playlist(self, anchor: QWidget, *, allow_create: bool = True) -> str | None:
        """
        功能：选择一个目标播放列表，用于在UI界面中指定播放列表操作的目标。
        参数：
            anchor (QWidget): 锚点UI组件，用于定位选择对话框的显示位置。
            allow_create (bool): 是否允许创建新播放列表，默认为True。
        返回值：
            str 或 None: 选择的播放列表ID，如果没有选择或创建则返回None。
        """
        # 如果当前有播放列表ID，则将其加入排除集合，以避免选择自身；否则使用空集合
        exclude = {self.current_playlist_id} if self.current_playlist_id else set()
        # 调用辅助函数来选择或创建播放列表，传递必要的参数
        return _choose_or_create_playlist(self, self.facade, anchor, exclude_ids=exclude, allow_create=allow_create)

    def copy_selected_tracks(self) -> None:
        track_ids = self.selected_track_ids()
        if not track_ids:
            return
        target = self._choose_target_playlist(self.btn_copy_playlist, allow_create=True)
        if not target:
            return
        try:
            result, cancelled = _run_chunked_ids_modal(
                self,
                title="复制到歌单",
                message="正在复制到歌单",
                ids=track_ids,
                step=lambda chunk: self.facade.add_tracks_to_playlist(target, chunk),
                chunk_size=512,
            )
        except Exception as exc:
            QMessageBox.warning(self, "操作失败", f"复制到歌单失败\n{exc}")
            return
        count = int(result.get("affected", 0) or 0)
        self.grid.set_status(f"已复制 {count} 首" + ("（已取消）" if cancelled else ""))
        self.reload_playlists()
        self.library_changed.emit()

    def move_selected_tracks(self) -> None:
        """
        将当前播放列表中选中的曲目移动到另一个播放列表。

        功能：
            1. 获取当前选中的曲目ID。
            2. 让用户选择一个目标播放列表（可新建）。
            3. 分块（每块512个ID）将选中的曲目添加到目标播放列表，并从原播放列表移除。
            4. 操作完成后刷新相关界面，并显示移动结果和状态。

        参数：
            无（使用 self 访问实例状态）

        返回值：
            无 (None)
        """
        # 检查是否已选择当前播放列表，若未选择则直接返回
        if not self.current_playlist_id:
            return

        # 获取当前选中的曲目ID列表
        track_ids = self.selected_track_ids()

        # 若没有选中任何曲目，则直接返回
        if not track_ids:
            return

        # 弹出选择框，让用户选择或创建目标播放列表
        target = self._choose_target_playlist(self.btn_move_playlist, allow_create=True)

        # 若用户未选择目标播放列表，则直接返回
        if not target:
            return

        # 定义移动操作的内部步骤函数，用于分块处理
        def _move_step(chunk: list[str]) -> int:
            # 将分块中的曲目添加到目标播放列表，并获取成功添加的数量
            added = int(self.facade.add_tracks_to_playlist(target, chunk) or 0)
            # 从原播放列表中移除已添加的曲目
            self.facade.remove_tracks_from_playlist(self.current_playlist_id, chunk)
            return added

        try:
            # 使用模态窗口执行分块移动操作，并显示进度
            result, cancelled = _run_chunked_ids_modal(
                self,
                title="移动到歌单",
                message="正在移动到歌单",
                ids=track_ids,
                step=_move_step,
                chunk_size=512,  # 每块大小设为512，以适应API或性能限制
            )
        except Exception as exc:
            # 捕获任何异常，弹出警告框提示用户操作失败
            QMessageBox.warning(self, "操作失败", f"移动到歌单失败\n{exc}")
            return

        # 从结果中获取受影响的曲目数量
        moved = int(result.get("affected", 0) or 0)

        # 刷新当前播放列表的曲目显示
        self.reload_playlist_tracks()
        # 刷新所有播放列表的显示（可能包含新创建的播放列表）
        self.reload_playlists()

        # 在界面状态栏显示移动结果，若操作被取消则附加说明
        self.grid.set_status(f"已移动 {moved} 首" + ("（已取消）" if cancelled else ""))

        # 发出信号，通知其他部分（如媒体库）已发生更改
        self.library_changed.emit()

    def on_export(self) -> None:
        """处理导出操作的方法。

        功能：
            1. 获取用户选中的音轨列表
            2. 验证选中音轨的有效性
            3. 打开导出对话框让用户选择导出参数
            4. 执行导出并在成功后更新状态栏显示

        参数：
            self: 对象实例，用于访问类属性和方法

        返回值：
            None (无返回值)
        """
        # 从网格组件获取当前选中的所有音轨
        tracks = self.grid.selected_tracks()

        # 从选中音轨中提取有效的音轨ID，转换为字符串格式
        # 使用列表推导式，过滤掉没有track_id的条目
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]

        # 检查是否有有效的音轨ID被选中，如果没有则直接返回
        if not track_ids:
            return

        # 打开导出对话框，获取用户确认和导出目标路径
        # _run_export_dialog函数返回一个元组：(用户是否确认, 目标路径)
        ok, target = _run_export_dialog(self, self.facade, tracks, playlist_name=self._current_playlist_name())

        # 如果用户取消了对话框或对话框执行失败，则直接返回
        if not ok:
            return

        # 更新网格组件的状态栏，显示导出成功的音轨数量和目标路径
        self.grid.set_status(f"已导出 {len(track_ids)} 条到 {target}")

    def _current_playlist_name(self) -> str:
        """获取当前播放列表的名称。

        参数:
            self: 实例自身。

        返回:
            str: 当前播放列表的名称，如果没有选中项则返回默认值 "playlist"。
        """
        item = self.tree.currentItem()  # 获取当前选中的树节点项
        if item is None:  # 如果没有选中任何项
            return "playlist"  # 返回默认播放列表名称
        return str(item.text(0) or "playlist")  # 提取项的文本（第一列），如果没有则使用默认值

    def export_current_playlist(self) -> None:
        if not self.current_playlist_id:
            return
        tracks = list(self.current_rows)
        if not tracks:
            QMessageBox.information(self, "导出歌单", "当前歌单没有歌曲。")
            return
        ok, target = _run_export_dialog(self, self.facade, tracks, playlist_name=self._current_playlist_name())
        if not ok:
            return
        self.grid.set_status(f"已导出歌单 {len(tracks)} 首到 {target}")

    def play_current_playlist(self) -> None:
        tracks = list(self.current_rows)
        if not tracks:
            QMessageBox.information(self, "播放歌单", "当前歌单没有可播放歌曲。")
            return
        _queue_play_tracks(self, tracks)

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
        """从库中删除选中的曲目。

        该方法处理从当前播放列表中删除选中曲目的操作，包括验证、模式选择、分块删除和状态更新。

        参数：
            无（self为实例引用）。

        返回值：
            None。
        """
        if not self.current_playlist_id:  # 如果当前没有播放列表ID，则直接返回
            return
        track_ids = self.selected_track_ids()  # 获取当前选中的曲目ID列表
        if not track_ids:  # 如果没有选中任何曲目，则直接返回
            return
        _release_player_for_file_ops(self)  # 释放播放器以进行文件操作，避免冲突
        mode = _resolve_delete_mode_and_maybe_save_default(self, self.facade, len(track_ids), track_ids)  # 解析删除模式，并可能保存默认设置
        if mode == "cancel":  # 如果用户选择取消操作，则直接返回
            return
        try:
            result, cancelled = _run_chunked_ids_modal(  # 运行分块删除模态框，处理大批量曲目删除
                self,
                title="移到回收站",
                message="正在移到回收站",
                ids=track_ids,
                step=lambda chunk: self.facade.delete_tracks(chunk, mode=mode),  # 定义每个分块的删除操作
                chunk_size=256,  # 设置分块大小为256个曲目
            )
        except Exception as exc:  # 捕获异常，显示警告消息
            QMessageBox.warning(self, "操作失败", f"移到回收站失败\n{exc}")
            return
        deleted = int(result.get("affected", 0) or 0)  # 获取实际删除的曲目数量
        self.reload_playlist_tracks()  # 刷新当前播放列表的曲目显示
        self.reload_playlists()  # 刷新所有播放列表信息
        self.grid.set_status(f"已移到回收站 {deleted} 条" + ("（已取消）" if cancelled else ""))  # 更新状态栏信息，显示删除结果
        self.library_changed.emit()  # 发出库更改信号，通知其他组件库已更新

    def on_track_field_edited(self, track_id: str, key: str, value) -> None:
        if not track_id:
            return
        logger.info("[PlaylistPage] on_track_field_edited: tid=%s key=%s value=%r", track_id, key, value)
        print(f"[edit] PlaylistPage 收到: tid={track_id} key={key} value={value!r}")
        if key == "custom_order":
            if not self.current_playlist_id:
                return
            try:
                parsed = int(value)
            except Exception:
                return
            try:
                self.facade.update_playlist_entries(self.current_playlist_id, {track_id: parsed})
            except Exception as exc:
                logger.error("[PlaylistPage] 编辑 custom_order 失败: tid=%s exc=%s", track_id, exc)
                QMessageBox.warning(self, "编辑失败", f"edit: editing failed\n{exc}")
                QTimer.singleShot(0, self.reload_playlist_tracks)
                return
            self.grid.select_track_ids([track_id])
            QTimer.singleShot(0, self.library_changed.emit)
            return
        if key == "lyrics_file_name":
            row = self.grid.track_by_id(track_id) or next(
                (r for r in self.current_rows if str(r.get("track_id", "")) == str(track_id)),
                None,
            )
            if row and _handle_track_lyrics_cell_action(self, self.facade, [row]):
                QTimer.singleShot(0, self.reload_playlist_tracks)
                QTimer.singleShot(0, self.library_changed.emit)
            return
        try:
            if key.startswith("tag:"):
                tag_name = key.split(":", 1)[1]
                logger.info("[PlaylistPage] 调用 facade.update_track_tag_values: tid=%s tag=%s val=%r", track_id, tag_name, value)
                self.facade.update_track_tag_values([track_id], tag_name, str(value))
            else:
                logger.info("[PlaylistPage] 调用 facade.update_tracks_fields: tid=%s key=%s val=%r", track_id, key, value)
                self.facade.update_tracks_fields([track_id], {key: value})
            logger.info("[PlaylistPage] 编辑成功: tid=%s key=%s", track_id, key)
            print(f"[edit] PlaylistPage 成功: tid={track_id} key={key}")
        except Exception as exc:
            logger.error("[PlaylistPage] 编辑失败: tid=%s key=%s exc=%s", track_id, key, exc)
            print(f"[edit] PlaylistPage 失败: tid={track_id} key={key} exc={exc}")
            QMessageBox.warning(self, "编辑失败", f"edit: editing failed\n{exc}")
            QTimer.singleShot(0, self.reload_playlist_tracks)
            return
        QTimer.singleShot(0, self.library_changed.emit)

    def _show_context_menu(self, pos, tracks: list[dict]) -> None:
        if not self.current_playlist_id:
            return
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        if not track_ids:
            return
        can_favorite = any(not bool(t.get("is_favorite")) for t in tracks)
        can_unfavorite = any(bool(t.get("is_favorite")) for t in tracks)

        menu = QMenu(self)
        action_play = menu.addAction("播放")
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
        action_change_lyrics = menu.addAction("更改歌词绑定")
        action_jump_lyrics = menu.addAction("跳转到歌词")
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
            _queue_play_tracks(self, tracks)
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
                target = _prompt_new_playlist(self, self.facade)
            if not target:
                return
            if mode in {"add", "add_new", "copy", "copy_new"}:
                try:
                    result, cancelled = _run_chunked_ids_modal(
                        self,
                        title="加到歌单",
                        message="正在写入歌单",
                        ids=track_ids,
                        step=lambda chunk: self.facade.add_tracks_to_playlist(target, chunk),
                        chunk_size=512,
                    )
                except Exception as exc:
                    QMessageBox.warning(self, "操作失败", f"加到歌单失败\n{exc}")
                    return
                count = int(result.get("affected", 0) or 0)
                self.grid.set_status(f"已添加 {count} 首" + ("（已取消）" if cancelled else ""))
                self.reload_playlists()
                self.library_changed.emit()
                return
            if mode in {"move", "move_new"}:
                def _move_step(chunk: list[str]) -> int:
                    added = int(self.facade.add_tracks_to_playlist(target, chunk) or 0)
                    self.facade.remove_tracks_from_playlist(self.current_playlist_id, chunk)
                    return added
                try:
                    result, cancelled = _run_chunked_ids_modal(
                        self,
                        title="移动到歌单",
                        message="正在移动到歌单",
                        ids=track_ids,
                        step=_move_step,
                        chunk_size=512,
                    )
                except Exception as exc:
                    QMessageBox.warning(self, "操作失败", f"移动到歌单失败\n{exc}")
                    return
                count = int(result.get("affected", 0) or 0)
                self.reload_playlist_tracks()
                self.reload_playlists()
                self.grid.set_status(f"已移动 {count} 首" + ("（已取消）" if cancelled else ""))
                self.library_changed.emit()
                return
            return
        if chosen == action_change_lyrics:
            if _handle_track_lyrics_cell_action(self, self.facade, tracks, action="change_mapping"):
                QTimer.singleShot(0, self.reload_playlist_tracks)
                QTimer.singleShot(0, self.library_changed.emit)
            return
        if chosen == action_jump_lyrics:
            _handle_track_lyrics_cell_action(self, self.facade, tracks, action="jump_to_lyrics")
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
            _reveal_in_file_manager(self, _storage_path_for_track_row(self.facade, first))
            return
        if chosen == action_copy_data:
            _copy_selected_cells(self.grid.table)
            return
        if chosen == action_detail:
            _show_track_details(self, tracks[0])


