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
        if state == "asc":
            return "↑"
        if state == "desc":
            return "↓"
        return "·"

    def _reload_items(self) -> None:
        self.list.clear()
        for rule in self._rules:
            item = QListWidgetItem(f"{rule.label} {self._state_symbol(rule.state)}")
            item.setData(Qt.ItemDataRole.UserRole, rule.key)
            self.list.addItem(item)

    def _cycle_item_state(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.ItemDataRole.UserRole)
        for rule in self._rules:
            if rule.key != key:
                continue
            if rule.state == "asc":
                rule.state = "desc"
            elif rule.state == "desc":
                rule.state = "off"
            else:
                rule.state = "asc"
            break
        self._reload_items()
        self.changed.emit(self.export_rules())

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

