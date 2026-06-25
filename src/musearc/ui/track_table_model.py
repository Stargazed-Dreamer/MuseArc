from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
import logging
import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont

from musearc.core.pinyin import first_letter

logger = logging.getLogger(__name__)


def _safe_int_value(value, default: int = 0) -> int:
    """
    安全地将给定值转换为整数。
    如果值是不可转换的类型（如列表、元组、字典、集合），则返回默认值。
    尝试将值转换为整数；如果转换失败，返回默认值。
    参数:
    value (any): 要转换的值。
    default (int, optional): 默认值，默认为0。
    返回:
    int: 转换后的整数或默认值。
    """
    if isinstance(value, (list, tuple, dict, set)):  # 检查是否为不可转换的类型（列表、元组、字典、集合）
        return default  # 返回默认值
    try:
        return int(value)  # 尝试将值转换为整数
    except Exception:
        return default  # 如果转换失败，返回默认值


def _safe_bool(value) -> bool:
    """
    将任意类型的值安全地转换为布尔值。

    参数:
        value (任意类型): 需要转换的值，支持列表、元组、集合、字典等容器类型，以及其他可转换为整数或布尔值的类型。

    返回值:
        bool: 转换后的布尔值。
            - 对于容器类型（list, tuple, set, dict），当容器非空时返回 True，空容器返回 False。
            - 对于其他类型，先尝试将其转换为整数，再转换为布尔值；若整数转换失败，则直接使用 bool() 函数转换。
    """
    if isinstance(value, (list, tuple, set, dict)):  # 检查值是否为容器类型（列表、元组、集合或字典）
        return bool(len(value))  # 对于容器，根据其长度判断：非空容器返回 True，空容器返回 False
    try:
        return bool(int(value))  # 尝试将值转换为整数，再转换为布尔值（例如，非零整数为 True，0 为 False）
    except Exception:  # 捕获所有转换为整数时可能发生的异常（如值为字符串、浮点数等无法直接转换为整数的情况）
        return bool(value)  # 若整数转换失败，则直接使用 bool() 函数转换（例如，非空字符串为 True，空字符串为 False）


def format_mmss(value) -> str:
    """将数值转换为“分钟:秒”格式的字符串。
    
    Args:
        value: 需要转换的数值，可以是数字或能转换为数字的字符串。
        
    Returns:
        格式为 "MM:SS" 的字符串，其中 MM 为两位分钟数，SS 为两位秒数。
    """
    try:
        # 尝试将输入值转换为整数，先转浮点数以处理各种数字格式
        sec = int(float(value))
    except Exception:
        # 如果转换失败（例如输入非数字），则将秒数默认为0
        sec = 0
    # 计算总秒数中的分钟数（整除）
    m = sec // 60
    # 计算剩余不足一分钟的秒数（取模）
    s = sec % 60
    # 使用格式化字符串输出两位数字，不足两位补零
    return f"{m:02d}:{s:02d}"


def basename(path_text: str) -> str:
    """
    提取路径字符串中的文件名或最后一个路径组件。

    参数：
        path_text (str): 输入的路径字符串。

    返回值：
        str: 路径的最后一个组件，如果路径为空则返回空字符串。
    """
    if not path_text:  # 检查路径是否为空或None
        return ""  # 如果为空，返回空字符串
    text = str(path_text).replace("\\", "/")  # 将路径转换为字符串，并将反斜杠替换为正斜杠以统一分隔符
    return text.split("/")[-1]  # 用正斜杠分割路径，返回最后一个元素作为文件名


def _path_parent_label(path_text: str) -> str:
    """提取路径的父级目录名。

    将输入的路径字符串标准化为正斜杠分隔，去除首尾斜杠后，
    获取其倒数第二个有效目录名。若路径层级不足或为空，则返回特殊标记。

    Args:
        path_text: 任意格式的路径字符串（支持反斜杠/正斜杠混合，自动转换）。

    Returns:
        str: 父级目录名。若路径为空或层级不足，则返回"(空)"。
    """
    # 统一替换为正斜杠并去除首尾斜杠，将None转为空字符串
    text = str(path_text or "").replace("\\", "/").strip("/")
    # 处理空路径情况
    if not text:
        return "(空)"
    # 按斜杠分割，过滤空片段和盘符（如"C:"），保留有效目录名
    parts = [p for p in text.split("/") if p and not p.endswith(":")]
    # 当路径至少包含两级目录时，返回倒数第二个部分（父目录）
    if len(parts) >= 2:
        return parts[-2]
    # 当路径只有一级或为空时，返回第一部分（若存在）或"(空)"
    return parts[0] if parts else "(空)"


def _first_non_space_char(text: str) -> str:
    """
    功能：返回字符串中的第一个非空格字符。
    参数：
        text (str): 输入的字符串，可能为None或空。
    返回值：
        str: 第一个非空格字符；如果字符串为空或只包含空格，则返回空字符串。
    """
    for ch in str(text or ""):  # 将输入转换为字符串，如果为None或空，则使用空字符串
        if not ch.isspace():  # 检查字符是否不是空格
            return ch  # 返回第一个非空格字符
    return ""  # 如果没有找到非空格字符，返回空字符串


def _char_lang_bucket(ch: str) -> tuple[str, str]:
    """根据输入字符的语言属性分类。

    此函数将单个字符分类为英语、中文或其他语言，并返回语言代码和处理后的字符。

    参数:
        ch (str): 输入字符，应为单个字符字符串。

    返回值:
        tuple[str, str]: 包含语言代码和处理后的字符的元组。语言代码为 "en"（英语）、"zh"（中文）或 "other"（其他）；处理后的字符为小写形式或原字符。
    """
    if not ch:  # 检查输入字符是否为空
        return "other", "(空)"  # 返回空字符的分类
    code = ord(ch)  # 获取字符的Unicode编码
    if "A" <= ch <= "Z" or "a" <= ch <= "z":  # 检查是否为英语字母（大写或小写）
        return "en", ch.lower()  # 返回英语分类和字符的小写形式
    if 0x4E00 <= code <= 0x9FFF:  # 检查是否为中文字符（Unicode范围：CJK统一表意文字基本区）
        return "zh", ch  # 返回中文分类和原字符
    return "other", ch.lower()  # 默认返回其他分类和字符的小写形式


def _lyrics_stem_label(text: str) -> str:
    """
    提取歌词文本的主干标签，去除文件扩展名和特殊字符。

    参数:
    text (str): 输入的歌词文本字符串，可以是文件路径或纯文本。

    返回:
    str: 处理后的主干字符串，通过去除扩展名和替换特殊字符为空格，然后去除首尾空格。
    """
    stem = basename(str(text or ""))  # 获取文本的基名，确保text为字符串，如果为空则使用空字符串
    if "." in stem:  # 检查基名中是否包含点号，表示可能有文件扩展名
        stem = stem.rsplit(".", 1)[0]  # 从右边分割一次，取第一部分，以去除最后一个扩展名
    stem = re.sub(r"[\s._-]+", " ", stem).strip()  # 使用正则表达式将空白、点、下划线或连字符替换为单个空格，并去除首尾空格
    return stem


def _marker_for_state(state: str) -> str:
    """根据传入的状态字符串返回对应的标记符号。

    参数:
        state (str): 表示状态的字符串，例如 "asc" 或 "desc"。

    返回值:
        str: 对应状态的符号标记。如果 state 为 "asc"，返回 "↑"；如果 state 为 "desc"，返回 "↓"；否则返回 "·"。
    """
    if state == "asc":
        # 如果状态为上升，返回上升箭头
        return "↑"
    if state == "desc":
        # 如果状态为下降，返回下降箭头
        return "↓"
    # 默认情况下，返回点符号表示中性状态
    return "·"


class TrackTableModel(QAbstractTableModel):
    track_field_edited = Signal(str, str, object)

    BASE_COLUMNS = [
        ("custom_order", "自定义排序", True),
        ("file_name", "文件名", True),
        ("title", "标题", True),
        ("artist", "艺术家", True),
        ("preference_level", "喜好(1-10)", True),
        ("duration_mmss", "时长", False),
        ("lyrics_file_name", "歌词文件名", False),
        ("language_kind", "语言", True),
        ("album", "专辑", True),
        ("source_fullpath", "Source Path", False),
        ("storage_relpath", "Storage Path", False),
        ("format", "格式", False),
        ("track_id", "数据库ID", False),
    ]

    def __init__(self, parent=None):
        """初始化方法，设置各种属性和定时器。

        参数:
            parent: 父对象，默认为 None。

        返回值:
            无返回值。
        """
        super().__init__(parent)  # 调用父类构造函数
        self.raw_tracks: list[dict] = []  # 初始化原始轨道列表
        self.sort_rules: list[dict] = []  # 初始化排序规则列表
        self.sort_state_map: dict[str, str] = {}  # 初始化排序状态映射
        self.custom_order_enabled: bool = False  # 初始化自定义顺序启用标志
        self.group_by: str | None = None  # 初始化分组键
        self.collapsed_group_keys: set[str] = set()  # 初始化折叠的分组键集合
        self.display_rows: list[dict] = []  # 初始化显示行列表
        self.visual_selected_track_ids: set[str] = set()  # 初始化可视选中轨道ID集合
        self.confirm_empty_edit_callback: Callable[[str, str], bool] | None = None  # 初始化确认空编辑回调函数
        self.tag_fields: list[str] = []  # 初始化标签字段列表
        self.columns: list[tuple[str, str, bool]] = list(self.BASE_COLUMNS)  # 基于 BASE_COLUMNS 初始化列定义
        self._rebuild_pending = False  # 初始化重建待处理标志
        self._rebuild_timer = QTimer(self)  # 创建重建定时器
        self._rebuild_timer.setSingleShot(True)  # 设置为单次触发
        # 避免在编辑器提交同一事件循环里立刻 reset model，触发 commitData/editor 生命周期错位。
        self._rebuild_timer.setInterval(24)  # 设置定时器间隔为24毫秒
        self._rebuild_timer.timeout.connect(self._flush_rebuild)  # 连接超时信号到刷新重建方法

    def set_tag_fields(self, tag_fields: list[str]) -> None:
        """
        设置标签字段，清理输入列表，去除空白和重复项，然后更新列配置并重建显示。

        参数:
            tag_fields (list[str]): 标签字段的列表。

        返回值:
            None
        """
        unique: list[str] = []  # 初始化一个列表来存储唯一的标签字段
        for name in tag_fields:  # 遍历输入的标签字段列表
            text = str(name).strip()  # 将字段名转换为字符串并去除首尾空白
            if not text or text in unique:  # 如果文本为空或已存在于unique列表中，则跳过
                continue
            unique.append(text)  # 将有效的文本添加到unique列表
        self.tag_fields = unique  # 将去重后的标签字段赋值给实例属性
        tag_cols = [(f"tag:{name}", name, True) for name in self.tag_fields]  # 创建标签列配置，每列包含格式化名称、显示名称和可见性
        self.columns = list(self.BASE_COLUMNS[:-1]) + tag_cols + [self.BASE_COLUMNS[-1]]  # 重建列列表：基础列（除最后一列） + 标签列 + 最后一列
        self._rebuild_display()  # 调用方法重建显示
        if self.columns:  # 如果列列表不为空
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self.columns) - 1)  # 发送信号通知水平头数据已更改

    def set_custom_order_enabled(self, enabled: bool) -> None:
        """设置是否启用自定义列顺序。

        参数:
            enabled (bool): True 表示启用自定义顺序，False 表示禁用。
        返回:
            None: 此方法无返回值。
        """
        # 使用 bool() 确保将参数转换为严格的布尔值，保证后续逻辑的可靠性。
        self.custom_order_enabled = bool(enabled)
        if self.columns:
            # 当模型有列时，发射表头数据变化信号。
            # Qt.Orientation.Horizontal 指定是水平表头，0 和 len(self.columns) - 1 指定变化范围为从第一列到最后一列。
            # 这会通知任何连接的视图（如 QTableView）刷新其水平表头。
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self.columns) - 1)
        # 重建内部的显示数据结构（如排序、分组等）。
        self._rebuild_display()

    def set_header_sort_states(self, state_map: dict[str, str]) -> None:
        self.sort_state_map = dict(state_map)
        if self.columns:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self.columns) - 1)

    def column_key(self, index: int) -> str:
        if 0 <= index < len(self.columns):
            return self.columns[index][0]
        return ""

    def column_index(self, key: str) -> int:
        """查找指定键对应的列索引。

        参数：
        key (str): 要查找的列键。

        返回值：
        int: 列的索引；如果未找到，返回-1。
        """
        for idx, (col_key, _, _editable) in enumerate(self.columns):  # 遍历列列表，获取索引和列键
            if col_key == key:  # 如果当前列键与目标键匹配
                return idx  # 返回索引
        return -1  # 如果未找到匹配，返回-1

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.columns)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """返回模型中的行数。
    
        本模型仅支持顶级项，不支持树形结构，因此当给定有效的父索引（表示非顶级项）时，直接返回0。
    
        参数:
            parent (QModelIndex, optional): 父项的索引。默认为空的QModelIndex()，表示请求顶级项的行数。
        
        返回值:
            int: 当parent为无效索引（顶级项）时，返回self.display_rows的长度；否则返回0。
        """
        # 检查父索引是否有效（即是否指向一个有效的父项）
        if parent.isValid():
            # 由于模型不支持子项，任何非顶级项（有效父索引）的行数都应为0
            return 0
        # 对于顶级项（无效父索引），返回数据行数
        return len(self.display_rows)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.columns):
            key, label, _editable = self.columns[section]
            if key == "custom_order":
                label = "自定义排序" if self.custom_order_enabled else "收藏"
            state = self.sort_state_map.get(key, "off")
            return f"{label} {_marker_for_state(state)}"
        return str(section + 1)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        row_obj = self.display_rows[index.row()]
        key = self.columns[index.column()][0]

        if role == Qt.ItemDataRole.DisplayRole:
            if row_obj["kind"] == "group":
                if index.column() == 0:
                    collapsed = row_obj["group_key"] in self.collapsed_group_keys
                    symbol = "▸" if collapsed else "▾"
                    return f"{symbol} {row_obj['group_label']} ({row_obj['group_count']}) {'─' * 18}"
                return ""

            track = row_obj["track"]
            if key == "custom_order":
                is_favorite = bool(track.get("is_favorite"))
                heart = "♥" if is_favorite else ""
                if self.custom_order_enabled and bool(track.get("_entry_editable")):
                    order_value = _safe_int_value(track.get("entry", 0), 0)
                    return f"{heart} {order_value}" if heart else f"  {order_value}"
                return heart

            value = str(self._value_for_key(track, key))
            if self.group_by and key == "file_name":
                return f"  {value}"
            return value

        if role == Qt.ItemDataRole.EditRole:
            if row_obj["kind"] == "group":
                return None
            track = row_obj["track"]
            return self._value_for_key(track, key)

        if role == Qt.ItemDataRole.ToolTipRole:
            if row_obj["kind"] == "group":
                return "单击可选中分组，双击可折叠/展开分组。"
            track = row_obj["track"]
            if key == "custom_order":
                return "收藏状态 + 自定义排序" if self.custom_order_enabled else "收藏状态"
            return str(self._value_for_key(track, key))

        if role == Qt.ItemDataRole.FontRole and row_obj["kind"] == "group":
            font = QFont()
            font.setBold(True)
            return font

        if role == Qt.ItemDataRole.ForegroundRole and row_obj["kind"] == "group":
            return QColor(38, 60, 82)

        if role == Qt.ItemDataRole.BackgroundRole:
            if row_obj["kind"] == "group":
                return QColor(236, 241, 246)
            track_id = str(row_obj["track"].get("track_id", ""))
            if track_id and track_id in self.visual_selected_track_ids:
                return QColor(85, 170, 255, 110)

        if role == Qt.ItemDataRole.TextAlignmentRole and key in {"preference_level", "duration_mmss"}:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        row_obj = self.display_rows[index.row()]
        if row_obj["kind"] == "group":
            return base
        key = self.columns[index.column()][0]
        editable = self._is_editable_key(key, row_obj["track"])
        result = base | Qt.ItemFlag.ItemIsEditable if editable else base
        return result

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole):
        """设置指定单元格的数据。

        这是Qt模型/视图框架中编辑数据的核心回调方法，用于响应用户在视图中的编辑操作。
        本方法会验证编辑请求的合法性、解析并格式化新值、更新底层数据，最后通知视图刷新。

        Args:
            index (QModelIndex): 要编辑的单元格的模型索引。
            value: 用户输入的新值，其类型取决于具体列的配置。
            role (int, optional): 编辑的角色类型，默认为 `Qt.ItemDataRole.EditRole`。
                                  只有此角色会被处理，其他角色将被忽略。

        Returns:
            bool: 如果数据成功更新，则返回 `True`；如果编辑被拒绝或发生错误，则返回 `False`。
        """
        # 检查编辑角色是否有效，以及索引是否指向一个有效的单元格
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        # 根据行索引获取对应的数据行对象
        row_obj = self.display_rows[index.row()]
        # 仅处理类型为“track”的行对象，忽略其他类型（如分隔行）
        if row_obj["kind"] != "track":
            return False

        # 根据列索引获取该列的字段键名（例如 "title", "artist", "tag:genre"）
        key = self.columns[index.column()][0]
        # 获取当前行对应的轨道数据字典
        track = row_obj["track"]
        # 检查此字段键（key）在当前轨道上下文中是否允许被编辑
        if not self._is_editable_key(key, track):
            logger.debug("[TrackTableModel] setData 拒绝: key=%s 不可编辑", key)
            return False

        # 获取并确保轨道ID为字符串类型，用于后续日志和信号发射
        track_id = str(track.get("track_id", ""))
        # 获取编辑前该字段的原始值，用于比较和日志记录
        old_value = self._value_for_key(track, key)
        # 记录详细的编辑日志
        logger.info("[TrackTableModel] setData: track_id=%s key=%s old=%r new=%r", track_id, key, old_value, value)
        print(f"[edit] TrackTableModel.setData: tid={track_id} key={key} old={old_value!r} new={value!r}")

        # 根据字段键（key）的不同，对新输入值（value）进行针对性的解析和验证
        if key == "preference_level":
            # 处理“偏好等级”字段：必须为1-10之间的整数
            try:
                parsed = int(value)
            except Exception:
                return False
            # 将值约束在 [1, 10] 的范围内
            parsed = max(1, min(10, parsed))
        elif key == "custom_order":
            # 处理“自定义排序序号”字段：必须为整数
            try:
                # 先尝试将输入转为字符串并去除首尾空格，再解析为整数
                parsed = int(str(value).strip())
            except Exception:
                return False
        else:
            # 处理其他普通文本字段：转为字符串并去除首尾空格
            parsed = str(value).strip()
            # 如果新旧值相同，则无需更新，直接返回
            if str(old_value) == parsed:
                return False
            # 如果新值为空，但旧值非空，并且存在确认回调函数，则询问用户是否确认清空
            if parsed == "" and str(old_value).strip() != "" and self.confirm_empty_edit_callback:
                if not self.confirm_empty_edit_callback(track_id, key):
                    return False

        # 根据字段键（key）的不同，将解析后的值（parsed）更新到对应的轨道数据中
        if key == "custom_order":
            # 处理“自定义排序序号”：检查新旧值是否真的不同
            if _safe_int_value(track.get("entry", 0), 0) == _safe_int_value(parsed, 0):
                return False
            # 更新轨道数据中的“entry”字段（即排序序号）
            track["entry"] = _safe_int_value(parsed, 0)
            # 确定后续需要发射信号的键名和值
            emit_key = "custom_order"
            emit_value = _safe_int_value(parsed, 0)
        elif key.startswith("tag:"):
            # 处理以“tag:”开头的标签字段（例如 "tag:genre"）
            if str(old_value) == str(parsed):
                return False
            # 从键名中提取标签名称（例如从 "tag:genre" 得到 "genre"）
            tag_name = key.split(":", 1)[1]
            # 获取轨道的标签字典副本，避免直接修改可能存在的共享字典
            tags = dict(track.get("tags", {}))
            # 如果新值非空，则更新或添加该标签；否则从字典中移除该标签
            if parsed.strip():
                tags[tag_name] = parsed
            else:
                tags.pop(tag_name, None)
            # 更新轨道数据中的标签字典
            track["tags"] = tags
            # 同时更新轨道数据中对应键的值（例如 track["tag:genre"] = "Rock"）
            track[key] = parsed
            emit_key = key
            emit_value = parsed
        else:
            # 处理其他普通字段
            if str(old_value) == str(parsed):
                return False
            # 直接更新轨道数据中对应键的值
            track[key] = parsed
            emit_key = key
            emit_value = parsed

        # 发出数据已改变的信号，通知视图进行局部刷新
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole])
        # 安排模型进行重建（如果需要，用于更新排序、过滤等）
        self._schedule_rebuild()
        # 如果存在有效的 track_id，则发射 track_field_edited 信号，通知外部组件（如播放队列、数据库）数据已变更
        if track_id:
            logger.info("[TrackTableModel] 将发射 track_field_edited: tid=%s key=%s value=%r", track_id, emit_key, emit_value)
            print(f"[edit] 发射信号: tid={track_id} key={emit_key} value={emit_value!r}")
            # 使用 QTimer.singleShot(0, ...) 将信号发射延迟到当前槽函数执行完毕、事件循环空闲时进行，避免潜在的重入问题
            QTimer.singleShot(0, lambda tid=track_id, k=emit_key, v=emit_value: self.track_field_edited.emit(tid, k, v))
        else:
            logger.warning("[TrackTableModel] setData 完成 but track_id 为空，无法发射信号")
        # 指示数据设置成功
        return True

    def set_tracks(self, rows: list[dict]) -> None:
        """设置曲目数据，处理原始曲目列表并更新内部状态。
    
        该方法遍历输入的行数据，对每个曲目进行数据标准化和格式化处理，包括：
        1. 时长格式转换
        2. 文件名提取和清理
        3. 语言类型标准化
        4. 偏好等级和序号的数值范围限制
        5. 布尔字段的规范化处理
        6. 格式字段的统一处理
        7. 标签字段的展开和映射
    
        参数：
            rows (list[dict]): 包含原始曲目数据的字典列表，每个字典代表一个曲目。
    
        返回值：
            None: 该方法不返回任何值，但会更新实例的raw_tracks属性并触发界面重建。
        """
        prepared: list[dict] = []  # 存储处理后的曲目数据列表
        for row in rows:  # 遍历每个原始曲目数据
            item = dict(row)  # 创建原始数据的副本，避免修改原始输入
            # 将duration_sec转换为MM:SS格式的时长字符串，若不存在则默认0
            item["duration_mmss"] = format_mmss(item.get("duration_sec", 0))
            # 从lyrics_source路径中提取文件名作为歌词文件名
            item["lyrics_file_name"] = basename(item.get("lyrics_source", ""))
            # 若没有file_name字段，则从source_relpath或source_fullpath中提取文件名
            if not item.get("file_name"):
                item["file_name"] = basename(item.get("source_relpath", "")) or basename(item.get("source_fullpath", ""))
            # 标准化语言类型字段，确保为字符串，若为空则设为"unknown"
            item["language_kind"] = str(item.get("language_kind") or "unknown")
            try:
                # 将偏好等级限制在1-10范围内，转换为整数
                item["preference_level"] = max(1, min(10, int(item.get("preference_level", 5))))
            except Exception:
                # 若转换失败或出现异常，使用默认值5
                item["preference_level"] = 5
            try:
                # 将序号字段转换为整数，空值或转换失败则使用0
                item["entry"] = int(item.get("entry", 0) or 0)
            except Exception:
                item["entry"] = 0
            # 确保_entry_editable字段为布尔值
            item["_entry_editable"] = bool(item.get("_entry_editable", False))
            # 使用安全的布尔值转换处理is_favorite字段
            item["is_favorite"] = _safe_bool(item.get("is_favorite", 0))
            # 规范化格式字段：优先使用format，其次storage_format，最后source_ext
            # 并去除点号并转换为小写
            item["format"] = (
                str(item.get("format") or item.get("storage_format") or item.get("source_ext") or "")
                .replace(".", "")
                .lower()
            )
            # 获取标签字段，确保为字典类型
            tags = item.get("tags", {})
            if not isinstance(tags, dict):
                tags = {}  # 若标签不是字典类型，则重置为空字典
            item["tags"] = tags
            # 将标签字典中的每个字段展开为单独的"tag:字段名"键值对
            for name in self.tag_fields:
                item[f"tag:{name}"] = str(tags.get(name, ""))
            prepared.append(item)  # 将处理后的曲目添加到结果列表
        self.raw_tracks = prepared  # 更新实例的原始曲目数据
        self._rebuild_display()  # 触发界面重建以反映数据变化

    def set_sort_rules(self, rules: list[dict]) -> None:
        """设置并应用新的排序规则。

        将传入的规则列表进行复制并保存到实例属性中，然后触发显示内容的重新构建。

        参数:
            rules (list[dict]): 一个字典列表，每个字典描述一条排序规则。

        返回:
            None: 该方法无返回值。
        """
        # 使用列表推导式，为输入的每个规则字典创建一个副本，存储到新的列表中，以避免意外修改原始规则。
        self.sort_rules = [dict(rule) for rule in rules]
        # 根据新的排序规则，重新构建或刷新用于显示的内容/状态。
        self._rebuild_display()

    def set_group_by(self, key: str | None) -> None:
        self.group_by = key if key and key != "none" else None
        self._rebuild_display()

    def set_confirm_empty_edit_callback(self, callback: Callable[[str, str], bool] | None) -> None:
        self.confirm_empty_edit_callback = callback

    def set_visual_selected_track_ids(self, track_ids: set[str]) -> None:
        """设置当前视图中视觉上选中的轨道ID集合。

        此方法用于更新内部记录的视觉选中状态，并触发相关UI区域的重绘。
        如果当前没有显示行（display_rows为空），则直接返回，不做任何操作。

        Args:
            track_ids (set[str]): 需要被设为视觉选中状态的轨道ID集合。

        Returns:
            None
        """
        # 更新内部的 visual_selected_track_ids 属性，确保传入的是新的集合对象
        self.visual_selected_track_ids = set(track_ids)

        # 如果 display_rows 为空，说明没有数据行需要显示或更新，提前返回
        if not self.display_rows:
            return

        # 获取表格左上角（第一行第一列）和右下角（最后一行最后一列）的模型索引
        # 用于标识需要更新的矩形区域
        top = self.index(0, 0)
        bottom = self.index(len(self.display_rows) - 1, len(self.columns) - 1)

        # 仅通知 BackgroundRole 变更，避免全角色 dataChanged 干扰正在编辑的 editor
        # 向视图发出数据已变更的信号，限定在背景角色，以最小化刷新影响
        self.dataChanged.emit(top, bottom, [Qt.ItemDataRole.BackgroundRole])

    def apply_value_to_tracks(self, track_ids: set[str], key: str, value) -> None:
        ids = {str(v) for v in track_ids if str(v)}
        if not ids:
            return
        changed = False
        for track in self.raw_tracks:
            track_id = str(track.get("track_id", ""))
            if track_id not in ids:
                continue
            if key == "custom_order":
                try:
                    parsed = int(value)
                except Exception:
                    continue
                track["entry"] = parsed
                changed = True
                continue
            if key == "preference_level":
                try:
                    parsed = max(1, min(10, int(value)))
                except Exception:
                    continue
                track["preference_level"] = parsed
                changed = True
                continue
            if key.startswith("tag:"):
                text = str(value).strip()
                tag_name = key.split(":", 1)[1]
                tags = dict(track.get("tags", {}))
                if text:
                    tags[tag_name] = text
                else:
                    tags.pop(tag_name, None)
                track["tags"] = tags
                track[key] = text
                changed = True
                continue
            text = str(value).strip()
            track[key] = text
            changed = True
        if changed:
            self._schedule_rebuild()

    def _schedule_rebuild(self) -> None:
        """安排重建操作。如果已有重建在等待，则直接返回；否则设置重建待处理标志并启动重建定时器。"""
        if self._rebuild_pending:  # 检查是否已经有重建操作在等待，以避免重复调度
            return
        self._rebuild_pending = True  # 设置重建待处理标志
        self._rebuild_timer.start()  # 启动重建定时器

    def _flush_rebuild(self) -> None:
        self._rebuild_pending = False
        self._rebuild_display()

    def _sort_tracks(self, rows: list[dict]) -> list[dict]:
        """
        对轨道列表进行排序。

        此方法根据实例中定义的排序规则，对输入的轨道字典列表进行排序。
        如果无有效排序规则，则会根据上下文设置一个默认的排序键（如自定义顺序或文件名）。

        参数:
            rows (list[dict]): 需要排序的轨道列表，每个元素是一个代表轨道的字典。

        返回:
            list[dict]: 排序后的轨道列表。
        """
        # 将输入的行列表转换为一个新列表，以便进行原位排序而不修改原数据
        items = list(rows)
        # 从排序规则中筛选出状态为“升序”或“降序”的有效规则
        active = [r for r in self.sort_rules if r.get("state") in {"asc", "desc"}]

        # 如果没有找到任何有效的排序规则
        if not active:
            # 确定默认的排序键：如果启用了自定义顺序且存在可编辑条目，则使用"custom_order"，否则使用"file_name"
            default_key = "custom_order" if self.custom_order_enabled and any(bool(r.get("_entry_editable")) for r in rows) else "file_name"
            # 创建一个包含默认排序键和升序状态的规则字典
            active = [{"key": default_key, "state": "asc"}]

        # 逆序遍历所有活跃规则（确保多字段排序时，最后添加的规则优先级最高）
        for rule in reversed(active):
            # 从规则中提取排序字段的键名，并确保其为字符串类型
            key = str(rule.get("key"))
            # 判断是否为降序排序
            reverse = rule.get("state") == "desc"
            # 对列表进行排序，使用 self._sort_value 方法获取用于比较的值，并根据规则决定排序方向
            items.sort(key=lambda x, k=key: self._sort_value(x, k), reverse=reverse)

        # 返回排序完成的列表
        return items

    # 已知的数值型标签名（排序/分组时按数值处理）
    NUMERIC_TAG_NAMES = {"播放次数", "指定播放次数", "播放秒数", "早期跳过次数", "喜爱程度", "比特率", "采样率", "位深度", "声道数"}

    def _sort_value(self, row: dict, key: str):
        """
        根据给定的键从行字典中提取值并转换为适合排序的格式。

        参数:
            row (dict): 包含数据的字典，表示一行数据。
            key (str): 用于排序的键名，可能包含特殊的映射逻辑。

        返回:
            该方法返回一个可以用于排序的值。对于数值型字段，返回浮点数；对于字符串字段，返回经过大小写折叠的字符串；
            对于标签字段，可能返回一个元组以确保不同类型之间的排序顺序（数值在前，字符串在后）。
        """
        # 特殊映射：将 "duration_mmss" 转换为实际存储的秒数字段 "duration_sec"
        mapped = "duration_sec" if key == "duration_mmss" else key
        # 自定义顺序处理：根据配置决定使用 "entry"（自定义顺序）还是 "is_favorite"（收藏状态）
        if mapped == "custom_order":
            mapped = "entry" if self.custom_order_enabled else "is_favorite"
        # 对于明确为数值的字段，尝试转换为浮点数，失败则返回0.0
        if mapped in {"duration_sec", "preference_level", "entry"}:
            try:
                return float(row.get(mapped, 0))
            except Exception:
                return 0.0
        # 收藏状态字段：转换为1.0（收藏）或0.0（未收藏）
        if mapped == "is_favorite":
            return 1.0 if bool(row.get("is_favorite")) else 0.0
        # 歌词文件名字段：取文件名部分并转为小写，以便进行字符串排序
        if mapped == "lyrics_file_name":
            return str(row.get("lyrics_file_name") or basename(row.get("lyrics_source", ""))).casefold()
        # tag:* 字段：数值型标签按数值排序
        if mapped.startswith("tag:"):
            # 提取冒号后的标签名，若无冒号则使用整个mapped值
            tag_name = mapped.split(":", 1)[1] if ":" in mapped else mapped
            raw = row.get(mapped, "")
            # 如果是预定义的数值标签，尝试转为浮点数，否则作为字符串处理
            if tag_name in self.NUMERIC_TAG_NAMES:
                try:
                    # 返回元组 (0, float_value) 保证数值排序在前
                    return (0, float(raw))
                except Exception:
                    # 转换失败时作为字符串，使用元组 (1, string_value) 保证排序在后
                    return (1, str(raw or "").casefold())
            return str(raw or "").casefold()
        # 通用字段：尝试数值解析
        raw = row.get(mapped, "")
        # 如果原始值已是数值类型，直接转换为浮点数
        if isinstance(raw, (int, float)):
            return (0, float(raw))
        text = str(raw or "")
        try:
            # 尝试将文本转换为浮点数，成功则返回元组 (0, 数值)
            return (0, float(text))
        except Exception:
            # 转换失败则作为字符串处理，返回元组 (1, 小写文本)
            return (1, text.casefold())

    def _group_rows(self, rows: list[dict]) -> list[dict]:
        """
        将行数据按指定条件分组并格式化输出。

        功能：
        如果未设置分组条件 (`self.group_by`)，则直接将每行包装为轨迹（track）字典。
        若设置了分组条件，则根据条件将行分组，为每个组生成摘要信息，并将组及组内轨迹按排序后的顺序输出。

        参数：
        rows (list[dict]): 待分组的行数据列表，每个元素是一个字典。

        返回：
        list[dict]: 格式化后的字典列表。
            - 若未分组，每个字典结构为 {"kind": "track", "track": <原始行字典>}。
            - 若分组，每个字典结构为：
              - 摘要组信息：{"kind": "group", "group_key": ..., "group_label": ..., "group_count": ...}
              - 轨迹信息：{"kind": "track", "group_key": ..., "track": <原始行字典>}
        """
        # 未设置分组条件，直接包装每行为 track 字典并返回
        if not self.group_by:
            return [{"kind": "track", "track": row} for row in rows]

        # 初始化分组数据结构：groups 存储分组键到行列表的映射，labels 存储分组键到显示标签的映射
        groups: dict[str, list[dict]] = defaultdict(list)
        labels: dict[str, str] = {}
        # 遍历所有行，计算分组键和标签，并进行归类
        for row in rows:
            gk, label = self._group_key_label(row, self.group_by)
            groups[gk].append(row)
            labels[gk] = label

        # 数值型分桶的 group_key 已包含前导零（如 tag:播放次数:0002），直接按 key 排序可保证数值顺序；
        # 文本型分组的 group_key 含前缀（如 name:zh:A），按 label 排序更直观。
        # 统一先按 group_key 排序（数值分桶正确），再对纯文本分组按 label 重排。
        # 判断分组条件是否是针对数值型标签（tag）的
        if self.group_by and (self.group_by.startswith("tag:") and self.group_by.split(":", 1)[1] in self.NUMERIC_TAG_NAMES):
            # 数值型分组：直接按分组键（包含前导零）排序，以保证数值顺序
            keys = sorted(groups.keys())
        else:
            # 文本型分组：按分组的显示标签（不区分大小写）排序，使输出更直观
            keys = sorted(groups.keys(), key=lambda k: labels.get(k, k).casefold())
        # 初始化最终输出列表
        display: list[dict] = []
        # 按排序后的分组键顺序遍历
        for gk in keys:
            rows_in_group = groups[gk]
            # 添加该分组的摘要信息
            display.append(
                {
                    "kind": "group",
                    "group_key": gk,
                    "group_label": labels.get(gk, gk),
                    "group_count": len(rows_in_group),
                }
            )
            # 如果该分组当前处于折叠状态，则跳过其包含的行数据，只保留组摘要
            if gk in self.collapsed_group_keys:
                continue
            # 若分组未折叠，将其包含的每一行都添加到输出列表
            for row in rows_in_group:
                display.append({"kind": "track", "group_key": gk, "track": row})
        return display

    def _group_key_label(self, row: dict, key: str) -> tuple[str, str]:
        """根据给定的键和行数据，生成分组键和对应的标签。

        该方法根据不同的键名，从行数据中提取信息并进行归类，返回一个元组。
        元组的第一个元素是用于分组的内部键（通常包含类别和数值信息），
        第二个元素是用于显示的可读标签。

        Args:
            row (dict): 包含单个数据条目所有字段的字典。
            key (str): 要进行分组处理的字段名或特殊键名（如 "custom_order", "duration_sec" 等）。

        Returns:
            tuple[str, str]: 一个包含两个字符串的元组：
                - 第一个字符串是分组键（内部标识符）。
                - 第二个字符串是对应的可读标签。
        """
        # 处理自定义排序键：根据是否启用自定义排序，返回不同逻辑
        if key == "custom_order":
            if self.custom_order_enabled:
                # 当启用自定义排序时，返回条目序号信息
                value = _safe_int_value(row.get("entry", 0), 0)
                return f"entry:{value}", f"排序 {value}"
            # 未启用自定义排序时，根据是否收藏进行分组
            fav = bool(row.get("is_favorite"))
            return ("fav:1", "已收藏") if fav else ("fav:0", "未收藏")

        # 处理时长相关键：将秒数转换为不同时间段的标签
        if key in {"duration_sec", "duration_mmss"}:
            # 获取时长，处理可能为None或0的情况
            sec = float(row.get("duration_sec", 0) or 0)
            # 根据秒数范围返回对应的分组键和标签
            if sec <= 10:
                return "dur:0-10", "<10s"
            if sec <= 60:
                return "dur:10-60", "10s~1min"
            if sec <= 300:
                return "dur:1-5", "1~5min"
            if sec <= 600:
                return "dur:5-10", "5~10min"
            if sec < 1800:
                return "dur:10-30", "10~30min"
            return "dur:30+", "30min+"

        # 处理标题键：根据语言和首字母进行分组
        if key == "title":
            # 获取语言种类，若为空则标记为"unknown"
            lang = str(row.get("language_kind") or "unknown")
            # 获取标题的首字母
            initial = first_letter(str(row.get(key) or ""))
            return f"name:{lang}:{initial}", f"{lang}/{initial}"

        # 处理文件名键：根据文件名首字符的语言和字符类型进行分组
        if key == "file_name":
            # 获取文件名首字符（忽略前导空格）
            first = _first_non_space_char(str(row.get("file_name") or ""))
            # 判断首字符的语言和分类
            lang, bucket = _char_lang_bucket(first)
            # 根据语言生成标签
            lang_label = "中文" if lang == "zh" else "英语" if lang == "en" else "其它"
            return f"name_file:{lang}:{bucket}", f"{lang_label}/{bucket or '(空)'}"

        # 处理歌词文件名键：逻辑类似文件名，但需要先处理歌词文件名
        if key == "lyrics_file_name":
            # 先对歌词文件名进行预处理（如去除扩展名），再获取首字符
            first = _first_non_space_char(_lyrics_stem_label(str(row.get("lyrics_file_name") or "")))
            lang, bucket = _char_lang_bucket(first)
            lang_label = "中文" if lang == "zh" else "英语" if lang == "en" else "其它"
            return f"name_lyrics:{lang}:{bucket}", f"{lang_label}/{bucket or '(空)'}"

        # 处理偏好等级键：将偏好等级限制在1-10之间
        if key == "preference_level":
            try:
                # 将偏好等级限制在1-10的整数范围内
                level = max(1, min(10, int(row.get("preference_level", 5))))
            except Exception:
                # 如果转换失败，使用默认值5
                level = 5
            return f"pref:{level}", f"喜好 {level}"

        # 处理路径相关键：根据父目录路径进行分组
        if key in {"source_fullpath", "storage_relpath", "source_relpath"}:
            # 获取路径的父目录标签
            parent = _path_parent_label(str(row.get(key, "")))
            return f"dir:{key}:{parent}", f"目录/{parent}"

        # 处理轨道ID键：使用ID的前两个字符作为分组前缀
        if key == "track_id":
            value = str(row.get("track_id", ""))
            # 取ID的前两个字符，如果不足两个则使用全部字符或"(空)"
            prefix = value[:2] if len(value) >= 2 else value or "(空)"
            return f"id:{prefix}", prefix

        # 处理以"tag:"开头的键：根据标签名和值进行复杂分组
        if key.startswith("tag:"):
            # 提取标签名
            tag_name = key.split(":", 1)[1] if ":" in key else key
            raw = row.get(key, "")
            # 如果标签是预定义的数字标签，则进行数值分组
            if tag_name in self.NUMERIC_TAG_NAMES:
                num = _safe_int_value(raw, 0)
                # 特殊处理"喜爱程度"标签：限制在0-100，并按10为间隔分组
                if tag_name == "喜爱程度":
                    num = max(0, min(100, num))
                    bucket = (num // 10) * 10
                    upper = min(100, bucket + 9)
                    label = f"{bucket}~{upper}"
                    return f"{key}:{bucket:04d}", label
                # 处理"播放秒数"标签：按时间范围分组
                if tag_name in {"播放秒数"}:
                    if num <= 0:
                        return f"{key}:0000", "0s"
                    if num <= 60:
                        return f"{key}:0001", "0~1min"
                    if num <= 300:
                        return f"{key}:0002", "1~5min"
                    if num <= 600:
                        return f"{key}:0003", "5~10min"
                    if num <= 1800:
                        return f"{key}:0004", "10~30min"
                    return f"{key}:0005", "30min+"
                # 处理"比特率"、"采样率"等音频质量标签
                if tag_name in {"比特率", "采样率"}:
                    if num <= 0:
                        return f"{key}:0000", "未知"
                    if num < 128000:
                        return f"{key}:0001", "<128k"
                    if num < 256000:
                        return f"{key}:0002", "128k~256k"
                    if num < 320000:
                        return f"{key}:0003", "256k~320k"
                    if num < 1000000:
                        return f"{key}:0004", "320k~1M"
                    return f"{key}:0005", "1M+"
                # 对于其他数字标签（如播放次数、位深度等），按数量级分组
                # 播放次数、指定播放次数、早期跳过次数、位深度、声道数等
                if num <= 0:
                    return f"{key}:0000", "0"
                if num <= 5:
                    return f"{key}:0001", "1~5"
                if num <= 10:
                    return f"{key}:0002", "6~10"
                if num <= 50:
                    return f"{key}:0003", "11~50"
                if num <= 100:
                    return f"{key}:0004", "51~100"
                return f"{key}:0005", "100+"
            # 对于非数字标签，直接使用其值作为分组键和标签
            value = str(raw or "(空)")
            return f"{key}:{value}", value

        # 默认处理：对于其他未特殊处理的键，直接使用其值作为分组键和标签
        value = str(row.get(key, "") or "(空)")
        return f"{key}:{value}", value

    def _rebuild_display(self) -> None:
        """
        重新构建显示行。

        参数：
            无。

        返回值：
            无。
        """
        sorted_rows = self._sort_tracks(self.raw_tracks)  # 对原始轨道进行排序
        built = self._group_rows(sorted_rows)  # 将排序后的行分组
        self.beginResetModel()  # 开始重置模型
        self.display_rows = built  # 设置显示行为分组后的数据
        self.endResetModel()  # 结束重置模型

    def _is_editable_key(self, key: str, track: dict) -> bool:
        """判断给定的键是否在轨道中可编辑。

        参数：
        key (str): 要检查的键。
        track (dict): 包含轨道信息的字典。

        返回值：
        bool: 如果键可编辑则返回True，否则返回False。
        """
        # 检查键是否为"custom_order"，并基于自定义顺序启用状态和轨道可编辑标志返回结果
        if key == "custom_order":
            return bool(self.custom_order_enabled and track.get("_entry_editable"))
        # 如果键以"tag:"开头，则认为该键可编辑
        if key.startswith("tag:"):
            return True
        # 检查键是否在预定义的可编辑键集合中
        return key in {"file_name", "title", "artist", "preference_level", "language_kind", "album"}

    def _value_for_key(self, track: dict, key: str):
        """根据给定的键从音轨字典中获取对应的值。

        该方法处理特定键的特殊格式化逻辑，对于其他键则直接返回原始值。

        参数:
            track (dict): 包含音轨信息的字典。
            key (str): 要获取值的键名。

        返回值:
            对应键的值，可能经过格式化处理。
        """
        # 处理自定义排序键，逻辑涉及两种情况：启用自定义顺序或按收藏状态排序
        if key == "custom_order":
            # 如果启用了自定义顺序功能
            if self.custom_order_enabled:
                # 返回音轨的`entry`字段的安全整数值，默认为0
                return _safe_int_value(track.get("entry", 0), 0)
            # 否则，根据是否收藏返回1或0作为排序值
            return 1 if bool(track.get("is_favorite")) else 0
        # 处理时长键，格式化为 "分:秒" 字符串
        if key == "duration_mmss":
            # 返回格式化后的时长，如果音轨中没有则根据秒数计算默认值
            return track.get("duration_mmss", format_mmss(track.get("duration_sec", 0)))
        # 处理歌词文件名键
        if key == "lyrics_file_name":
            # 返回歌词文件名，如果音轨中没有则根据歌词源路径提取基本文件名作为默认值
            return track.get("lyrics_file_name", basename(track.get("lyrics_source", "")))
        # 处理以 "tag:" 开头的自定义标签键
        if key.startswith("tag:"):
            # 将获取到的值转换为字符串返回，确保类型一致性
            return str(track.get(key, ""))
        # 默认情况：直接返回键对应的原始值，如果不存在则返回空字符串
        return track.get(key, "")

    def is_group_row(self, row: int) -> bool:
        """判断指定行是否为分组行。

        Args:
            row (int): 要检查的行的索引。

        Returns:
            bool: 如果该行是分组行则返回 True，否则返回 False。
        """
        # 边界检查：行索引是否有效
        if row < 0 or row >= len(self.display_rows):
            return False
        # 核心判断：该行的 kind 字段是否为 "group"
        return self.display_rows[row].get("kind") == "group"

    def toggle_group_row(self, row: int) -> bool:
        if not self.is_group_row(row):
            return False
        gk = self.display_rows[row]["group_key"]
        if gk in self.collapsed_group_keys:
            self.collapsed_group_keys.remove(gk)
        else:
            self.collapsed_group_keys.add(gk)
        self._rebuild_display()
        return True

    def group_track_ids(self, row: int) -> list[str]:
        """根据指定的行号，返回该行所属分组中的所有轨道ID。

        参数:
            row (int): 要查询的行的索引号。

        返回:
            list[str]: 一个字符串列表，包含所有属于该行所在分组的轨道ID。
                       如果行不是分组行或未设置分组，则返回空列表。
        """
        # 前置检查：如果行不是分组行，或者实例未设置分组依据，则直接返回空列表。
        if not self.is_group_row(row) or not self.group_by:
            return []
        # 获取目标分组的标识键（group_key）。
        gk = self.display_rows[row]["group_key"]
        # 初始化一个列表，用于存放结果轨道ID。
        out: list[str] = []
        # 遍历经过排序的原始轨道数据列表。
        for item in self._sort_tracks(self.raw_tracks):
            # 计算当前遍历条目所属的分组键和标签。
            item_gk, _ = self._group_key_label(item, self.group_by)
            # 如果当前条目的分组键与目标分组键一致，并且该条目包含有效的‘track_id’。
            if item_gk == gk and item.get("track_id"):
                # 将轨道ID转换为字符串并添加到结果列表中。
                out.append(str(item["track_id"]))
        # 返回收集到的轨道ID列表。
        return out

    def row_indexes_for_track_ids(self, track_ids: set[str]) -> list[int]:
        """功能：返回给定track IDs对应的行索引。
        参数：track_ids (set[str]): 要查找的track IDs集合。
        返回值：list[int]: 匹配的行索引列表。"""
        out: list[int] = []  # 初始化空列表，用于存储匹配行的索引
        for idx, row_obj in enumerate(self.display_rows):  # 使用enumerate遍历self.display_rows，获取索引和行对象
            if row_obj.get("kind") != "track":  # 检查行对象的"kind"键是否为"track"，如果不是则跳过
                continue
            track_id = str(row_obj["track"].get("track_id", ""))  # 从行对象的"track"字典中提取"track_id"，默认为空字符串，并转换为字符串
            if track_id in track_ids:  # 检查提取的track_id是否存在于给定的track_ids集合中
                out.append(idx)  # 如果匹配，将当前索引添加到输出列表中
        return out  # 返回包含所有匹配行索引的列表

    def track_for_row(self, row: int) -> dict | None:
        if row < 0 or row >= len(self.display_rows):
            return None
        row_obj = self.display_rows[row]
        if row_obj.get("kind") != "track":
            return None
        return row_obj.get("track")

    def selected_track_ids_from_rows(self, rows: list[int]) -> list[str]:
        out: list[str] = []
        for row in rows:
            track = self.track_for_row(row)
            if track and track.get("track_id"):
                out.append(str(track["track_id"]))
        return out
