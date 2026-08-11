from __future__ import annotations

"""???????

????????????????????????
??????????????????
"""

import re
from collections import deque
from pathlib import Path
from time import monotonic

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableView,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from musearc.app.facade import MuseArcFacade
from musearc.ui.review_page_mixins_lyrics import ReviewPageLyricsMixin
from musearc.ui.review_page_mixins_song import ReviewPageSongMixin
from musearc.ui.table_models import ColumnDef, DictTableModel


def _apply_button_scale(button: QPushButton, scale: float) -> None:
    button.setMinimumHeight(max(30, int(28 * scale)))


def _install_tree_copy_shortcut(tree: QTreeWidget) -> None:
    """为QTreeWidget安装复制快捷键，允许用户通过Ctrl+C将选中的树节点内容复制到剪贴板。

    Args:
        tree (QTreeWidget): 需要安装复制快捷键的树控件实例。

    Returns:
        None: 此函数不返回任何值。
    """
    def _copy_rows() -> None:
        selected = tree.selectedItems()
        if not selected:
            return
        lines = []
        col_count = tree.columnCount()
        for item in selected:
            # 将每个选中节点的每列文本用制表符连接成一行
            lines.append("\t".join(item.text(i) for i in range(col_count)))
        # 将所有行用换行符连接后复制到系统剪贴板
        QApplication.clipboard().setText("\n".join(lines))

    # 创建复制快捷键（通常是Ctrl+C）
    shortcut = QShortcut(QKeySequence.StandardKey.Copy, tree)
    # 将快捷键激活信号连接到复制功能函数
    shortcut.activated.connect(_copy_rows)
    # 将快捷键对象存储为树控件的属性，避免被垃圾回收
    tree._copy_shortcut = shortcut


def _safe_float(value, default: float = 0.0) -> float:
    """
    安全地将输入值转换为浮点数。如果转换失败，则返回指定的默认值。

    参数：
        value: 要转换为浮点数的输入值。
        default (float): 转换失败时返回的默认浮点数，默认为0.0。

    返回：
        float: 转换后的浮点数或默认值。
    """
    try:
        # 尝试将输入值转换为浮点数
        return float(value)
    except Exception:
        # 如果转换失败，返回默认值
        return default


def _safe_int(value, default: int = 0) -> int:
    """安全地将值转换为整数。

    参数:
    value: 要转换的值，可以是任意类型。
    default: 默认值，当转换失败时返回，默认为0。

    返回值:
    int: 转换后的整数或默认值。
    """
    if isinstance(value, (list, tuple, dict, set)): # 检查值是否是列表、元组、字典或集合，避免转换这些类型
        return default # 直接返回默认值
    try:
        return int(value or 0) # 尝试转换为整数；如果value为假值（如None、0、空字符串），则使用0
    except Exception: # 捕获任何异常，确保转换失败时安全返回
        return default # 返回默认值


def _format_mmss(seconds: int) -> str:
    """将秒数格式化为 'mm:ss' 格式的字符串。

    Args:
        seconds (int): 要格式化的秒数。

    Returns:
        str: 格式化后的时间字符串，格式为 'mm:ss'。
    """
    sec = max(0, _safe_int(seconds, 0))  # 使用 _safe_int 安全转换为整数，确保非负
    return f"{sec // 60:02d}:{sec % 60:02d}"  # 整数除法得到分钟，取模得到秒，并格式化为两位数


def _track_label(track: dict) -> str:
    return f"{track.get('artist', '')} - {track.get('title', '')} ({track.get('track_id', '')})"


def _canonical_lyrics_name(file_name: str) -> str:
    """将文件名转换为规范化的歌词标识名。

    功能：
        处理输入的文件名字符串，移除扩展名、特殊字符和括号内容，
        生成一个用于统一匹配的规范化歌词名称。

    参数：
        file_name (str): 原始文件名字符串。

    返回：
        str: 规范化后的歌词标识名（小写、无括号、单词间单空格分隔）。
    """
    # 提取文件主干（去掉扩展名），转为小写并去除首尾空白
    stem = Path(str(file_name or "")).stem.casefold().strip()
    # 将连续空白、点、下划线、连字符统一替换为单个空格
    stem = re.sub(r"[\s._-]+", " ", stem)
    # 移除末尾的括号及其内容（支持中英文括号）
    stem = re.sub(r"\s*[\(\[（【].*?[\)\]）】]\s*$", "", stem)
    # 返回去除首尾空白的规范化名称
    return stem.strip()


def _lyrics_file_bracket_count(file_name: str) -> int:
    """统计文件名中的括号对数量。

    该函数从给定的文件名（或完整路径）中提取文件名主干，并计算其中包含的括号对总数。
    支持中文和英文的多种括号类型。

    Args:
        file_name (str): 输入的文件名字符串。

    Returns:
        int: 文件名主干中匹配的括号对数量。
    """
    # 从可能包含路径的输入中，提取不带扩展名的文件名主干部分
    # 使用 str(file_name or "") 防御性处理 None 或空字符串
    stem = Path(str(file_name or "")).stem
    # 使用正则表达式在文件名主干中查找所有括号对
    # [\(\[（【].*?[\)\]）】] 匹配以 (、[、（、【 开始，并以 )、]、）】 结束的任意非贪婪字符序列
    # 非贪婪匹配 (.*?) 确保匹配到最近的闭合括号，避免过度匹配
    return len(re.findall(r"[\(\[（【].*?[\)\]）】]", stem))


def _derive_lyrics_group_title(group_key: str, source_rel: str) -> str:
    """
    根据组键和源文件相对路径推导出歌词组标题。

    参数:
        group_key (str): 组键，用于标识歌词组。
        source_rel (str): 源文件的相对路径。

    返回:
        str: 推导出的歌词组标题。
    """
    key = str(group_key or "").strip()  # 将group_key转换为字符串，若为None则用空字符串，并去除首尾空格
    if key and not key.startswith("lyr_grp_"):  # 如果key非空且不以"lyr_grp_"开头，则认为是有效标题
        return key  # 直接返回key作为标题
    stem = Path(str(source_rel or "")).stem.strip()  # 从源文件相对路径提取文件名（不含扩展名），并去除空格
    if not stem:  # 如果提取的文件名为空
        return key or "未分组"  # 返回key或默认标题"未分组"
    cleaned = re.sub(r"\s*[\(\[（【].*?[\)\]）】]\s*$", "", stem).strip()  # 使用正则表达式移除文件名末尾的括号及其内容（支持中文和英文括号）
    return cleaned or stem  # 如果清理后的标题非空则返回，否则返回原始文件名


def _derive_song_group_title(group_key: str, source_path: str) -> str:
    """根据提供的组键和源路径，衍生歌曲组标题。

    参数:
        group_key (str): 组的键，可能为空或包含其他字符。
        source_path (str): 源文件的路径，用于提取文件名。

    返回值:
        str: 衍生的标题字符串。如果组键有效则使用组键，否则从文件名中清理出标题，或返回默认值。
    """
    # 将 group_key 转换为字符串，如果为空则为空字符串，并去除首尾空白
    key = str(group_key or "").strip()
    # 从 source_path 中提取文件名（去除扩展名），并去除首尾空白
    stem = Path(str(source_path or "")).stem.strip()
    # 如果 key 非空、长度大于6，并且不全是十六进制字符或下划线（即不是纯数字或简单标识符），则直接返回 key
    if key and len(key) > 6 and not re.fullmatch(r"[0-9a-fA-F_]+", key):
        return key
    # 如果 stem 非空，则尝试清理掉末尾的括号及其内容（包括各种括号类型）
    if stem:
        cleaned = re.sub(r"\s*[\(\[（【].*?[\)\]）】]\s*$", "", stem).strip()
        # 如果清理后的字符串非空，则返回清理后的字符串；否则返回原始的 stem
        return cleaned or stem
    # 如果以上都不满足，则返回 key（如果非空）或默认标题“未分组”
    return key or "未分组"


class _ClickableFrame(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """处理鼠标按下事件。

        当鼠标按钮被按下时触发，主要处理左键点击并发射信号，
        同时确保父类事件处理逻辑正常执行。

        Args:
            event (QMouseEvent): 鼠标事件对象，包含按键信息。

        Returns:
            None: 该方法不返回任何值。
        """
        if event.button() == Qt.MouseButton.LeftButton:  # 检查是否为鼠标左键按下
            self.clicked.emit()  # 发射自定义的clicked信号
        super().mousePressEvent(event)  # 调用父类的鼠标按下事件处理，保持默认行为


class _TrackPickerDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        facade: MuseArcFacade,
        *,
        initial_query: str = "",
        lyrics_preview_text: str = "",
        preselected_track_id: str | None = None,
    ):
        """
        初始化歌曲映射选择对话框。

        本方法负责构建整个对话框的UI布局、加载数据并建立信号槽连接。
        它为用户提供一个界面，用于搜索、浏览并选择一个已有的数据库曲目，
        以建立歌词的映射关系，或者清空已有的映射。

        参数:
            parent (QWidget): 父窗口部件。
            facade (MuseArcFacade): 应用程序门面对象，用于访问后端功能（如获取曲目列表）。
            initial_query (str): 对话框打开时搜索框的初始查询字符串。
            lyrics_preview_text (str): 需要预览的歌词文本内容。
            preselected_track_id (str | None): 预选中的曲目ID，用于高亮显示。

        返回值:
            None: 此方法无返回值，直接初始化对象。
        """
        # 调用父类QWidget的初始化方法，并设置父对象
        super().__init__(parent)
        # 保存门面对象引用，以便后续调用其方法获取数据
        self.facade = facade
        # 初始化当前选中的曲目ID为空（用户尚未选择）
        self.selected_track_id: str | None = None
        # 将预选ID转为字符串并去除首尾空格，处理None值
        self._preselected_track_id = str(preselected_track_id or "").strip()
        # 设置对话框窗口标题
        self.setWindowTitle("选择映射歌曲")
        # 设置对话框初始大小
        self.resize(1100, 680)

        # 创建主垂直布局容器
        root = QVBoxLayout(self)
        # 创建顶部水平布局（用于放置搜索框和搜索按钮）
        top = QHBoxLayout()
        # 创建搜索输入框
        self.search_input = QLineEdit()
        # 设置搜索框的占位提示文本
        self.search_input.setPlaceholderText("搜索 文件名/标题/艺术家/专辑")
        # 创建搜索按钮
        self.btn_search = QPushButton("搜索")
        # 将搜索框添加到顶部布局，并设置伸展因子为1（占据大部分空间）
        top.addWidget(self.search_input, 1)
        # 将搜索按钮添加到顶部布局
        top.addWidget(self.btn_search)

        # 定义表格的列结构（列ID和显示名称）
        self.model = DictTableModel(
            [
                ColumnDef("file_name", "文件名"),
                ColumnDef("title", "标题"),
                ColumnDef("artist", "艺术家"),
                ColumnDef("album", "专辑"),
                ColumnDef("bound_lyrics", "已绑歌词"),
                ColumnDef("track_id", "数据库ID"),
            ]
        )
        # 创建表格视图控件
        self.table = QTableView()
        # 将数据模型设置到表格视图
        self.table.setModel(self.model)
        # 设置表格选择行为：整行选择
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # 设置表格选择模式：单选
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # 禁用表格编辑触发（表格内容只读）
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # 启用交替行颜色，提高可读性
        self.table.setAlternatingRowColors(True)
        # 启用表头点击排序功能
        self.table.setSortingEnabled(True)
        # 设置水平表头最后一列自动拉伸以填充剩余空间
        self.table.horizontalHeader().setStretchLastSection(True)

        # 创建按钮盒容器
        self.buttons = QDialogButtonBox()
        # 向按钮盒中添加“确定”按钮，角色为接受（AcceptRole）
        self.btn_ok = self.buttons.addButton("确定", QDialogButtonBox.ButtonRole.AcceptRole)
        # 向按钮盒中添加“取消”按钮，角色为拒绝（RejectRole）
        self.btn_cancel = self.buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        # 向按钮盒中添加“清空映射”按钮，角色为破坏性（DestructiveRole，通常显示为红色或警告色）
        self.btn_clear = self.buttons.addButton("清空映射", QDialogButtonBox.ButtonRole.DestructiveRole)

        # 创建水平分割器，用于调整左右面板宽度
        split = QSplitter(Qt.Orientation.Horizontal)
        # 创建左侧面板容器
        left = QWidget()
        # 为左侧面板创建垂直布局
        left_layout = QVBoxLayout(left)
        # 移除左侧面板布局的边距
        left_layout.setContentsMargins(0, 0, 0, 0)
        # 将表格控件添加到左侧面板，并设置伸展因子为1
        left_layout.addWidget(self.table, 1)
        # 将左侧面板添加到分割器
        split.addWidget(left)

        # 创建右侧面板容器
        right = QWidget()
        # 为右侧面板创建垂直布局
        right_layout = QVBoxLayout(right)
        # 设置右侧面板布局的左边距（用于与左侧表格视觉分离）
        right_layout.setContentsMargins(8, 0, 0, 0)
        # 添加“当前歌词预览”标签
        right_layout.addWidget(QLabel("当前歌词预览"))
        # 创建歌词预览文本编辑框
        self.preview = QPlainTextEdit()
        # 设置预览框为只读模式
        self.preview.setReadOnly(True)
        # 设置预览框的文本内容：优先使用传入的歌词预览文本，否则显示默认提示
        self.preview.setPlainText(str(lyrics_preview_text or "").strip() or "（无可用歌词预览）")
        # 将预览框添加到右侧面板，并设置伸展因子为1
        right_layout.addWidget(self.preview, 1)
        # 将右侧面板添加到分割器
        split.addWidget(right)
        # 设置分割器初始比例：左侧占3份，右侧占2份
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)

        # 将顶部搜索栏布局添加到主布局
        root.addLayout(top)
        # 将分割器添加到主布局，并设置伸展因子为1（占据主要垂直空间）
        root.addWidget(split, 1)
        # 将按钮盒添加到主布局底部
        root.addWidget(self.buttons)

        # 通过门面对象从后端获取所有曲目数据（限制数量为200,000，防止数据量过大）
        all_rows = self.facade.list_tracks(limit=200_000)
        # 初始化存储所有曲目数据的列表
        self._all_rows = []
        # 遍历原始数据，进行预处理
        for row in all_rows:
            # 将行数据转换为字典（确保可修改）
            item = dict(row)
            # 处理歌词来源路径：替换反斜杠为正斜杠，去除首尾空格
            source = str(item.get("lyrics_source", "") or "").replace("\\", "/").strip()
            # 从完整路径中提取文件名作为“已绑歌词”列的值，若无来源则置为空字符串
            item["bound_lyrics"] = Path(source).name if source else ""
            # 将处理后的数据行添加到列表
            self._all_rows.append(item)
        # 如果提供了初始查询字符串，则设置到搜索框
        if str(initial_query or "").strip():
            self.search_input.setText(str(initial_query).strip())
        # 应用初始过滤（根据可能的初始查询和预选ID）
        self._apply_filter()

        # 连接信号与槽：当搜索按钮被点击时，应用过滤
        self.btn_search.clicked.connect(self._apply_filter)
        # 连接信号与槽：当搜索框文本变化时，应用过滤（使用lambda忽略传入的文本参数）
        self.search_input.textChanged.connect(lambda _text: self._apply_filter())
        # 连接信号与槽：当搜索框内按下回车键时，应用过滤
        self.search_input.returnPressed.connect(self._apply_filter)
        # 连接信号与槽：当表格行被双击时，接受当前选择（调用_accept_selected方法）
        self.table.doubleClicked.connect(lambda _idx: self._accept_selected())
        # 连接信号与槽：当“确定”按钮被点击时，接受当前选择
        self.btn_ok.clicked.connect(self._accept_selected)
        # 连接信号与槽：当“取消”按钮被点击时，关闭对话框（调用reject方法）
        self.btn_cancel.clicked.connect(self.reject)
        # 连接信号与槽：当“清空映射”按钮被点击时，执行清空映射操作
        self.btn_clear.clicked.connect(self._accept_clear)

    def _apply_filter(self) -> None:
        """应用搜索过滤功能。根据搜索输入框中的文本，过滤并更新显示的数据行。
        如果搜索文本为空，则显示所有行；否则，只显示包含搜索文本的行。
        完成过滤后，会尝试选中之前记录的待选曲目。

        Args:
            self: 类实例本身。

        Returns:
            None: 此方法无返回值。
        """
        # 获取并清理搜索输入框的文本，并转换为小写以便进行不区分大小写的匹配
        token = self.search_input.text().strip().casefold()
        if not token:
            # 如果没有搜索词，则显示所有数据行
            rows = list(self._all_rows)
        else:
            # 有搜索词时，开始过滤行
            rows = []
            for row in self._all_rows:
                # 将当前行中的多个关键字段拼接成一个字符串，用于后续的文本匹配
                text = " | ".join(
                    [
                        str(row.get("file_name", "")),
                        str(row.get("title", "")),
                        str(row.get("artist", "")),
                        str(row.get("album", "")),
                        str(row.get("bound_lyrics", "")),
                    ]
                ).casefold()
                # 检查搜索词是否存在于拼接后的文本中
                if token in text:
                    rows.append(row)
        # 使用过滤后的行数据更新模型
        self.model.set_rows(rows)
        # 根据之前保存的ID，尝试在可见列表中选中对应的曲目
        self._select_track_if_visible(self._preselected_track_id)

    def _select_track_if_visible(self, track_id: str) -> None:
        """根据给定的 track_id，在表格模型中查找并选中对应的行（如果该行可见）。

        Args:
            track_id (str): 需要查找并选中的轨道ID。
        Returns:
            None: 此方法不返回任何值。
        """
        # 确保目标 track_id 是一个干净的字符串
        target = str(track_id or "").strip()
        # 如果目标为空，则直接返回
        if not target:
            return
        # 遍历表格模型中的所有行
        for row_idx in range(self.model.rowCount()):
            # 获取当前行的数据，如果为 None 则视为空字典
            row = self.model.row_at(row_idx) or {}
            # 比较当前行的 track_id 与目标 track_id，不匹配则跳过
            if str(row.get("track_id", "") or "") != target:
                continue
            # 获取当前行的第一个单元格的模型索引
            idx = self.model.index(row_idx, 0)
            # 如果索引无效，则跳过
            if not idx.isValid():
                continue
            # 设置表格的当前索引为找到的索引
            self.table.setCurrentIndex(idx)
            # 选中找到的整行
            self.table.selectRow(row_idx)
            # 将表格滚动到选中行的位置，使其可见
            self.table.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
            # 找到并选中后，立即返回
            return

    def _accept_selected(self) -> None:
        sm = self.table.selectionModel()
        selected = sm.selectedRows() if sm is not None else []
        if not selected:
            QMessageBox.warning(self, "选择映射歌曲", "请先选择一首歌曲。")
            return
        row = self.model.row_at(selected[0].row()) or {}
        track_id = str(row.get("track_id", "") or "")
        if not track_id:
            QMessageBox.warning(self, "选择映射歌曲", "当前行没有有效 track_id。")
            return
        self.selected_track_id = track_id
        self.accept()

    def _accept_clear(self) -> None:
        self.selected_track_id = None
        self.accept()


class ReviewPage(ReviewPageSongMixin, ReviewPageLyricsMixin, QWidget):
    review_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade
        self._track_map: dict[str, dict] = {}
        self._lyrics_by_source: dict[str, dict] = {}
        self._lyrics_by_id: dict[str, dict] = {}
        self._review_ref_cache_deadline: float = 0.0
        self._preview_rows: deque[dict] = deque(maxlen=2)
        self._sync_preview_scroll = False
        self._button_scale = 1.0
        self._static_buttons: list[QPushButton] = []
        self._dynamic_buttons: list[QPushButton] = []
        self._song_group_controls: dict[str, dict] = {}
        self._lyrics_group_controls: dict[str, dict] = {}
        self._lyrics_row_controls: dict[str, dict] = {}
        self._lyrics_review_order: list[dict] = []
        self._lyrics_map_dialog_open = False
        self._review_filter_options: dict[str, list[str]] = {
            "song": [],
            "lyrics": [],
            "file": [],
            "other": [],
        }
        self._review_filter_selected: dict[str, set[str]] = {
            "song": set(),
            "lyrics": set(),
            "file": set(),
            "other": set(),
        }

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        self.song_tab = QWidget()
        self.lyrics_tab = QWidget()
        self.file_tab = QWidget()
        self.other_tab = QWidget()
        self.tabs.addTab(self.song_tab, "歌曲待审查")
        self.tabs.addTab(self.lyrics_tab, "歌词待审查")
        self.tabs.addTab(self.file_tab, "文件异常")
        self.tabs.addTab(self.other_tab, "其它")

        self._build_song_tab()
        self._build_lyrics_tab()
        self._build_file_tab()
        self._build_other_tab()

        row_bottom = QHBoxLayout()
        self.btn_reload = QPushButton("刷新审查")
        self._register_static_button(self.btn_reload)
        row_bottom.addWidget(self.btn_reload)
        row_bottom.addStretch(1)
        root.addLayout(row_bottom)

        self.btn_reload.clicked.connect(self.reload_reviews)
        self.reload_reviews()

    def _register_static_button(self, button: QPushButton) -> None:
        self._static_buttons.append(button)
        _apply_button_scale(button, self._button_scale)

    def _register_dynamic_button(self, button: QPushButton) -> None:
        self._dynamic_buttons.append(button)
        _apply_button_scale(button, self._button_scale)

    def _build_song_tab(self) -> None:
        """构建“歌曲”标签页的界面布局。

        功能：创建“歌曲”标签页的完整UI，包括顶部操作栏（筛选按钮和标签）、
             以及一个可滚动的区域用于显示歌曲分组内容。

        参数：无。

        返回值：无。
        """
        root = QVBoxLayout(self.song_tab)  # 创建主垂直布局，并设置父控件为song_tab
        row_top = QHBoxLayout()  # 创建顶部行的水平布局

        self.btn_song_filter = QPushButton("筛选问题")  # 创建筛选按钮
        self.lbl_song_filter = QLabel("")  # 创建一个空标签，用于显示筛选信息
        self._register_static_button(self.btn_song_filter)  # 注册按钮到静态管理器（用于样式或功能管理）

        # 将筛选按钮和标签添加到顶部行
        row_top.addWidget(self.btn_song_filter)
        row_top.addWidget(self.lbl_song_filter)
        row_top.addStretch(1)  # 添加弹性空间，使按钮和标签靠左显示

        # 创建滚动区域，用于承载大量内容
        self.song_scroll = QScrollArea()
        self.song_scroll.setWidgetResizable(True)  # 设置滚动区域内的控件大小可随区域调整

        # 创建用于放置歌曲分组的宿主控件和布局
        self.song_groups_host = QWidget()
        self.song_groups_layout = QVBoxLayout(self.song_groups_host)
        self.song_groups_layout.setContentsMargins(8, 8, 8, 8)  # 设置布局内边距
        self.song_groups_layout.setSpacing(12)  # 设置布局内控件间的垂直间距
        self.song_groups_layout.addStretch(1)  # 添加弹性空间，将内容推向上方

        self.song_scroll.setWidget(self.song_groups_host)  # 将宿主控件设置到滚动区域

        # 将顶部行和滚动区域添加到根布局
        root.addLayout(row_top)
        root.addWidget(self.song_scroll, 1)  # 参数1表示滚动区域占据剩余空间的伸缩因子

        # 连接筛选按钮的点击信号到对应的槽函数，传入"song"作为类型标识
        self.btn_song_filter.clicked.connect(lambda: self._open_review_filter_dialog("song"))

    def _build_lyrics_tab(self) -> None:
        """构建“歌词”标签页的UI布局。

        该方法负责创建歌词标签页的所有图形界面元素，包括顶部按钮行、左侧歌词文件分组滚动区域和右侧歌词对比预览区。
        所有控件通过布局管理器进行组织，并将相关信号连接到槽函数。

        参数:
            self (MainWindow): 主窗口实例。

        返回:
            None: 此方法无返回值，仅完成UI构建。
        """
        root = QVBoxLayout(self.lyrics_tab)  # 创建歌词标签页的主垂直布局
        row_top = QHBoxLayout()  # 创建顶部水平布局，用于放置筛选按钮和标签
        self.btn_lyrics_filter = QPushButton("筛选问题")  # 创建筛选按钮
        self.lbl_lyrics_filter = QLabel("")  # 创建一个用于显示筛选状态的空标签
        self._register_static_button(self.btn_lyrics_filter)  # 将按钮注册到静态按钮管理器
        row_top.addWidget(self.btn_lyrics_filter)  # 将筛选按钮添加到顶部行
        row_top.addWidget(self.lbl_lyrics_filter)  # 将状态标签添加到顶部行
        row_top.addStretch(1)  # 在顶部行末尾添加弹性空间，使控件靠左对齐
        split = QSplitter(Qt.Orientation.Horizontal)  # 创建一个水平分割器，用于左右分割主区域
        # 构建左侧歌词文件列表滚动区域
        self.lyrics_scroll = QScrollArea()  # 创建滚动区域
        self.lyrics_scroll.setWidgetResizable(True)  # 允许内部小部件根据滚动区域自动调整大小
        self.lyrics_groups_host = QWidget()  # 创建承载所有歌词分组的宿主部件
        self.lyrics_groups_layout = QVBoxLayout(self.lyrics_groups_host)  # 为宿主部件创建垂直布局
        self.lyrics_groups_layout.setContentsMargins(8, 8, 8, 8)  # 设置布局边距
        self.lyrics_groups_layout.setSpacing(12)  # 设置布局内部件之间的间距
        self.lyrics_groups_layout.addStretch(1)  # 在布局末尾添加弹性空间，使内容顶部对齐
        self.lyrics_scroll.setWidget(self.lyrics_groups_host)  # 将宿主部件设置为滚动区域的内容
        split.addWidget(self.lyrics_scroll)  # 将歌词列表滚动区域添加到分割器左侧
        # 构建右侧歌词对比预览区域
        preview_host = QWidget()  # 创建预览区域的宿主部件
        preview_layout = QVBoxLayout(preview_host)  # 为预览宿主部件创建垂直布局
        preview_layout.addWidget(QLabel("歌词对比预览（最近点击的两个文件）"))  # 添加预览区标题标签
        preview_split = QSplitter(Qt.Orientation.Horizontal)  # 创建另一个水平分割器，用于左右预览文本框
        self.preview_left = QPlainTextEdit()  # 创建左侧预览文本框
        self.preview_left.setReadOnly(True)  # 设置左侧预览文本框为只读
        self.preview_right = QPlainTextEdit()  # 创建右侧预览文本框
        self.preview_right.setReadOnly(True)  # 设置右侧预览文本框为只读
        preview_split.addWidget(self.preview_left)  # 将左侧预览文本框添加到预览分割器
        preview_split.addWidget(self.preview_right)  # 将右侧预览文本框添加到预览分割器
        preview_split.setStretchFactor(0, 1)  # 设置左侧预览框的拉伸因子为1
        preview_split.setStretchFactor(1, 1)  # 设置右侧预览框的拉伸因子为1，使其与左侧等宽
        preview_split.setSizes([1, 1])  # 设置两个预览框初始大小比例为1:1
        preview_layout.addWidget(preview_split, 1)  # 将预览分割器添加到预览布局，并设置拉伸因子为1使其占据大部分空间
        split.addWidget(preview_host)  # 将预览宿主部件添加到主分割器右侧
        split.setStretchFactor(0, 5)  # 设置左侧歌词列表的拉伸因子为5
        split.setStretchFactor(1, 1)  # 设置右侧预览区域的拉伸因子为1
        split.setSizes([1100, 240])  # 设置左右区域的初始宽度分别为1100和240像素
        root.addLayout(row_top)  # 将顶部行布局添加到主布局
        root.addWidget(split, 1)  # 将主分割器添加到主布局，并设置拉伸因子为1
        # 连接信号与槽函数
        self.btn_lyrics_filter.clicked.connect(lambda: self._open_review_filter_dialog("lyrics"))  # 将筛选按钮的点击信号连接到打开筛选对话框的方法，并传递参数"lyrics"
        # 连接两个预览文本框的滚动条信号，实现同步滚动
        self.preview_left.verticalScrollBar().valueChanged.connect(
            lambda v: self._sync_preview_scrollbars(from_left=True, value=v)  # 左侧滚动条值变化时，同步右侧滚动条
        )
        self.preview_right.verticalScrollBar().valueChanged.connect(
            lambda v: self._sync_preview_scrollbars(from_left=False, value=v)  # 右侧滚动条值变化时，同步左侧滚动条
        )

    def _build_file_tab(self) -> None:
        """构建文件选项卡界面。

        创建文件管理界面，包含筛选、反选、重试导入、保存和忽略等功能按钮，
        以及用于显示文件列表的树形控件。

        参数:
            无（除self外）

        返回值:
            无
        """
        # 创建垂直布局作为文件选项卡的主布局
        root = QVBoxLayout(self.file_tab)
        # 创建水平布局用于放置功能按钮
        row = QHBoxLayout()
        # 创建筛选问题按钮
        self.btn_file_filter = QPushButton("筛选问题")
        # 创建反选按钮，用于切换选中状态
        self.btn_file_invert = QPushButton("反选")
        # 创建重试导入选中路径按钮
        self.btn_file_retry = QPushButton("重试导入选中路径")
        # 创建保存勾选文件按钮
        self.btn_file_save = QPushButton("保存勾选的文件")
        # 创建忽略勾选项按钮
        self.btn_file_ignore = QPushButton("忽略勾选")
        # 创建用于显示筛选条件的标签
        self.lbl_file_filter = QLabel("")
        # 将按钮注册为静态按钮，用于样式管理
        self._register_static_button(self.btn_file_filter)
        self._register_static_button(self.btn_file_invert)
        self._register_static_button(self.btn_file_retry)
        self._register_static_button(self.btn_file_save)
        self._register_static_button(self.btn_file_ignore)
        # 将所有按钮和标签添加到水平布局中
        row.addWidget(self.btn_file_filter)
        row.addWidget(self.lbl_file_filter)
        row.addWidget(self.btn_file_invert)
        row.addWidget(self.btn_file_retry)
        row.addWidget(self.btn_file_save)
        row.addWidget(self.btn_file_ignore)
        # 添加弹性空间，使按钮靠左对齐
        row.addStretch(1)

        # 创建树形控件用于显示文件列表
        self.file_tree = QTreeWidget()
        # 设置表头标签：保留、标题、来源、详情、审查ID
        self.file_tree.setHeaderLabels(["保留", "标题", "来源", "详情", "审查ID"])
        # 启用交替行颜色，提高可读性
        self.file_tree.setAlternatingRowColors(True)
        # 设置为扩展选择模式，支持多选
        self.file_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # 设置复选框指示器的样式大小
        self.file_tree.setStyleSheet("QTreeWidget::indicator{width:30px;height:30px;}")
        # 安装树形控件的复制快捷键功能
        _install_tree_copy_shortcut(self.file_tree)
        # 将按钮行布局添加到主布局
        root.addLayout(row)
        # 将树形控件添加到主布局，拉伸因子为1使其占满剩余空间
        root.addWidget(self.file_tree, 1)

        # 连接反选按钮的点击信号到反选复选框状态的方法
        self.btn_file_invert.clicked.connect(lambda: self._invert_check_state_tree(self.file_tree))
        # 连接重试按钮的点击信号到重试选中文件的方法
        self.btn_file_retry.clicked.connect(self._retry_selected_file_issues)
        # 连接保存按钮的点击信号到解析已勾选项的方法，标记为"resolved"已解决状态
        self.btn_file_save.clicked.connect(lambda: self._resolve_checked_items(self.file_tree, "resolved"))
        # 连接忽略按钮的点击信号到解析已勾选项的方法，标记为"ignored"忽略状态
        self.btn_file_ignore.clicked.connect(lambda: self._resolve_checked_items(self.file_tree, "ignored"))
        # 连接筛选按钮的点击信号到打开审查筛选对话框的方法，传入"file"类型
        self.btn_file_filter.clicked.connect(lambda: self._open_review_filter_dialog("file"))

    def _build_other_tab(self) -> None:
        """构建"其他"选项卡的UI界面。

        该方法负责初始化并设置"其他"选项卡的所有用户界面元素，
        包括按钮、标签、树形控件和布局，并为按钮绑定相应的事件处理函数。

        参数:
            self (QWidget): 类实例本身，用于访问实例属性和方法。

        返回值:
            None: 该方法没有返回值，直接构建UI。
        """
        # 创建垂直布局作为选项卡的根布局
        root = QVBoxLayout(self.other_tab)
        # 创建水平布局用于放置按钮和标签
        row = QHBoxLayout()
        # 创建"筛选问题"按钮
        self.btn_other_filter = QPushButton("筛选问题")
        # 创建"反选"按钮
        self.btn_other_invert = QPushButton("反选")
        # 创建"保存勾选的文件"按钮
        self.btn_other_save = QPushButton("保存勾选的文件")
        # 创建"忽略勾选"按钮
        self.btn_other_ignore = QPushButton("忽略勾选")
        # 创建一个空标签，可能用于显示筛选信息
        self.lbl_other_filter = QLabel("")
        # 注册"筛选问题"按钮为静态按钮（可能用于统一管理样式或行为）
        self._register_static_button(self.btn_other_filter)
        # 注册"反选"按钮为静态按钮
        self._register_static_button(self.btn_other_invert)
        # 注册"保存勾选的文件"按钮为静态按钮
        self._register_static_button(self.btn_other_save)
        # 注册"忽略勾选"按钮为静态按钮
        self._register_static_button(self.btn_other_ignore)
        # 将按钮和标签添加到水平布局中
        row.addWidget(self.btn_other_filter)
        row.addWidget(self.lbl_other_filter)
        row.addWidget(self.btn_other_invert)
        row.addWidget(self.btn_other_save)
        row.addWidget(self.btn_other_ignore)
        # 添加一个伸缩项，将按钮推向左侧
        row.addStretch(1)

        # 创建树形控件，用于显示数据
        self.other_tree = QTreeWidget()
        # 设置树形控件的列标题
        self.other_tree.setHeaderLabels(["保留", "类型", "标题", "数据", "审查ID"])
        # 启用交替行颜色，提高可读性
        self.other_tree.setAlternatingRowColors(True)
        # 设置选择模式为扩展选择（允许选择多个项目）
        self.other_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # 设置复选框指示器的样式，使其更大以便操作
        self.other_tree.setStyleSheet("QTreeWidget::indicator{width:30px;height:30px;}")
        # 为树形控件安装复制快捷键（可能用于复制选中的数据）
        _install_tree_copy_shortcut(self.other_tree)
        # 将水平布局（包含按钮和标签）添加到根布局
        root.addLayout(row)
        # 将树形控件添加到根布局，并设置伸展因子为1使其占据剩余空间
        root.addWidget(self.other_tree, 1)

        # 连接"反选"按钮的点击信号到反转树形控件选中状态的槽函数
        self.btn_other_invert.clicked.connect(lambda: self._invert_check_state_tree(self.other_tree))
        # 连接"保存勾选的文件"按钮的点击信号到处理选中项目的槽函数，状态为"resolved"
        self.btn_other_save.clicked.connect(lambda: self._resolve_checked_items(self.other_tree, "resolved"))
        # 连接"忽略勾选"按钮的点击信号到处理选中项目的槽函数，状态为"ignored"
        self.btn_other_ignore.clicked.connect(lambda: self._resolve_checked_items(self.other_tree, "ignored"))
        # 连接"筛选问题"按钮的点击信号到打开筛选对话框的槽函数，类型为"other"
        self.btn_other_filter.clicked.connect(lambda: self._open_review_filter_dialog("other"))

    def apply_button_scale(self, scale: float) -> None:
        """
        应用按钮的缩放比例。

        此方法用于设置并应用按钮的缩放因子。确保缩放比例不小于1.0，
        并将该比例应用到实例中的所有静态和动态按钮上。

        参数:
            scale (float): 期望的按钮缩放因子。

        返回:
            None: 此方法不返回任何值。
        """
        # 将传入的缩放比例转换为浮点数，并确保其值不小于1.0，然后存储到实例属性中
        self._button_scale = max(1.0, float(scale))
        # 遍历并缩放所有静态按钮
        for btn in self._static_buttons:
            _apply_button_scale(btn, self._button_scale)
        # 遍历并缩放所有动态按钮
        for btn in self._dynamic_buttons:
            _apply_button_scale(btn, self._button_scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        """设置指定的外观（facade）并更新相关的缓存和评论。

        参数:
            facade (MuseArcFacade): 要设置的外观对象。

        返回值:
            None
        """
        self.facade = facade  # 设置实例的外观属性
        self._invalidate_review_reference_cache()  # 使评论参考缓存无效
        self.reload_reviews(force_refresh_refs=True)  # 强制刷新评论并刷新参考

    def refresh_page(self) -> None:
        self.reload_reviews(force_refresh_refs=True)

    def _invalidate_review_reference_cache(self) -> None:
        self._review_ref_cache_deadline = 0.0

    def _ensure_review_reference_maps(self, *, force: bool = False) -> None:
        now = monotonic()
        if (
            not force
            and self._track_map
            and self._lyrics_by_source
            and self._lyrics_by_id
            and now < float(self._review_ref_cache_deadline)
        ):
            return
        tracks = self.facade.list_tracks(limit=200_000)
        self._track_map = {str(r.get("track_id", "")): r for r in tracks if r.get("track_id")}
        lyrics_rows = self.facade.list_lyrics(limit=300_000)
        self._lyrics_by_source = {
            str(r.get("source_relpath", "")).replace("\\", "/"): r
            for r in lyrics_rows
            if str(r.get("source_relpath", "")).strip()
        }
        self._lyrics_by_id = {
            str(r.get("lyrics_id", "")): r
            for r in lyrics_rows
            if str(r.get("lyrics_id", "")).strip()
        }
        self._review_ref_cache_deadline = now + 8.0

    @staticmethod
    def _issue_type_of(row: dict) -> str:
        """从行数据中提取问题类型。

        参数：
        row (dict): 包含行数据的字典，其中可能包含 'issue_type' 键。

        返回值：
        str: 问题类型的字符串，如果不存在或无效则返回 "(未分类)"。
        """
        text = str((row or {}).get("issue_type", "") or "").strip()  # 确保 row 是字典，获取 issue_type 键的值，如果不存在或为空则使用空字符串，然后转换为字符串并去除空白
        return text or "(未分类)"  # 如果提取的文本为空或假值，则返回默认值 "(未分类)"

    def _update_filter_options(self, scope: str, rows: list[dict]) -> None:
        """更新指定范围的过滤选项。

        从rows中提取issue_type，排序后设置为该范围的过滤选项。
        并更新已选中的过滤选项，确保与新选项一致。

        参数：
            scope (str): 过滤范围标识。
            rows (list[dict]): 数据行列表，每个字典应包含用于提取issue_type的信息。

        返回值：
            None
        """
        options = sorted({self._issue_type_of(r) for r in rows}, key=lambda s: s.casefold())  # 提取issue_type并排序（大小写不敏感）
        self._review_filter_options[scope] = options  # 存储排序后的选项
        selected = set(self._review_filter_selected.get(scope, set()))  # 获取当前已选中的选项
        if not selected:  # 如果没有选中任何选项
            self._review_filter_selected[scope] = set(options)  # 设置选中为所有选项
            return  # 返回
        self._review_filter_selected[scope] = {v for v in selected if v in options}  # 更新选中，只保留新选项中存在的值
        if not self._review_filter_selected[scope] and options:  # 如果更新后无选中但选项非空
            self._review_filter_selected[scope] = set(options)  # 设置选中为所有选项

    def _apply_filter_scope(self, scope: str, rows: list[dict]) -> list[dict]:
        """根据指定的过滤作用域（scope）对数据行进行过滤。

        本方法通过一个预先选中的问题类型集合，对输入的数据行列表进行筛选。
        只有其问题类型存在于该选中集合中的行，才会被包含在最终的返回结果中。

        Args:
            scope (str): 要应用的过滤作用域的名称（键名）。
            rows (list[dict]): 待过滤的数据行列表，每行是一个字典。

        Returns:
            list[dict]: 过滤后的数据行列表。如果输入行为空或没有选中任何过滤类型，则可能返回空列表或原始列表的副本。
        """
        selected = set(self._review_filter_selected.get(scope, set()))  # 从实例的筛选配置中获取当前作用域下的‘已选问题类型’集合
        if not rows:  # 如果输入的行列表为空
            return []  # 直接返回空列表
        if not selected:  # 如果没有选中任何过滤类型（即集合为空）
            return list(rows)  # 则返回原始行列表的完整副本
        return [r for r in rows if self._issue_type_of(r) in selected]  # 使用列表推导式，只保留问题类型在‘已选集合’selcted中的行

    def _refresh_filter_labels(self) -> None:
        """刷新筛选标签的显示状态。此方法根据当前筛选选项和已选状态，更新各个过滤标签的文本显示。无参数，无返回值。"""
        # 建立筛选类型与对应UI标签的映射
        mapping = {
            "song": getattr(self, "lbl_song_filter", None),
            "lyrics": getattr(self, "lbl_lyrics_filter", None),
            "file": getattr(self, "lbl_file_filter", None),
            "other": getattr(self, "lbl_other_filter", None),
        }
        # 遍历每种筛选类型及其对应的标签控件
        for scope, label in mapping.items():
            # 如果获取到的不是有效的QLabel控件，则跳过本次循环
            if not isinstance(label, QLabel):
                continue
            # 获取当前筛选类型下的所有可选项
            options = self._review_filter_options.get(scope, [])
            # 获取当前筛选类型下已被选中的项
            selected = self._review_filter_selected.get(scope, set())
            # 如果没有可筛选项，则标签显示提示信息
            if not options:
                label.setText("无可筛选项")
                continue
            # 如果没有选中任何项，或者选中项数量等于总选项数量，则视为“全部”
            if not selected or len(selected) >= len(options):
                label.setText(f"全部({len(options)})")
            else:
                # 否则，显示已选数量与总数的比例
                label.setText(f"已选 {len(selected)}/{len(options)}")

    def _open_review_filter_dialog(self, scope: str) -> None:
        """
        打开问题类型筛选对话框。

        功能：根据传入的范围（scope）显示一个对话框，让用户勾选要显示的问题类型。
             如果当前范围没有可筛选的选项，则提示用户并直接返回。
             用户可以通过对话框全选、全不选或手动勾选问题类型。
             最后将用户的选择保存并重新加载审查列表。

        参数：
            scope (str): 筛选范围，用于从 _review_filter_options 字典中获取对应的选项列表，
                         并决定 _review_filter_selected 字典中存储的键。

        返回值：
            None: 该方法没有返回值，但会通过弹窗提示用户，或直接更新筛选状态并刷新界面。
        """
        # 从实例的 _review_filter_options 字典中获取指定范围的选项列表，如果不存在则返回空列表
        options = list(self._review_filter_options.get(scope, []))
        # 如果没有可筛选的选项，弹出提示框并直接返回
        if not options:
            QMessageBox.information(self, "筛选问题", "当前页没有可筛选的问题类型。")
            return

        # 获取当前范围已保存的筛选项，如果没有则默认全选所有选项
        selected = set(self._review_filter_selected.get(scope, set())) or set(options)
        # 创建一个对话框，父级为当前实例
        dialog = QDialog(self)
        dialog.setWindowTitle("筛选问题类型")
        # 创建对话框的垂直布局
        root = QVBoxLayout(dialog)
        # 在布局中添加提示标签
        root.addWidget(QLabel("勾选要显示的问题类型："))

        # 创建一个列表控件，用于显示问题类型选项
        list_widget = QListWidget()
        # 设置列表为无选择模式（即只能通过复选框选择，不能高亮选中行）
        list_widget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        # 遍历所有选项，为每个选项创建一个可勾选的列表项
        for option in options:
            item = QListWidgetItem(option)
            # 给列表项添加可勾选标志
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # 根据该选项是否在已选集合中，设置复选框的初始状态（选中或未选中）
            item.setCheckState(Qt.CheckState.Checked if option in selected else Qt.CheckState.Unchecked)
            # 将列表项添加到列表控件中
            list_widget.addItem(item)
        # 将列表控件添加到布局中，并设置拉伸因子为1（使其尽可能占据垂直空间）
        root.addWidget(list_widget, 1)

        # 创建一个对话框按钮盒，包含“确定”和“取消”标准按钮
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        # 向按钮盒中添加自定义的“全选”和“全不选”按钮，并设置其角色为动作角色
        btn_all = btns.addButton("全选", QDialogButtonBox.ButtonRole.ActionRole)
        btn_none = btns.addButton("全不选", QDialogButtonBox.ButtonRole.ActionRole)
        # 将按钮盒添加到布局中
        root.addWidget(btns)

        # 定义一个内部函数，用于设置所有列表项的勾选状态
        def _set_all(checked: bool) -> None:
            # 根据传入的布尔值决定设置为选中还是未选中状态
            state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            # 遍历列表控件中的所有项，逐个设置状态
            for idx in range(list_widget.count()):
                it = list_widget.item(idx)
                # 确保列表项存在
                if it is not None:
                    it.setCheckState(state)

        # 将“全选”和“全不选”按钮的点击信号连接到 _set_all 函数
        btn_all.clicked.connect(lambda: _set_all(True))
        btn_none.clicked.connect(lambda: _set_all(False))
        # 将按钮盒的“确定”和“取消”信号连接到对话框的接受和拒绝槽函数
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)

        # 显示对话框并等待用户操作，如果用户点击了“取消”或关闭对话框，则直接返回
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # 创建一个新的集合，用于存储用户最终勾选的问题类型
        new_selected: set[str] = set()
        # 遍历列表控件中的所有项，收集用户勾选的选项
        for idx in range(list_widget.count()):
            it = list_widget.item(idx)
            # 跳过不存在的列表项
            if it is None:
                continue
            # 如果该项被勾选，则将其文本（即问题类型）添加到新集合中
            if it.checkState() == Qt.CheckState.Checked:
                new_selected.add(it.text())
        # 如果用户没有勾选任何选项，则弹出提示并要求至少选择一项，然后返回
        if not new_selected:
            QMessageBox.information(self, "筛选问题", "至少勾选一个问题类型。")
            return
        # 将用户的选择保存到实例的 _review_filter_selected 字典中，键为传入的 scope
        self._review_filter_selected[scope] = new_selected
        # 重新加载审查列表以应用筛选
        self.reload_reviews()

    def reload_reviews(self, force_refresh_refs: bool = False) -> None:
        """重新加载待审查项并更新各个审查树形视图。

        此方法从数据访问层获取待审查记录，根据记录类型（如重复歌曲、歌词匹配、文件问题等）进行分类处理，
        并更新对应的UI树形视图。同时会处理过滤选项和动态按钮。

        参数:
            force_refresh_refs (bool): 是否强制刷新参考映射（如曲目映射和歌词映射）。默认为 False。

        返回:
            None: 此方法无返回值，其作用是更新UI状态。
        """
        # 从数据访问层获取待处理的审查记录，限制最多5000条
        rows = self.facade.pending_reviews(limit=5000)
        # 确保审查参考映射（曲目映射和歌词映射）已加载，根据参数决定是否强制刷新
        self._ensure_review_reference_maps(force=force_refresh_refs)

        # 初始化四个列表，用于分别存储不同类型的审查记录
        song_rows: list[dict] = []      # 存储歌曲相关的审查记录（如重复、文件问题等）
        lyrics_rows_out: list[dict] = []  # 存储歌词匹配相关的审查记录
        file_rows: list[dict] = []        # 存储通用文件问题的审查记录
        other_rows: list[dict] = []       # 存储其他类型审查记录

        # 遍历每一条待审查记录，根据其类型（kind）进行分派处理
        for row in rows:
            # 提取审查记录ID，确保为字符串，如果缺失则为空字符串
            review_id = str(row.get("review_id", "") or "")
            # 提取审查记录的类型
            kind = str(row.get("kind", "") or "")
            # 提取记录的负载（payload），如果为空则初始化为空字典
            payload = row.get("payload") or {}

            # 处理重复歌曲类型的审查记录
            if kind == "duplicate":
                # 从记录中提取标题，用于后续判断（如是否为重新导入已删除歌曲）
                review_title = str(row.get("title", "") or "")
                # 从负载中提取已存在的曲目ID
                existing_track_id = str(payload.get("existing_track_id") or "")
                # 从曲目映射表中获取该曲目的元数据，如果不存在则为空字典
                track_meta = self._track_map.get(existing_track_id) or {}
                # 提取源文件路径（可能是绝对或相对路径）
                source_path = str(payload.get("path", "") or "")
                # 获取候选文件的完整路径和文件名
                candidate_path = str(track_meta.get("source_fullpath", "") or "")
                candidate_file_name = str(track_meta.get("file_name", "") or "").strip()
                # 如果候选文件名为空，则尝试从相对路径中提取文件名
                if not candidate_file_name:
                    candidate_file_name = Path(
                        str(track_meta.get("source_relpath", "") or track_meta.get("storage_relpath", "") or candidate_path)
                    ).name
                # 如果文件名以 "trk_" 开头，可能是系统默认名，尝试用源文件的完整路径的文件名替换
                if candidate_file_name.startswith("trk_"):
                    source_full = str(track_meta.get("source_fullpath", "") or "").strip()
                    if source_full:
                        source_name = Path(source_full).name
                        if source_name:
                            candidate_file_name = source_name
                # 构建歌曲行字典，包含审查ID、问题类型、分组信息、文件信息、候选曲目信息等
                song_rows.append(
                    {
                        "review_id": review_id,
                        "issue_type": review_title or "疑似重复",  # 问题类型，如果标题为空则使用默认值
                        # 分组键：优先使用负载中的group_key，否则使用曲目ID前8位或文件路径主干，最后使用"未分组"
                        "group_key": str(payload.get("group_key") or existing_track_id[:8] or Path(source_path).stem or "未分组"),
                        # 分组标题：通过辅助函数根据分组键和源路径生成
                        "group_title": _derive_song_group_title(
                            str(payload.get("group_key") or existing_track_id[:8] or ""),
                            source_path,
                        ),
                        "source_file": Path(source_path).name,  # 源文件名
                        "source_path": source_path,  # 源文件路径
                        "candidate_track_id": existing_track_id,  # 候选曲目的ID
                        # 候选曲目的显示标签，如果有元数据则通过_track_label生成，否则使用ID
                        "candidate_track": _track_label(track_meta) if track_meta else existing_track_id,
                        "candidate_file_name": candidate_file_name,  # 候选文件名
                        "candidate_path": candidate_path,  # 候选文件路径
                        # 候选曲目时长（秒），安全转换为浮点数
                        "candidate_duration_sec": _safe_float(track_meta.get("duration_sec", 0), 0),
                        # 匹配分数，安全转换为浮点数
                        "score": _safe_float(payload.get("score", 0), 0.0),
                        # 匹配原因，去除可能的"原因"前缀
                        "reason": str(payload.get("reason", "") or "疑似重复音频").replace("原因", ""),
                        # 候选曲目的完整元数据字典
                        "candidate_meta": dict(track_meta),
                        # 如果标题是特定值（如重新导入已删除歌曲），则设置恢复曲目ID，否则为空
                        "restore_track_id": existing_track_id if review_title in {"已删除歌曲重新导入", "reimport_deleted_track"} else "",
                        # 是否为延迟导入
                        "deferred_import": bool(payload.get("deferred_import", False)),
                    }
                )
                continue  # 处理完重复歌曲类型，跳过后续类型判断

            # 处理文件问题类型中，特定问题（如指纹提取失败、响度归一不可用）的审查记录
            if kind == "file_issue" and str(row.get("title", "") or "") in {
                "指纹提取失败",
                "响度归一不可用",
                "fingerprint_failed",
                "loudness_normalization_unavailable",
            }:
                # 提取问题标题和源路径信息
                issue_title = str(row.get("title", "") or "文件异常")
                source_path = str(payload.get("path", "") or "")
                source_file = Path(source_path).name
                title_hint = str(payload.get("title_hint", "") or "")
                group_key = str(payload.get("group_key", "") or Path(source_path).stem or "未分组")
                # 获取负载中的候选建议列表
                suggestions = payload.get("suggest_candidates") or []
                # 如果建议列表非空且是列表类型，则处理每个建议
                if isinstance(suggestions, list) and suggestions:
                    for sug in suggestions:
                        if not isinstance(sug, dict):  # 确保建议是字典类型
                            continue
                        # 从建议中提取曲目ID并获取其元数据
                        tid = str(sug.get("track_id", "") or "")
                        track_meta = self._track_map.get(tid) or {}
                        # 获取候选路径和文件名（处理逻辑与重复歌曲部分类似）
                        candidate_path = str(track_meta.get("source_fullpath", "") or "")
                        candidate_file_name = str(track_meta.get("file_name", "") or "").strip()
                        if not candidate_file_name:
                            candidate_file_name = Path(
                                str(track_meta.get("source_relpath", "") or track_meta.get("storage_relpath", "") or candidate_path)
                            ).name
                        if candidate_file_name.startswith("trk_"):
                            source_full = str(track_meta.get("source_fullpath", "") or "").strip()
                            if source_full:
                                source_name = Path(source_full).name
                                if source_name:
                                    candidate_file_name = source_name
                        # 将处理好的建议添加到song_rows中
                        song_rows.append(
                            {
                                "review_id": review_id,
                                "issue_type": issue_title,
                                "group_key": group_key,
                                "group_title": _derive_song_group_title(group_key, source_path),
                                "source_file": source_file,
                                "source_path": source_path,
                                "candidate_track_id": tid,
                                "candidate_track": _track_label(track_meta) if track_meta else str(sug.get("title", "") or tid),
                                "candidate_file_name": candidate_file_name,
                                "candidate_path": candidate_path,
                                "candidate_duration_sec": _safe_float(track_meta.get("duration_sec", 0), 0),
                                "score": _safe_float(sug.get("score", 0), 0.0),
                                "reason": f"指纹失败/名称相近 {title_hint}",  # 构建包含提示的原因
                                "candidate_meta": dict(track_meta),
                            }
                        )
                else:
                    # 如果没有候选建议，则添加一条没有候选信息的记录
                    song_rows.append(
                        {
                            "review_id": review_id,
                            "issue_type": issue_title,
                            "group_key": group_key,
                            "group_title": _derive_song_group_title(group_key, source_path),
                            "source_file": source_file,
                            "source_path": source_path,
                            "candidate_track_id": "",
                            "candidate_track": "",
                            "candidate_file_name": "",
                            "candidate_path": "",
                            "candidate_duration_sec": 0,
                            "score": 0.0,
                            "reason": "指纹失败，暂无候选",
                            "candidate_meta": {},
                        }
                    )
                continue  # 处理完特定文件问题类型，跳过后续类型判断

            # 处理歌词匹配类型的审查记录
            if kind == "lyrics_match":
                review_title = str(row.get("title", "") or "")
                # 提取歌词源路径，并将反斜杠替换为正斜杠以统一路径格式
                source_rel = str(payload.get("lyrics_source", "") or "").replace("\\", "/")
                # 获取建议匹配的曲目ID和元数据
                suggest_id = str(payload.get("suggest_track_id") or "")
                suggest_track = self._track_map.get(suggest_id) or {}
                # 从歌词映射中通过源路径获取匹配的歌词记录
                matched = self._lyrics_by_source.get(source_rel) or {}
                # 获取存储的相对路径，并尝试获取文件的修改时间
                storage_relpath = str(matched.get("storage_relpath", "") or "")
                source_mtime = 0.0
                if storage_relpath:
                    # 构建存储的绝对路径，并获取其修改时间
                    storage_abs = Path(self.facade.library_root) / storage_relpath
                    try:
                        source_mtime = float(storage_abs.stat().st_mtime)
                    except Exception:
                        source_mtime = 0.0  # 如果获取失败，则设置为0
                # 构建歌词行字典，包含丰富的歌词信息和匹配信息
                lyrics_rows_out.append(
                    {
                        "review_id": review_id,
                        "issue_type": review_title or "歌词匹配待审查",
                        # 分组键：优先使用负载中的特定键，否则使用路径主干
                        "group_key": str(payload.get("lyrics_group_key") or payload.get("group_key") or Path(source_rel).stem or "未分组"),
                        "group_title": _derive_lyrics_group_title(
                            str(payload.get("lyrics_group_title") or payload.get("lyrics_group_key") or payload.get("group_key") or ""),
                            source_rel,
                        ),
                        "lyrics_source": source_rel,  # 歌词源路径
                        "lyrics_file": Path(source_rel).name,  # 歌词文件名
                        "lyrics_id": str(payload.get("lyrics_id") or matched.get("lyrics_id") or ""),  # 歌词ID
                        "lyrics_title": str(matched.get("lyrics_title", "") or ""),  # 歌词标题
                        "lyrics_artist": str(matched.get("lyrics_artist", "") or ""),  # 歌词艺术家
                        "storage_relpath": storage_relpath,  # 存储相对路径
                        # 建议匹配的曲目标签
                        "suggest_track": _track_label(suggest_track) if suggest_track else suggest_id,
                        "suggest_track_id": suggest_id,  # 建议匹配的曲目ID
                        # 匹配分数
                        "score": _safe_float(payload.get("score", 0), 0.0),
                        # 匹配原因
                        "reason": str(payload.get("reason", "") or "匹配置信度不足").replace("原因", ""),
                        # 歌词行数，安全转换为整数
                        "line_count": _safe_int(matched.get("line_count", 0), 0),
                        "imported_at": str(matched.get("imported_at", "") or ""),  # 导入时间
                        "source_mtime": source_mtime,  # 源文件修改时间
                        # 歌词预览，将列表连接成字符串
                        "preview": "\n".join(payload.get("lyrics_preview") or []),
                        # 如果是重新导入已删除歌词，则设置恢复歌词ID，否则为空
                        "restore_lyrics_id": str(payload.get("lyrics_id", "") or "") if review_title == "已删除歌词重新导入" else "",
                        "readonly_reference": False,  # 标记为非只读参考（这是一个主要匹配项）
                    }
                )
                # 检查负载中是否包含现有歌词ID，如果有，则添加一条只读的参考记录
                existing_lyrics_id = str(payload.get("existing_lyrics_id", "") or "").strip()
                if existing_lyrics_id:
                    # 获取现有歌词的源路径、记录、预览行和相似度
                    existing_source = str(payload.get("existing_lyrics_source", "") or "").replace("\\", "/")
                    existing_row = self._lyrics_by_id.get(existing_lyrics_id) or {}
                    existing_preview_lines = payload.get("existing_lyrics_preview") or []
                    if not isinstance(existing_preview_lines, list):
                        existing_preview_lines = []
                    existing_similarity = _safe_float(payload.get("existing_lyrics_similarity", 0.0), 0.0)
                    # 添加一条只读参考记录，用于在UI中显示现有歌词作为参考
                    lyrics_rows_out.append(
                        {
                            "review_id": "",
                            "issue_type": "库内歌词参考",  # 固定类型，表示这是一个参考项
                            "group_key": str(payload.get("lyrics_group_key") or payload.get("group_key") or Path(source_rel).stem or "未分组"),
                            "group_title": _derive_lyrics_group_title(
                                str(payload.get("lyrics_group_title") or payload.get("lyrics_group_key") or payload.get("group_key") or ""),
                                existing_source,
                            ),
                            "lyrics_source": existing_source,
                            # 文件名：如果存在源路径则用其文件名，否则用ID加.lrc后缀
                            "lyrics_file": Path(existing_source).name if existing_source else f"{existing_lyrics_id}.lrc",
                            "lyrics_id": existing_lyrics_id,
                            "lyrics_title": str(existing_row.get("lyrics_title", "") or ""),
                            "lyrics_artist": str(existing_row.get("lyrics_artist", "") or ""),
                            "storage_relpath": str(existing_row.get("storage_relpath", "") or ""),
                            "suggest_track": "",  # 参考项没有建议曲目
                            "suggest_track_id": "",
                            "score": existing_similarity,  # 使用现有相似度作为分数
                            "reason": "库内已存在歌词（参考）",
                            "line_count": 0,
                            "imported_at": "",
                            "source_mtime": 0.0,
                            # 预览：将现有预览行连接成字符串，处理可能的空值
                            "preview": "\n".join(str(v or "") for v in existing_preview_lines),
                            "restore_lyrics_id": "",  # 参考项没有恢复ID
                            "readonly_reference": True,  # 标记为只读参考
                        }
                    )
                continue  # 处理完歌词匹配类型，跳过后续类型判断

            # 处理通用的文件问题类型（不包括已在上面处理的特定问题）
            if kind == "file_issue":
                file_rows.append(
                    {
                        "review_id": review_id,
                        "issue_type": str(row.get("title", "") or "文件异常"),
                        "title": str(row.get("title", "") or ""),
                        "path": str(payload.get("path", "") or ""),
                        # 详细信息：优先从错误或时长中获取，并去除可能的"原因"前缀
                        "detail": str(payload.get("error", "") or payload.get("duration_sec", "") or "").replace("原因", ""),
                    }
                )
                continue  # 处理完文件问题类型，跳过后续类型判断

            # 如果记录类型不匹配以上任何一种，则将其归类为“其它”类型
            other_rows.append(
                {
                    "review_id": review_id,
                    # 问题类型：优先使用标题，其次使用kind，最后使用"其它"
                    "issue_type": str(row.get("title", "") or kind or "其它"),
                    "kind": kind,
                    "title": str(row.get("title", "") or ""),
                    "payload": str(payload),  # 将负载转换为字符串表示，便于查看
                }
            )

        # 更新各个审查树的过滤选项
        self._update_filter_options("song", song_rows)
        self._update_filter_options("lyrics", lyrics_rows_out)
        self._update_filter_options("file", file_rows)
        self._update_filter_options("other", other_rows)

        # 应用当前过滤范围到各行数据，得到过滤后的结果
        song_rows_filtered = self._apply_filter_scope("song", song_rows)
        lyrics_rows_filtered = self._apply_filter_scope("lyrics", lyrics_rows_out)
        file_rows_filtered = self._apply_filter_scope("file", file_rows)
        other_rows_filtered = self._apply_filter_scope("other", other_rows)
        # 刷新过滤标签的显示
        self._refresh_filter_labels()

        # 清空动态按钮列表，准备重新填充
        self._dynamic_buttons.clear()
        # 使用过滤后的数据填充各个树形视图
        self._fill_song_tree(song_rows_filtered)
        self._fill_lyrics_tree(lyrics_rows_filtered)
        self._fill_file_tree(file_rows_filtered)
        self._fill_other_tree(other_rows_filtered)
        # 应用按钮缩放比例
        self.apply_button_scale(self._button_scale)

    def _style_group_header(self, item: QTreeWidgetItem) -> None:
        """设置组头项目的样式，使其字体加粗并增大字号。
        参数:
            item (QTreeWidgetItem): 需要样式的树控件项目。
        返回:
            None
        """
        font = item.font(0)  # 获取项目索引0的字体
        font.setBold(True)  # 设置字体加粗
        font.setPointSize(max(font.pointSize() + 4, 13))  # 设置字号：当前字号加4，但不小于13
        item.setFont(0, font)  # 将样式应用回项目

    @staticmethod
    def _clear_group_layout(layout: QVBoxLayout) -> None:
        """递归清除QVBoxLayout布局中的所有项目并释放内存。

        该方法用于清空指定垂直布局中的所有子项，包括嵌套在子布局中的项目。
        它会遍历布局中的每个项目，识别其中包含的子布局和小部件，
        并递归删除子布局中的项目，最后安全地释放所有小部件的内存。

        Args:
            layout (QVBoxLayout): 需要被清空的垂直布局对象

        Returns:
            None: 该方法不返回任何值
        """
        # 循环处理布局中的所有项目，直到布局为空
        while layout.count():
            # 从布局中取出第一个项目（索引为0）
            item = layout.takeAt(0)
            # 尝试获取项目中的小部件
            widget = item.widget()
            # 尝试获取项目中的子布局
            child_layout = item.layout()
            # 如果存在子布局，则递归清理子布局中的内容
            if child_layout is not None:
                # 循环处理子布局中的所有项目
                while child_layout.count():
                    # 从子布局中取出第一个项目
                    sub = child_layout.takeAt(0)
                    # 获取子布局项目中的小部件
                    sub_widget = sub.widget()
                    # 如果子布局项目包含小部件，则计划稍后删除
                    if sub_widget is not None:
                        # 使用deleteLater()安全地删除小部件，避免立即释放可能导致的崩溃
                        sub_widget.deleteLater()
            # 如果当前项目包含小部件，则计划稍后删除
            if widget is not None:
                # 使用deleteLater()安全地删除小部件
                widget.deleteLater()

    @staticmethod
    def _iter_tree_leaf_items(tree: QTreeWidget) -> list[QTreeWidgetItem]:
        """遍历QTreeWidget，返回所有有效叶子节点的列表

        Args:
            tree: QTreeWidget 控件对象

        Returns:
            list[QTreeWidgetItem]: 包含所有有效叶子节点的列表
        """
        out: list[QTreeWidgetItem] = []  # 存储结果的列表
        # 初始化栈，包含树的所有顶层项（第一层节点）
        stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]

        while stack:  # 当栈不为空时继续遍历
            node = stack.pop()  # 弹出栈顶节点进行处理

            # 将当前节点的所有子节点压入栈中（深度优先遍历）
            for i in range(node.childCount()):
                stack.append(node.child(i))

            # 获取节点的数据（第0列，用户角色数据）
            row = node.data(0, Qt.ItemDataRole.UserRole) or {}

            if not row:  # 如果没有数据则跳过
                continue

            # 跳过特定类型的行（元数据行、链接行、页脚行）
            if row.get("_meta_row") or row.get("_link_row") or row.get("_footer"):
                continue

            out.append(node)  # 将有效叶子节点加入结果列表

        return out  # 返回收集到的叶子节点列表

    def _group_parent_of(self, item: QTreeWidgetItem | None) -> QTreeWidgetItem | None:
        if item is None:
            return None
        node = item
        while node.parent() is not None:
            node = node.parent()
        return node

    def _iter_group_leaf_items(self, group: dict) -> list[QTreeWidgetItem]:
        tree = group.get("tree") if isinstance(group, dict) else None
        if not isinstance(tree, QTreeWidget):
            return []
        return self._iter_tree_leaf_items(tree)

    def _find_meta_child(self, item: QTreeWidgetItem) -> QTreeWidgetItem | None:
        for i in range(item.childCount()):
            child = item.child(i)
            row = child.data(0, Qt.ItemDataRole.UserRole) or {}
            if row.get("_meta_row"):
                return child
        return None

    def _iter_meta_children(self, item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        """遍历指定树形控件项的所有子项，筛选出包含元数据行的子项。

        Args:
            item (QTreeWidgetItem): 要遍历其子项的树形控件项

        Returns:
            list[QTreeWidgetItem]: 包含所有符合条件的子项的列表
        """
        out: list[QTreeWidgetItem] = []
        for i in range(item.childCount()):  # 遍历所有子项
            child = item.child(i)  # 获取当前子项
            row = child.data(0, Qt.ItemDataRole.UserRole) or {}  # 获取第0列的用户数据，若为None则使用空字典
            if row.get("_meta_row"):  # 检查是否存在"_meta_row"键且其值为真
                out.append(child)  # 将符合条件的子项添加到结果列表
        return out

    def _fill_file_tree(self, rows: list[dict]) -> None:
        """填充文件树方法。

        根据提供的行数据，将每一行数据添加为文件树的顶级节点。

        参数:
            self: 类实例自身。
            rows: 包含文件信息的字典列表，每个字典包含标题、路径、详情、评审ID等字段。

        返回值:
            None: 该方法不返回任何值。
        """
        self.file_tree.clear()  # 清空现有的文件树
        for row in rows:  # 遍历每一行数据
            # 创建树节点，按顺序设置列内容：勾选列、标题、路径、详情、评审ID
            item = QTreeWidgetItem(
                [
                    "",  # 第一列通常用于勾选，初始为空
                    str(row.get("title", "")),  # 获取标题，若不存在则为空字符串
                    str(row.get("path", "")),  # 获取路径
                    # 获取详情并移除"原因"前缀
                    str(row.get("detail", "")).replace("原因", ""),
                    str(row.get("review_id", "")),  # 获取评审ID
                ]
            )
            # 为节点添加可勾选的标志
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # 设置初始勾选状态为已勾选
            item.setCheckState(0, Qt.CheckState.Checked)
            # 将原始行数据存储到节点的用户角色数据中
            item.setData(0, Qt.ItemDataRole.UserRole, dict(row))
            # 将创建的节点添加为文件树的顶级项
            self.file_tree.addTopLevelItem(item)

    def _fill_other_tree(self, rows: list[dict]) -> None:
        """使用给定的字典列表数据，填充 self.other_tree 树形控件。

        Args:
            rows (list[dict]): 一个字典列表，每个字典代表一行数据，
                              应包含 'kind', 'title', 'payload', 'review_id' 等字段。

        Returns:
            None
        """
        # 清空树形控件中的所有现有项
        self.other_tree.clear()
        # 遍历传入的每一行数据
        for row in rows:
            # 创建一个树项，其列数据依次为空字符串、kind、title、payload、review_id
            item = QTreeWidgetItem(
                [
                    "",
                    str(row.get("kind", "")),  # 获取kind字段，若无则为空字符串
                    str(row.get("title", "")),  # 获取title字段
                    str(row.get("payload", "")),  # 获取payload字段
                    str(row.get("review_id", "")),  # 获取review_id字段
                ]
            )
            # 设置项标志，增加“用户可勾选”特性
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # 设置该项第0列的初始勾选状态为“已勾选”
            item.setCheckState(0, Qt.CheckState.Checked)
            # 将原始行数据字典存储到该项第0列的用户角色数据中，以便后续访问
            item.setData(0, Qt.ItemDataRole.UserRole, dict(row))
            # 将创建并配置好的项添加为树形控件的顶级项
            self.other_tree.addTopLevelItem(item)

    def _iter_checked_review_ids(self, tree: QTreeWidget) -> list[str]:
        """
        遍历树形结构，返回所有选中项的review_id列表。

        参数:
            tree (QTreeWidget): 树形控件对象。
        返回:
            list[str]: 包含所有选中项review_id的字符串列表。
        """
        ids: list[str] = []  # 初始化一个空列表，用于存储选中项的review_id
        stack: list[QTreeWidgetItem] = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]  # 初始化栈，包含所有顶级项，使用列表推导式获取
        while stack:  # 当栈不为空时，继续处理
            item = stack.pop()  # 从栈中弹出最后一个项，用于遍历
            for i in range(item.childCount()):  # 遍历当前项的所有子项
                stack.append(item.child(i))  # 将子项压入栈中，实现深度优先遍历
            row = item.data(0, Qt.ItemDataRole.UserRole) or {}  # 获取项的数据，如果为空则默认为空字典
            if not row:  # 如果数据为空，则跳过当前项
                continue
            if item.checkState(0) != Qt.CheckState.Checked:  # 如果项未被选中，则跳过
                continue
            rid = str(row.get("review_id", "") or "")  # 从数据中提取review_id，确保为字符串类型
            if rid:  # 如果review_id不为空
                ids.append(rid)  # 将其添加到结果列表中
        return ids  # 返回所有选中项的review_id列表

    def _resolve_checked_items(self, tree: QTreeWidget, status: str) -> None:
        """解析树控件中勾选的审查项，并根据指定状态进行处理。

        参数:
            tree (QTreeWidget): 包含审查项的树形控件。
            status (str): 处理状态，例如 'approved' 或 'rejected'。

        返回:
            None
        """
        ids = self._iter_checked_review_ids(tree)  # 获取所有勾选的审查ID
        if not ids:  # 如果没有勾选的项，则提示用户并返回
            QMessageBox.information(self, "审查处理", "请先勾选要处理的项。")
            return
        count = self.facade.resolve_reviews(ids, status=status)  # 调用facade处理这些审查项，返回处理的数量
        self.reload_reviews()  # 重新加载审查列表以更新UI
        self.review_changed.emit()  # 发射审查改变信号，通知其他组件
        QMessageBox.information(self, "审查处理", f"已处理 {count} 项。")  # 显示处理完成的消息

    def _invert_check_state_tree(self, tree: QTreeWidget) -> None:
        """反转QTreeWidget树中所有有效项的复选框状态。

        遍历给定的QTreeWidget树结构，将其中每个“数据行”有效项的复选框状态进行反转（即选中的变为未选，未选的变为选中）。
        无效项（如元数据行或链接行）会被跳过。

        参数:
            tree (QTreeWidget): 需要反转复选框状态的QTreeWidget树控件。

        返回:
            None: 此方法没有返回值。
        """
        # 初始化一个栈，用于深度优先遍历树结构。先将所有顶层项加入栈。
        stack: list[QTreeWidgetItem] = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]

        # 当栈不为空时，持续处理节点。
        while stack:
            # 从栈顶弹出一个节点进行处理。
            item = stack.pop()

            # 将当前节点的所有子节点压入栈中，以便后续处理。
            for i in range(item.childCount()):
                stack.append(item.child(i))

            # 获取节点第一列关联的用户数据（预期为字典），如果为空则默认为{}。
            row = item.data(0, Qt.ItemDataRole.UserRole) or {}

            # 如果数据行为空，或者是元数据行（_meta_row）或链接行（_link_row），则跳过此节点。
            if not row or row.get("_meta_row") or row.get("_link_row"):
                continue

            # 反转复选框状态：如果当前是选中状态，则设为未选中；否则设为选中。
            item.setCheckState(
                0,
                Qt.CheckState.Unchecked if item.checkState(0) == Qt.CheckState.Checked else Qt.CheckState.Checked,
            )

    def _review_ids_for_group(self, group: dict) -> list[str]:
        ids: set[str] = set()
        rows = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in rows if isinstance(rows, list) else []:
            row = row_ctrl.get("row") if isinstance(row_ctrl, dict) else {}
            if not isinstance(row, dict):
                continue
            rid = str(row.get("review_id", "") or "")
            if rid:
                ids.add(rid)
        return sorted(ids)

    def _retry_selected_file_issues(self) -> None:
        """重试导入选中的文件问题项。

        从文件树中收集所有已勾选且包含有效路径和review_id的异常项，然后尝试重新导入它们。
        导入完成后，将对应的review状态标记为已解决，并刷新界面显示结果。

        参数:
            无（除了self，它代表当前实例）

        返回:
            None（直接操作界面和数据，无返回值）
        """
        # 初始化栈：将顶层项作为起始节点
        stack = [self.file_tree.topLevelItem(i) for i in range(self.file_tree.topLevelItemCount())]
        # 用于存储待重试项的列表，每项是一个(审查ID, 文件路径)的元组
        pairs: list[tuple[str, str]] = []
        # 使用深度优先遍历（栈）处理整个树结构
        while stack:
            item = stack.pop()
            # 将当前节点的所有子节点压入栈中，以便后续处理
            for i in range(item.childCount()):
                stack.append(item.child(i))
            # 获取节点存储的行数据，如果不存在则为空字典
            row = item.data(0, Qt.ItemDataRole.UserRole) or {}
            # 跳过没有有效数据的节点
            if not row:
                continue
            # 只处理被勾选的节点
            if item.checkState(0) != Qt.CheckState.Checked:
                continue
            # 提取审查ID和文件路径，确保它们为有效字符串
            review_id = str(row.get("review_id", "") or "")
            path = str(row.get("path", "") or "").strip()
            # 如果两者都有效，则添加到待重试列表
            if review_id and path:
                pairs.append((review_id, path))
        # 如果没有找到任何有效的待重试项，则提示用户并返回
        if not pairs:
            QMessageBox.information(self, "重试导入", "请先勾选包含有效路径的异常项。")
            return
        # 设置等待光标，表示正在处理
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        # 初始化成功和失败计数器
        ok_count = 0
        fail_count = 0
        # 遍历所有待重试项
        for _review_id, path in pairs:
            try:
                # 尝试重新导入文件
                self.facade.import_from(path)
                ok_count += 1
            except Exception:
                # 导入失败则增加失败计数
                fail_count += 1
        # 恢复光标为正常状态
        QApplication.restoreOverrideCursor()
        # 将所有处理过的审查项标记为已解决
        self.facade.resolve_reviews([rid for rid, _path in pairs], status="resolved")
        # 清除审查引用缓存，确保后续刷新获取最新数据
        self._invalidate_review_reference_cache()
        # 重新加载审查列表，并强制刷新引用
        self.reload_reviews(force_refresh_refs=True)
        # 发出审查已变更的信号，通知其他组件更新
        self.review_changed.emit()
        # 向用户显示操作结果汇总
        QMessageBox.information(self, "重试导入", f"已重试 {len(pairs)} 项，成功 {ok_count}，失败 {fail_count}。")
