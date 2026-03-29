from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt


@dataclass(slots=True)
class ColumnDef:
    key: str
    title: str


class DictTableModel(QAbstractTableModel):
    def __init__(self, columns: list[ColumnDef], parent=None):
        super().__init__(parent)
        self.columns = columns
        self.rows: list[dict] = []

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        row = self.rows[index.row()]
        key = self.columns[index.column()].key
        value = row.get(key, "")
        return "" if value is None else str(value)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.columns):
            return self.columns[section].title
        return str(section + 1)

    def row_at(self, row: int) -> dict | None:
        if 0 <= row < len(self.rows):
            return self.rows[row]
        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        if column < 0 or column >= len(self.columns):
            return
        key = self.columns[column].key
        reverse = order == Qt.SortOrder.DescendingOrder

        def _sort_key(row: dict):
            value = row.get(key, "")
            if isinstance(value, (int, float)):
                return (0, float(value))
            text = str(value or "")
            try:
                return (0, float(text))
            except Exception:
                return (1, text.casefold())

        self.layoutAboutToBeChanged.emit()
        self.rows.sort(key=_sort_key, reverse=reverse)
        self.layoutChanged.emit()
