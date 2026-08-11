from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget


@dataclass(slots=True)
class SortRule:
    key: str
    label: str
    state: str = "asc"  # asc / desc / off


class SortCriteriaWidget(QWidget):
    changed = Signal(list)

    def __init__(self, rules: list[SortRule], parent=None):
        super().__init__(parent)
        self._rules: list[SortRule] = [SortRule(r.key, r.label, r.state) for r in rules]

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.list = QListWidget()
        self.list.setFlow(QListWidget.Flow.LeftToRight)
        self.list.setWrapping(False)
        self.list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list.setMaximumHeight(30)
        self.list.setSpacing(2)
        self.list.setStyleSheet(
            """
            QListWidget {
              padding: 1px 4px;
            }
            QListWidget::item {
              padding: 2px 8px;
              border-radius: 3px;
            }
            """
        )
        self.list.viewport().installEventFilter(self)

        root.addWidget(self.list)

        self.list.itemClicked.connect(self._cycle_item_state)
        self.list.model().rowsMoved.connect(self._on_order_changed)

        self._reload_items()

    def eventFilter(self, obj, event):
        if obj is self.list.viewport() and event.type() == QEvent.Type.Wheel:
            item = self.list.itemAt(event.position().toPoint())
            if item is not None:
                self._cycle_item_state(item)
                return True
        return super().eventFilter(obj, event)

    def _state_symbol(self, state: str) -> str:
        """将状态字符串转换为符号表示。

        参数：
            state (str): 状态字符串，如 "asc" 表示上升，"desc" 表示下降。

        返回值：
            str: 对应的符号字符串，如 "↑"、"↓" 或 "·"。
        """
        if state == "asc":  # 检查状态是否为上升
            return "↑"      # 返回上升符号
        if state == "desc": # 检查状态是否为下降
            return "↓"      # 返回下降符号
        return "·"          # 其他状态返回默认中性符号

    def _reload_items(self) -> None:
        self.list.clear()
        for rule in self._rules:
            item = QListWidgetItem(f"{rule.label} {self._state_symbol(rule.state)}")
            item.setData(Qt.ItemDataRole.UserRole, rule.key)
            self.list.addItem(item)

    def _cycle_item_state(self, item: QListWidgetItem) -> None:
        """功能：循环切换指定项目的排序状态。
        参数：
            item (QListWidgetItem): 要切换状态的列表项。
        返回值：无。
        """
        key = item.data(Qt.ItemDataRole.UserRole)  # 从item中提取用户数据键值
        for rule in self._rules:  # 遍历规则列表
            if rule.key != key:  # 如果规则键不匹配，则跳过当前规则
                continue
            if rule.state == "asc":  # 当前状态为升序
                rule.state = "desc"  # 切换为降序
            elif rule.state == "desc":  # 当前状态为降序
                rule.state = "off"  # 切换为关闭
            else:  # 当前状态为关闭或其他
                rule.state = "asc"  # 切换为升序
            break  # 找到匹配规则后立即退出循环
        self._reload_items()  # 重新加载列表项
        self.changed.emit(self.export_rules())  # 发出规则已更改的信号

    def _on_order_changed(self, *_args) -> None:
        ordered_keys: list[str] = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            ordered_keys.append(str(item.data(Qt.ItemDataRole.UserRole)))

        mapping = {r.key: r for r in self._rules}
        self._rules = [mapping[k] for k in ordered_keys if k in mapping]
        self._reload_items()
        self.changed.emit(self.export_rules())

    def export_rules(self) -> list[dict]:
        return [{"key": r.key, "label": r.label, "state": r.state} for r in self._rules]

    def set_state_all_off(self) -> None:
        for rule in self._rules:
            rule.state = "off"
        self._reload_items()
        self.changed.emit(self.export_rules())

