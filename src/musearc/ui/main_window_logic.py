from __future__ import annotations

"""主窗口行为逻辑层。

此文件只放“行为”，不放页面细节：
- 菜单与快捷键
- 历史撤销/重做
- 跨页面刷新
- 程序关闭时资源回收
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QCloseEvent, QKeyEvent, QKeySequence
from PySide6.QtWidgets import QAbstractSpinBox, QFileDialog, QLineEdit, QListWidgetItem, QMessageBox, QPlainTextEdit, QTextEdit

from musearc.app.facade import MuseArcFacade
from musearc.ui.id3_update_window import Id3MetadataUpdateWindow
from musearc.ui.lrclib_window import LrcLibFetchWindow
from musearc.ui.main_window_components import _apply_button_scale, _history_action_label


def _safe_int(value, default: int = 0) -> int:
    """安全地将输入值转换为整数。如果输入是集合类型（如列表、元组、集合或字典）或转换失败，则返回默认值。
    
    参数：
    value -- 需要转换为整数的输入值（可以是任意类型）
    default -- 转换失败时的默认整数值，默认为 0
    
    返回值：
    整数 -- 转换成功后的整数，或转换失败时的默认值
    """
    if isinstance(value, (list, tuple, set, dict)):  # 检查输入是否为集合类型，以避免尝试转换为整数时出错
        return default
    try:
        return int(value or 0)  # 尝试将值转换为整数，如果值为假值（如None、False或0）则使用0进行转换
    except Exception:  # 捕获所有可能的转换异常，例如非数字字符串无法转换为整数
        return default


class MainWindowLogicMixin:
    def _release_player_for_file_ops(self) -> None:
        """
        释放与文件操作相关的播放器资源。

        此方法尝试调用self.release_player_for_file_ops（如果存在且可调用）以释放相关资源。

        参数：
            无额外参数。

        返回：
            无。
        """
        handler = getattr(self, "release_player_for_file_ops", None)  # 尝试获取release_player_for_file_ops属性，如果不存在则返回None
        if callable(handler):  # 如果handler是可调用的
            handler()  # 调用handler方法

    def _build_menu(self) -> None:
        """
        构建应用程序的菜单栏。
        该方法创建主菜单（文件、页面、更多）并向其中添加各种功能动作。
        """
        # 创建“文件”菜单
        menu_file = self.menuBar().addMenu("文件")
        # 创建“打开音乐库”动作
        action_open = QAction("打开音乐库", self)
        # 将动作的触发信号连接到打开音乐库的方法
        action_open.triggered.connect(self._open_library)
        # 将动作添加到“文件”菜单
        menu_file.addAction(action_open)
        # 创建“保存当前更改”动作
        action_save = QAction("保存当前更改", self)
        # 为动作设置快捷键（通常是 Ctrl+S）
        action_save.setShortcut(QKeySequence.StandardKey.Save)
        # 将动作的触发信号连接到立即保存的方法
        action_save.triggered.connect(self._save_now)
        # 将动作添加到“文件”菜单
        menu_file.addAction(action_save)

        # 创建“页面”菜单
        menu_view = self.menuBar().addMenu("页面")
        # 创建“刷新当前页面”动作
        action_refresh = QAction("刷新当前页面", self)
        # 将动作的触发信号连接到刷新当前页面的方法
        action_refresh.triggered.connect(self._refresh_current_page)
        # 将动作添加到“页面”菜单
        menu_view.addAction(action_refresh)

        # 创建“更多”菜单
        menu_more = self.menuBar().addMenu("更多")
        # 创建“补全歌词”动作
        action_lrclib = QAction("补全歌词", self)
        # 将动作的触发信号连接到打开歌词补全窗口的方法
        action_lrclib.triggered.connect(self._open_lrclib_window)
        # 将动作添加到“更多”菜单
        menu_more.addAction(action_lrclib)
        # 创建“使用ID3和歌词更新歌曲元信息”动作
        action_id3_update = QAction("使用ID3和歌词更新歌曲元信息", self)
        # 将动作的触发信号连接到打开ID3更新窗口的方法
        action_id3_update.triggered.connect(self._open_id3_update_window)
        # 将动作添加到“更多”菜单
        menu_more.addAction(action_id3_update)
        # 创建“用歌词语言信息更新歌曲语言”动作
        action_sync_lang = QAction("用歌词语言信息更新歌曲语言", self)
        # 将动作的触发信号连接到从歌词同步歌曲语言的方法
        action_sync_lang.triggered.connect(self._sync_track_language_from_lyrics)
        # 将动作添加到“更多”菜单
        menu_more.addAction(action_sync_lang)

    def _open_lrclib_window(self) -> None:
        """打开LrcLibFetchWindow窗口并管理其生命周期。

        该方法创建一个新的LrcLibFetchWindow实例，设置窗口在关闭时自动删除，
        显示窗口，并将其添加到工具窗口列表中。同时连接destroyed信号以在窗口销毁时从列表中移除。

        参数：
            无（仅self引用）。

        返回值：
            无。
        """
        window = LrcLibFetchWindow(self.facade)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)  # 设置窗口在关闭时自动删除，以避免内存泄漏
        window.show()  # 显示窗口
        self._tool_windows.append(window)  # 将新窗口添加到工具窗口列表
        window.destroyed.connect(  # 连接destroyed信号，当窗口销毁时从工具窗口列表中移除
            lambda *_args, w=window: self._tool_windows.remove(w) if w in self._tool_windows else None
        )

    def _open_id3_update_window(self) -> None:
        window = Id3MetadataUpdateWindow(self.facade)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        window.show()
        self._tool_windows.append(window)
        window.destroyed.connect(
            lambda *_args, w=window: self._tool_windows.remove(w) if w in self._tool_windows else None
        )

    def _sync_track_language_from_lyrics(self) -> None:
        """从歌曲的歌词中同步语言信息到歌曲的元数据。
        此方法会弹出对话框询问用户更新策略，然后调用后端服务执行同步，最后刷新界面并报告结果。
        Args:
            无额外参数。
        Returns:
            None: 此方法不返回任何值。
        """
        # 弹出一个询问对话框，让用户选择更新策略。
        answer = QMessageBox.question(
            self,
            "同步歌曲语言",
            "是否仅更新“unknown”的歌曲语言？\n选择“否”将覆盖已有语言。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        # 如果用户点击了“取消”按钮，则直接结束此方法。
        if answer == QMessageBox.StandardButton.Cancel:
            return
        # 根据用户的选择，确定是否只更新语言标记为“unknown”的歌曲。
        only_unknown = answer == QMessageBox.StandardButton.Yes
        # 调用外观层（facade）的方法执行实际同步，并获取更新的歌曲数量。
        count = int(self.facade.sync_track_language_from_lyrics(only_unknown=only_unknown) or 0)
        # 刷新所有与歌曲信息相关的页面，以显示更新后的数据。
        self._reload_related_pages()
        # 弹出信息框，告知用户同步操作完成了多少首歌曲。
        QMessageBox.information(self, "同步歌曲语言", f"已更新 {count} 首歌曲。")

    def _save_now(self) -> None:
        """
        立即执行保存操作，并在状态栏显示保存成功的提示消息。

        该方法用于触发保存功能，不接受任何参数，也不返回任何值。
        它通过调用外观层（facade）的保存方法来执行实际保存，
        然后在应用程序的状态栏中短暂显示一条确认消息。

        参数:
            无

        返回值:
            None
        """
        # 调用外观层对象的save_now方法来执行实际的保存操作
        self.facade.save_now()
    
        # 在状态栏中显示保存成功的提示消息，持续时间为1800毫秒（1.8秒）
        self.statusBar().showMessage("已保存更改", 1800)

    def _delete_selected_current_page(self) -> None:
        """处理删除当前页面选中项的操作。

        功能：
            该方法用于删除当前页面中被选中的项目。它会先检查当前焦点部件，如果是一个可编辑的控件且非只读，
            则直接返回，避免干扰用户的输入。然后，它会尝试调用当前页面对象上预定义的删除方法来执行删除。

        参数：
            self (object): 实例对象本身。

        返回值：
            None: 该方法没有返回值。
        """
        # 获取当前拥有焦点的部件
        focus = self.focusWidget()
        # 判断焦点部件是否为常见的可编辑控件（如行编辑、文本编辑器、数字输入框等）
        if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
            # 如果该控件具有`isReadOnly`方法，并且它不是只读状态，则认为用户可能正在编辑，因此直接返回，不执行删除
            if hasattr(focus, "isReadOnly") and not bool(focus.isReadOnly()):
                return

        # 获取当前堆栈窗口中显示的页面部件
        page = self.stack.currentWidget()
        # 如果没有当前页面，则直接返回
        if page is None:
            return

        # 释放可能与文件操作相关的播放器资源
        self._release_player_for_file_ops()

        # 定义一个方法名列表，这些是页面对象可能具有的删除相关方法
        for method_name in (
            "on_delete",
            "on_delete_from_library",
            "_on_delete_from_library",
            "_delete_selected_lyrics",
        ):
            # 尝试从当前页面对象获取指定名称的方法，如果不存在则返回None
            fn = getattr(page, method_name, None)
            # 如果获取到的方法是可调用的，则调用它并立即返回，因为只需要一个删除方法生效
            if callable(fn):
                fn()
                return

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            page = self.stack.currentWidget() if hasattr(self, "stack") else None
            clear_fn = getattr(page, "clear_search_with_undo", None) if page is not None else None
            if callable(clear_fn):
                clear_fn()
                event.accept()
                return
        super().keyPressEvent(event)

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
        """跳转到历史记录中的特定项目。

        此方法用于根据用户在历史记录列表中选择的项目，将应用状态回退或前进到对应的历史时刻。
        它通过执行一系列撤销或重做操作来达到目标历史索引，然后刷新界面以反映新的状态。

        Args:
            item (QListWidgetItem): 用户在历史记录列表中选中的项目，其中存储了目标历史索引。

        Returns:
            None: 此方法无返回值，其作用体现在对应用状态和界面的更新上。
        """
        # 从选中的列表项中安全地获取目标历史索引，若获取失败则默认为-1
        target = _safe_int(item.data(Qt.ItemDataRole.UserRole), -1)
        # 如果目标索引无效（小于0），则直接返回，不执行任何操作
        if target < 0:
            return

        # 从外观（facade）获取操作时间线，限制为最近500条记录
        timeline = self.facade.list_action_timeline(limit=500)
        # 安全地获取当前时间线的索引，若获取失败则默认为-1
        current = _safe_int(timeline.get("current_index", -1), -1)

        # 如果目标索引与当前索引相同，说明无需移动，直接返回
        if target == current:
            return

        # 根据目标索引与当前索引的大小关系，决定执行撤销还是重做操作
        if target < current:
            # 需要向前（即撤销）移动的次数
            for _ in range(current - target):
                # 执行撤销操作，如果返回"no_action"表示没有更多操作可撤销，提前终止循环
                if self.facade.undo_last_action() == "no_action":
                    break
        else:
            # 需要向后（即重做）移动的次数
            for _ in range(target - current):
                # 执行重做操作，如果返回"no_action"表示没有更多操作可重做，提前终止循环
                if self.facade.redo_last_action() == "no_action":
                    break

        # 重新加载所有页面以更新显示
        self._reload_all_pages()
        # 刷新操作历史列表，并选中当前（即新的）操作项目
        self._refresh_action_history(select_current=True)

    def _refresh_action_history(self, select_current: bool = True) -> None:
        """
        刷新操作历史列表。
        根据当前索引高亮显示最新操作，并更新撤销/重做按钮的可用状态。

        参数:
            select_current (bool): 是否在刷新后自动选中当前操作项，默认为True。
        返回:
            None
        """
        timeline = self.facade.list_action_timeline(limit=500)
        history = list(timeline.get("history", []))
        current_index = _safe_int(timeline.get("current_index", -1), -1)

        self.list_history.blockSignals(True)  # 阻止信号，防止在更新列表时触发不必要的事件
        self.list_history.clear()  # 清空现有列表
        for idx, row in enumerate(history):
            action_type = str(row.get("action_type", ""))
            created_at = str(row.get("created_at", ""))[:19].replace("T", " ")  # 截取并格式化时间字符串
            marker = "●" if idx <= current_index else "○"  # 根据索引与当前索引的关系选择标记符号
            text = f"{marker} {_history_action_label(action_type)}  {created_at}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, idx)  # 将索引存储为用户自定义数据
            if idx == current_index:
                item.setBackground(QColor(225, 240, 255))  # 当前项设置浅蓝色背景高亮
            self.list_history.addItem(item)
        self.list_history.blockSignals(False)  # 恢复信号

        self.btn_undo.setEnabled(current_index >= 0)  # 当存在可撤销操作时启用撤销按钮
        self.btn_redo.setEnabled(current_index < len(history) - 1)  # 当存在可重做操作时启用重做按钮

        if select_current and 0 <= current_index < self.list_history.count():  # 如果需要且索引有效，则选中当前操作项
            self.list_history.setCurrentRow(current_index)

    def _ensure_page_dirty_state(self) -> None:
        """确保页面脏状态字典存在并正确初始化。
    
        该方法检查实例是否已存在有效的页面脏状态字典（_page_dirty属性），
        若不存在或不是字典类型，则根据栈的元素数量重新初始化。
    
        Args:
            self: 类实例本身
    
        Returns:
            None: 该方法不返回任何值，仅设置或验证实例状态
        """
        # 检查是否已存在有效的页面脏状态字典
        if hasattr(self, "_page_dirty") and isinstance(self._page_dirty, dict):
            return  # 已存在则直接返回，无需重新初始化
    
        # 获取栈的元素数量，若栈不存在则默认为0
        count = self.stack.count() if hasattr(self, "stack") else 0
    
        # 重新初始化页面脏状态字典：为每个索引创建False值
        self._page_dirty = {idx: False for idx in range(count)}

    def _reload_page_by_index(self, index: int, *, force: bool = False) -> None:
        """
        根据索引重新加载指定页面的内容。
    
        参数:
            index (int): 要重新加载的页面索引，从0开始计数。
            force (bool): 是否强制重新加载，即使页面未被修改过。默认为False。
    
        返回值:
            None: 该方法不返回任何值。
        """
        # 确保页面脏状态已正确初始化
        self._ensure_page_dirty_state()
        # 如果索引为负数，则直接返回，不执行后续操作
        if index < 0:
            return
        # 如果未强制重新加载且页面未被修改过，则直接返回
        if not force and not bool(self._page_dirty.get(index, False)):
            return
        # 根据不同的页面索引，调用相应页面的重新加载方法
        if index == 0:
            # 重新加载曲目页面
            self.page_tracks.reload_tracks_from_db()
        elif index == 1:
            # 重新加载导入历史页面
            self.page_imports.reload_history()
        elif index == 2:
            # 重新加载评论页面
            self.page_review.reload_reviews()
        elif index == 3:
            # 重新加载全盘扫描页面
            self.page_fullscan.reload_works()
        elif index == 4:
            # 重新加载播放列表页面
            self.page_playlist.reload_playlists()
        elif index == 5:
            # 重新加载标签页面
            self.page_tags.reload_tags()
        elif index == 6:
            # 重新加载歌词页面
            self.page_lyrics.reload_lyrics()
        elif index == 7:
            # 刷新播放器链接页面
            self.page_player_link.refresh_page()
        elif index == 8:
            # 重新加载回收站页面
            self.page_trash.reload_trash()
        elif index == 9:
            # 刷新设置页面
            self.page_settings.refresh_page()
        # 重新加载完成后，将页面脏状态重置为False
        self._page_dirty[index] = False

    def _reload_related_pages(self) -> None:
        """重新加载相关页面。

        该方法用于强制刷新除当前页面外的所有相关页面的数据。
        它会先确保页面的脏状态，然后标记非当前页面为需要重新加载，
        最后刷新操作历史但不选择当前页面。

        Args:
            无

        Returns:
            无
        """
        self._ensure_page_dirty_state()  # 确保页面脏状态已正确设置
        current = self.stack.currentIndex()  # 获取当前页面的索引
        for idx in (0, 1, 2, 3, 4, 5, 6, 7, 8):  # 遍历所有相关页面索引
            if idx == current:  # 如果是当前页面
                self._page_dirty[idx] = False  # 标记当前页面为干净（无需重新加载）
                continue
            self._page_dirty[idx] = True  # 标记非当前页面为脏（需要重新加载）
        self._refresh_action_history(select_current=False)  # 刷新操作历史，但不选择当前页面

    def _reload_all_pages(self) -> None:
        self._ensure_page_dirty_state()
        for idx in range(self.stack.count()):
            self._page_dirty[idx] = True
            self._reload_page_by_index(idx, force=True)

    def _on_tags_changed(self) -> None:
        """当标签（tags）发生变更时被调用的回调方法。

        此方法负责刷新相关页面的标签字段显示，并更新页面脏状态，最后刷新操作历史记录。

        Args:
            self: 类实例自身。

        Returns:
            None
        """
        # 遍历包含轨道页面、全扫描页面、播放列表页面和标签页面的列表
        for page in [self.page_tracks, self.page_fullscan, self.page_playlist, self.page_tags]:
            # 获取每个页面的网格（grid）对象，如果不存在则为None
            grid = getattr(page, "grid", None)
            # 检查网格对象是否存在且具有刷新标签字段的方法
            if grid is not None and hasattr(grid, "refresh_tag_fields"):
                # 调用网格的刷新标签字段方法以更新UI显示
                grid.refresh_tag_fields()
        # 确保页面当前处于脏状态（即需要保存的状态）
        self._ensure_page_dirty_state()
        # 获取当前显示页面的索引
        current = self.stack.currentIndex()
        # 遍历需要更新状态的特定页面索引列表
        for idx in (0, 2, 3, 4, 5, 6, 7, 8):
            # 如果遍历到的索引等于当前页面索引
            if idx == current:
                # 标记当前页面为非脏状态（已处理或未修改）
                self._page_dirty[idx] = False
                # 继续处理下一个索引
                continue
            # 对于非当前页面，标记为脏状态（需要刷新或检查）
            self._page_dirty[idx] = True
        # 刷新操作历史记录，但不自动选中当前项（select_current=False）
        self._refresh_action_history(select_current=False)

    def _on_settings_saved(self) -> None:
        """当设置保存时调用，用于应用按钮缩放配置、配置自动保存计时器，并为各个页面设置facade。"""
        self._apply_button_scale_from_config()  # 从配置应用按钮缩放
        self._configure_autosave_timer()  # 配置自动保存计时器
        # 为各个页面对象设置facade
        self.page_tracks.set_facade(self.facade)
        self.page_playlist.set_facade(self.facade)
        self.page_fullscan.set_facade(self.facade)
        self.page_tags.set_facade(self.facade)
        self.page_lyrics.set_facade(self.facade)
        self.page_player_link.set_facade(self.facade)
        self.page_trash.set_facade(self.facade)

    def _apply_button_scale_from_config(self) -> None:
        """
        从配置文件中读取按钮缩放比例，并将其应用到所有相关的界面页面和按钮上。

        此方法首先从运行时配置中获取UI的按钮缩放比例，然后依次将该比例应用到各个功能页面的按钮以及主界面的部分关键按钮。
        它还会检查播放器栏是否存在，如果存在，则将其内部的播放控制按钮也应用缩放比例。

        参数:
            无。

        返回:
            None
        """
        # 从运行时配置中获取按钮缩放比例，并确保将其转换为浮点数类型
        scale = float(self.facade.get_runtime_config().ui.button_scale)
        # 将缩放比例应用到各个功能页面的按钮
        self.page_tracks.apply_button_scale(scale)
        self.page_imports.apply_button_scale(scale)
        self.page_review.apply_button_scale(scale)
        self.page_fullscan.apply_button_scale(scale)
        self.page_playlist.apply_button_scale(scale)
        self.page_tags.apply_button_scale(scale)
        self.page_lyrics.apply_button_scale(scale)
        self.page_player_link.apply_button_scale(scale)
        self.page_trash.apply_button_scale(scale)
        self.page_settings.apply_button_scale(scale)
        # 将缩放比例应用到主界面的撤销和重做按钮
        _apply_button_scale(self.btn_undo, scale)
        _apply_button_scale(self.btn_redo, scale)
        # 检查播放器栏(player_bar)是否已初始化且存在
        if hasattr(self, "player_bar") and self.player_bar is not None:
            # 遍历播放器栏上的播放控制按钮（上一曲、播放、下一曲、关闭），并为它们应用缩放比例
            for btn in [
                self.player_bar.btn_prev,
                self.player_bar.btn_play,
                self.player_bar.btn_next,
                self.player_bar.btn_close,
            ]:
                _apply_button_scale(btn, scale)

    def _on_page_changed(self, index: int) -> None:
        """当页面索引发生变化时调用的方法。
    
        功能：
            1. 确保当前页面的脏状态被记录
            2. 根据新的页面索引重新加载页面
            3. 刷新操作历史记录（不选择当前页面）
    
        参数：
            index (int): 变化后的页面索引
        
        返回值：
            None: 该方法不返回任何值
        """
        # 确保当前页面的脏状态被记录，避免丢失未保存的修改
        self._ensure_page_dirty_state()
        # 根据新的页面索引重新加载页面内容
        self._reload_page_by_index(index)
        # 刷新操作历史记录，但不自动选择当前页面
        self._refresh_action_history(select_current=False)

    def _open_library(self) -> None:
        """打开音乐库文件夹，初始化facade，并更新所有页面和配置。
    
        功能：
            弹出文件夹选择对话框让用户选择音乐库路径。
            如果选择了有效路径，创建MuseArcFacade对象。
            将facade设置到所有页面，并执行其他初始化操作。
    
        参数：
            无。
    
        返回值：
            无。
        """
        folder = QFileDialog.getExistingDirectory(self, "选择音乐库路径")  # 使用Qt文件对话框获取音乐库文件夹路径
        if not folder:  # 如果用户取消选择或未选择，folder为空字符串
            return  # 直接返回，不执行后续操作
        self.facade = MuseArcFacade(str(Path(folder).resolve()))  # 使用pathlib解析绝对路径并创建MuseArcFacade实例

        # 为所有页面设置facade，以共享同一个MuseArcFacade实例
        self.page_tracks.set_facade(self.facade)
        self.page_imports.set_facade(self.facade)
        self.page_review.set_facade(self.facade)
        self.page_fullscan.set_facade(self.facade)
        self.page_playlist.set_facade(self.facade)
        self.page_tags.set_facade(self.facade)
        self.page_lyrics.set_facade(self.facade)
        self.page_trash.set_facade(self.facade)
        self.page_settings.set_facade(self.facade)

        # 执行后续初始化和配置操作
        self._apply_button_scale_from_config()  # 应用配置中的按钮缩放设置
        self._configure_autosave_timer()  # 配置自动保存定时器
        self._reload_all_pages()  # 重新加载所有页面以更新数据
        self._refresh_action_history()  # 刷新操作历史记录

    def closeEvent(self, event: QCloseEvent) -> None:
        """
        窗口关闭事件处理函数。
    
        当主窗口关闭时，确保所有后台任务被安全终止，并执行清理操作。
    
        参数:
            self: 类实例
            event (QCloseEvent): 窗口关闭事件对象，可用于控制关闭流程
    
        返回值:
            None
        """
        # 检查是否存在导入页面且是否有正在运行的导入任务
        if hasattr(self, "page_imports") and self.page_imports.has_running_import():
            # 在状态栏显示提示信息
            self.statusBar().showMessage("正在停止导入任务，请稍候…", 3000)
        
            # 尝试在指定时间内终止运行中的导入任务
            if not self.page_imports.shutdown_running_import(timeout_ms=20000):
                # 如果终止失败，显示警告并阻止窗口关闭
                QMessageBox.warning(self, "仍在处理", "导入线程仍在运行，请稍后再退出。")
                event.ignore()  # 忽略关闭事件，保持窗口打开
                return
    
        # 检查是否存在播放器栏且播放器栏实例有效
        if hasattr(self, "player_bar") and self.player_bar is not None:
            try:
                # 尝试停止播放并隐藏播放器栏
                self.player_bar.stop_and_hide()
            except Exception:
                # 忽略任何可能的异常，避免影响窗口关闭流程
                pass
    
        # 调用父类的closeEvent方法，完成标准的关闭处理
        super().closeEvent(event)

