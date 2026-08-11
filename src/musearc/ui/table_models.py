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
        """获取数据模型的行数。

        Args:
            parent (QModelIndex): 父项模型索引，用于指定行所属的父节点。默认为空索引（即顶级）。

        Returns:
            int: 模型中的数据行数。
        """
        # 如果给定的父索引有效，则说明它是一个子节点索引，我们返回0，因为我们不支持树状结构下的行计数
        if parent.isValid():
            return 0
        # 返回顶层数据的总行数
        return len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """返回给定父项下的列数。

        参数:
            parent (QModelIndex): 父项索引，默认为无效索引（表示根项）。

        返回:
            int: 列数。如果父项有效则返回0，否则返回根项的列数（即self.columns的长度）。
        """
        # 如果父项有效，说明是子项，子项没有列（列数为0）
        if parent.isValid():
            return 0
        # 返回根项的列数（即模型列定义的总列数）
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
        """返回指定表头的数据。

        当请求的角色是 DisplayRole 时，根据方向和列索引返回列标题。
        否则，对于垂直表头，返回基于列索引的字符串（从1开始计数）。

        参数:
            section (int): 表头的列索引（对于水平方向）或行索引（对于垂直方向）。
            orientation (Qt.Orientation): 表头的方向，水平或垂直。
            role (int, optional): 请求的数据角色，默认为 DisplayRole。

        返回值:
            Optional[str]: 返回请求的字符串数据，或者当角色不匹配时返回 None。
        """
        # 只处理显示角色，其他角色直接返回 None
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        # 如果是水平表头且索引有效，则返回对应列的标题
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.columns):
            return self.columns[section].title
        # 对于其他情况（如垂直表头或无效索引），返回基于 section 的字符串
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
        """
        设置表格模型中的数据。

        功能：根据提供的索引、值和角色来更新表格模型中的数据。
              仅当角色为编辑角色且索引有效时，才会处理编辑请求。
              如果列不可编辑或新旧值相同，则拒绝修改。
              成功修改后，会触发数据更改信号，并记录日志。
              特别地，如果子类定义了 field_edited 信号且存在 ID 键，则会异步发射该信号。

        参数：
            index (QModelIndex): 要设置数据的单元格索引。
            value: 要设置的新值。
            role (int, 可选): 数据角色，默认为编辑角色 (Qt.ItemDataRole.EditRole)。

        返回：
            bool: 如果数据设置成功返回 True，否则返回 False。
        """
        # 如果角色不是编辑角色或索引无效，则直接返回 False
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False

        # 获取索引所在的列对象
        col = self.columns[index.column()]
        # 如果该列不可编辑，则返回 False
        if not col.editable:
            return False

        # 获取索引所在的行数据（字典）
        row = self.rows[index.row()]
        # 获取列定义中的键名
        key = col.key
        # 获取旧值，并转换为字符串，若为空或 None 则用空字符串表示
        old_value = str(row.get(key, "") or "")
        # 将新值转换为字符串并去除首尾空白，若 value 为 None 则用空字符串表示
        new_value = str(value).strip() if value is not None else ""

        # 如果新值与旧值相同，则无需修改，返回 False
        if new_value == old_value:
            return False

        # 将新值写入行数据中对应的键
        row[key] = new_value
        # 发出数据更改信号，通知视图和相关观察者数据已更新
        # 记录调试日志：显示编辑的键、旧值和新值
        logger.debug("[DictTableModel] 编辑: key=%s old=%r new=%r", key, old_value, new_value)
        # 打印日志到控制台
        print(f"[table_edit] {key}: {old_value!r} -> {new_value!r}")

        # 如果子类定义了 field_edited 信号且存在 ID 键，则异步发射信号
        if self._id_key and self.field_edited is not None:
            # 获取行数据中的 ID 值，转换为字符串，若为空或 None 则用空字符串表示
            row_id = str(row.get(self._id_key, "") or "")
            # 如果行 ID 存在，则异步发射 field_edited 信号
            if row_id:
                # 导入 QTimer，用于实现异步调用（避免信号嵌套可能导致的递归问题）
                # 使用 QTimer.singleShot 在下一个事件循环中异步发射信号，传递行 ID、键名和新值
                self.field_edited.emit(row_id, key, new_value)

        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole])

        # 数据设置成功，返回 True
        return True

    def row_at(self, row: int) -> dict | None:
        if 0 <= row < len(self.rows):
            return self.rows[row]
        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        """对数据按指定列进行排序。

        Args:
            column: 要排序的列索引，从0开始。
            order: 排序顺序，Qt.SortOrder.AscendingOrder表示升序，Qt.SortOrder.DescendingOrder表示降序，默认为升序。

        Returns:
            None: 此方法不返回任何值，而是直接修改self.rows的数据顺序。
        """
        # 检查列索引是否有效，如果无效则直接返回
        if column < 0 or column >= len(self.columns):
            return

        # 获取指定列的键，用于从行数据中提取值
        key = self.columns[column].key
        # 根据排序顺序确定是否反向排序
        reverse = order == Qt.SortOrder.DescendingOrder

        # 定义排序键函数，用于提取行的排序值
        def _sort_key(row: dict):
            # 获取行中对应键的值，如果不存在则默认为空字符串
            value = row.get(key, "")
            # 如果值是数值类型（整数或浮点数），直接转换为浮点数排序，并标记为数值类型（0表示数值）
            if isinstance(value, (int, float)):
                return (0, float(value))
            # 将值转换为字符串，如果为空则使用空字符串
            text = str(value or "")
            # 尝试将字符串转换为浮点数，以进行数值排序
            try:
                return (0, float(text))
            except Exception:
                # 如果转换失败，按文本排序，使用casefold进行大小写不敏感的排序，并标记为文本类型（1表示文本）
                return (1, text.casefold())

        # 发出布局即将改变的信号，通知视图准备更新
        self.layoutAboutToBeChanged.emit()
        # 使用自定义排序键函数对行数据进行排序
        self.rows.sort(key=_sort_key, reverse=reverse)
        # 发出布局已改变的信号，通知视图更新完成
        self.layoutChanged.emit()
