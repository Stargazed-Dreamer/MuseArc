from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ColumnDef:
    key: str
    title: str
    editable: bool = False


class DictTableModel(QAbstractTableModel):
    field_edited = None  # 子类可覆盖为 Signal(str, str, object)

    def __init__(self, columns: list[ColumnDef], parent=None):
        super().__init__(parent)
        self.columns = columns
        self.rows: list[dict] = []
        self._id_key: str = ""  # 子类可设置，用于 field_edited 信号

    def set_id_key(self, key: str) -> None:
        """设置行 ID 字段名，用于编辑信号发射。"""
        self._id_key = key

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
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole or role == Qt.ItemDataRole.ToolTipRole:
            row = self.rows[index.row()]
            key = self.columns[index.column()].key
            value = row.get(key, "")
            return "" if value is None else str(value)
        if role == Qt.ItemDataRole.EditRole:
            row = self.rows[index.row()]
            key = self.columns[index.column()].key
            value = row.get(key, "")
            return "" if value is None else str(value)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.columns):
            return self.columns[section].title
        return str(section + 1)

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        col = self.columns[index.column()]
        if col.editable:
            base |= Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        col = self.columns[index.column()]
        if not col.editable:
            return False
        row = self.rows[index.row()]
        key = col.key
        old_value = str(row.get(key, "") or "")
        new_value = str(value).strip() if value is not None else ""
        if new_value == old_value:
            return False
        row[key] = new_value
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole])
        logger.debug("[DictTableModel] 编辑: key=%s old=%r new=%r", key, old_value, new_value)
        print(f"[table_edit] {key}: {old_value!r} -> {new_value!r}")
        # 如果子类定义了 field_edited 信号且有 ID key，发射信号
        if self._id_key and self.field_edited is not None:
            row_id = str(row.get(self._id_key, "") or "")
            if row_id:
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda rid=row_id, k=key, v=new_value: self.field_edited.emit(rid, k, v))
        return True

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
