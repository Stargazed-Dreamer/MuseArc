from __future__ import annotations

import logging

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from musearc.app.facade import MuseArcFacade
from musearc.ui.selection import SelectionController, SelectionMode
from musearc.ui.table_models import ColumnDef, DictTableModel
from musearc.ui.track_table_model import TrackTableModel
from musearc.ui.main_window_helpers import _apply_button_scale
from musearc.ui.long_task import run_modal_task

logger = logging.getLogger(__name__)


def _copy_selected_cells(table: QTableView) -> None:
    """复制选中的单元格到剪贴板。

    参数:
        table (QTableView): 表格视图对象。

    返回:
        None: 函数直接操作剪贴板，不返回值。
    """
    # 获取表格的选择模型
    selection_model = table.selectionModel()
    # 如果选择模型不存在，则直接返回
    if selection_model is None:
        return
    # 获取所有选中的单元格索引
    indexes = selection_model.selectedIndexes()
    # 如果没有选中索引，但表格有controller和model，则通过controller的selected_rows获取选中的行
    if not indexes and hasattr(table, "controller") and table.model() is not None:
        # 获取controller对象
        controller = getattr(table, "controller", None)
        # 获取选中的行，并排序
        selected_rows = sorted(getattr(controller, "selected_rows", set())) if controller is not None else []
        if selected_rows:
            # 获取表格模型
            model = table.model()
            # 遍历选中的行
            for row in selected_rows:
                # 遍历所有列
                for col in range(model.columnCount()):
                    # 创建单元格索引
                    idx = model.index(row, col)
                    # 如果索引有效，添加到索引列表
                    if idx.isValid():
                        indexes.append(idx)
    # 如果没有有效的索引，则直接返回
    if not indexes:
        return

    # 创建字典存储单元格数据，键为行号，值为列号到数据的映射
    cells: dict[int, dict[int, str]] = {}
    # 初始化最大列号
    max_col = 0
    # 遍历所有索引，构建cells字典
    for idx in indexes:
        row = idx.row()
        col = idx.column()
        # 更新最大列号
        max_col = max(max_col, col)
        # 将单元格数据转换为字符串，如果为空则使用空字符串
        cells.setdefault(row, {})[col] = str(idx.data() or "")

    # 创建列表存储每行的文本
    lines: list[str] = []
    # 按行号排序，遍历所有行
    for row in sorted(cells.keys()):
        # 获取当前行的所有列数据
        cols = cells[row]
        # 构建该行的单元格列表，从0到max_col，缺失的列用空字符串填充
        line = [cols.get(col, "") for col in range(max_col + 1)]
        # 将单元格列表用制表符连接，并添加到lines列表
        lines.append("\t".join(line))

    # 将所有行用换行符连接，并设置到剪贴板
    QApplication.clipboard().setText("\n".join(lines))


def _install_copy_support(table: QTableView) -> None:
    """
    为QTableView安装复制支持功能。

    通过设置键盘快捷键，允许用户复制表格中选中的单元格。

    参数:
        table (QTableView): 要安装复制功能的表格视图对象。

    返回:
        None: 此函数不返回任何值。
    """
    # 创建一个快捷键对象，使用标准复制键（Ctrl+C）绑定到表格视图
    shortcut = QShortcut(QKeySequence.StandardKey.Copy, table)
    # 连接快捷键的激活信号到复制函数，当快捷键被按下时触发复制操作
    shortcut.activated.connect(lambda: _copy_selected_cells(table))
    # 将快捷键存储为表格视图的属性，以便于后续访问或管理
    table._copy_shortcut = shortcut


def _next_sort_state(state: str) -> str:
    """获取下一个排序状态。

    参数：
        state (str): 当前排序状态，可以是 "asc"、"desc" 或 "off"。

    返回：
        str: 下一个排序状态。
    """
    if state == "asc":
        # 如果当前状态是升序，则返回降序
        return "desc"
    if state == "desc":
        # 如果当前状态是降序，则返回关闭
        return "off"
    # 默认情况下，返回升序状态
    return "asc"


def _safe_int(value, default: int = 0) -> int:
    """安全地将输入值转换为整数。如果输入是容器类型（列表、元组、集合、字典），则直接返回默认值；否则尝试转换，失败时也返回默认值。

    参数:
        value: 任意类型的值，将被尝试转换为整数。
        default: 整数，默认值，当转换失败或输入为容器类型时返回，默认为0。

    返回:
        int: 转换后的整数或指定的默认值。
    """
    # 如果值是容器类型（如列表、元组等），则返回默认值
    if isinstance(value, (list, tuple, set, dict)):
        return default
    try:
        # 尝试将值转换为整数；使用 value or 0 来处理空值或None
        return int(value or 0)
    except Exception:
        # 捕获任何转换异常，返回默认值
        return default


def _marker_for_state(state: str) -> str:
    """
    根据排序状态返回对应的标记符号。
    
    Args:
        state: 表示排序状态的字符串，预期为 "asc"、"desc" 或其他值。
        
    Returns:
        对应状态的标记符号：
        - "asc" 返回上箭头 "↑"
        - "desc" 返回下箭头 "↓"
        - 其他状态返回中点符号 "·"
    """
    if state == "asc":  # 升序状态
        return "↑"      # 返回上箭头标记
    if state == "desc": # 降序状态
        return "↓"      # 返回下箭头标记
    return "·"          # 默认/无序状态，返回中点符号


class TrackTableView(QTableView):
    context_menu_requested = Signal(object)
    ctrl_edit_requested = Signal(object)

    def __init__(self, controller: SelectionController):
        super().__init__()
        self.controller = controller
        self.edit_mode = False
        self._drag_origin: int | None = None
        self._drag_preview_base: set[int] | None = None
        self._dragging = False
        self._press_row: int | None = None

        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.setAlternatingRowColors(False)
        self.verticalHeader().setVisible(False)
        self.setTabKeyNavigation(True)

    def set_mode(self, mode: SelectionMode) -> None:
        """设置当前模式为指定模式。

        参数：
            mode (SelectionMode): 选择模式，用于控制视图的选择行为。
        返回值：
            None
        """
        self.controller.mode = mode  # 将控制器的模式设置为传入的mode。
        if mode == SelectionMode.MULTI:  # 检查是否为多选模式。
            self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)  # 设置选择模式为无选择，禁用选择高亮。
            self.setStyleSheet(  # 设置样式表，使选择背景透明，避免默认高亮。
                "QTableView{selection-background-color: transparent; selection-color: inherit;}"
                "QTableView::item:selected{background: transparent; color: inherit;}"
                "QTableView::item:selected:active{background: transparent; color: inherit;}"
                "QTableView::item:selected:!active{background: transparent; color: inherit;}"
                "QTableView::item:focus{border:1px solid #2f7dff;}"  # 为获得焦点的项添加蓝色边框。
            )
            self.clearSelection()  # 清除当前所有选择，确保状态干净。
        else:  # 如果不是多选模式。
            self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)  # 设置选择模式为扩展选择。
            self.setStyleSheet("")  # 清除样式表，恢复默认样式。
        self.apply_controller_selection()  # 应用控制器中的选择状态到视图。

    def set_edit_mode(self, enabled: bool) -> None:
        self.edit_mode = bool(enabled)

    def row_count(self) -> int:
        """返回模型中的行数。如果模型未设置，则返回0。

        参数：无（self为对象实例）。

        返回值：整数，表示行数。

        """
        if self.model() is None:  # 检查模型是否为None
            return 0  # 如果模型未设置，返回0
        return self.model().rowCount()  # 如果模型已设置，返回其行数

    def selected_rows(self) -> list[int]:
        """获取当前选中的行索引列表，按升序排列返回。根据控制器的选择模式执行不同逻辑，并同步视觉选择状态。
        无参数。
        返回：
            list[int]: 按升序排列的选中行索引列表。
        """
        # 判断控制器当前是否为多选模式
        if self.controller.mode == SelectionMode.MULTI:
            # 在多选模式下，从控制器已记录的选中行集合中获取行索引，并进行排序
            rows = sorted(self.controller.selected_rows)
            # 将当前数据模型的选中状态同步到视图的视觉选择上
            self._sync_visual_selection()
            return rows
        # 在其他模式（如单选）下，从视图的选择模型中获取所有选中的行索引，使用集合推导式去重，并排序
        rows = sorted({idx.row() for idx in self.selectionModel().selectedRows()})
        # 如果存在选中的行
        if rows:
            # 将处理后的选中行索引集合保存回控制器，用于记录状态
            self.controller.selected_rows = set(rows)
            # 更新控制器记录的焦点行（通常为最后选中的行）和锚点行（通常为首次选中的行），以支持键盘导航等
            self.controller.focus_row = rows[-1]
            self.controller.anchor_row = rows[0]
        # 同步视觉选择状态
        self._sync_visual_selection()
        return rows

    def _sync_visual_selection(self) -> None:
        """
        同步视觉选择。

        功能：根据控制器中选中的行，从模型获取对应的track_ids，并设置模型的视觉选择。
        参数：无（实例方法）。
        返回值：无。
        """
        model = self.model()  # 获取关联的模型
        if model is None:  # 如果模型不存在
            return  # 直接返回
        if not hasattr(model, "selected_track_ids_from_rows"):  # 检查模型是否有selected_track_ids_from_rows方法
            return  # 如果没有，直接返回
        if not hasattr(model, "set_visual_selected_track_ids"):  # 检查模型是否有set_visual_selected_track_ids方法
            return  # 如果没有，直接返回
        rows = sorted(self.controller.selected_rows)  # 排序控制器中选中的行
        track_ids = set(model.selected_track_ids_from_rows(rows))  # 从行获取track_ids并转换为集合
        model.set_visual_selected_track_ids(track_ids)  # 设置模型的视觉选择track_ids

    def set_selected_rows(self, rows: list[int]) -> None:
        """设置表格中选中的行，并更新焦点行和锚点行。

        Args:
            rows (list[int]): 需要设置为选中状态的行索引列表。

        Returns:
            None: 无返回值。
        """
        # 使用集合推导式筛选出有效的行索引（在0到总行数-1范围内），并转换为集合以提高查找效率
        rows_set = {r for r in rows if 0 <= r < self.row_count()}
        # 将筛选后的有效行集合赋值给控制器的selected_rows属性，更新选中状态
        self.controller.selected_rows = rows_set
        # 如果有选中的行，则需要设置焦点行和锚点行
        if rows_set:
            # 找出选中行中的最小行号作为焦点行
            row = min(rows_set)
            # 设置焦点行为当前行，键盘事件处理和滚动定位会基于此行
            self.controller.focus_row = row
            # 设置锚点行为当前行，用于范围选择时的起始点
            self.controller.anchor_row = row
        # 应用控制器中的选中状态，触发表格的重新绘制和相关事件处理
        self.apply_controller_selection()

    def apply_controller_selection(self) -> None:
        """应用控制器选择状态到视图。

        此方法根据控制器中存储的选择状态和焦点信息，
        同步更新当前视图的选择项和当前项。

        Args:
            self: 视图实例。

        Returns:
            None: 此方法不返回任何值。
        """
        # 如果模型或选择模型不存在，则无法操作，直接返回
        if self.model() is None or self.selectionModel() is None:
            return

        # 阻塞信号，避免在更新选择状态时触发不必要的视图或控制器更新
        self.blockSignals(True)

        # 清除视图当前的所有选择
        self.selectionModel().clearSelection()

        # 如果控制器处于普通选择模式，则处理行选择
        if self.controller.mode == SelectionMode.NORMAL:
            # 获取控制器中已选中的行号，并进行排序和范围检查
            rows = sorted(r for r in self.controller.selected_rows if 0 <= r < self.row_count())
            if rows:
                # 创建一个选择项对象，用于构建连续的选择范围
                selection = QItemSelection()
                # 初始化连续选择范围的起点和终点
                start = rows[0]
                end = rows[0]
                # 遍历已排序的行号（跳过第一个），尝试合并连续的行
                for row in rows[1:]:
                    # 如果当前行号等于上一个行号的下一行，说明是连续的，更新终点
                    if row == end + 1:
                        end = row
                        continue
                    # 否则，将之前积累的连续范围 [start, end] 添加到选择项中
                    selection.select(self.model().index(start, 0), self.model().index(end, 0))
                    # 开始一个新的连续范围
                    start = row
                    end = row
                # 添加最后一个连续范围 [start, end]
                selection.select(self.model().index(start, 0), self.model().index(end, 0))
                # 将构建好的选择项应用到视图的选择模型，选择整行
                self.selectionModel().select(
                    selection,
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )

        # 如果控制器指定了焦点行且范围有效，则设置当前项到该行
        if self.controller.focus_row is not None and 0 <= self.controller.focus_row < self.row_count():
            self.setCurrentIndex(self.model().index(self.controller.focus_row, 0))

        # 解除信号阻塞，恢复正常的信号发射
        self.blockSignals(False)

        # 调用内部方法，确保视图的视觉高亮与选择模型状态同步
        self._sync_visual_selection()
        # 强制视口重绘，以反映最新的选择和焦点状态
        self.viewport().update()

    def _row_at_event(self, event: QMouseEvent) -> int:
        idx = self.indexAt(event.pos())
        return idx.row() if idx.isValid() else -1

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.controller.mode == SelectionMode.NORMAL:
            idx = self.indexAt(event.pos())
            if event.button() == Qt.MouseButton.RightButton:
                sm = self.selectionModel()
                selected_rows = {i.row() for i in sm.selectedRows()} if sm is not None else set()
                if idx.isValid() and idx.row() not in selected_rows and sm is not None:
                    sm.clearSelection()
                    sm.select(
                        idx,
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                    )
                    self.setCurrentIndex(idx)
                self.selected_rows()
                return
            super().mousePressEvent(event)
            self.selected_rows()
            if self.edit_mode and event.button() == Qt.MouseButton.LeftButton and idx.isValid():
                model = self.model()
                if model is not None and bool(model.flags(idx) & Qt.ItemFlag.ItemIsEditable):
                    self.edit(idx)
            return

        idx = self.indexAt(event.pos())
        if not idx.isValid():
            return
        row = idx.row()

        if event.button() == Qt.MouseButton.RightButton:
            if row not in self.controller.selected_rows:
                self.controller.selected_rows = {row}
                self.controller.anchor_row = row
                self.controller.focus_row = row
                self.apply_controller_selection()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        if bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.controller.focus_row = row
            self.setCurrentIndex(idx)
            self.ctrl_edit_requested.emit(idx)
            return

        use_anchor = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier) and self.controller.anchor_row is not None
        self._drag_origin = self.controller.anchor_row if use_anchor else row
        self._drag_preview_base = set(self.controller.selected_rows)
        self._dragging = False
        self._press_row = row
        self.setCurrentIndex(idx)
        if not use_anchor:
            self.controller.anchor_row = row
        self._apply_drag_preview(row)

    def _apply_drag_preview(self, end_row: int) -> None:
        """应用拖拽选择预览。
    
        根据当前的拖拽起点 (`self._drag_origin`) 和参数传入的结束行 (`end_row`)，
        计算并应用一个临时选择范围，以实现拖拽过程中的视觉反馈。

        Args:
            end_row (int): 拖拽当前指向的目标行索引。

        Returns:
            None
        """
        # 如果拖拽起点或预览的基准选中状态不存在，则直接返回，不进行任何操作。
        if self._drag_origin is None or self._drag_preview_base is None:
            return

        # 确定本次拖拽预览所覆盖的实际行范围（起点到终点）。
        start = min(self._drag_origin, end_row)
        end = max(self._drag_origin, end_row)

        # 将行范围转换为一个集合，用于后续的集合运算。
        range_set = set(range(start, end + 1))

        # 计算新的选中行集合：在基准选中状态 `_drag_preview_base` 的基础上，
        # 进行对称差集操作。这会实现“切换”效果：原本选中的行在拖拽范围内会被取消选中，
        # 而原本未选中的行在拖拽范围内则会被选中。
        self.controller.selected_rows = self._drag_preview_base.symmetric_difference(range_set)

        # 设置控制器的锚点行（anchor）和焦点行（focus）。
        # 锚点通常代表选择的起始参考点，焦点是当前交互点。
        self.controller.anchor_row = self._drag_origin
        self.controller.focus_row = end_row

        # 将计算出的新选择状态应用到控制器，并通知界面更新。
        self.apply_controller_selection()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """处理鼠标移动事件，用于拖拽功能。

        参数:
            event (QMouseEvent): 鼠标事件对象。

        返回值:
            None: 无返回值。
        """
        if self.controller.mode == SelectionMode.NORMAL: # 如果控制器模式为正常模式
            super().mouseMoveEvent(event) # 调用父类方法处理事件
            return # 退出方法
        if self._drag_origin is None or self._drag_preview_base is None: # 如果拖拽原点或预览基础为空
            return # 退出方法，不执行拖拽逻辑
        row = self._row_at_event(event) # 根据鼠标事件获取所在行索引
        if row >= 0: # 如果行索引有效（非负）
            self._dragging = True # 设置拖拽状态为True
            self._apply_drag_preview(row) # 应用拖拽预览到该行

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """处理鼠标释放事件。
    
        根据控制器当前模式（普通模式或自定义模式）执行相应操作：
        1. 普通模式下，委托父类处理事件，并触发右键菜单信号。
        2. 自定义模式下，处理拖拽操作结束后的状态重置。
    
        Args:
            event (QMouseEvent): 鼠标事件对象，包含按键类型、位置等信息。
        
        Returns:
            None
        """
        # 如果当前是普通选择模式
        if self.controller.mode == SelectionMode.NORMAL:
            # 调用父类的标准鼠标释放处理逻辑
            super().mouseReleaseEvent(event)
            # 更新选中的行
            self.selected_rows()
            # 如果是右键释放，则触发上下文菜单请求信号
            if event.button() == Qt.MouseButton.RightButton:
                self.context_menu_requested.emit(event.globalPosition().toPoint())
            # 普通模式下处理完毕，直接返回
            return

        # 以下是自定义模式下的处理
        # 如果是右键释放，触发上下文菜单请求信号
        if event.button() == Qt.MouseButton.RightButton:
            self.context_menu_requested.emit(event.globalPosition().toPoint())
        # 如果是左键释放且之前有拖拽起始点（说明是拖拽操作结束）
        elif event.button() == Qt.MouseButton.LeftButton and self._drag_origin is not None:
            # 选择预览在按下/移动时已经应用，避免在释放时重复应用，
            # 否则在某些事件序列下，单击切换操作可能会出现延迟感。
            pass

        # 重置所有拖拽相关的状态变量
        self._drag_origin = None          # 拖拽操作的起始位置
        self._drag_preview_base = None    # 拖拽预览的基准数据
        self._dragging = False            # 是否处于拖拽状态的标志
        self._press_row = None            # 鼠标按下时所在的行

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        idx = self.indexAt(event.pos())
        if not idx.isValid():
            super().mouseDoubleClickEvent(event)
            return

        model = self.model()
        if model is None:
            super().mouseDoubleClickEvent(event)
            return
        if hasattr(model, "is_group_row") and model.is_group_row(idx.row()):
            return
        # 诊断：双击编辑触发
        key = model.column_key(idx.column()) if hasattr(model, "column_key") else ""
        editable = bool(model.flags(idx) & Qt.ItemFlag.ItemIsEditable)
        logger.debug("[TrackTableView] 双击: row=%d col=%d key=%s editable=%s", idx.row(), idx.column(), key, editable)
        print(f"[edit] 双击触发: row={idx.row()} col={idx.column()} key={key} editable={editable}")
        super().mouseDoubleClickEvent(event)

    def _move_cursor_and_edit(self, row_delta: int, col_delta: int) -> bool:
        current = self.currentIndex()
        model = self.model()
        if model is None or not current.isValid():
            return False
        row = max(0, min(model.rowCount() - 1, current.row() + row_delta))
        col = max(0, min(model.columnCount() - 1, current.column() + col_delta))
        idx = model.index(row, col)
        if not idx.isValid():
            return False
        self.setCurrentIndex(idx)
        if bool(model.flags(idx) & Qt.ItemFlag.ItemIsEditable):
            self.edit(idx)
        return True

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """
        处理键盘事件，根据按键和修饰键执行相应的操作。
        参数:
            event (QKeyEvent): 键盘事件对象。
        返回:
            None
        """
        key = event.key()  # 获取按下的键码
        mods = event.modifiers()  # 获取修饰键状态（如Shift、Ctrl等）
        if self.edit_mode:  # 如果处于编辑模式
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):  # 如果按下回车键或Enter键
                if bool(mods & Qt.KeyboardModifier.ShiftModifier):  # 检查Shift键是否按下
                    if self._move_cursor_and_edit(-1, 0):  # 向上移动光标并编辑
                        return
                else:  # 未按Shift键
                    if self._move_cursor_and_edit(1, 0):  # 向下移动光标并编辑
                        return
            if key == Qt.Key.Key_Tab:  # 如果按下Tab键
                if bool(mods & Qt.KeyboardModifier.ShiftModifier):  # 检查Shift键是否按下
                    if self._move_cursor_and_edit(0, -1):  # 向左移动光标并编辑
                        return
                else:  # 未按Shift键
                    if self._move_cursor_and_edit(0, 1):  # 向右移动光标并编辑
                        return

        if self.controller.mode == SelectionMode.NORMAL:  # 如果控制器模式为普通选择模式
            super().keyPressEvent(event)  # 调用父类的键盘事件处理方法
            self.selected_rows()  # 更新选中的行
            return

        total = self.row_count()  # 获取总行数

        if key == Qt.Key.Key_Up:  # 如果按下上箭头键
            self.controller.move_focus(total, -1)  # 向上移动焦点
            self.apply_controller_selection()  # 应用控制器选择
            return
        if key == Qt.Key.Key_Down:  # 如果按下下箭头键
            self.controller.move_focus(total, 1)  # 向下移动焦点
            self.apply_controller_selection()  # 应用控制器选择
            return
        if key == Qt.Key.Key_Left:  # 如果按下左箭头键
            row_h = max(1, self.verticalHeader().defaultSectionSize())  # 计算行高，最小为1
            visible = max(1, self.viewport().height() // row_h)  # 计算可见行数，最小为1
            self.controller.page_focus(total, visible, -1)  # 向上翻页移动焦点
            self.apply_controller_selection()  # 应用控制器选择
            return
        if key == Qt.Key.Key_Right:  # 如果按下右箭头键
            row_h = max(1, self.verticalHeader().defaultSectionSize())  # 计算行高，最小为1
            visible = max(1, self.viewport().height() // row_h)  # 计算可见行数，最小为1
            self.controller.page_focus(total, visible, 1)  # 向下翻页移动焦点
            self.apply_controller_selection()  # 应用控制器选择
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):  # 如果按下回车键、Enter键或空格键
            shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)  # 检查Shift键是否按下
            current = self.currentIndex()  # 获取当前索引
            model = self.model()  # 获取数据模型
            if self.edit_mode and current.isValid() and model is not None and bool(model.flags(current) & Qt.ItemFlag.ItemIsEditable):  # 如果处于编辑模式、当前索引有效、模型存在且项可编辑
                self.edit(current)  # 编辑当前项
                return
            self.controller.keyboard_activate(shift=shift)  # 激活键盘操作，考虑Shift键状态
            self.apply_controller_selection()  # 应用控制器选择
            return

        super().keyPressEvent(event)  # 如果未匹配任何按键，调用父类的键盘事件处理方法


class TrackGridWidget(QWidget):
    track_field_edited = Signal(str, str, object)
    context_menu_requested = Signal(object, list)

    def __init__(self, facade: MuseArcFacade):
        """
        初始化轨道列表视图界面，设置UI组件和信号连接。

        功能：
            - 创建并配置主界面布局，包括工具栏、表格视图和状态栏
            - 初始化表格数据模型和选择控制器
            - 设置各种UI控件（下拉框、复选框、按钮等）及其信号槽连接
            - 配置表格视图的列头属性和交互行为
            - 初始化排序状态和选中记录管理功能

        参数：
            facade (MuseArcFacade): 门面对象，用于与其他模块交互

        返回值：
            无（初始化方法，无返回值）
        """
        super().__init__()
        self.facade = facade  # 保存门面对象的引用
        self.controller = SelectionController()  # 创建选择控制器
        self._base_status = "准备就绪"  # 基础状态文本
        self._sort_states: dict[str, str] = {}  # 存储各列的排序状态
        self._bulk_edit_session: dict | None = None  # 批量编辑会话状态

        root = QVBoxLayout(self)  # 创建主垂直布局

        # 创建控制栏布局
        ctrl = QHBoxLayout()
        self.lbl_group_mode = QLabel("分组模式")

        # 创建分组模式下拉框
        self.combo_group = QComboBox()
        self.combo_group.addItem("不分组", "none")
    
        # 创建多选和编辑模式复选框
        self.chk_multi = QCheckBox("多选模式")
        self.chk_edit_mode = QCheckBox("编辑模式")
    
        # 创建功能按钮
        self.btn_invert = QPushButton("反选")
        self.btn_save_selection = QPushButton("保存选中")
        self.btn_apply_snapshot = QPushButton("应用选中记录")
    
        # 创建选中记录下拉框
        self.snapshot_combo = QComboBox()
        self.snapshot_combo.setMinimumWidth(170)  # 设置最小宽度

        # 将控件添加到控制栏布局
        ctrl.addWidget(self.btn_invert)
        ctrl.addWidget(self.lbl_group_mode)
        ctrl.addWidget(self.combo_group)
        ctrl.addWidget(self.chk_multi)
        ctrl.addWidget(self.chk_edit_mode)
        ctrl.addWidget(self.btn_save_selection)
        ctrl.addWidget(self.snapshot_combo)
        ctrl.addWidget(self.btn_apply_snapshot)
        ctrl.addStretch(1)  # 添加伸缩项，使控件靠左对齐

        # 创建表格数据模型和视图
        self.model = TrackTableModel()
        self.table = TrackTableView(self.controller)
        self.table.setModel(self.model)
    
        # 配置表格水平表头属性
        self.table.horizontalHeader().setStretchLastSection(False)  # 最后一列不自动拉伸
        self.table.horizontalHeader().setSectionsMovable(True)  # 允许拖动列头调整顺序
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)  # 交互式调整列宽
        self.table.horizontalHeader().setVisible(True)  # 显示水平表头

        # 安装表格复制支持
        _install_copy_support(self.table)

        # 创建状态栏标签
        self.status = QLabel("准备就绪")

        # 将组件添加到主布局
        root.addLayout(ctrl)  # 添加控制栏
        root.addWidget(self.table, 1)  # 添加表格，设置拉伸因子为1
        root.addWidget(self.status)  # 添加状态栏

        # 连接表格列头点击信号到排序处理函数
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
    
        # 连接列头移动信号到排序同步函数
        self.table.horizontalHeader().sectionMoved.connect(lambda *_args: self._sync_sort_from_header())
    
        # 连接分组模式变化信号
        self.combo_group.currentIndexChanged.connect(self._on_group_changed)
    
        # 连接多选模式切换信号
        self.chk_multi.toggled.connect(self._on_toggle_multi)
    
        # 连接编辑模式切换信号
        self.chk_edit_mode.toggled.connect(self._on_toggle_edit_mode)
    
        # 连接反选按钮点击信号
        self.btn_invert.clicked.connect(self._on_invert_selection)
    
        # 连接保存选中按钮点击信号
        self.btn_save_selection.clicked.connect(self._on_save_snapshot)
    
        # 连接应用选中记录按钮点击信号
        self.btn_apply_snapshot.clicked.connect(self._on_apply_snapshot)
    
        # 连接模型中轨道字段编辑信号
        self.model.track_field_edited.connect(self._on_model_track_field_edited)
    
        # 连接表格点击和双击信号
        self.table.clicked.connect(self._on_table_clicked)
        self.table.doubleClicked.connect(self._on_table_double_clicked)
    
        # 连接自定义上下文菜单请求信号
        self.table.context_menu_requested.connect(self._on_context_menu_requested)
    
        # 连接控制编辑请求信号
        self.table.ctrl_edit_requested.connect(self._on_ctrl_edit_requested)
    
        # 连接表格选择变化信号到状态刷新函数
        self.table.selectionModel().selectionChanged.connect(lambda *_args: self._refresh_status())
    
        # 设置模型的空编辑确认回调
        self.model.set_confirm_empty_edit_callback(self._confirm_empty_edit)

        # 初始化门面连接和排序状态
        self.set_facade(facade)
        self._init_sort_states()
        self._sync_sort_from_header()

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade
        cfg = self.facade.get_runtime_config()
        self.force_save_threshold = int(cfg.ui.force_save_threshold)
        self.model.set_confirm_empty_edit_callback(self._confirm_empty_edit)
        self.refresh_tag_fields()

    def set_button_scale(self, scale: float) -> None:
        """
        设置按钮的缩放比例。

        此方法通过调用辅助函数 _apply_button_scale，将指定的缩放比例应用到类中的多个按钮实例。

        参数:
            scale (float): 按钮需要缩放的倍数。例如，1.0 表示原始大小，0.5 表示缩小一半，2.0 表示放大一倍。

        返回值:
            None: 此方法不返回任何值，其作用是直接修改按钮的视觉大小。
        """
        # 对“反转”按钮应用缩放
        _apply_button_scale(self.btn_invert, scale)
        # 对“保存选区”按钮应用缩放
        _apply_button_scale(self.btn_save_selection, scale)
        # 对“应用快照”按钮应用缩放
        _apply_button_scale(self.btn_apply_snapshot, scale)

    def set_tracks(self, rows: list[dict], *, entry_editable: bool = False) -> None:
        """
        设置轨道数据，并根据entry_editable参数调整排序和编辑状态。

        参数:
            rows (list[dict]): 包含轨道信息的字典列表。
            entry_editable (bool): 是否启用条目编辑，默认为False。

        返回:
            None
        """
        keep_ids = list(self.model.visual_selected_track_ids)  # 保存当前选中的轨道ID列表
        focus_track_id = self._focus_track_id()  # 获取当前焦点轨道ID
        self.model.set_custom_order_enabled(bool(entry_editable))  # 根据entry_editable设置是否启用自定义顺序
        prepared = []  # 初始化准备好的轨道列表
        for row in rows:  # 遍历输入的行数据
            item = dict(row)  # 将行转换为字典副本
            item["_entry_editable"] = bool(entry_editable and "entry" in item)  # 根据条件设置可编辑标志
            prepared.append(item)  # 添加到准备列表
        self.model.set_tracks(prepared)  # 将准备好的轨道数据设置到模型
        if "custom_order" not in self._sort_states:  # 如果排序状态中没有自定义顺序
            self._init_sort_states()  # 初始化排序状态字典
        active_keys = [k for k, v in self._sort_states.items() if v in {"asc", "desc"}]  # 提取当前活跃的排序键（升序或降序）
        if entry_editable and (not active_keys or active_keys == ["file_name"]):  # 如果启用了条目编辑且无活跃排序或仅文件名排序
            for key in list(self._sort_states.keys()):  # 遍历所有排序键
                self._sort_states[key] = "off"  # 关闭所有排序状态
            self._sort_states["custom_order"] = "asc"  # 启用自定义顺序的升序
        if not entry_editable and active_keys == ["custom_order"]:  # 如果未启用条目编辑且仅自定义顺序活跃
            self._sort_states["custom_order"] = "off"  # 关闭自定义顺序
            self._sort_states["file_name"] = "asc"  # 启用文件名排序的升序
        self._sync_sort_from_header()  # 同步排序状态到界面表头
        self._restore_selection_by_ids(keep_ids, focus_track_id)  # 恢复之前保存的选中状态和焦点
        self._base_status = f"已加载 {len(rows)} 条"  # 设置基本状态消息为加载的行数
        self._refresh_status()  # 刷新状态显示

    def set_status(self, text: str) -> None:
        """
        功能：设置对象的基础状态，并刷新状态。
        参数：
            text (str): 要设置的状态文本，字符串类型。
        返回值：
            None（无返回值）
        """
        self._base_status = text  # 将基础状态属性设置为传入的文本
        self._refresh_status()    # 调用刷新状态方法以更新相关显示或操作

    def _refresh_status(self) -> None:
        """更新状态栏显示。
    
        根据当前已选中的轨道数量，刷新界面底部的状态栏文本。
    
        参数：
            无。
        返回值：
            无。
        """
        selected = len(self.selected_track_ids())  # 获取已选轨道ID列表并计算其长度
        self.status.setText(f"{self._base_status} | 已选 {selected} 条")  # 更新状态栏文本，包含基础状态和已选数量

    def selected_tracks(self) -> list[dict]:
        """获取表格中选中行对应的曲目信息。

        Args:
            无

        Returns:
            包含曲目信息的字典列表。每个字典包含一个选中的曲目数据。
        """
        rows = self.table.selected_rows()  # 获取表格中当前被选中的所有行
        out = []  # 初始化一个空列表，用于收集结果
        for row in rows:  # 遍历每一行
            track = self.model.track_for_row(row)  # 根据行索引从模型中获取对应的曲目数据
            if track and track.get("track_id"):  # 筛选出有效（非空且包含track_id）的曲目信息
                out.append(track)  # 将有效的曲目字典添加到结果列表中
        return out  # 返回所有选中且有效的曲目信息列表

    def selected_track_ids(self) -> list[str]:
        return [str(t.get("track_id", "")) for t in self.selected_tracks() if t.get("track_id")]

    def select_track_ids(self, track_ids: list[str]) -> None:
        """根据指定的track IDs选择对应的表格行。

        参数：
        track_ids (list[str]): 要选择的track IDs列表。

        返回值：
        None
        """
        # 将track_ids转换为集合以去重，并获取对应的行索引
        row_indexes = self.model.row_indexes_for_track_ids(set(track_ids))
        # 使用行索引设置表格的选中行
        self.table.set_selected_rows(row_indexes)
        # 刷新状态以更新显示
        self._refresh_status()

    def _focus_track_id(self) -> str | None:
        """获取当前聚焦轨道的ID。

        本方法通过当前聚焦行查找对应的轨道，并返回该轨道的ID。
        如果当前没有聚焦行，或聚焦行没有对应的轨道，或轨道没有ID，则返回None。

        参数：
            无

        返回：
            str | None: 聚焦轨道的ID字符串，若不存在则返回None。
        """
        # 如果当前没有聚焦行，则直接返回None
        if self.controller.focus_row is None:
            return None
        # 根据聚焦行查找对应的轨道信息
        track = self.model.track_for_row(self.controller.focus_row)
        # 如果找到了有效的轨道且该轨道包含'track_id'字段，则将其转换为字符串并返回
        if track and track.get("track_id"):
            return str(track.get("track_id"))
        # 其他情况返回None
        return None

    def _restore_selection_by_ids(self, track_ids: list[str], focus_track_id: str | None = None) -> None:
        """根据给定的track ID列表恢复选择状态。

        参数:
            track_ids (list[str]): 要恢复的track ID列表。
            focus_track_id (str | None, 可选): 要聚焦的track ID。默认为None。

        返回值:
            None
        """
        rows = self.model.row_indexes_for_track_ids(set(track_ids))  # 获取track ID对应的行索引集合
        self.controller.selected_rows = set(rows)  # 设置控制器的选中行为这些行索引
        if rows:  # 如果有选中的行
            if focus_track_id:  # 如果指定了要聚焦的track ID
                focus_rows = self.model.row_indexes_for_track_ids({focus_track_id})  # 获取聚焦track ID的行索引
                if focus_rows:  # 如果找到了聚焦行索引
                    self.controller.focus_row = focus_rows[0]  # 设置焦点行为第一个聚焦行索引
                    self.controller.anchor_row = focus_rows[0]  # 设置锚点行为第一个聚焦行索引
                else:  # 如果没有找到聚焦行索引
                    self.controller.focus_row = min(rows)  # 默认焦点行为选中行中的最小索引
                    self.controller.anchor_row = min(rows)  # 默认锚点行为选中行中的最小索引
            else:  # 如果没有指定聚焦track ID
                self.controller.focus_row = min(rows)  # 焦点行为选中行中的最小索引
                self.controller.anchor_row = min(rows)  # 锚点行为选中行中的最小索引
        else:  # 如果没有选中的行
            if self.controller.mode == SelectionMode.NORMAL:  # 如果控制器处于普通选择模式
                self.controller._normalize_for_normal(self.model.rowCount())  # 调用方法标准化选择状态
            else:  # 其他模式
                self.controller.selected_rows.clear()  # 清空选中的行
                self.controller.anchor_row = None  # 锚点行设为None
                self.controller.focus_row = None  # 焦点行设为None
        self.table.apply_controller_selection()  # 将控制器的选择状态应用到表格上

    def _init_sort_states(self) -> None:
        """初始化排序状态。

        参数：
            self (实例自身): 当前类的实例。

        返回值：
            None
        """
        keys = [self.model.column_key(i) for i in range(self.model.columnCount())]  # 获取所有列的键
        keep: dict[str, str] = {}  # 初始化字典，用于保存排序状态
        for key in keys:
            keep[key] = self._sort_states.get(key, "off")  # 从现有排序状态中获取状态，默认为 "off"
        if all(state == "off" for state in keep.values()):  # 检查是否所有列的排序状态都是 "off"
            if "custom_order" in keep and bool(self.model.custom_order_enabled) and any(
                bool(r.get("_entry_editable")) for r in self.model.raw_tracks
            ):  # 如果 "custom_order" 列存在，且自定义排序启用，且任何轨道可编辑
                keep["custom_order"] = "asc"  # 则设置为升序
            elif "file_name" in keep:  # 否则，如果 "file_name" 列存在
                keep["file_name"] = "asc"  # 则设置为升序
        self._sort_states = keep  # 更新排序状态
        self.model.set_header_sort_states(self._sort_states)  # 通知模型排序状态已更改

    def _sync_sort_from_header(self) -> None:
        """从表头同步排序状态到模型，并恢复之前选中的轨道和焦点。

        该方法从表头获取排序状态并应用到模型，同时恢复之前选中的轨道ID和焦点轨道。

        参数：
            无。
        返回值：
            无。
        """
        selected_ids = list(self.model.visual_selected_track_ids)  # 获取当前可视选中的轨道ID列表
        focus_track_id = self._focus_track_id()  # 获取焦点轨道的ID
        header = self.table.horizontalHeader()  # 获取表头对象
        logical_indexes = sorted(range(self.model.columnCount()), key=lambda i: header.visualIndex(i))  # 根据表头的可视索引排序逻辑索引，以获取列的实际显示顺序
        rules = []  # 初始化排序规则列表
        for logical in logical_indexes:  # 遍历逻辑索引，为每个列确定排序状态
            key = self.model.column_key(logical)  # 获取列的键
            state = self._sort_states.get(key, "off")  # 从排序状态字典中获取状态，默认为"off"
            rules.append({"key": key, "state": state})  # 将键和状态添加到规则列表
        self.model.set_header_sort_states(self._sort_states)  # 设置模型的表头排序状态
        self.model.set_sort_rules(rules)  # 设置排序规则
        self._restore_selection_by_ids(selected_ids, focus_track_id)  # 通过ID恢复选择状态
        self._refresh_status()  # 刷新状态

    def _on_header_clicked(self, logical_section: int) -> None:
        """当表头被点击时触发排序状态更新。参数：logical_section: int，表示逻辑列的索引。返回值：无。"""
        key = self.model.column_key(logical_section)  # 获取指定逻辑列的键
        if not key:  # 如果键不存在，直接返回
            return
        self._sort_states[key] = _next_sort_state(self._sort_states.get(key, "off"))  # 更新该列的排序状态为下一个状态
        self._sync_sort_from_header()  # 同步排序状态到头部

    def _rebuild_group_combo(self) -> None:
        """重建分组下拉框（combo_group）的内容，并保持之前选中的分组项。
        该方法会清除下拉框的原有选项，重新从数据模型中添加所有可用的列作为分组依据，
        然后尝试恢复用户之前选中的分组项。主要用于在数据列发生变化后刷新UI。
        """
        # 记录当前用户选中的分组键，如果为空则默认为 "none"（不分组）
        keep_key = str(self.combo_group.currentData() or "none")
        # 临时阻塞信号，避免在重建过程中触发不必要的事件（如 currentIndexChanged）
        self.combo_group.blockSignals(True)
        # 清空下拉框中的所有现有项
        self.combo_group.clear()
        # 添加一个固定的“不分组”选项，其关联数据为 "none"
        self.combo_group.addItem("不分组", "none")
        # 遍历数据模型的所有列
        for idx in range(self.model.columnCount()):
            # 获取该列对应的键（用于程序内部标识）
            key = self.model.column_key(idx)
            # 获取该列的显示标题（表头文本）
            label = self.model.headerData(idx, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
            # 对标题文本进行处理，去掉末尾可能存在的空格和序号（如 “名称 #1” -> “名称”）
            text = str(label).rsplit(" ", 1)[0]
            # 将处理后的文本作为显示项，列的键作为关联数据，添加到下拉框中
            self.combo_group.addItem(text, key)
        # 根据之前记录的键（keep_key），在下拉框中查找其索引
        target = self.combo_group.findData(keep_key)
        # 设置当前选中的项，如果找不到则使用索引0（即“不分组”项）
        self.combo_group.setCurrentIndex(max(0, target))
        # 重新启用信号，恢复下拉框的正常事件响应
        self.combo_group.blockSignals(False)

    def refresh_tag_fields(self) -> None:
        rows = self.facade.list_tag_fields()
        names = [str(r.get("tag_name", "")).strip() for r in rows if str(r.get("tag_name", "")).strip()]
        self.model.set_tag_fields(names)
        self._init_sort_states()
        self._sync_sort_from_header()
        self._rebuild_group_combo()

    def _on_group_changed(self) -> None:
        """功能：处理组变更事件，当用户改变组合框选择时触发。
        参数：无（self为实例自身）。
        返回值：None。
        """
        selected_ids = list(self.model.visual_selected_track_ids)  # 获取当前选中的轨道ID列表
        focus_track_id = self._focus_track_id()  # 获取当前焦点轨道的ID
        self.model.set_group_by(str(self.combo_group.currentData()))  # 根据组合框的当前值设置分组方式
        self._restore_selection_by_ids(selected_ids, focus_track_id)  # 恢复之前的选择状态
        self._refresh_status()  # 刷新界面状态

    def _on_toggle_multi(self, checked: bool) -> None:
        """
        处理多选模式切换事件。

        根据复选框的选中状态，切换列表的选择模式为多选模式（MULTI）或普通模式（NORMAL），
        并同步更新相关的控制器、表格、下拉框和状态栏。

        参数:
            checked (bool): 如果为 True，则启用多选模式；如果为 False，则切换回普通模式。

        返回:
            None
        """
        # 根据 checked 参数决定目标选择模式
        mode = SelectionMode.MULTI if checked else SelectionMode.NORMAL
        # 通知控制器更新选择模式，并传递当前行数和强制保存阈值
        self.controller.set_mode(mode, self.model.rowCount(), self.force_save_threshold)
        # 更新表格视图的显示模式
        self.table.set_mode(mode)
        # 刷新与快照相关的下拉框选项（可能因模式改变而需要更新）
        self.refresh_snapshot_combo()
        # 刷新状态栏的显示信息
        self._refresh_status()

    def _on_toggle_edit_mode(self, checked: bool) -> None:
        self.table.set_edit_mode(bool(checked))

    def _on_invert_selection(self) -> None:
        """反选当前可见的轨道行。参数：无。返回值：无。"""
        visible_track_rows: list[int] = [idx for idx, row_obj in enumerate(self.model.display_rows) if row_obj.get("kind") == "track"]  # 获取所有可见轨道行的索引列表
        if not visible_track_rows:  # 如果没有可见轨道行，则直接返回
            return
        visible_set = set(visible_track_rows)  # 将可见轨道行索引转换为集合，便于后续操作
        current = {r for r in self.controller.selected_rows if r in visible_set}  # 获取当前已选中的行中，属于可见轨道行的部分

        target_rows: set[int]  # 声明目标行集合，用于存储反选后的行
        if len(visible_track_rows) >= 20000:  # 如果可见轨道行数量大于或等于20000，使用模态任务处理以避免界面卡顿
            snapshot_rows = list(visible_track_rows)  # 创建快照，避免在任务中修改原列表
            snapshot_current = set(current)  # 创建当前选中集合的快照

            def _task(progress, is_cancelled):  # 定义模态任务函数，处理反选逻辑
                total = max(1, len(snapshot_rows))  # 计算总行数，避免除零错误
                selected: list[int] = []  # 初始化选中行列表，用于存储反选结果
                step = max(1, total // 200)  # 设置进度更新的步长，控制更新频率
                for idx, row in enumerate(snapshot_rows, 1):  # 遍历所有可见行，索引从1开始
                    if is_cancelled():  # 检查任务是否被取消
                        return {"rows": selected, "cancelled": True}  # 返回已选中行和取消状态
                    if row not in snapshot_current:  # 如果行不在当前选中集合中，则添加到选中列表
                        selected.append(row)
                    if idx == total or (idx % step == 0):  # 如果是最后一行或达到步长，更新进度
                        progress(idx, total, "正在计算反选")
                return {"rows": selected, "cancelled": False}  # 返回选中行和未取消状态

            outcome = run_modal_task(self, "反选", _task)  # 运行模态任务执行反选操作
            if outcome.error is not None:  # 如果任务出错，显示警告信息
                QMessageBox.warning(self, "反选失败", f"反选失败\n{outcome.error}")
                return
            payload = outcome.result if isinstance(outcome.result, dict) else {}  # 获取任务结果，如果是字典则使用，否则为空字典
            if bool(payload.get("cancelled")) and not payload.get("rows"):  # 如果任务被取消且没有行选中
                self.set_status("反选已取消")  # 设置状态提示
                return
            target_rows = {int(v) for v in payload.get("rows", [])}  # 从结果中提取目标行集合
        else:  # 如果可见行少于20000，直接计算反选
            target_rows = visible_set.difference(current)  # 计算反选：可见行集合减去当前选中集合

        self.controller.selected_rows = target_rows  # 将目标行集合设置为控制器的选中行
        if self.controller.selected_rows:  # 如果有选中行
            focus = min(self.controller.selected_rows)  # 获取最小索引的行作为焦点
            self.controller.focus_row = focus  # 设置焦点行为该行
            self.controller.anchor_row = focus  # 设置锚点行为该行
        else:  # 如果没有选中行
            self.controller.focus_row = None  # 清空焦点行
            self.controller.anchor_row = None  # 清空锚点行
        self.table.apply_controller_selection()  # 应用选中状态到表格
        self._refresh_status()  # 刷新界面状态显示

    def _on_save_snapshot(self) -> None:
        """当保存快照时调用，执行保存快照并刷新快照组合框的操作。

        参数:
            self: 实例对象本身。

        返回值:
            None
        """
        self.controller.save_snapshot()  # 保存快照
        self.refresh_snapshot_combo()  # 刷新快照组合框

    def _on_apply_snapshot(self) -> None:
        index = self.snapshot_combo.currentIndex()
        if index < 0:
            return
        self.controller.load_snapshot(index)
        self.table.apply_controller_selection()
        self._refresh_status()

    def refresh_snapshot_combo(self) -> None:
        """刷新快照下拉框组件的选项列表。
        功能：清空并重新生成当前“快照记录”下拉框（QComboBox）的所有选项。
        参数：self - 实例本身。
        返回：None
        """
        # 暂时屏蔽下拉框的信号，避免在添加项目时触发如currentIndexChanged等事件。
        self.snapshot_combo.blockSignals(True)
        # 清空当前所有选项，为重新生成列表做准备。
        self.snapshot_combo.clear()
        # 遍历控制器（controller）中保存的所有快照记录（saved_snapshots）。
        for i, snap in enumerate(self.controller.saved_snapshots):
            # 为每个快照添加一个格式化标签，显示序号（从1开始）和该快照包含的数据点数量。
            self.snapshot_combo.addItem(f"记录{i + 1} ({len(snap)})")
        # 重新启用下拉框的信号响应。
        self.snapshot_combo.blockSignals(False)

    def _on_table_clicked(self, index: QModelIndex) -> None:
        """
        处理表格单元格被点击的事件。
        根据被点击的行是否为分组行，执行不同的选中和状态刷新逻辑。

        参数:
            index (QModelIndex): 被点击单元格的模型索引。
        返回:
            None
        """
        row = index.row()  # 获取被点击行的索引
        if not self.model.is_group_row(row):  # 如果点击的不是分组行
            if self.controller.mode == SelectionMode.NORMAL:  # 检查控制器是否为普通选择模式
                self.table.selected_rows()  # 处理普通行的选中逻辑
            self._refresh_status()  # 刷新界面状态栏
            return  # 处理完毕，结束方法

        # 以下处理点击的是分组行的情况
        ids = self.model.group_track_ids(row)  # 获取该分组下所有曲目的ID
        rows = self.model.row_indexes_for_track_ids(set(ids))  # 根据ID查找对应的行索引集合
        if not rows:  # 如果找不到对应的行
            return  # 直接返回

        # 以下是展开分组并选中其下所有行的逻辑
        self.controller.selected_rows = set(rows)  # 将控制器选中的行设置为该分组的所有行
        self.controller.anchor_row = min(rows)  # 设置选择的锚点行为最小的行索引
        self.controller.focus_row = min(rows)  # 设置焦点行为最小的行索引
        self.table.apply_controller_selection()  # 将控制器的选中状态应用到表格控件上
        self._refresh_status()  # 刷新界面状态栏

    def _on_table_double_clicked(self, index: QModelIndex) -> None:
        """处理表格双击事件。当双击组行时切换组状态，当双击歌词文件名列时触发字段编辑信号。
    
        参数：
            index (QModelIndex): 被双击的索引。
    
        返回值：
            None
        """
        row = index.row()  # 获取被双击的行索引
        if self.model.is_group_row(row):  # 检查是否为组行
            self.model.toggle_group_row(row)  # 切换组行的展开/折叠状态
            self.table.apply_controller_selection()  # 应用控制器选择到表格
            self._refresh_status()  # 刷新状态栏或相关显示
            return  # 处理完组行后返回，不再执行后续逻辑
        if self.model.column_key(index.column()) == "lyrics_file_name":  # 检查是否为歌词文件名列
            track = self.model.track_for_row(row) or {}  # 获取对应行的轨道信息，如果不存在则为空字典
            track_id = str(track.get("track_id", "") or "")  # 提取轨道ID，并转换为字符串，如果不存在则为空字符串
            if track_id:  # 如果轨道ID有效
                self.track_field_edited.emit(track_id, "lyrics_file_name", "")  # 触发字段编辑信号，参数为轨道ID、字段名和空值
            return  # 处理完歌词文件名列后返回

    def _on_ctrl_edit_requested(self, index: QModelIndex) -> None:
        """
        处理表格单元格的编辑请求。

        当用户请求编辑表格中的某个单元格时，此方法会被调用。它负责执行编辑前的各种检查，
        设置批量编辑会话（如果适用），并最终触发编辑操作。

        参数:
            index (QModelIndex): 被请求编辑的单元格的模型索引。

        返回值:
            None
        """
        # 检查索引是否有效，无效则直接返回
        if not index.isValid():
            return
        # 检查该行是否为组行（如专辑标题行），组行不应被直接编辑
        if self.model.is_group_row(index.row()):
            return
        # 检查被点击的列是否为“歌词文件名”列
        if self.model.column_key(index.column()) == "lyrics_file_name":
            # 获取当前行的音轨数据，如果不存在则默认为空字典
            track = self.model.track_for_row(index.row()) or {}
            # 安全地获取 track_id，并确保为字符串
            track_id = str(track.get("track_id", "") or "")
            # 如果存在有效的 track_id，则发射信号清空该音轨的歌词文件名字段
            if track_id:
                self.track_field_edited.emit(track_id, "lyrics_file_name", "")
            return
        # 检查当前单元格的标志位，判断其是否可编辑
        if not bool(self.model.flags(index) & Qt.ItemFlag.ItemIsEditable):
            return

        # 获取当前行对应音轨的源数据
        source = self.model.track_for_row(index.row()) or {}
        # 提取源音轨的 track_id
        source_id = str(source.get("track_id", ""))
        # 获取当前单元格对应的字段键（如 'title', 'artist'）
        key = self.model.column_key(index.column())
        # 获取所有当前选中的音轨，并提取其 track_id 列表
        targets = [str(t.get("track_id", "")) for t in self.selected_tracks() if t.get("track_id")]
        # 判断是否需要启动批量编辑会话：
        # 条件是：1. 源ID有效；2. 源ID在目标列表中（即被点击的行也被选中）；3. 总共选中了多条音轨。
        if source_id and source_id in targets and len(targets) > 1:
            # 设置批量编辑会话信息，记录源ID、编辑字段和所有目标ID
            self._bulk_edit_session = {"source_track_id": source_id, "key": key, "target_ids": targets}
        else:
            # 否则，清空批量编辑会话
            self._bulk_edit_session = None

        # 将表格视图的当前索引设置为被点击的单元格
        self.table.setCurrentIndex(index)
        # 触发对该单元格的编辑操作
        self.table.edit(index)

    def _on_model_track_field_edited(self, track_id: str, key: str, value) -> None:
        """
        当模型中的轨道字段被编辑时调用此方法。

        功能：接收编辑信号，记录日志，发射信号，并在批量编辑会话存在时批量应用值到其他轨道。

        参数：
            track_id (str): 被编辑的轨道ID。
            key (str): 被编辑的字段键名。
            value: 被编辑的字段值。

        返回值：
            None
        """
        # 保存当前选中的轨道ID列表，以便在操作后恢复选择
        keep_ids = list(self.model.visual_selected_track_ids)
        # 记录日志，显示收到的编辑信号信息
        logger.info("[TrackGridWidget] 收到 model 编辑信号: tid=%s key=%s value=%r", track_id, key, value)
        # 打印调试信息，显示编辑的详细信息
        print(f"[edit] Grid中转: tid={track_id} key={key} value={value!r}")
        # 发射track_field_edited信号，通知其他部分编辑已发生
        self.track_field_edited.emit(track_id, key, value)

        # 获取当前的批量编辑会话
        session = self._bulk_edit_session
        # 如果没有批量编辑会话，则恢复选择并返回
        if not session:
            self._restore_selection_by_ids(keep_ids, track_id)
            return
        # 检查会话中的源轨道ID是否与当前track_id匹配，不匹配则恢复选择并返回
        if str(session.get("source_track_id")) != str(track_id):
            self._restore_selection_by_ids(keep_ids, track_id)
            return
        # 检查会话中的键是否与当前key匹配，不匹配则恢复选择并返回
        if str(session.get("key")) != str(key):
            self._restore_selection_by_ids(keep_ids, track_id)
            return

        # 从会话中获取目标轨道ID列表，过滤掉空字符串和与当前track_id相同的ID
        target_ids = [tid for tid in session.get("target_ids", []) if str(tid) and str(tid) != str(track_id)]
        # 如果没有有效的目标轨道ID，则清空会话、恢复选择并返回
        if not target_ids:
            self._bulk_edit_session = None
            self._restore_selection_by_ids(keep_ids, track_id)
            return

        # 应用值到所有目标轨道
        self.model.apply_value_to_tracks(set(target_ids), key, value)
        # 为每个目标轨道发射编辑信号
        for tid in target_ids:
            self.track_field_edited.emit(str(tid), key, value)
        # 清空批量编辑会话
        self._bulk_edit_session = None
        # 恢复之前的选择状态
        self._restore_selection_by_ids(keep_ids, track_id)

    def _on_context_menu_requested(self, global_pos) -> None:
        self.context_menu_requested.emit(global_pos, self.selected_tracks())

    def _confirm_empty_edit(self, _track_id: str, _key: str) -> bool:
        """显示一个确认对话框，让用户在编辑内容为空时选择是保留原值还是将值设为空。

        Args:
            _track_id (str): 相关记录的轨道ID。
            _key (str): 正在编辑的配置项或属性的键名。

        Returns:
            bool: 如果用户选择“留空保存”则返回 True，否则返回 False。
        """
        # 从外观层获取当前的运行时配置对象
        cfg = self.facade.get_runtime_config()
        # 检查配置中是否设置了在空编辑时显示确认提示框
        if not bool(cfg.ui.prompt_empty_edit_confirm):
            return True  # 如果配置为不显示提示，则直接视为用户确认，返回True
        # 创建一个模态消息框，其父窗口为self
        box = QMessageBox(self)
        # 设置消息框的窗口标题
        box.setWindowTitle("空值确认")
        # 设置消息框的主要提示文本
        box.setText("当前输入为空。请选择保留原值，或保存为空。")
        # 添加“不修改”按钮，角色为拒绝（通常用于取消或否定操作）
        keep_btn = box.addButton("不修改", QMessageBox.ButtonRole.RejectRole)
        # 添加“留空保存”按钮，角色为接受（通常用于确认或肯定操作）
        empty_btn = box.addButton("留空保存", QMessageBox.ButtonRole.AcceptRole)
        # 显示消息框并等待用户交互
        box.exec()
        # 判断用户最终点击的是否是“留空保存”按钮
        # （逻辑上等价于只判断点击了empty_btn，但为了更清晰地排除其他可能性而加上与keep_btn的比较）
        return box.clickedButton() == empty_btn and box.clickedButton() != keep_btn

    def track_by_id(self, track_id: str) -> dict | None:
        """根据提供的音轨ID，在模型的原始音轨列表中查找并返回匹配的音轨记录。

        参数:
            track_id (str): 要查找的音轨ID字符串。

        返回:
            dict | None: 如果找到匹配的音轨，返回包含该音轨数据的字典；如果未找到或输入无效，则返回 None。
        """
        # 将传入的 track_id 参数转换为字符串，处理 None 或空值，并去除首尾空格以确保匹配的准确性
        target = str(track_id or "").strip()
        # 如果处理后的目标字符串为空，说明输入无效，直接返回 None
        if not target:
            return None
        # 遍历模型中的原始音轨列表，逐条进行比较
        for row in self.model.raw_tracks:
            # 将当前行的 track_id 值也转换为字符串并处理可能的空值，然后与目标字符串进行比较
            if str(row.get("track_id", "") or "") == target:
                # 找到匹配项，立即返回该行的数据字典
                return row
        # 遍历完整个列表仍未找到匹配项，返回 None
        return None


class LyricsTableModel(DictTableModel):
    lyrics_field_edited = Signal(str, str, object)

    _EDITABLE = {"file_name", "lyrics_title", "lyrics_artist", "lyrics_album", "lyrics_author"}

    def __init__(self, columns: list[ColumnDef], parent=None):
        """初始化方法。
    
        功能：初始化实例，设置列定义和父组件，并初始化排序状态映射。
    
        参数：
        - columns (list[ColumnDef]): 列定义列表，定义表格的列结构。
        - parent (可选): 父组件，默认为None，用于建立组件间的父子关系。
    
        返回值：无。
        """
        # 调用父类的初始化方法，传递列定义和父组件以继承父类功能
        super().__init__(columns, parent)
        # 初始化排序状态映射字典，键为列名，值为排序状态（如"asc"或"desc"），用于记录每列的排序方向
        self._sort_state_map: dict[str, str] = {}

    def set_header_sort_states(self, state_map: dict[str, str]) -> None:
        """设置表头排序状态。

        功能：将传入的排序状态映射复制到实例变量，并触发表头数据更改信号以更新UI。

        参数：
        state_map (dict[str, str]): 一个字典，映射表头列名到排序状态。

        返回值：
        None
        """
        self._sort_state_map = dict(state_map)  # 将传入的字典复制为新的实例变量，避免直接引用修改原数据
        if self.columns:  # 如果存在列数据
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self.columns) - 1)  # 触发表头数据更改信号，通知视图从第一列到最后一列数据已更新

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.columns):
                col = self.columns[section]
                marker = _marker_for_state(self._sort_state_map.get(col.key, "off"))
                return f"{col.title} {marker}"
        return super().headerData(section, orientation, role)

    def flags(self, index: QModelIndex):
        """根据索引返回项目的显示标志
    
        Args:
            index (QModelIndex): 要查询的模型索引
        
        Returns:
            Qt.ItemFlag: 返回项目的标志组合，包括启用、可选和可编辑等属性
        """
        if not index.isValid():
            # 索引无效时返回空标志，表示项目不可用
            return Qt.ItemFlag.NoItemFlags
        # 设置基本标志：项目可用且可被选中
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        # 根据索引所在的列，获取对应的键名
        key = self.columns[index.column()].key
        # 如果该列键名在可编辑列集合中，则添加可编辑标志
        if key in self._EDITABLE:
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        """
        获取模型数据。

        参数:
            index (QModelIndex): 数据的索引。
            role (int): 数据角色，默认为Qt.ItemDataRole.DisplayRole。

        返回值:
            根据角色和索引返回对应的数据，如果索引无效则返回None，否则根据角色返回相应值。
        """
        if not index.isValid():  # 检查索引是否有效
            return None
        if role == Qt.ItemDataRole.EditRole:  # 如果角色是编辑角色
            row = self.row_at(index.row()) or {}  # 获取行数据，如果为None则用空字典
            key = self.columns[index.column()].key  # 获取列的键
            value = row.get(key, "")  # 获取值，键不存在时返回空字符串
            return "" if value is None else str(value)  # 返回处理后的值
        return super().data(index, role)  # 调用父类方法处理其他角色

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        key = self.columns[index.column()].key
        if key not in self._EDITABLE:
            return False
        row = self.row_at(index.row())
        if not row:
            return False
        old_value = str(row.get(key, "") or "")
        new_value = str(value).strip()
        if new_value == old_value:
            return False
        row[key] = new_value
        self.dataChanged.emit(index, index)
        lyrics_id = str(row.get("lyrics_id", "") or "")
        if lyrics_id:
            QTimer.singleShot(0, lambda lid=lyrics_id, k=key, v=new_value: self.lyrics_field_edited.emit(lid, k, v))
        return True


