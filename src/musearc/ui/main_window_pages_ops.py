from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTableView,
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
    _handle_track_lyrics_cell_action,
    _install_row_function_shortcuts,
    _prompt_new_playlist,
    _resolve_delete_mode_and_maybe_save_default,
    _reveal_in_file_manager,
    _run_export_dialog,
    _show_track_details,
    _storage_path_for_track_row,
)
from musearc.ui.main_window_pages_common import _queue_play_tracks, _release_player_for_file_ops
from musearc.ui.table_models import ColumnDef, DictTableModel
from musearc.ui.track_grid import TrackGridWidget, _copy_selected_cells, _install_copy_support

logger = logging.getLogger(__name__)


# ?????
# 1) FullScanPage???????????????????/???
# 2) TrashPage???????????? / ??????
# 3) TagManagementPage????? + ?????????????


def _run_chunked_ids_modal(
    parent: QWidget,
    *,
    title: str,
    message: str,
    ids: list[str],
    step,
    chunk_size: int = 512,
) -> tuple[dict, bool]:
    """
    功能：运行一个分块处理任务的模态对话框，并返回结果和取消状态。

    参数：
        parent (QWidget): 父部件。
        title (str): 对话框的标题。
        message (str): 显示的消息字符串。
        ids (list[str]): 需要处理的ID列表。
        step: 处理步骤（类型未指定）。
        chunk_size (int, optional): 分块大小，默认为512。

    返回值：
        tuple[dict, bool]:
            - dict: 处理结果，包含 'processed'、'affected' 和 'cancelled' 键。
            - bool: 表示任务是否被取消。
    """
    # 创建分块任务，使用给定的ids、分块大小、消息和步骤
    task = make_chunked_task(ids, chunk_size=chunk_size, message=message, step=step)
    # 运行模态任务，传入父部件、标题和任务对象
    outcome = run_modal_task(parent, title, task)
    # 如果任务执行过程中有错误，则抛出异常
    if outcome.error is not None:
        raise outcome.error
    # 处理结果：如果结果是字典则直接使用，否则构造一个默认字典，包含processed、affected和cancelled信息
    result = outcome.result if isinstance(outcome.result, dict) else {"processed": 0, "affected": 0, "cancelled": outcome.cancelled}
    # 返回结果字典和任务是否被取消的布尔值
    return result, bool(outcome.cancelled)

class FullScanPage(QWidget):
    library_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade
        self.current_work_id: str | None = None

        root = QVBoxLayout(self)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("工作"))
        self.combo_work = QComboBox()
        self.combo_work.setMinimumWidth(920)
        self.btn_new_work = QPushButton("新建工作")
        self.btn_delete_work = QPushButton("删除工作")
        row1.addWidget(self.combo_work, 1)
        row1.addWidget(self.btn_new_work)
        row1.addWidget(self.btn_delete_work)

        row2 = QHBoxLayout()
        self.btn_pass = QPushButton("过（从当前工作移除）")
        self.btn_add_playlist = QPushButton("添加到歌单")
        self.btn_favorite = QPushButton("收藏")
        self.btn_unfavorite = QPushButton("取消收藏")
        self.btn_export = QPushButton("导出")
        self.btn_delete = QPushButton("从音乐库中删除")
        self.btn_delete.setStyleSheet("background-color:#b3261e;color:white;")
        for btn in [
            self.btn_pass,
            self.btn_add_playlist,
            self.btn_favorite,
            self.btn_unfavorite,
            self.btn_export,
            self.btn_delete,
        ]:
            row2.addWidget(btn)
        row2.addStretch(1)

        self.grid = TrackGridWidget(self.facade)

        root.addLayout(row1)
        root.addLayout(row2)
        root.addWidget(self.grid, 1)

        self.btn_new_work.clicked.connect(self.create_work)
        self.btn_delete_work.clicked.connect(self.delete_work)
        self.combo_work.currentIndexChanged.connect(self.on_work_changed)
        self.btn_pass.clicked.connect(self.pass_selected)
        self.btn_add_playlist.clicked.connect(self.add_selected_to_playlist)
        self.btn_favorite.clicked.connect(self.on_favorite)
        self.btn_unfavorite.clicked.connect(self.on_unfavorite)
        self.btn_export.clicked.connect(self.on_export)
        self.btn_delete.clicked.connect(self.on_delete)
        self.grid.track_field_edited.connect(self.on_track_field_edited)
        self.grid.context_menu_requested.connect(self._show_context_menu)
        _install_row_function_shortcuts(
            self,
            [
                self.btn_pass,
                self.btn_add_playlist,
                self.btn_favorite,
                self.btn_unfavorite,
                self.btn_export,
                self.btn_delete,
            ],
            start_f=3,
        )

        self.reload_works()

    def apply_button_scale(self, scale: float) -> None:
        """
        根据给定的缩放比例，调整界面中一组按钮的大小。

        本方法会遍历预定义的按钮列表，并应用新的缩放比例。随后，对布局网格（grid）也进行同样的缩放调整。

        参数:
            scale (float): 用于缩放按钮的数值因子。大于1表示放大，小于1（且大于0）表示缩小，1表示不变。

        返回值:
            None: 此方法不返回任何值。
        """
        # 遍历一个包含多个按钮对象的列表
        for btn in [
            self.btn_new_work,
            self.btn_delete_work,
            self.btn_pass,
            self.btn_add_playlist,
            self.btn_favorite,
            self.btn_unfavorite,
            self.btn_export,
            self.btn_delete,
        ]:
            # 依次对列表中的每个按钮应用传入的缩放比例
            _apply_button_scale(btn, scale)
        # 对负责管理这些按钮的网格布局也应用相同的缩放比例
        self.grid.set_button_scale(scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        """设置组件的外观属性并更新关联状态。

        将传入的 `facade` 实例存储在当前对象上，并同步设置其子组件 `grid` 的外观。
        最后，调用 `reload_works` 方法以反映外观变化可能带来的数据刷新需求。

        Args:
            facade (MuseArcFacade): 要设置的新外观实例。
        Returns:
            None: 此方法不返回任何值。
        """
        # 保存传入的 `facade` 实例的引用
        self.facade = facade
        # 将外观实例也传递给内部的 `grid` 组件进行设置
        self.grid.set_facade(facade)
        # 重新加载作品数据，以确保外观和数据同步
        self.reload_works()

    def refresh_page(self) -> None:
        self.reload_works()

    def reload_works(self) -> None:
        """从数据源重新加载所有任务到下拉框，并尝试保持之前的选择状态。

        该方法用于刷新当前任务选择控件，清空旧选项并从后端获取最新的任务列表，
        随后尝试恢复用户之前选中的任务，最后触发一次选择变更事件以更新界面。

        Args:
            self: 实例对象自身。
        Returns:
            None
        """
        # 从门面层获取全量扫描任务列表
        rows = self.facade.list_fullscan_works()
        # 记录当前正在操作的任务ID，以便后续尝试恢复
        keep = self.current_work_id

        # 阻塞下拉框的信号，避免在添加过程中触发不必要的变更事件
        self.combo_work.blockSignals(True)
        self.combo_work.clear()
        for row in rows:
            # 格式化下拉框显示的标签，显示任务名称和待处理/总计项数
            label = f"{row.get('name', '')} (待处理 {row.get('todo_items', 0)} / 总计 {row.get('total_items', 0)})"
            # 将标签和对应的任务ID作为数据添加到下拉框
            self.combo_work.addItem(label, row.get("work_id"))
        # 重新启用信号
        self.combo_work.blockSignals(False)

        # 如果下拉框中没有任何任务
        if self.combo_work.count() == 0:
            # 将当前工作ID设为None
            self.current_work_id = None
            # 清空轨道网格
            self.grid.set_tracks([])
            # 在网格状态栏显示提示信息
            self.grid.set_status("暂无全量筛选工作")
            # 结束方法
            return

        # 默认选择第一个选项（索引0）
        idx = 0
        # 如果之前有选中的任务ID
        if keep:
            # 遍历下拉框的所有项
            for i in range(self.combo_work.count()):
                # 查找与之前任务ID匹配的项
                if str(self.combo_work.itemData(i)) == keep:
                    # 找到则更新索引并停止查找
                    idx = i
                    break
        # 设置下拉框的当前选中索引
        self.combo_work.setCurrentIndex(idx)
        # 手动触发任务变更事件，以更新相关界面
        self.on_work_changed()

    def on_work_changed(self) -> None:
        """
        当工作项目选择发生变化时触发的处理方法。

        功能：
            1. 检查当前选择的工作项目是否有效
            2. 根据选择的工作项目加载对应的扫描工作项
            3. 更新网格显示和状态信息

        参数：
            无（除了self实例引用）

        返回值：
            无
        """
        # 检查组合框当前选择的索引是否小于0（表示未选择任何项目）
        if self.combo_work.currentIndex() < 0:
            # 当前没有选择工作项目，将当前工作ID设为None
            self.current_work_id = None
            # 清空网格中的所有轨道数据
            self.grid.set_tracks([])
            # 提前返回，不执行后续操作
            return

        # 获取当前选择的工作ID，并将其转换为字符串类型存储
        self.current_work_id = str(self.combo_work.currentData())

        # 通过门面模式获取当前工作项目的完整扫描工作项数据，限制最多获取200万条记录
        rows = self.facade.get_fullscan_work_items(self.current_work_id, limit=2_000_000)

        # 将获取到的工作项数据设置到网格控件中显示
        self.grid.set_tracks(rows)

        # 更新网格状态栏，显示当前加载的工作项总数
        self.grid.set_status(f"工作项目 {len(rows)} 条")
    def create_work(self) -> None:
        """
        创建一个新的“全量筛选工作”。
        该方法会弹出一个对话框，让用户选择工作类型（全量、元数据相似、指纹相似），
        设置工作名称和相似度阈值（如适用），确认后通过后台任务创建工作。
        """
        # 创建一个模态对话框作为主界面
        dialog = QDialog(self)
        dialog.setWindowTitle("新建全量筛选工作")
        # 使用垂直布局管理器
        layout = QVBoxLayout(dialog)

        # 创建一个按钮组，用于管理互斥的单选按钮
        group = QButtonGroup(dialog)
        # 创建三个单选按钮选项
        opt_all = QRadioButton("全部歌曲")
        opt_meta = QRadioButton("筛选名称高相似歌曲")
        opt_fp = QRadioButton("按新阈值筛选相似歌曲")
        # 设置默认选中“全部歌曲”
        opt_all.setChecked(True)
        # 将单选按钮添加到按钮组，并为每个分配一个唯一的ID（0， 1， 2）
        group.addButton(opt_all, 0)
        group.addButton(opt_meta, 1)
        group.addButton(opt_fp, 2)
        # 将单选按钮添加到布局中
        layout.addWidget(opt_all)
        layout.addWidget(opt_meta)
        layout.addWidget(opt_fp)

        # 创建工作名称输入行
        row_name = QHBoxLayout()
        row_name.addWidget(QLabel("工作名称"))
        # 创建文本输入框，并设置默认值
        edit_name = QLineEdit("全量歌曲筛选")
        edit_name.setPlaceholderText("请输入工作名称")
        # 将输入框添加到行中，并设置伸展因子使其占据多余空间
        row_name.addWidget(edit_name, 1)
        # 将整行添加到主布局
        layout.addLayout(row_name)

        # 创建相似度阈值区间输入行（仅当选择“指纹相似”时启用）
        row_threshold = QHBoxLayout()
        row_threshold.addWidget(QLabel("相似度区间"))
        # 创建并配置最低阈值输入框
        spin_low = QDoubleSpinBox()
        spin_low.setRange(0.0, 1.0)
        spin_low.setSingleStep(0.01)
        spin_low.setDecimals(3)
        spin_low.setValue(0.88)
        # 创建并配置最高阈值输入框
        spin_high = QDoubleSpinBox()
        spin_high.setRange(0.0, 1.0)
        spin_high.setSingleStep(0.01)
        spin_high.setDecimals(3)
        spin_high.setValue(0.96)
        # 将输入框和分隔符添加到行中
        row_threshold.addWidget(spin_low)
        row_threshold.addWidget(QLabel("~"))
        row_threshold.addWidget(spin_high)
        # 添加伸展项，使输入框靠左
        row_threshold.addStretch(1)
        # 将整行添加到主布局
        layout.addLayout(row_threshold)

        # 创建警告标签（默认隐藏，当阈值过低时显示）
        warn = QLabel("提示：区间过低会包含大量歌曲。")
        warn.setStyleSheet("color:#b3261e;")
        warn.hide()
        layout.addWidget(warn)

        # 定义一个映射，将选项的ID与对应的默认工作名称关联起来
        default_name_map = {
            0: "全量歌曲筛选",
            1: "元数据高相似歌曲",
            2: "指纹高相似歌曲",
        }
        # 用一个字典来标记用户是否手动修改过名称（使用字典以在嵌套函数中修改其值）
        name_touched = {"value": False}
        # 记录上一次自动生成的默认名称
        last_default = {"value": "全量歌曲筛选"}

        # 内部函数：根据当前选中的选项设置默认工作名称
        def _set_default_name() -> None:
            selected_id = group.checkedId()
            default_name = default_name_map.get(selected_id, "全量歌曲筛选")
            current = edit_name.text().strip()
            # 如果用户从未手动修改过名称，或者当前名称仍等于上一个默认名称，则更新为新的默认名称
            if (not name_touched["value"]) or current == last_default["value"]:
                edit_name.setText(default_name)
            # 更新上一个默认名称的记录
            last_default["value"] = default_name

        # 内部函数：当名称输入框的文本发生变化时，标记用户已手动修改
        def _on_name_changed(_text: str) -> None:
            name_touched["value"] = True

        # 连接信号：名称输入框文本变化 -> 更新修改标记
        edit_name.textChanged.connect(_on_name_changed)

        # 内部函数：刷新UI状态（根据选项启用/禁用阈值输入，显示/隐藏警告）
        def _refresh_ui() -> None:
            is_fp = opt_fp.isChecked()
            # 仅当选择“指纹相似”时，启用阈值输入框
            spin_low.setEnabled(is_fp)
            spin_high.setEnabled(is_fp)
            low = float(spin_low.value())
            # 当选择“指纹相似”且最低阈值低于0.60时，显示警告
            warn.setVisible(bool(is_fp and low < 0.60))
            # 同时尝试更新默认工作名称
            _set_default_name()

        # 连接信号：三个单选按钮的切换、最低阈值的变化 -> 刷新UI
        opt_all.toggled.connect(_refresh_ui)
        opt_meta.toggled.connect(_refresh_ui)
        opt_fp.toggled.connect(_refresh_ui)
        spin_low.valueChanged.connect(lambda _v: _refresh_ui())
        # 初始化时手动调用一次以设置正确的UI状态
        _refresh_ui()

        # 创建标准对话框按钮（确定/取消）
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        # 连接按钮信号到对话框的接受/拒绝槽
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        # 最后一次尝试设置默认名称（以防有未覆盖的场景）
        _set_default_name()
        # 显示对话框并等待用户交互，如果用户取消则直接返回
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # 获取用户选择
        selected = group.checkedId()
        # 获取最终的工作名称，如果输入框为空则使用默认值
        name = edit_name.text().strip() or default_name_map.get(selected, "全量歌曲筛选")
        work_id = ""
        # 根据选择的选项，执行相应的工作创建逻辑
        if selected == 0:
            # 选项0：创建全量歌曲筛选工作的后台任务函数
            def _task_all(progress, _is_cancelled):
                progress(0, 1, "正在创建工作")
                wid = self.facade.create_fullscan_work_all(name)
                progress(1, 1, "正在创建工作")
                return {"work_id": wid}
            # 在模态对话框中执行后台任务
            outcome = run_modal_task(self, "创建全量筛选工作", _task_all)
            # 检查任务是否出错
            if outcome.error is not None:
                QMessageBox.warning(self, "创建失败", f"创建工作失败\n{outcome.error}")
                return
            # 安全地获取结果中的work_id
            payload = outcome.result if isinstance(outcome.result, dict) else {}
            work_id = str(payload.get("work_id", "") or "")
        elif selected == 1:
            # 选项1：创建元数据高相似歌曲工作的后台任务函数
            def _task_meta(progress, is_cancelled):
                wid = self.facade.create_fullscan_work_metadata_similar(
                    name,
                    progress_callback=progress,
                    is_cancelled=is_cancelled,
                )
                return {"work_id": wid}
            # 在模态对话框中执行后台任务
            outcome = run_modal_task(self, "创建元数据高相似工作", _task_meta)
            # 检查任务是否出错
            if outcome.error is not None:
                QMessageBox.warning(self, "创建失败", f"创建工作失败\n{outcome.error}")
                return
            # 检查任务是否被用户取消
            if outcome.cancelled:
                self.grid.set_status("创建工作已取消")
                return
            # 安全地获取结果中的work_id
            payload = outcome.result if isinstance(outcome.result, dict) else {}
            work_id = str(payload.get("work_id", "") or "")
        else:
            # 选项2：创建指纹高相似歌曲工作
            lower = float(spin_low.value())
            upper = float(spin_high.value())
            # 如果阈值低于0.60，弹出确认对话框
            if min(lower, upper) < 0.60:
                answer = QMessageBox.question(
                    self,
                    "阈值较低",
                    "当前阈值可能包含大量歌曲，是否继续创建？",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            # 创建指纹高相似歌曲工作的后台任务函数
            def _task_fp(progress, is_cancelled):
                wid = self.facade.create_fullscan_work_fingerprint_similar(
                    min_score=lower,
                    max_score=upper,
                    base_name=name,
                    progress_callback=progress,
                    is_cancelled=is_cancelled,
                )
                return {"work_id": wid}
            # 在模态对话框中执行后台任务
            outcome = run_modal_task(self, "创建指纹高相似工作", _task_fp)
            # 检查任务是否出错
            if outcome.error is not None:
                QMessageBox.warning(self, "创建失败", f"创建工作失败\n{outcome.error}")
                return
            # 检查任务是否被用户取消
            if outcome.cancelled:
                self.grid.set_status("创建工作已取消")
                return
            # 安全地获取结果中的work_id
            payload = outcome.result if isinstance(outcome.result, dict) else {}
            work_id = str(payload.get("work_id", "") or "")
        # 如果没有成功获取到work_id，则提前返回
        if not work_id:
            return
        # 设置当前活动的工作ID
        self.current_work_id = work_id
        # 重新加载工作列表以显示新创建的工作
        self.reload_works()
        # 发出信号，通知库内容已更改
        self.library_changed.emit()

    def delete_work(self) -> None:
        """删除当前选中的工作。此方法会显示确认对话框，如果用户确认，则删除工作并更新界面。参数：无。返回值：无。"""
        if not self.current_work_id:  # 检查是否有当前工作ID，如果没有则不执行删除
            return
        answer = QMessageBox.question(self, "删除工作", "确定删除当前工作吗？")  # 显示确认对话框
        if answer != QMessageBox.StandardButton.Yes:  # 如果用户没有确认，则取消删除
            return
        self.facade.delete_fullscan_work(self.current_work_id)  # 调用facade方法删除当前工作
        self.current_work_id = None  # 重置当前工作ID
        self.reload_works()  # 重新加载工作列表以更新界面
        self.library_changed.emit()  # 发出信号通知库已更改

    def selected_track_ids(self) -> list[str]:
        return self.grid.selected_track_ids()

    def pass_selected(self) -> None:
        """从当前工作中移除所有选中的轨道。

        此方法会检查当前工作和选中的轨道是否存在，然后通过分块操作从后端数据源中批量移除它们，并在操作完成后更新界面状态。

        Args:
            self: 类实例本身。

        Returns:
            None
        """
        # 如果没有当前工作的ID，则直接返回，不执行任何操作
        if not self.current_work_id:
            return

        # 获取所有被选中的轨道ID列表
        track_ids = self.selected_track_ids()

        # 如果没有选中任何轨道，则直接返回
        if not track_ids:
            return

        try:
            # 调用分块处理函数，执行移除操作
            # 参数包括：父窗口(self)、对话框标题、消息、待处理的ID列表、执行移除的lambda函数、每批处理的大小
            result, cancelled = _run_chunked_ids_modal(
                self,
                title="从工作移除",
                message="正在从工作移除",
                ids=track_ids,
                step=lambda chunk: self.facade.remove_fullscan_items(self.current_work_id, chunk),
                chunk_size=512,
            )
        except Exception as exc:
            # 如果过程中发生任何异常，捕获并显示警告对话框
            QMessageBox.warning(self, "操作失败", f"从工作移除失败\n{exc}")
            # 异常后提前返回，不执行后续状态更新
            return

        # 从返回的结果中获取受影响的行数，如果键不存在或值为None则默认为0，并转换为整数
        count = int(result.get("affected", 0) or 0)

        # 调用回调方法，通知相关部件（如数据网格）工作数据已发生变更，以便刷新显示
        self.on_work_changed()

        # 根据操作是否被用户取消，设置状态栏显示不同的完成消息
        self.grid.set_status(f"已从工作移除 {count} 条" + ("（已取消）" if cancelled else ""))

    def add_selected_to_playlist(self) -> None:
        """将当前选中的曲目添加到指定的歌单。

        该方法会获取用户选中的曲目列表，提示用户选择或创建目标歌单，
        然后通过模态对话框分批将曲目添加到歌单中，并更新界面状态。

        Args:
            self: 实例自身，通常是一个播放列表或库管理器的界面类实例。

        Returns:
            None
        """
        track_ids = self.selected_track_ids()  # 获取当前选中的所有曲目ID列表
        if not track_ids:  # 如果没有选中任何曲目，则提前返回
            return
        playlist_id = _choose_or_create_playlist(self, self.facade, self.btn_add_playlist)  # 调用函数让用户选择或创建目标歌单
        if not playlist_id:  # 如果用户未选择或创建歌单（例如取消了操作），则提前返回
            return
        try:
            # 调用分块处理模态对话框，将曲目ID分批添加到歌单
            result, cancelled = _run_chunked_ids_modal(
                self,  # 父窗口实例，用于显示模态对话框
                title="加到歌单",  # 对话框标题
                message="正在写入歌单",  # 对话框中显示的进度信息
                ids=track_ids,  # 需要处理的所有曲目ID列表
                step=lambda chunk: self.facade.add_tracks_to_playlist(playlist_id, chunk),  # 每个分块的处理函数，调用facade将分块添加到歌单
                chunk_size=512,  # 每个分块的大小，设置为512以平衡内存和网络请求
            )
        except Exception as exc:  # 如果添加过程中发生任何异常
            QMessageBox.warning(self, "操作失败", f"加到歌单失败\n{exc}")  # 显示警告对话框，告知用户失败原因
            return  # 发生异常后提前返回
        count = int(result.get("affected", 0) or 0)  # 从结果中获取实际添加的曲目数量，如果结果为空或无affected字段则默认为0
        # 更新状态栏信息，显示添加的曲目数量，并在操作被取消时附加提示
        self.grid.set_status(f"已添加 {count} 条到歌单" + ("（已取消）" if cancelled else ""))
        self.library_changed.emit()  # 发出库已改变的信号，通知其他部分更新（例如刷新UI）

    def on_favorite(self) -> None:
        tracks = self.grid.selected_tracks()
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id") and not bool(t.get("is_favorite"))]
        if not track_ids:
            return
        count = self.facade.add_to_favorites(track_ids)
        self.on_work_changed()
        self.grid.set_status(f"已收藏 {count} 条")
        self.library_changed.emit()

    def on_unfavorite(self) -> None:
        tracks = self.grid.selected_tracks()
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id") and bool(t.get("is_favorite"))]
        if not track_ids:
            return
        count = self.facade.remove_from_favorites(track_ids)
        self.on_work_changed()
        self.grid.set_status(f"已取消收藏 {count} 条")
        self.library_changed.emit()

    def on_export(self) -> None:
        tracks = self.grid.selected_tracks()
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        if not track_ids:
            return
        ok, target = _run_export_dialog(self, self.facade, tracks, playlist_name="全量筛选")
        if not ok:
            return
        self.grid.set_status(f"已导出 {len(track_ids)} 条到 {target}")

    def on_delete(self) -> None:
        """处理删除操作的方法。

        将选中的曲目移动到回收站，支持分块处理。
        功能包括验证操作前提、选择删除模式、执行分块删除、更新界面状态。

        参数:
            self: 实例对象本身，包含当前工作区、选中曲目、界面控件等状态。

        返回值:
            None: 该方法无返回值，通过副作用执行删除操作并更新界面。
        """
        # 检查是否有当前工作区ID，若无则直接返回
        if not self.current_work_id:
            return
        # 获取当前选中的曲目ID列表
        track_ids = self.selected_track_ids()
        # 如果没有选中任何曲目，直接返回
        if not track_ids:
            return
        # 释放播放器资源以进行文件操作
        _release_player_for_file_ops(self)
        # 解析删除模式（如“删除文件”或“仅从列表移除”），并根据用户选择保存为默认模式
        mode = _resolve_delete_mode_and_maybe_save_default(self, self.facade, len(track_ids), track_ids)
        # 如果用户选择取消，则直接返回
        if mode == "cancel":
            return
        # 定义内部函数_step，用于处理单个分块的删除操作
        def _step(chunk: list[str]) -> int:
            # 调用facade删除曲目，返回实际删除数量
            deleted = int(self.facade.delete_tracks(chunk, mode=mode) or 0)
            # 从全扫描结果中移除已删除的曲目
            self.facade.remove_fullscan_items(self.current_work_id, chunk)
            return deleted
        # 尝试执行分块删除操作
        try:
            # 调用_run_chunked_ids_modal显示进度对话框，并执行分块删除
            result, cancelled = _run_chunked_ids_modal(
                self,
                title="移到回收站",
                message="正在移到回收站",
                ids=track_ids,
                step=_step,
                chunk_size=256,  # 设置每个分块的大小为256
            )
        except Exception as exc:
            # 捕获异常并显示警告对话框
            QMessageBox.warning(self, "操作失败", f"移到回收站失败\n{exc}")
            return
        # 从结果中获取受影响的曲目数量
        count = int(result.get("affected", 0) or 0)
        # 触发工作区变化事件以刷新界面
        self.on_work_changed()
        # 更新网格视图状态栏信息，显示删除数量及是否取消
        self.grid.set_status(f"已移到回收站 {count} 条" + ("（已取消）" if cancelled else ""))
        # 发出library_changed信号通知其他组件曲库已更新
        self.library_changed.emit()

    def on_track_field_edited(self, track_id: str, key: str, value) -> None:
        """处理轨道字段编辑事件。

        参数:
            track_id (str): 轨道的唯一标识符。
            key (str): 编辑的字段键。
            value: 字段的新值。

        返回值:
            None
        """
        # 如果轨道ID为空或字段键为"custom_order"，则直接返回，无需处理
        if not track_id or key == "custom_order":
            return
        # 记录编辑事件的日志和打印信息，用于调试和监控
        logger.info("[OpsPage] on_track_field_edited: tid=%s key=%s value=%r", track_id, key, value)
        print(f"[edit] OpsPage 收到: tid={track_id} key={key} value={value!r}")
        # 特殊处理歌词文件名编辑：如果键为"lyrics_file_name"，则触发歌词处理逻辑
        if key == "lyrics_file_name":
            # 通过网格获取对应轨道的行数据
            row = self.grid.track_by_id(track_id)
            # 如果行存在且歌词处理函数执行成功，则设置定时器触发工作变更和库变更信号
            if row and _handle_track_lyrics_cell_action(self, self.facade, [row], action=None):
                QTimer.singleShot(0, self.on_work_changed)
                QTimer.singleShot(0, self.library_changed.emit)
            return
        # 对于其他字段，使用try-except块处理更新操作，以捕获可能的异常
        try:
            # 如果键以"tag:"开头，表示是标签字段，提取标签名称并更新标签值
            if key.startswith("tag:"):
                tag_name = key.split(":", 1)[1]  # 从键中提取标签名，例如"tag:genre"提取为"genre"
                logger.info("[OpsPage] 调用 facade.update_track_tag_values: tid=%s tag=%s val=%r", track_id, tag_name, value)
                self.facade.update_track_tag_values([track_id], tag_name, str(value))
            else:
                # 否则，更新常规轨道字段
                logger.info("[OpsPage] 调用 facade.update_tracks_fields: tid=%s key=%s val=%r", track_id, key, value)
                self.facade.update_tracks_fields([track_id], {key: value})
            # 记录编辑成功的信息
            logger.info("[OpsPage] 编辑成功: tid=%s key=%s", track_id, key)
            print(f"[edit] OpsPage 成功: tid={track_id} key={key}")
        except Exception as exc:
            # 捕获异常，记录错误日志，显示警告，并触发工作变更以恢复状态
            logger.error("[OpsPage] 编辑失败: tid=%s key=%s exc=%s", track_id, key, exc)
            print(f"[edit] OpsPage 失败: tid={track_id} key={key} exc={exc}")
            QMessageBox.warning(self, "编辑失败", f"edit: editing failed\n{exc}")
            QTimer.singleShot(0, self.on_work_changed)
            return
        # 编辑成功后，设置定时器发射库变更信号，通知界面更新
        QTimer.singleShot(0, self.library_changed.emit)

    def _show_context_menu(self, pos, tracks: list[dict]) -> None:
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        if not track_ids:
            return
        can_favorite = any(not bool(t.get("is_favorite")) for t in tracks)
        can_unfavorite = any(bool(t.get("is_favorite")) for t in tracks)

        menu = QMenu(self)
        action_play = menu.addAction("播放")
        action_pass = menu.addAction("从当前工作移除")
        action_favorite = menu.addAction("收藏")
        action_unfavorite = menu.addAction("取消收藏")
        action_favorite.setEnabled(can_favorite)
        action_unfavorite.setEnabled(can_unfavorite)

        submenu_add = menu.addMenu("加到歌单")
        add_map: dict[QAction, str] = {}
        playlists = [p for p in self.facade.list_playlists() if str(p.get("playlist_id", "")) != FAVORITES_PLAYLIST_ID]
        for row in playlists:
            action = submenu_add.addAction(str(row.get("name", "")))
            add_map[action] = str(row.get("playlist_id", ""))
        if playlists:
            submenu_add.addSeparator()
        action_add_new = submenu_add.addAction("新建歌单...")

        menu.addSeparator()
        action_change_lyrics = menu.addAction("更改歌词绑定")
        action_jump_lyrics = menu.addAction("跳转到歌词")
        action_delete = menu.addAction("移到回收站")
        action_export = menu.addAction("导出")
        action_reveal = menu.addAction("使用文件管理器查看")
        action_copy = menu.addAction("复制行数据")
        action_detail = menu.addAction("查看详情")

        chosen = menu.exec(pos)
        if not chosen:
            return
        if chosen == action_play:
            _queue_play_tracks(self, tracks)
            return
        if chosen == action_pass:
            self.pass_selected()
            return
        if chosen == action_favorite:
            self.on_favorite()
            return
        if chosen == action_unfavorite:
            self.on_unfavorite()
            return
        if chosen in add_map:
            try:
                result, cancelled = _run_chunked_ids_modal(
                    self,
                    title="加到歌单",
                    message="正在写入歌单",
                    ids=track_ids,
                    step=lambda chunk: self.facade.add_tracks_to_playlist(add_map[chosen], chunk),
                    chunk_size=512,
                )
            except Exception as exc:
                QMessageBox.warning(self, "操作失败", f"加到歌单失败\n{exc}")
                return
            count = int(result.get("affected", 0) or 0)
            self.grid.set_status(f"已添加 {count} 条到歌单" + ("（已取消）" if cancelled else ""))
            self.library_changed.emit()
            return
        if chosen == action_add_new:
            target = _prompt_new_playlist(self, self.facade)
            if target:
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
                self.grid.set_status(f"已添加 {count} 条到歌单" + ("（已取消）" if cancelled else ""))
                self.library_changed.emit()
            return
        if chosen == action_change_lyrics:
            if _handle_track_lyrics_cell_action(self, self.facade, tracks, action="change_mapping"):
                QTimer.singleShot(0, self.on_work_changed)
                QTimer.singleShot(0, self.library_changed.emit)
            return
        if chosen == action_jump_lyrics:
            _handle_track_lyrics_cell_action(self, self.facade, tracks, action="jump_to_lyrics")
            return
        if chosen == action_delete:
            self.on_delete()
            return
        if chosen == action_export:
            self.on_export()
            return
        if chosen == action_reveal:
            first = tracks[0] if tracks else {}
            _reveal_in_file_manager(self, _storage_path_for_track_row(self.facade, first))
            return
        if chosen == action_copy:
            _copy_selected_cells(self.grid.table)
            return
        if chosen == action_detail:
            _show_track_details(self, tracks[0])

class TrashPage(QWidget):
    library_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        """
        初始化已删除项目管理对话框

        参数:
            facade (MuseArcFacade): 门面对象，用于访问应用的各种功能

        返回值:
            None
        """
        super().__init__()  # 调用父类构造函数初始化
        self.facade = facade  # 保存门面对象引用

        # 创建主布局
        root = QVBoxLayout(self)
        # 创建按钮行
        row = QHBoxLayout()
        self.btn_restore = QPushButton("恢复选中")  # 恢复选中项目按钮
        self.btn_delete_file = QPushButton("删除文件（保留元数据）")  # 彻底删除文件按钮
        self.btn_delete_file.setStyleSheet("background-color:#b3261e;color:white;")  # 设置红色背景样式
        self.btn_delete_meta = QPushButton("删除元数据")  # 删除元数据按钮
        self.btn_delete_meta.setStyleSheet("background-color:#8b1e1e;color:white;")  # 设置深红色背景样式

        # 将按钮添加到按钮行
        row.addWidget(self.btn_restore)
        row.addWidget(self.btn_delete_file)
        row.addWidget(self.btn_delete_meta)
        row.addStretch(1)  # 在按钮右侧添加弹性空间
        row.addWidget(QLabel("搜索"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索回收站中的类型、文件名、标题、艺术家或 ID")
        self.search_edit.setClearButtonEnabled(True)
        row.addWidget(self.search_edit, 1)

        # 创建分割器（水平分割布局）
        split = QSplitter(Qt.Orientation.Horizontal)

        # 左侧面板（文件仍存在，可恢复或彻底删除文件）
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)  # 设置无边距布局
        left_layout.addWidget(QLabel("文件仍在（可恢复 / 可彻底删文件）"))  # 添加标题标签

        # 创建左侧表格的数据模型
        self.left_model = DictTableModel(
            [
                ColumnDef("item_type_label", "类型"),  # 项目类型列
                ColumnDef("file_name", "文件名"),       # 文件名列
                ColumnDef("title", "标题"),              # 标题列
                ColumnDef("artist", "艺术家"),           # 艺术家列
                ColumnDef("deleted_at", "删除时间"),     # 删除时间列
                ColumnDef("item_id", "ID"),             # ID列
            ]
        )

        # 创建左侧表格视图
        self.left_table = QTableView()
        self.left_table.setModel(self.left_model)  # 设置数据模型
        self.left_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)  # 选择整行
        self.left_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)  # 允许多选
        self.left_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)  # 禁止编辑
        self.left_table.setAlternatingRowColors(True)  # 启用交替行颜色
        self.left_table.setSortingEnabled(True)  # 启用排序功能
        self.left_table.horizontalHeader().setStretchLastSection(True)  # 最后一列拉伸填充
        _install_copy_support(self.left_table)  # 安装复制支持功能

        # 设置表格上下文菜单（右键菜单）
        self.left_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.left_table.customContextMenuRequested.connect(lambda pos: self._show_context_menu(self.left_table, pos))

        # 将表格添加到左侧布局，拉伸因子为1
        left_layout.addWidget(self.left_table, 1)

        # 右侧面板（仅元数据，文件已删除）
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)  # 设置无边距布局
        right_layout.addWidget(QLabel("仅元数据（文件已删）"))  # 添加标题标签

        # 创建右侧表格的数据模型
        self.right_model = DictTableModel(
            [
                ColumnDef("item_type_label", "类型"),  # 项目类型列
                ColumnDef("file_name", "文件名"),       # 文件名列
                ColumnDef("title", "标题"),              # 标题列
                ColumnDef("artist", "艺术家"),           # 艺术家列
                ColumnDef("deleted_at", "删除时间"),     # 删除时间列
                ColumnDef("item_id", "ID"),             # ID列
            ]
        )

        # 创建右侧表格视图
        self.right_table = QTableView()
        self.right_table.setModel(self.right_model)  # 设置数据模型
        self.right_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)  # 选择整行
        self.right_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)  # 允许多选
        self.right_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)  # 禁止编辑
        self.right_table.setAlternatingRowColors(True)  # 启用交替行颜色
        self.right_table.setSortingEnabled(True)  # 启用排序功能
        self.right_table.horizontalHeader().setStretchLastSection(True)  # 最后一列拉伸填充
        _install_copy_support(self.right_table)  # 安装复制支持功能

        # 设置表格上下文菜单（右键菜单）
        self.right_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.right_table.customContextMenuRequested.connect(lambda pos: self._show_context_menu(self.right_table, pos))

        # 将表格添加到右侧布局，拉伸因子为1
        right_layout.addWidget(self.right_table, 1)

        # 将左右面板添加到分割器
        split.addWidget(left)
        split.addWidget(right)
        # 设置左右面板的拉伸因子（1:1）
        split.setStretchFactor(0, 1)  # 左侧拉伸因子
        split.setStretchFactor(1, 1)  # 右侧拉伸因子

        # 将按钮行和分割器添加到主布局
        root.addLayout(row)
        root.addWidget(split, 1)  # 分割器拉伸因子为1

        excluded_row = QHBoxLayout()
        excluded_row.addWidget(QLabel("已排除路径"))
        self.excluded_search_edit = QLineEdit()
        self.excluded_search_edit.setPlaceholderText("搜索已排除的音频源路径")
        self.excluded_search_edit.setClearButtonEnabled(True)
        excluded_row.addWidget(self.excluded_search_edit, 1)
        self.btn_remove_excluded = QPushButton("删除选中路径")
        excluded_row.addWidget(self.btn_remove_excluded)
        root.addLayout(excluded_row)

        self.excluded_model = DictTableModel([ColumnDef("path", "路径")])
        self.excluded_table = QTableView()
        self.excluded_table.setModel(self.excluded_model)
        self.excluded_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.excluded_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.excluded_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.excluded_table.setAlternatingRowColors(True)
        self.excluded_table.horizontalHeader().setStretchLastSection(True)
        _install_copy_support(self.excluded_table)
        root.addWidget(self.excluded_table, 0)

        # 创建状态标签并添加到布局
        self.status = QLabel("-")
        root.addWidget(self.status)

        # 连接按钮点击信号到相应的槽函数
        self.btn_restore.clicked.connect(self.restore_selected)       # 恢复选中项目
        self.btn_delete_file.clicked.connect(self.delete_selected_files)  # 删除选中文件
        self.btn_delete_meta.clicked.connect(self.delete_selected_metadata)  # 删除选中元数据
        self.btn_remove_excluded.clicked.connect(self.remove_selected_excluded_paths)
        self.search_edit.textChanged.connect(self._apply_trash_filter)
        self.excluded_search_edit.textChanged.connect(self._apply_excluded_filter)

        self._trash_rows: list[dict] = []
        self._excluded_rows: list[dict] = []

        # 加载回收站数据
        self.reload_trash()

    def apply_button_scale(self, scale: float) -> None:
        _apply_button_scale(self.btn_restore, scale)
        _apply_button_scale(self.btn_delete_file, scale)
        _apply_button_scale(self.btn_delete_meta, scale)
        _apply_button_scale(self.btn_remove_excluded, scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        """设置实例的facade属性并重新加载trash。

        参数：
            self: 类的实例
            facade: MuseArcFacade类型的对象，用于设置facade

        返回值：
            无（None）
        """
        self.facade = facade  # 将传入的facade赋值给实例的facade属性
        self.reload_trash()  # 调用reload_trash方法以重新加载trash

    def refresh_page(self) -> None:
        self.reload_trash()

    def reload_trash(self) -> None:
        """重新加载回收站内容，根据文件存在状态将数据分为左右两栏显示。

        功能：
            从后端获取已删除项目列表，按文件是否存在分为两组：
            - left_rows: 文件仍然存在的记录
            - right_rows: 仅保留元数据的记录

            更新左右视图模型并刷新状态栏统计信息。

        参数：
            无

        返回值：
            None
        """
        self._trash_rows = self.facade.list_deleted_items(limit=2_000_000)
        self._excluded_rows = [{"path": value} for value in self.facade.list_excluded_import_paths()]
        self._apply_trash_filter()
        self._apply_excluded_filter()

    def _apply_trash_filter(self) -> None:
        query = self.search_edit.text().strip().casefold()
        rows = self._trash_rows
        if query:
            fields = ("item_type_label", "file_name", "title", "artist", "album", "item_id", "storage_relpath")
            rows = [row for row in rows if any(query in str(row.get(field, "") or "").casefold() for field in fields)]
        left_rows = [row for row in rows if bool(row.get("file_exists"))]
        right_rows = [row for row in rows if not bool(row.get("file_exists"))]
        self.left_model.set_rows(left_rows)
        self.right_model.set_rows(right_rows)
        self.status.setText(
            f"回收站 共 {len(self._trash_rows)} 条 | 当前显示 {len(rows)} | 文件仍在 {len(left_rows)} | 仅元数据 {len(right_rows)}"
        )

    def _apply_excluded_filter(self) -> None:
        query = self.excluded_search_edit.text().strip().casefold()
        rows = self._excluded_rows
        if query:
            rows = [row for row in rows if query in str(row.get("path", "") or "").casefold()]
        self.excluded_model.set_rows(rows)

    def remove_selected_excluded_paths(self) -> None:
        rows = self._selected_rows_from(self.excluded_table, self.excluded_model)
        paths = [str(row.get("path", "") or "") for row in rows if str(row.get("path", "") or "").strip()]
        if not paths:
            return
        answer = QMessageBox.question(
            self,
            "删除已排除路径",
            f"确定删除选中的 {len(paths)} 条路径吗？删除后对应文件可再次参与扫描导入。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = self.facade.remove_excluded_import_paths(paths)
        self.reload_trash()
        self.status.setText(f"已删除 {removed} 条排除路径；对应文件可再次参与扫描导入")

    def _selected_rows_from(self, table: QTableView, model: DictTableModel) -> list[dict]:
        """从QTableView中获取选中的行，并从DictTableModel中提取对应行的数据。
        参数:
            table (QTableView): 表格视图对象。
            model (DictTableModel): 字典表格模型对象。
        返回:
            list[dict]: 一个字典列表，每个字典代表一行选中行的数据。
        """
        sm = table.selectionModel()  # 获取表格的选择模型
        if sm is None:  # 如果没有选择模型
            return []  # 返回空列表
        rows: list[dict] = []  # 初始化列表以存储选中行的数据
        for idx in sm.selectedRows():  # 遍历所有选中的行索引
            row = model.row_at(idx.row())  # 根据行索引从模型中获取行数据
            if row:  # 如果行数据存在
                rows.append(row)  # 添加到结果列表
        return rows  # 返回包含所有选中行数据的列表

    def _selected_items(self) -> list[dict]:
        """
        获取当前已选中的项目列表。

        该方法从左右两侧的表格中提取已选中的行数据，
        基于项目类型和项目ID进行去重处理，并返回去重后的项目列表。

        Args:
            self: 实例对象本身。

        Returns:
            list[dict]: 包含所有选中项目信息的字典列表，每个字典代表一个项目。
        """
        # 初始化一个字典，用于存储去重后的选中项目。键为元组 (item_type, item_id)，值为对应的行数据字典。
        picked: dict[tuple[str, str], dict] = {}
        # 遍历从左侧表格和右侧表格中分别获取的已选中行数据，合并为一个列表进行统一处理。
        for row in self._selected_rows_from(self.left_table, self.left_model) + self._selected_rows_from(self.right_table, self.right_model):
            # 从当前行数据中提取 item_type 和 item_id，组合成一个元组作为唯一标识键。
            key = (str(row.get("item_type", "")), str(row.get("item_id", "")))
            # 仅当键的两个组成部分（item_type 和 item_id）均不为空时，才认为该行是有效数据并进行存储，避免无效数据。
            if key[0] and key[1]:
                # 以 (item_type, item_id) 为键存入字典，自然实现去重，因为相同标识的项会被后者覆盖。
                picked[key] = row
        # 将去重后的项目字典的值（即行数据字典）转换为列表并返回。
        return list(picked.values())

    def restore_selected(self) -> None:
        """
        恢复选中的项目，这些项目必须存在于“文件仍在”列表中。

        功能：
            从当前选中的项目中筛选出文件存在的项，然后批量恢复它们。恢复过程中通过模态任务显示进度，完成后更新界面、状态栏，并发出库变化信号。

        参数：
            无额外参数（self 是实例引用）。

        返回值：
            None。方法通过消息框和状态栏显示恢复结果，不直接返回数据。
        """
        # 筛选出文件仍存在的选中项目
        items = [r for r in self._selected_items() if bool(r.get("file_exists"))]
        # 如果没有符合条件的项目，提示用户并提前返回
        if not items:
            QMessageBox.information(self, "恢复", "仅“文件仍在”列表中的项目可恢复。")
            return
        # 计算需要恢复的总项目数
        total = len(items)
        # 定义内部任务函数，用于执行恢复操作并更新进度
        def _task(progress, is_cancelled):
            tracks = 0  # 记录恢复的歌曲数量
            lyrics = 0  # 记录恢复的歌词数量
            chunk_size = 128  # 分块处理大小，避免一次性处理过多数据
            processed = 0  # 已处理的项目计数
            # 分块遍历项目，每 chunk_size 个为一批
            for start in range(0, total, chunk_size):
                # 如果任务被取消，立即终止循环
                if is_cancelled():
                    break
                # 获取当前批次的项目切片
                chunk = items[start : start + chunk_size]
                # 调用外观层恢复当前批次的项目，获取结果
                part = self.facade.restore_deleted_items(chunk)
                # 累加恢复的歌曲和歌词数量，使用 or 0 处理可能的 None 值
                tracks += int(part.get("tracks", 0) or 0)
                lyrics += int(part.get("lyrics", 0) or 0)
                # 更新已处理项目数
                processed += len(chunk)
                # 通过进度回调函数更新进度条和消息
                progress(processed, total, "正在恢复")
            # 返回包含恢复结果和取消状态的字典
            return {"tracks": tracks, "lyrics": lyrics, "cancelled": bool(is_cancelled() and processed < total)}
        # 运行模态任务，执行恢复操作
        outcome = run_modal_task(self, "恢复项目", _task)
        # 如果任务出错，显示警告并返回
        if outcome.error is not None:
            QMessageBox.warning(self, "恢复失败", f"恢复失败\n{outcome.error}")
            return
        # 从任务结果中提取恢复数据，如果结果类型不匹配则使用默认值
        restored = outcome.result if isinstance(outcome.result, dict) else {"tracks": 0, "lyrics": 0}
        # 重新加载回收站以刷新界面
        self.reload_trash()
        # 更新状态栏显示恢复结果，如果任务被取消则附加提示
        self.status.setText(
            f"已恢复 歌曲 {restored.get('tracks',0)} 条，歌词 {restored.get('lyrics',0)} 条"
            + ("（已取消）" if bool(restored.get("cancelled")) else "")
        )
        # 发出库变化信号，通知其他组件更新
        self.library_changed.emit()

    def delete_selected_files(self) -> None:
        items = self._selected_items()
        if not items:
            return
        _release_player_for_file_ops(self)
        answer = QMessageBox.question(self, "删除文件", f"仅删除 {len(items)} 条对应文件（保留元数据）？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        total = len(items)
        def _task(progress, is_cancelled):
            tracks = 0
            lyrics = 0
            chunk_size = 128
            processed = 0
            for start in range(0, total, chunk_size):
                if is_cancelled():
                    break
                chunk = items[start : start + chunk_size]
                part = self.facade.purge_deleted_item_files(chunk)
                tracks += int(part.get("tracks", 0) or 0)
                lyrics += int(part.get("lyrics", 0) or 0)
                processed += len(chunk)
                progress(processed, total, "正在删除文件")
            return {"tracks": tracks, "lyrics": lyrics, "cancelled": bool(is_cancelled() and processed < total)}
        outcome = run_modal_task(self, "删除文件", _task)
        if outcome.error is not None:
            QMessageBox.warning(self, "删除失败", f"删除文件失败\n{outcome.error}")
            return
        removed = outcome.result if isinstance(outcome.result, dict) else {"tracks": 0, "lyrics": 0}
        self.status.setText(
            f"已删除文件 歌曲 {removed.get('tracks',0)} 个，歌词 {removed.get('lyrics',0)} 个（元数据保留）"
            + ("（已取消）" if bool(removed.get("cancelled")) else "")
        )
        self.reload_trash()

    def delete_selected_metadata(self) -> None:
        """删除选中的回收站元数据。

        通过两次对话框确认后，执行批量删除操作，并在状态栏显示删除结果。
        如果用户取消或操作失败，会进行相应提示。

        Args:
            self: 类实例本身。

        Returns:
            None: 无返回值。
        """
        # 获取当前在界面上选中的回收站项目列表
        items = self._selected_items()
        # 如果没有选中任何项目，则直接结束方法
        if not items:
            return
        # 弹出第一个警告对话框，询问用户是否继续删除操作
        answer1 = QMessageBox.warning(
            self,
            "删除元数据",
            f"将永久删除 {len(items)} 条回收站元数据，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        # 如果用户在第一个对话框中选择“否”，则取消操作并返回
        if answer1 != QMessageBox.StandardButton.Yes:
            return
        # 弹出第二个警告对话框，进行最终确认，提示操作不可撤销
        answer2 = QMessageBox.warning(
            self,
            "再次确认",
            "该操作不可撤销，确认永久删除元数据？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        # 如果用户在第二个对话框中选择“否”，则取消操作并返回
        if answer2 != QMessageBox.StandardButton.Yes:
            return
        # 计算待处理项目的总数
        total = len(items)
        # 定义内部任务函数，该函数将在模态任务对话框中执行
        def _task(progress, is_cancelled):
            # 初始化已删除的歌曲和歌词计数器
            tracks = 0
            lyrics = 0
            # 设置每次处理的数据块大小，以避免一次性处理过多数据
            chunk_size = 128
            # 初始化已处理项目数
            processed = 0
            # 以固定大小的块遍历所有待处理项目
            for start in range(0, total, chunk_size):
                # 在开始处理每个块之前，检查是否收到了取消信号
                if is_cancelled():
                    break
                # 获取当前数据块（切片）
                chunk = items[start : start + chunk_size]
                # 调用外观层（facade）的方法，删除当前数据块的元数据
                part = self.facade.delete_deleted_items_metadata(chunk)
                # 累加本批次删除的歌曲数，处理可能为 None 或 0 的情况
                tracks += int(part.get("tracks", 0) or 0)
                # 累加本批次删除的歌词数，处理可能为 None 或 0 的情况
                lyrics += int(part.get("lyrics", 0) or 0)
                # 更新已处理项目数
                processed += len(chunk)
                # 调用进度回调函数，更新UI进度显示
                progress(processed, total, "正在删除元数据")
            # 任务完成，返回结果字典，包含删除计数和是否被取消的标志
            return {"tracks": tracks, "lyrics": lyrics, "cancelled": bool(is_cancelled() and processed < total)}
        # 使用 run_modal_task 在模态对话框中运行上述定义的任务
        outcome = run_modal_task(self, "删除元数据", _task)
        # 检查任务执行结果中是否包含错误
        if outcome.error is not None:
            # 如果有错误，弹出警告对话框显示失败信息
            QMessageBox.warning(self, "删除失败", f"删除元数据失败\n{outcome.error}")
            return
        # 安全地从结果中获取删除数据，如果结果类型不匹配则使用默认值
        removed = outcome.result if isinstance(outcome.result, dict) else {"tracks": 0, "lyrics": 0}
        # 更新主界面的状态栏文本，显示成功删除的歌曲和歌词数量
        self.status.setText(
            f"已删除元数据 歌曲 {removed.get('tracks',0)} 条，歌词 {removed.get('lyrics',0)} 条"
            + ("（已取消）" if bool(removed.get("cancelled")) else "")
        )
        # 重新加载回收站视图，以反映最新的数据
        self.reload_trash()
        # 发射库内容改变的信号，通知其他组件进行更新
        self.library_changed.emit()

    def _show_context_menu(self, table: QTableView, pos) -> None:
        """
        显示表格的右键上下文菜单。

        功能：
            根据传入的表格（左表或右表）和鼠标位置，显示一个包含多种操作选项的右键菜单。
            用户选择菜单项后，会触发对应的操作（如恢复文件、删除、查看详情等）。

        参数：
            table (QTableView): 触发菜单的表格视图对象，用于判断是左表还是右表。
            pos (QPoint): 鼠标点击的局部坐标（相对于表格的viewport）。

        返回值：
            None: 此方法不返回任何值，所有操作通过调用其他方法或显示消息框完成。
        """
        # 根据传入的table参数判断使用左侧还是右侧的数据模型
        model = self.left_model if table is self.left_table else self.right_model
        # 从表格中获取用户选中的行索引列表
        rows = self._selected_rows_from(table, model)
        # 如果没有选中任何行，则直接返回，不显示菜单
        if not rows:
            return
        # 获取第一个选中行的数据，用于后续操作（如查看详情）
        first = rows[0]
        # 创建右键菜单
        menu = QMenu(self)
        # 向菜单中添加各个操作选项，并保存对应的菜单动作对象
        action_restore = menu.addAction("恢复")
        action_delete_file = menu.addAction("删除文件（保留元数据）")
        action_delete_meta = menu.addAction("删除元数据")
        action_reveal = menu.addAction("使用文件管理器查看")
        action_copy = menu.addAction("复制行数据")
        action_detail = menu.addAction("查看详情")
        # 将鼠标点击的局部坐标转换为全局屏幕坐标，以便在正确位置显示菜单
        global_pos = table.viewport().mapToGlobal(pos)
        # 在全局坐标处显示菜单并等待用户选择，返回用户选择的动作
        chosen = menu.exec(global_pos)
        # 如果用户没有选择任何选项（例如点击了菜单外部），则直接返回
        if not chosen:
            return
        # 根据用户选择的动作，执行相应的操作
        if chosen == action_restore:
            self.restore_selected()
            return
        if chosen == action_delete_file:
            self.delete_selected_files()
            return
        if chosen == action_delete_meta:
            self.delete_selected_metadata()
            return
        if chosen == action_reveal:
            # 获取第一个选中行的相对存储路径，并去除前后空白
            rel = str(first.get("storage_relpath", "") or "").strip()
            # 如果相对路径非空，则构建完整路径字符串
            path_text = str(Path(self.facade.library_root) / rel) if rel else ""
            # 在文件管理器中定位到该路径（如果路径为空，则可能无法定位）
            _reveal_in_file_manager(self, path_text)
            return
        if chosen == action_copy:
            # 复制用户选中单元格的数据到剪贴板
            _copy_selected_cells(table)
            return
        if chosen == action_detail:
            # 构建详情信息的字符串列表，包含第一行数据的各个字段
            lines = [
                f"类型: {first.get('item_type_label','')}",
                f"ID: {first.get('item_id','')}",
                f"文件名: {first.get('file_name','')}",
                f"标题: {first.get('title','')}",
                f"艺术家: {first.get('artist','')}",
                f"专辑: {first.get('album','')}",
                f"Storage: {first.get('storage_relpath','')}",
                f"Deleted At: {first.get('deleted_at','')}",
            ]
            # 使用消息框显示所有详情信息，每行用换行符分隔
            QMessageBox.information(self, "回收站详情", "\n".join(lines))

class TagManagementPage(QWidget):
    tags_changed = Signal()
    library_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        """
        初始化标签管理界面。

        功能：创建并配置UI组件，包括标签树、操作按钮和歌曲网格，连接相关信号与槽。
        参数：
            facade (MuseArcFacade): 用于数据交互的门面实例。
        返回值：无。
        """
        # 调用父类构造函数以初始化QWidget基础部分
        super().__init__()
        # 保存门面实例引用，用于后续数据操作
        self.facade = facade
        # 初始化当前选中的标签名，默认为None
        self.current_tag_name: str | None = None

        # 创建主垂直布局作为根布局
        root = QVBoxLayout(self)

        # 创建水平布局用于放置顶部操作按钮
        row = QHBoxLayout()
        # 初始化标签操作按钮：新增、删除和小工具
        self.btn_add = QPushButton("新增标签")
        self.btn_delete = QPushButton("删除标签")
        self.btn_tools = QPushButton("小工具")
        # 将按钮添加到顶部行布局
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_delete)
        row.addWidget(self.btn_tools)
        # 添加弹性空间使按钮靠左
        row.addStretch(1)

        # 创建分割器以实现左右分栏布局
        splitter = QSplitter()
        # 创建左侧容器和布局，用于放置标签树
        left = QWidget()
        left_layout = QVBoxLayout(left)
        # 初始化树形控件，用于显示标签列表
        self.tree = QTreeWidget()
        # 设置树形控件的列标题为"标签"和"歌曲数"
        self.tree.setHeaderLabels(["标签", "歌曲数"])
        # 启用交替行颜色以提高可读性
        self.tree.setAlternatingRowColors(True)
        # 将树形控件添加到左侧布局
        left_layout.addWidget(self.tree)

        # 创建右侧容器和布局，用于放置操作按钮和歌曲网格
        right = QWidget()
        right_layout = QVBoxLayout(right)
        # 创建水平布局用于放置操作按钮行
        row_ops = QHBoxLayout()
        # 初始化歌曲操作按钮
        self.btn_remove_from_tag = QPushButton("从本标签中移除")
        self.btn_export = QPushButton("导出")
        self.btn_favorite = QPushButton("收藏")
        self.btn_unfavorite = QPushButton("取消收藏")
        self.btn_delete_from_library = QPushButton("从音乐库中删除")
        # 为删除按钮设置醒目红色背景，以警示危险操作
        self.btn_delete_from_library.setStyleSheet("background-color:#b3261e;color:white;")
        # 将所有操作按钮添加到按钮行布局
        for btn in [
            self.btn_remove_from_tag,
            self.btn_export,
            self.btn_favorite,
            self.btn_unfavorite,
            self.btn_delete_from_library,
        ]:
            row_ops.addWidget(btn)
        # 添加弹性空间使按钮靠左
        row_ops.addStretch(1)

        # 初始化歌曲网格控件，用于显示和编辑歌曲列表
        self.grid = TrackGridWidget(self.facade)
        # 将操作按钮行添加到右侧布局
        right_layout.addLayout(row_ops)
        # 将歌曲网格添加到右侧布局，并设置拉伸因子为1以占满剩余空间
        right_layout.addWidget(self.grid, 1)

        # 将左右容器添加到分割器
        splitter.addWidget(left)
        splitter.addWidget(right)
        # 设置分割器的拉伸因子：左侧固定，右侧可拉伸
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        # 将顶部行和分割器添加到根布局
        root.addLayout(row)
        root.addWidget(splitter, 1)

        # 连接按钮点击信号到对应的槽函数
        self.btn_add.clicked.connect(self._on_add)
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_tools.clicked.connect(self._open_tools_menu)
        self.btn_remove_from_tag.clicked.connect(self._remove_selected_from_tag)
        self.btn_export.clicked.connect(self._on_export)
        self.btn_favorite.clicked.connect(self._on_favorite)
        self.btn_unfavorite.clicked.connect(self._on_unfavorite)
        self.btn_delete_from_library.clicked.connect(self._on_delete_from_library)
        # 连接标签树当前项变化信号，以更新当前标签
        self.tree.currentItemChanged.connect(self._on_tag_changed)
        # 连接歌曲网格的字段编辑和上下文菜单信号
        self.grid.track_field_edited.connect(self._on_track_field_edited)
        self.grid.context_menu_requested.connect(self._show_context_menu)
        # 安装行功能快捷键，为操作按钮行添加从F3开始的快捷键
        _install_row_function_shortcuts(
            self,
            [
                self.btn_remove_from_tag,
                self.btn_export,
                self.btn_favorite,
                self.btn_unfavorite,
                self.btn_delete_from_library,
            ],
            start_f=3,
        )

        # 加载并显示标签列表
        self.reload_tags()

    def apply_button_scale(self, scale: float) -> None:
        _apply_button_scale(self.btn_add, scale)
        _apply_button_scale(self.btn_delete, scale)
        _apply_button_scale(self.btn_tools, scale)
        _apply_button_scale(self.btn_remove_from_tag, scale)
        _apply_button_scale(self.btn_export, scale)
        _apply_button_scale(self.btn_favorite, scale)
        _apply_button_scale(self.btn_unfavorite, scale)
        _apply_button_scale(self.btn_delete_from_library, scale)
        self.grid.set_button_scale(scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade
        self.grid.set_facade(facade)
        self.reload_tags()

    def refresh_page(self) -> None:
        self.reload_tags()
        self._reload_tracks_for_current_tag()

    def reload_tags(self) -> None:
        """重新加载标签列表到树形控件中。

        该方法从数据源获取所有标签信息，清空并重新填充树形控件，同时尝试保持或恢复之前选中的标签项。
        如果没有可选标签，则会清空关联网格并显示提示信息。

        参数：
            无（方法依赖self实例的状态和facade数据接口）

        返回值：
            无（直接操作界面控件）
        """
        # 保存当前已选中的标签名，用于后续尝试恢复选中状态
        keep = self.current_tag_name
        # 从外观层（facade）获取所有标签字段数据列表
        rows = self.facade.list_tag_fields()
        # 清空树形控件中的所有现有项
        self.tree.clear()
        # 用于记录匹配的树形控件项，初始为None
        target_item: QTreeWidgetItem | None = None
        # 遍历所有标签数据行，为每条数据创建一个树形控件项
        for row in rows:
            # 从行数据中获取标签名，默认为空字符串
            tag_name = str(row.get("tag_name", ""))
            # 创建树形控件项，显示标签名和关联的曲目数量
            item = QTreeWidgetItem([tag_name, str(row.get("track_count", 0))])
            # 将标签名作为用户数据存储在第0列，便于后续获取
            item.setData(0, Qt.ItemDataRole.UserRole, tag_name)
            # 将创建的项添加为树形控件的顶级项
            self.tree.addTopLevelItem(item)
            # 如果之前有选中的标签名，且当前项的标签名与之匹配，则记录为目标项
            if keep and keep == tag_name:
                target_item = item
        # 如果未找到匹配项，但树形控件中有顶级项，则默认选中第一项
        if target_item is None and self.tree.topLevelItemCount() > 0:
            target_item = self.tree.topLevelItem(0)
        # 如果最终有目标项（即找到匹配项或默认项）
        if target_item is not None:
            # 将树形控件的当前选中项设置为目标项
            self.tree.setCurrentItem(target_item)
            # 更新实例的当前标签名为目标项的用户数据（标签名）
            self.current_tag_name = str(target_item.data(0, Qt.ItemDataRole.UserRole) or "")
        # 如果没有目标项（即树形控件为空）
        else:
            # 将当前标签名设置为None
            self.current_tag_name = None
            # 清空网格控件中显示的曲目列表
            self.grid.set_tracks([])
            # 在网格状态栏显示“暂无标签”的提示信息
            self.grid.set_status("暂无标签")

    def _reload_tracks_for_current_tag(self) -> None:
        """
        根据当前选择的标签重新加载曲目列表。

        功能说明：
            检查是否有选定的标签。若无，则清空网格并显示提示；若有，则获取全部曲目，
            筛选出包含当前标签的曲目，并更新网格显示和状态信息。

        参数：
            无

        返回值：
            None
        """
        if not self.current_tag_name:
            # 无有效标签时，清空网格内容并更新状态提示
            self.grid.set_tracks([])
            self.grid.set_status("未选择标签")
            return
        # 从外观（facade）获取所有曲目，限制数量为200万
        rows = self.facade.list_tracks(limit=2_000_000)
        # 构造标签在字典中的可能键名
        key = f"tag:{self.current_tag_name}"
        # 筛选包含当前标签的曲目。同时检查两种可能的字段格式，并处理可能为空的情况
        filtered = [r for r in rows if str((r.get("tags", {}) or {}).get(self.current_tag_name, "")).strip() or str(r.get(key, "")).strip()]
        # 将筛选后的曲目列表设置到网格中显示
        self.grid.set_tracks(filtered)
        # 更新状态栏，显示当前标签下的曲目总数
        self.grid.set_status(f"标签“{self.current_tag_name}”共 {len(filtered)} 首")

    def _selected_tag_name(self) -> str | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        name = str(item.data(0, Qt.ItemDataRole.UserRole) or "").strip()
        return name or None

    def _on_tag_changed(self) -> None:
        self.current_tag_name = self._selected_tag_name()
        self._reload_tracks_for_current_tag()

    def _selected_tracks(self) -> list[dict]:
        return self.grid.selected_tracks()

    def _selected_track_ids(self) -> list[str]:
        return [str(t.get("track_id", "")) for t in self._selected_tracks() if t.get("track_id")]

    def _on_add(self) -> None:
        """新增一个标签字段。

        功能：弹出对话框让用户输入新标签名称，若名称有效则创建该标签，并更新界面。
        参数：self - 指向当前实例的引用。
        返回值：None
        """
        # 使用QInputDialog弹出一个输入对话框，让用户输入新标签名称。
        name, ok = QInputDialog.getText(self, "新增标签", "标签名称")
        # 如果用户点击了“取消”按钮（ok为False），则直接结束方法。
        if not ok:
            return
        # 将获取到的名称转换为字符串并去除首尾空白字符。
        text = str(name).strip()
        # 如果处理后的文本为空，则直接结束方法。
        if not text:
            return
        # 调用门面类(facade)的方法尝试创建标签字段。若失败（如名称重复或非法），则显示警告。
        if not self.facade.create_tag_field(text):
            QMessageBox.warning(self, "新增标签", "标签可能已存在或名称无效。")
            return
        # 标签创建成功，更新当前活动的标签名称为新创建的标签名。
        self.current_tag_name = text
        # 重新从数据源加载标签列表。
        self.reload_tags()
        # 刷新显示标签字段的网格视图。
        self.grid.refresh_tag_fields()
        # 发出标签列表已改变的信号，通知其他连接的部件进行更新。
        self.tags_changed.emit()

    def _on_delete(self) -> None:
        """删除当前选中的标签。

        该方法通过弹窗确认用户意图，调用外观层（facade）执行标签删除操作，
        并在成功后更新界面状态与组件。

        Args:
            self: 类实例自身。

        Returns:
            None: 该方法无返回值，但会触发界面更新和信号发射。
        """
        name = self._selected_tag_name()  # 获取当前选中的标签名称
        if not name:  # 若未选中任何标签，则直接返回
            return
        answer = QMessageBox.question(self, "删除标签", f"确定删除标签“{name}”吗？")  # 弹窗询问用户确认删除
        if answer != QMessageBox.StandardButton.Yes:  # 用户未确认，则直接返回
            return
        count = self.facade.delete_tag_field(name)  # 调用外观层方法执行删除，并获取影响行数
        if count <= 0:  # 若影响行数小于等于0，表示删除失败（默认标签不可删除或标签不存在）
            QMessageBox.warning(self, "删除标签", "默认标签不可删除，或标签不存在。")  # 弹窗提示删除失败
            return
        self.current_tag_name = None  # 将当前选中标签名称重置为None
        self.reload_tags()  # 重新加载标签列表
        self.grid.refresh_tag_fields()  # 刷新网格视图中的标签字段
        self.tags_changed.emit()  # 发射标签已更改的信号，通知其他组件

    def _remove_selected_from_tag(self) -> None:
        """从标签中移除选中的曲目。

        功能：
            将当前选中的曲目从指定的标签中移除（将标签值置为空字符串）。
            操作会以分块方式执行，并在状态栏显示处理结果。

        参数：
            无参数（方法通过self访问实例属性和调用其他方法）。

        返回值：
            None（无返回值）。
        """
        # 获取当前选中的标签名称
        tag_name = self._selected_tag_name()
        # 如果没有选中标签，则直接返回，不执行后续操作
        if not tag_name:
            return
        # 获取当前选中的曲目ID列表
        track_ids = self._selected_track_ids()
        # 如果没有选中曲目，则直接返回，不执行后续操作
        if not track_ids:
            return
        try:
            # 调用分块处理模态对话框，执行实际的移除操作
            # 每个分块将指定曲目的标签值置为空字符串
            result, cancelled = _run_chunked_ids_modal(
                self,
                title="从标签移除",
                message="正在清理标签",
                ids=track_ids,
                # 使用lambda函数定义分块执行步骤：将指定曲目的标签值置为空字符串
                step=lambda chunk: self.facade.update_track_tag_values(chunk, tag_name, ""),
                chunk_size=512,
            )
        # 捕获异常，显示警告对话框并返回
        except Exception as exc:
            QMessageBox.warning(self, "操作失败", f"从标签移除失败\n{exc}")
            return
        # 从结果中获取受影响的曲目数量，默认为0
        count = int(result.get("affected", 0) or 0)
        # 在网格状态栏显示操作结果，包括是否取消
        self.grid.set_status(f"已从标签“{tag_name}”移除 {count} 首" + ("（已取消）" if cancelled else ""))
        # 重新加载当前标签对应的曲目列表
        self._reload_tracks_for_current_tag()
        # 重新加载标签列表
        self.reload_tags()
        # 发出库已更改信号，通知其他组件
        self.library_changed.emit()

    def _on_export(self) -> None:
        """导出选定的轨道到指定位置。

        功能：将当前选中的轨道导出为播放列表文件。
        参数：无。
        返回值：None。
        """
        # 获取当前选中的轨道列表
        tracks = self._selected_tracks()
        # 从每个轨道中提取 track_id（如果存在），转换为字符串，并过滤掉没有 track_id 的项
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        # 如果没有有效的 track_id，直接返回，不进行导出
        if not track_ids:
            return
        # 弹出导出对话框，让用户选择导出目标。如果用户取消，ok 为 False
        ok, target = _run_export_dialog(self, self.facade, tracks, playlist_name=f"标签_{self.current_tag_name or 'tracks'}")
        # 如果用户取消导出对话框，直接返回
        if not ok:
            return
        # 在网格状态栏中显示导出成功的信息，包括导出的轨道数量和目标路径
        self.grid.set_status(f"已导出 {len(track_ids)} 条到 {target}")

    def _open_tools_menu(self) -> None:
        """打开工具菜单，提供计算喜爱程度和喜好同步等功能。

        功能：创建并显示一个右键菜单，根据用户选择的操作执行相应计算和数据同步。
        参数：无
        返回值：无
        """
        # 创建一个工具菜单，以当前窗口为父对象
        menu = QMenu(self)
        # 添加菜单项：计算喜爱程度
        action_love = menu.addAction("计算喜爱程度")
        # 添加菜单项：喜好同步
        action_sync_preference = menu.addAction("喜好同步")
        # 显示菜单，并获取用户选择的操作
        chosen = menu.exec(self.btn_tools.mapToGlobal(self.btn_tools.rect().bottomLeft()))
        # 如果用户选择了"计算喜爱程度"
        if chosen == action_love:
            # 调用外观层方法重新计算喜爱程度标签，返回受影响的曲目数量
            count = self.facade.recompute_love_score_tag()
            # 更新状态栏显示已更新曲目数
            self.grid.set_status(f"已更新 {count} 首的喜爱程度")
            # 重新加载标签数据
            self.reload_tags()
            # 重新加载当前标签下的曲目列表
            self._reload_tracks_for_current_tag()
            # 发射库变更信号，通知其他组件数据已更新
            self.library_changed.emit()
            return
        # 如果用户选择了"喜好同步"
        if chosen == action_sync_preference:
            # 弹出确认对话框，询问是否将喜爱程度同步到喜好(1-10)
            answer = QMessageBox.question(
                self,
                "喜好同步",
                "将标签【喜爱程度】同步到【喜好(1-10)】（除以10并四舍五入）？",
            )
            # 如果用户没有确认，则直接返回
            if answer != QMessageBox.StandardButton.Yes:
                return
            # 调用外观层方法同步喜好数据，返回同步的曲目数量
            count = self.facade.sync_preference_from_love_tag()
            # 更新状态栏显示已同步曲目数
            self.grid.set_status(f"已同步 {count} 首的喜好")
            # 重新加载当前标签下的曲目列表
            self._reload_tracks_for_current_tag()
            # 发射库变更信号，通知其他组件数据已更新
            self.library_changed.emit()
            return

    def _on_favorite(self) -> None:
        """将选中的未收藏歌曲添加到收藏夹并更新状态。"""
        # 获取当前选中的曲目
        tracks = self._selected_tracks()
        # 提取未收藏歌曲的ID：遍历选中曲目，过滤出有track_id且未收藏的歌曲，并转换为字符串ID
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id") and not bool(t.get("is_favorite"))]
        # 如果没有符合条件的歌曲ID，则提前返回
        if not track_ids:
            return
        # 调用facade方法将歌曲添加到收藏夹，并获取添加的数量
        count = self.facade.add_to_favorites(track_ids)
        # 在界面状态栏显示收藏成功的消息
        self.grid.set_status(f"已收藏 {count} 条")
        # 重新加载当前标签下的曲目以反映更改
        self._reload_tracks_for_current_tag()
        # 发射信号通知库已更改
        self.library_changed.emit()

    def _on_unfavorite(self) -> None:
        """处理取消收藏操作

        功能：获取当前选中的已收藏曲目，将其从收藏列表中移除，并更新界面状态和库信息。
        参数：无
        返回值：无
        """
        tracks = self._selected_tracks()
        # 从选中的曲目中筛选出已收藏的曲目，并提取其track_id
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id") and bool(t.get("is_favorite"))]

        # 如果没有找到可取消收藏的曲目，则提前结束方法
        if not track_ids:
            return

        # 调用facade层移除收藏，并获取实际移除的数量
        count = self.facade.remove_from_favorites(track_ids)

        # 在网格控件中显示操作结果状态
        self.grid.set_status(f"已取消收藏 {count} 条")

        # 重新加载当前标签下的曲目列表
        self._reload_tracks_for_current_tag()

        # 发射库数据变化信号，通知其他组件更新
        self.library_changed.emit()

    def _on_delete_from_library(self) -> None:
        """处理从库中删除选中轨道的操作。参数：无。返回值：无。"""
        track_ids = self._selected_track_ids()  # 获取用户在界面中选中的轨道ID列表
        if not track_ids:  # 如果没有选中任何轨道
            return  # 终止操作，不做任何处理
        _release_player_for_file_ops(self)  # 释放播放器资源，以便进行文件操作
        mode = _resolve_delete_mode_and_maybe_save_default(self, self.facade, len(track_ids), track_ids)  # 解析删除模式（如永久删除或移到回收站），并可能保存默认设置
        if mode == "cancel":  # 如果用户选择取消
            return  # 终止操作
        try:
            result, cancelled = _run_chunked_ids_modal(  # 尝试执行分块删除操作
                self,
                title="移到回收站",  # 对话框标题
                message="正在移到回收站",  # 对话框消息
                ids=track_ids,  # 要删除的轨道ID列表
                step=lambda chunk: self.facade.delete_tracks(chunk, mode=mode),  # 每个块的删除步骤
                chunk_size=256,  # 每块大小
            )
        except Exception as exc:  # 捕获任何异常
            QMessageBox.warning(self, "操作失败", f"移到回收站失败\n{exc}")  # 显示错误警告
            return  # 终止操作
        deleted = int(result.get("affected", 0) or 0)  # 获取实际删除的轨道数量
        self.grid.set_status(f"已移到回收站 {deleted} 条" + ("（已取消）" if cancelled else ""))  # 更新状态栏显示删除信息
        self._reload_tracks_for_current_tag()  # 重新加载当前标签下的轨道列表
        self.reload_tags()  # 刷新标签列表
        self.library_changed.emit()  # 发出库已更改的信号，通知其他组件更新

    def _on_track_field_edited(self, track_id: str, key: str, value) -> None:
        if not track_id or key == "custom_order":
            return
        if key == "lyrics_file_name":
            row = self.grid.track_by_id(track_id)
            if row and _handle_track_lyrics_cell_action(self, self.facade, [row], action=None):
                QTimer.singleShot(0, self._reload_tracks_for_current_tag)
                QTimer.singleShot(0, self.library_changed.emit)
            return
        try:
            if key.startswith("tag:"):
                tag_name = key.split(":", 1)[1]
                self.facade.update_track_tag_values([track_id], tag_name, str(value))
            else:
                self.facade.update_tracks_fields([track_id], {key: value})
        except Exception as exc:
            QMessageBox.warning(self, "编辑失败", f"edit: editing failed\n{exc}")
            QTimer.singleShot(0, self._reload_tracks_for_current_tag)
            QTimer.singleShot(0, self.reload_tags)
            return
        if key == f"tag:{self.current_tag_name or ''}":
            QTimer.singleShot(0, self._reload_tracks_for_current_tag)
            QTimer.singleShot(0, self.reload_tags)
        QTimer.singleShot(0, self.library_changed.emit)

    def _show_context_menu(self, pos, tracks: list[dict]) -> None:
        """显示右键上下文菜单。

        根据传入的轨道列表和鼠标位置，构建并显示包含播放、标签管理、收藏/取消收藏、歌单操作、歌词操作等功能的右键菜单。

        Args:
            pos (QPoint): 菜单显示的全局坐标位置。
            tracks (list[dict]): 当前选中的轨道数据列表，每个元素是一个字典。

        Returns:
            None: 无返回值。所有操作通过信号或直接调用方法完成。
        """
        # 提取所有有效轨道的ID，并转换为字符串列表
        track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
        # 如果没有有效轨道，则不显示菜单
        if not track_ids:
            return

        # 检查选中的轨道中是否存在可收藏或可取消收藏的项
        # 只有当至少一个轨道未被收藏时，才允许“收藏”操作
        can_favorite = any(not bool(t.get("is_favorite")) for t in tracks)
        # 只有当至少一个轨道已被收藏时，才允许“取消收藏”操作
        can_unfavorite = any(bool(t.get("is_favorite")) for t in tracks)

        # 创建主菜单
        menu = QMenu(self)
        # 添加常用操作菜单项
        action_play = menu.addAction("播放")
        action_remove_tag = menu.addAction("从本标签中移除")
        action_favorite = menu.addAction("收藏")
        action_unfavorite = menu.addAction("取消收藏")
        # 根据前面的检查结果，启用或禁用收藏相关菜单项
        action_favorite.setEnabled(can_favorite)
        action_unfavorite.setEnabled(can_unfavorite)

        # 创建“加到歌单”子菜单
        submenu_add = menu.addMenu("加到歌单")
        # 创建一个字典，用于将子菜单项(QAction)映射到对应的歌单ID
        add_map: dict[QAction, str] = {}
        # 获取所有歌单，并排除“收藏”歌单本身
        playlists = [p for p in self.facade.list_playlists() if str(p.get("playlist_id", "")) != FAVORITES_PLAYLIST_ID]
        # 遍历歌单列表，在子菜单中为每个歌单创建一个操作项
        for row in playlists:
            action = submenu_add.addAction(str(row.get("name", "")))
            add_map[action] = str(row.get("playlist_id", ""))
        # 如果有歌单，则在它们和“新建歌单”选项之间添加分隔线
        if playlists:
            submenu_add.addSeparator()
        # 在子菜单最后添加“新建歌单...”选项
        action_add_new = submenu_add.addAction("新建歌单...")

        # 在主菜单中添加分隔线
        menu.addSeparator()
        # 添加其他功能操作菜单项
        action_change_lyrics = menu.addAction("更改歌词绑定")
        action_jump_lyrics = menu.addAction("跳转到歌词")
        action_delete = menu.addAction("移到回收站")
        action_export = menu.addAction("导出")
        action_reveal = menu.addAction("使用文件管理器查看")
        action_copy = menu.addAction("复制行数据")
        action_detail = menu.addAction("查看详情")

        # 在指定位置执行（显示）菜单，并获取用户选择的动作
        chosen = menu.exec(pos)
        # 如果用户没有选择任何项（如点击菜单外区域），则直接返回
        if not chosen:
            return

        # 根据用户选择的动作，执行相应的操作
        if chosen == action_play:
            _queue_play_tracks(self, tracks)  # 播放选中的轨道
            return
        if chosen == action_remove_tag:
            self._remove_selected_from_tag()  # 将选中轨道从当前标签移除
            return
        if chosen == action_favorite:
            self._on_favorite()  # 将选中轨道添加到收藏
            return
        if chosen == action_unfavorite:
            self._on_unfavorite()  # 将选中轨道从收藏中移除
            return
        # 如果选择的动作存在于歌单映射字典中，说明是“加到歌单”子菜单项
        if chosen in add_map:
            try:
                # 以分块方式执行“添加到歌单”操作，并显示进度对话框
                result, cancelled = _run_chunked_ids_modal(
                    self,
                    title="加到歌单",
                    message="正在写入歌单",
                    ids=track_ids,
                    # 使用lambda延迟执行实际添加操作，chunk_size控制每批处理的ID数
                    step=lambda chunk: self.facade.add_tracks_to_playlist(add_map[chosen], chunk),
                    chunk_size=512,
                )
            except Exception as exc:
                # 如果操作失败，弹出警告对话框
                QMessageBox.warning(self, "操作失败", f"加到歌单失败\n{exc}")
                return
            # 从结果中获取受影响的记录数，若为None则默认为0
            count = int(result.get("affected", 0) or 0)
            # 在状态栏显示操作结果，包括是否被取消
            self.grid.set_status(f"已添加 {count} 条到歌单" + ("（已取消）" if cancelled else ""))
            # 发射库已变更信号，通知其他部分刷新
            self.library_changed.emit()
            return
        if chosen == action_add_new:
            # 弹出对话框让用户输入新歌单名称，并返回新歌单ID
            target = _prompt_new_playlist(self, self.facade)
            if target:
                try:
                    # 与上文类似，以分块方式执行添加到新歌单的操作
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
                self.grid.set_status(f"已添加 {count} 条到歌单" + ("（已取消）" if cancelled else ""))
                self.library_changed.emit()
            return
        if chosen == action_change_lyrics:
            # 调用专门的函数处理歌词绑定更改，如果成功则刷新相关视图
            if _handle_track_lyrics_cell_action(self, self.facade, tracks, action="change_mapping"):
                QTimer.singleShot(0, self._reload_tracks_for_current_tag)  # 刷新当前标签下的轨道列表
                QTimer.singleShot(0, self.library_changed.emit)  # 发送库变更信号
            return
        if chosen == action_jump_lyrics:
            # 调用专门的函数处理跳转到歌词的动作
            _handle_track_lyrics_cell_action(self, self.facade, tracks, action="jump_to_lyrics")
            return
        if chosen == action_delete:
            self._on_delete_from_library()  # 将选中轨道移到回收站
            return
        if chosen == action_export:
            self._on_export()  # 导出选中的轨道
            return
        if chosen == action_reveal:
            # 获取第一个选中轨道的数据（如果有的话）
            first = tracks[0] if tracks else {}
            # 调用函数在文件管理器中显示该轨道对应的文件路径
            _reveal_in_file_manager(self, _storage_path_for_track_row(self.facade, first))
            return
        if chosen == action_copy:
            # 复制表格中选中单元格的数据到剪贴板
            _copy_selected_cells(self.grid.table)
            return
        if chosen == action_detail:
            # 显示第一个选中轨道的详细信息
            _show_track_details(self, tracks[0])

