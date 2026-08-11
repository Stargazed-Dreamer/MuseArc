from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SelectionMode(StrEnum):
    NORMAL = "normal"
    MULTI = "multi"


@dataclass(slots=True)
class SelectionController:
    mode: SelectionMode = SelectionMode.NORMAL
    selected_rows: set[int] = field(default_factory=set)
    anchor_row: int | None = None
    focus_row: int | None = None
    saved_snapshots: list[list[int]] = field(default_factory=list)
    saved_this_session: bool = False

    def set_mode(self, mode: SelectionMode, total_rows: int, force_save_threshold: int) -> None:
        """设置选择模式。

        功能：将当前选择模式切换为指定的模式，并在切换前根据条件保存快照，
             切换后根据新模式调整已选行的状态。

        参数：
            mode (SelectionMode): 目标选择模式（如MULTI或NORMAL）。
            total_rows (int): 数据总行数，用于验证和调整已选行。
            force_save_threshold (int): 强制保存阈值，当已选行数超过此值时触发保存。

        返回值：
            None: 无返回值。
        """
        # 如果从多选模式切换到单选模式，且已选行数超过阈值且本次会话未保存过，则保存快照
        if self.mode == SelectionMode.MULTI and mode == SelectionMode.NORMAL:
            if len(self.selected_rows) > force_save_threshold and not self.saved_this_session:
                self.save_snapshot()

        # 更新当前模式
        self.mode = mode

        # 如果是单选模式，过滤无效的已选行，并确保至少选中一行
        if self.mode == SelectionMode.NORMAL:
            # 过滤出有效的行索引（0到total_rows-1之间）
            self.selected_rows = {r for r in self.selected_rows if 0 <= r < total_rows}

            # 如果过滤后没有选中行且总行数大于0，则默认选中焦点行或第一行
            if not self.selected_rows and total_rows > 0:
                # 确定焦点行，若无则默认为0
                row = self.focus_row if self.focus_row is not None else 0
                # 确保行索引在有效范围内
                row = max(0, min(total_rows - 1, row))
                self.selected_rows = {row}
                # 更新锚点和焦点行为选中的行
                self.anchor_row = row
                self.focus_row = row
        else:
            # 非单选模式（多选模式）下，重置本次会话的保存状态
            self.saved_this_session = False

    def normal_click(self, row: int) -> None:
        """处理普通单击操作，将选中状态设置为指定的单个行。

        当用户进行普通单击（非Shift或Ctrl修饰）时，会清除所有之前的选中行，
        仅选中当前点击的行，并将该行设置为锚点和焦点行。

        参数:
            row (int): 要选中的行的索引号。

        返回:
            None: 该方法不返回任何值，直接修改内部状态。
        """
        self.selected_rows = {row}  # 将选中行集合设置为仅包含当前点击行的集合
        self.anchor_row = row       # 将锚点行设置为当前行（用于后续Shift选择的参考点）
        self.focus_row = row        # 将焦点行设置为当前行（用于键盘导航和焦点管理）

    def multi_click_toggle(self, row: int) -> None:
        """
        切换指定行在选中行集合中的状态，并更新锚点行和焦点行以反映最新操作。

        参数:
            row (int): 要切换的行索引。

        返回值:
            None
        """
        if row in self.selected_rows:  # 检查行是否在选中集合中
            self.selected_rows.remove(row)  # 如果已选中，则移除该行
        else:
            self.selected_rows.add(row)  # 如果未选中，则添加该行
        self.anchor_row = row  # 更新锚点行为当前行
        self.focus_row = row  # 更新焦点行为当前行

    def multi_toggle_range(self, row_a: int, row_b: int) -> None:
        start = min(row_a, row_b)
        end = max(row_a, row_b)
        for row in range(start, end + 1):
            if row in self.selected_rows:
                self.selected_rows.remove(row)
            else:
                self.selected_rows.add(row)
        self.anchor_row = row_b
        self.focus_row = row_b

    def move_focus(self, total_rows: int, delta: int) -> int:
        """移动焦点到新的行位置。

        参数:
            total_rows (int): 总行数，必须大于0。
            delta (int): 焦点行的变化量。

        返回值:
            int: 更新后的焦点行索引，如果total_rows <= 0则返回-1。
        """
        if total_rows <= 0:
            # 如果总行数无效，重置焦点行并返回-1
            self.focus_row = None
            return -1
        base = self.focus_row if self.focus_row is not None else 0  # 获取当前焦点行，如果为None则默认为0
        row = max(0, min(total_rows - 1, base + delta))  # 计算新行，确保在0到total_rows-1的范围内
        self.focus_row = row  # 更新焦点行
        if self.mode == SelectionMode.NORMAL:  # 如果模式是普通选择模式
            self.selected_rows = {row}  # 更新选中行为当前行
            self.anchor_row = row  # 更新锚点行
        return row  # 返回更新后的焦点行

    def page_focus(self, total_rows: int, visible_rows: int, direction: int) -> int:
        """
        实现分页焦点移动功能，通过调整步长在指定方向移动焦点。

        参数:
            total_rows (int): 总行数。
            visible_rows (int): 可见行数。
            direction (int): 移动方向，正数表示向下移动，负数表示向上移动。

        返回:
            int: 调用move_focus方法后的结果，表示焦点移动后的位置或状态。
        """
        # 计算步长：取可见行数的70%作为基础步长，但确保至少为1，避免零步长
        step = max(1, int(visible_rows * 0.7))
        # 调用move_focus方法，传入总行数和调整后的步长（步长乘以方向因子）
        return self.move_focus(total_rows, step * direction)

    def keyboard_activate(self, shift: bool = False) -> None:
        """根据键盘shift键的状态和当前焦点行，执行相应的点击或切换操作。

        参数:
            shift (bool): 表示是否按住shift键，默认为False。

        返回值:
            None
        """
        # 如果焦点行为None，则不执行任何操作
        if self.focus_row is None:
            return
        # 如果处于正常选择模式，则执行正常点击
        if self.mode == SelectionMode.NORMAL:
            self.normal_click(self.focus_row)
            return
        # 如果shift键按下且锚点行存在，则执行范围切换
        if shift and self.anchor_row is not None:
            self.multi_toggle_range(self.anchor_row, self.focus_row)
        else:
            # 否则，执行单点切换
            self.multi_click_toggle(self.focus_row)

    def save_snapshot(self) -> None:
        """保存当前选中行号的快照。

        此方法将当前选中的行号列表排序后，作为一个快照添加到历史记录中。
        为了节省空间，最多只保留最近5次保存的快照。

        Args:
            self: 实例对象本身。

        Returns:
            None: 此方法没有返回值。
        """
        snapshot = sorted(self.selected_rows)  # 将当前选中的行号排序后保存
        if not snapshot:  # 如果没有选中任何行（列表为空）
            return  # 则直接返回，不保存空快照
        self.saved_snapshots.append(snapshot)  # 将快照添加到历史记录列表末尾
        if len(self.saved_snapshots) > 5:  # 如果历史记录超过5条
            self.saved_snapshots = self.saved_snapshots[-5:]  # 只保留最后5条（最新的5个）
        self.saved_this_session = True  # 设置标志，表明本会话已保存过快照

    def load_snapshot(self, index: int) -> None:
        """
        加载指定索引处保存的快照。

        功能：
            根据传入的索引，从已保存的快照列表中加载对应快照数据，并更新当前的选中行集合。
            如果索引无效或快照为空，则不做任何操作。

        参数：
            index (int): 要加载的快照在 `self.saved_snapshots` 列表中的索引。

        返回：
            None: 此方法不返回任何值。
        """
        # 检查传入的索引是否有效（不小于0且不超出已保存快照的长度）
        if index < 0 or index >= len(self.saved_snapshots):
            return  # 索引无效，直接返回，不执行后续操作
        # 将对应索引的快照列表转换为集合并赋值给 `self.selected_rows`
        self.selected_rows = set(self.saved_snapshots[index])
        # 如果选中的行集合不为空，则更新锚点和焦点行为集合中的最小行号
        if self.selected_rows:
            # `anchor_row` 用于记录用户最初选择的起始行
            self.anchor_row = min(self.selected_rows)
            # `focus_row` 用于记录当前键盘或鼠标光标所在的行
            self.focus_row = self.anchor_row

    def _normalize_for_normal(self, total_rows: int) -> None:
        if total_rows <= 0:
            self.selected_rows.clear()
            self.anchor_row = None
            self.focus_row = None
            return
        if not self.selected_rows:
            row = self.focus_row if self.focus_row is not None else 0
            row = max(0, min(total_rows - 1, row))
            self.selected_rows = {row}
            self.anchor_row = row
            self.focus_row = row
            return
        row = min(self.selected_rows)
        row = max(0, min(total_rows - 1, row))
        self.selected_rows = {row}
        self.anchor_row = row
        self.focus_row = row
