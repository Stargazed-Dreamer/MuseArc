from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from musearc.app.facade import MuseArcFacade
from musearc.ui.long_task import run_modal_task
from musearc.ui.main_window_helpers import (
    TrackPickerDialog,
    _apply_button_scale,
    _clear_line_edit_with_undo,
    _install_inline_clear_button,
    _install_row_function_shortcuts,
    _reveal_in_file_manager,
)
from musearc.ui.selection import SelectionController, SelectionMode
from musearc.ui.table_models import ColumnDef
from musearc.ui.track_grid import (
    LyricsTableModel,
    TrackTableView,
    _copy_selected_cells,
    _install_copy_support,
    _safe_int,
)

logger = logging.getLogger(__name__)


# ?????
# LyricsManagementPage ???????????????
# - ?????
# - ??????????
# - ???????????

class LyricsManagementPage(QWidget):
    library_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        """
        初始化歌词管理界面的主窗口。

        功能：
            设置整个UI布局，包括搜索、筛选、分组、多选、编辑等控件，
            并建立所有信号与槽的连接，初始化歌词数据模型和表格视图。

        参数：
            facade (MuseArcFacade): 门面对象，提供对外部服务的统一访问接口。

        返回值：
            None
        """
        super().__init__()
        self.facade = facade  # 存储门面对象引用
        self._all_rows: list[dict] = []  # 存储所有歌词数据行的列表
        self._sort_states: dict[str, str] = {}  # 存储各列的排序状态

        root = QVBoxLayout(self)  # 创建主垂直布局作为根布局

        # 顶部搜索行
        row_top = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索 文件名/标题/艺术家/专辑/歌词作者")
        self.btn_search = QPushButton("搜索")
        row_top.addWidget(self.search_input, 1)  # 输入框占据大部分空间
        row_top.addWidget(self.btn_search)

        # 搜索防抖定时器，避免频繁触发筛选
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)  # 单次触发模式
        self._search_timer.setInterval(120)  # 120毫秒延迟
        self._search_timer.timeout.connect(self.apply_filter)

        # 为搜索输入框安装清除按钮
        _install_inline_clear_button(self.search_input, on_cleared=self.apply_filter)

        # 控制行：包含分组、反选、多选模式、编辑模式
        row_ctrl = QHBoxLayout()
        self.combo_group = QComboBox()
        # 添加分组选项，每项存储对应的数据字段名
        self.combo_group.addItem("不分组", "none")
        self.combo_group.addItem("文件名", "file_name")
        self.combo_group.addItem("歌曲标题", "lyrics_title")
        self.combo_group.addItem("艺术家", "lyrics_artist")
        self.combo_group.addItem("专辑", "lyrics_album")
        self.combo_group.addItem("歌词文件作者", "lyrics_author")
        self.combo_group.addItem("语言", "lyrics_language")
        self.combo_group.addItem("对应歌曲", "mapped_track")
        self.btn_invert = QPushButton("反选")
        self.chk_multi = QCheckBox("多选模式")
        self.chk_multi.setChecked(False)
        self.chk_edit_mode = QCheckBox("编辑模式")
        # 将控件添加到控制行布局
        row_ctrl.addWidget(self.btn_invert)
        row_ctrl.addWidget(QLabel("分组"))
        row_ctrl.addWidget(self.combo_group)
        row_ctrl.addWidget(self.chk_multi)
        row_ctrl.addWidget(self.chk_edit_mode)
        row_ctrl.addStretch(1)  # 添加弹性空间

        # 操作行：包含映射歌曲、批量改作者、删除歌词按钮
        row_ops = QHBoxLayout()
        self.btn_map_track = QPushButton("映射到歌曲")
        self.btn_edit_author = QPushButton("批量改作者")
        self.btn_delete = QPushButton("删除歌词")
        self.btn_delete.setStyleSheet("background-color:#b3261e;color:white;")  # 红色危险样式
        self.chk_preview = QCheckBox("预览歌词")
        row_ops.addWidget(self.btn_map_track)
        row_ops.addWidget(self.btn_edit_author)
        row_ops.addWidget(self.btn_delete)
        row_ops.addStretch(1)  # 添加弹性空间

        # 预览行：包含预览复选框
        row_preview = QHBoxLayout()
        row_preview.addStretch(1)  # 右对齐
        row_preview.addWidget(self.chk_preview)

        # 创建可调整大小的分割器，水平分割表格和预览区域
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧区域：歌词表格
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)  # 移除边距

        # 初始化歌词数据模型，定义列结构
        self.model = LyricsTableModel(
            [
                ColumnDef("file_name", "文件名"),
                ColumnDef("lyrics_title", "歌曲标题"),
                ColumnDef("lyrics_artist", "艺术家"),
                ColumnDef("lyrics_album", "专辑"),
                ColumnDef("lyrics_author", "歌词文件作者"),
                ColumnDef("lyrics_language", "语言"),
                ColumnDef("line_count", "歌词行数"),
                ColumnDef("mapped_track", "对应歌曲"),
                ColumnDef("lyrics_id", "歌词ID"),
            ]
        )

        # 创建并配置表格视图
        self.selection = SelectionController()
        self._selected_lyrics_ids_memory: set[str] = set()
        self._restoring_selection = False
        self.table = TrackTableView(self.selection)
        self.table.setModel(self.model)  # 设置数据模型
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)  # 行选择模式
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)  # 支持多选
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)  # 默认禁用编辑
        self.table.setAlternatingRowColors(True)  # 交替行颜色
        self.table.setSortingEnabled(False)  # 初始禁用排序，通过自定义逻辑管理
        self.table.horizontalHeader().setSectionsMovable(True)  # 允许拖动列
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)  # 列宽可交互调整
        self.table.horizontalHeader().setStretchLastSection(True)  # 最后一列自动拉伸
        _install_copy_support(self.table)  # 安装复制支持
        left_layout.addWidget(self.table)

        # 右侧预览区域：歌词预览文本框
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)  # 只读
        self.preview.setPlaceholderText("歌词预览")

        # 将左右区域添加到分割器
        self.splitter.addWidget(left)
        self.splitter.addWidget(self.preview)
        self.splitter.setStretchFactor(0, 1)  # 左侧拉伸比例
        self.splitter.setStretchFactor(1, 1)  # 右侧拉伸比例
        self.preview.hide()  # 初始隐藏预览区域

        # 将各个行布局添加到根布局
        root.addLayout(row_top)
        root.addLayout(row_ctrl)
        root.addLayout(row_ops)
        root.addLayout(row_preview)
        root.addWidget(self.splitter, 1)  # 分割器占据剩余空间

        # 连接所有信号与槽
        self.btn_search.clicked.connect(self.apply_filter)  # 搜索按钮点击
        self.btn_invert.clicked.connect(self._invert_selection)  # 反选按钮点击
        self.search_input.returnPressed.connect(self.apply_filter)  # 回车键触发搜索
        self.search_input.textChanged.connect(self._on_search_text_changed)  # 输入文本变化
        self.combo_group.currentIndexChanged.connect(self.apply_filter)  # 分组下拉框变化
        self.chk_multi.toggled.connect(self._on_toggle_multi)  # 多选模式切换
        self.chk_edit_mode.toggled.connect(self._on_toggle_edit_mode)  # 编辑模式切换
        self.btn_map_track.clicked.connect(self._map_selected_to_track)  # 映射按钮点击
        self.btn_edit_author.clicked.connect(self._edit_author_for_selected)  # 批量改作者按钮点击
        self.btn_delete.clicked.connect(self._delete_selected_lyrics)  # 删除按钮点击
        self.chk_preview.toggled.connect(self._on_toggle_preview)  # 预览复选框切换
        self.table.clicked.connect(self._on_click_cell)  # 表格单击
        self.table.doubleClicked.connect(self._on_double_click_cell)  # 表格双击
        self.table.ctrl_edit_requested.connect(self._on_ctrl_edit_requested)
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)  # 表头点击（排序）
        self.table.horizontalHeader().sectionMoved.connect(lambda *_args: self._sync_sort_from_header())  # 列移动同步排序状态
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)  # 设置自定义右键菜单策略
        self.table.customContextMenuRequested.connect(self._show_context_menu)  # 右键菜单请求
        self.table.installEventFilter(self)  # 为表格安装事件过滤器
        self.model.lyrics_field_edited.connect(self._on_lyrics_field_edited)  # 模型数据编辑信号

        # 连接选择变化信号以刷新预览，需先检查选择模型是否存在
        if self.table.selectionModel() is not None:
            self.table.selectionModel().selectionChanged.connect(self._on_table_selection_changed)

        # 为操作按钮安装行功能快捷键（F3开始）
        _install_row_function_shortcuts(
            self,
            [
                self.btn_map_track,
                self.btn_edit_author,
                self.btn_delete,
            ],
            start_f=3,
        )

        # 初始化状态
        self._on_toggle_multi(self.chk_multi.isChecked())  # 根据复选框状态初始化多选模式
        self._on_toggle_edit_mode(self.chk_edit_mode.isChecked())  # 根据复选框状态初始化编辑模式
        self._init_sort_states()  # 初始化排序状态字典
        self.reload_lyrics()  # 重新加载歌词数据

    def apply_button_scale(self, scale: float) -> None:
        """
        应用按钮缩放比例。

        此方法对实例中的多个按钮对象依次应用给定的缩放系数。

        参数:
            self (object): 类的实例。
            scale (float): 要应用的缩放比例，通常为一个浮点数。

        返回:
            None: 此方法无返回值。
        """
        # 应用搜索按钮的缩放
        _apply_button_scale(self.btn_search, scale)
        # 应用反转按钮的缩放
        _apply_button_scale(self.btn_invert, scale)
        # 应用地图轨迹按钮的缩放
        _apply_button_scale(self.btn_map_track, scale)
        # 应用编辑作者按钮的缩放
        _apply_button_scale(self.btn_edit_author, scale)
        # 应用删除按钮的缩放
        _apply_button_scale(self.btn_delete, scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        """设置facade属性并重新加载歌词。

        参数:
            facade (MuseArcFacade): 要设置的facade对象，用于控制外观或界面。

        返回:
            None
        """
        self.facade = facade  # 将传入的facade赋值给实例变量self.facade
        self.reload_lyrics()  # 调用reload_lyrics方法以重新加载歌词数据

    def refresh_page(self) -> None:
        self.reload_lyrics()

    def reload_lyrics(self) -> None:
        self._all_rows = self.facade.list_lyrics(limit=200_000)
        for row in self._all_rows:
            row["lyrics_language"] = str(row.get("lyrics_language", "") or "unknown")
        available_ids = {
            str(row.get("lyrics_id", "") or "")
            for row in self._all_rows
            if str(row.get("lyrics_id", "") or "")
        }
        self._selected_lyrics_ids_memory.intersection_update(available_ids)
        self.apply_filter()

    def _is_realtime_search_enabled(self) -> bool:
        """
        检查实时搜索功能是否在运行时配置中启用。

        参数:
            无。

        返回值:
            布尔值，表示实时搜索是否启用。
            如果配置属性存在且为真值，则返回True；否则返回False。
            如果属性不存在，则默认返回True。
        """
        cfg = self.facade.get_runtime_config()  # 从facade获取运行时配置
        return bool(getattr(cfg.ui, "realtime_search_enabled", True))  # 使用getattr获取"realtime_search_enabled"属性，如果不存在则使用默认值True，并将结果转换为布尔值

    def _on_search_text_changed(self, _text: str) -> None:
        if not self._is_realtime_search_enabled():
            return
        self._search_timer.start()

    def clear_search_with_undo(self) -> None:
        """
        清除搜索输入框的内容，并支持撤销操作。

        该方法会清空搜索框中的文本。如果启用了实时搜索模式，清除后将启动一个计时器，
        以便在短暂延迟后自动应用搜索过滤；否则将立即应用过滤。

        参数:
            无（除了self，它指向当前实例）

        返回值:
            None
        """
        # 调用底层函数清除搜索输入框内容，并保留撤销操作支持
        _clear_line_edit_with_undo(self.search_input)
        # 检查是否启用了实时搜索功能
        if self._is_realtime_search_enabled():
            # 如果是实时搜索模式，启动计时器，延迟后自动触发搜索，避免每次输入都立即搜索
            self._search_timer.start()
        else:
            # 如果不是实时搜索模式，则立即应用当前的过滤条件
            self.apply_filter()

    def _init_sort_states(self) -> None:
        """
        初始化排序状态。

        功能：从模型列中获取键，初始化排序状态字典，根据现有排序状态或默认值"off"设置每个键的状态。
              如果所有状态均为"off"且存在"file_name"键，则将"file_name"的状态设置为"asc"。
              最后更新排序状态并设置到模型中。

        参数：无。

        返回值：无。
        """
        keys = [str(col.key) for col in self.model.columns]  # 从模型列中提取所有键，并转换为字符串
        keep: dict[str, str] = {}  # 初始化一个空字典用于存储排序状态
        for key in keys:
            keep[key] = self._sort_states.get(key, "off")  # 为每个键设置排序状态，如果存在则使用现有值，否则默认为"off"
        if all(v == "off" for v in keep.values()) and "file_name" in keep:  # 检查是否所有状态均为"off"且"file_name"键存在
            keep["file_name"] = "asc"  # 如果条件满足，则将"file_name"的状态设置为"asc"
        self._sort_states = keep  # 更新实例的排序状态字典
        self.model.set_header_sort_states(self._sort_states)  # 将排序状态应用到模型的表头

    def _next_sort_state(self, state: str) -> str:
        if state == "asc":
            return "desc"
        if state == "desc":
            return "off"
        return "asc"

    def _sync_sort_from_header(self) -> None:
        self.model.set_header_sort_states(self._sort_states)
        self.apply_filter()

    @staticmethod
    def _lyrics_sort_value(row: dict, key: str):
        """为歌词数据生成排序值。
        根据指定的键从行数据中提取值，并将其转换为适合排序的格式。
        对于特定的键（如 "line_count"），会进行专门的数值转换；
        对于其他键，尝试转换为浮点数，若失败则返回大小写不敏感的字符串。

        参数:
            row (dict): 包含歌词行数据的字典。
            key (str): 需要提取和排序的字段名。

        返回:
            int 或 float 或 str: 转换后的排序值。
                - 对于 "line_count" 键，返回整数。
                - 对于可转换为数字的值，返回浮点数。
                - 否则，返回原始值的小写不敏感形式。
        """
        # 从行字典中安全地获取指定键对应的值，若键不存在则默认为空字符串
        value = row.get(key, "")
        # 所有返回值使用统一的二元组结构，避免数字和字符串在同一列中直接比较。
        if key == "line_count":
            return (0, _safe_int(value, 0))
        return (1, str(value or "").casefold())

    def _sort_rows_by_rules(self, rows: list[dict]) -> list[dict]:
        out = list(rows)
        header = self.table.horizontalHeader()
        logical_indexes = sorted(range(len(self.model.columns)), key=lambda i: header.visualIndex(i))
        active: list[tuple[str, str]] = []
        for logical in logical_indexes:
            key = str(self.model.columns[logical].key)
            state = self._sort_states.get(key, "off")
            if state in {"asc", "desc"}:
                active.append((key, state))
        if not active:
            active = [("file_name", "asc")]
        for key, state in reversed(active):
            out.sort(key=lambda r, k=key: self._lyrics_sort_value(r, k), reverse=(state == "desc"))
        return out

    def _on_header_clicked(self, logical: int) -> None:
        """处理表头点击事件，根据点击的列索引更新排序状态。

        参数:
            logical (int): 点击的列索引，表示列在模型中的位置。

        返回值:
            None
        """
        if logical < 0 or logical >= len(self.model.columns):  # 检查列索引是否在有效范围内，无效则直接返回
            return
        key = str(self.model.columns[logical].key)  # 获取对应列的键并转换为字符串
        self._sort_states[key] = self._next_sort_state(self._sort_states.get(key, "off"))  # 更新排序状态，从当前状态获取下一个状态（默认为"off"）
        self._sync_sort_from_header()  # 同步排序状态到表头或其他相关组件

    def apply_filter(self) -> None:
        """
        应用过滤器，根据搜索文本和分组条件筛选并显示数据行。

        功能：
            - 从搜索框获取关键词，在多个字段中进行模糊匹配
            - 按照排序规则和分组条件对结果进行排序
            - 更新表格模型并刷新预览

        参数：
            无（通过 self 访问实例属性）

        返回值：
            None（无返回值，直接更新界面显示）
        """
        if self.selection.mode == SelectionMode.MULTI:
            selected_ids = set(self._selected_lyrics_ids_memory)
        else:
            selected_ids = set(self.model.selected_track_ids_from_rows(self.table.selected_rows()))
        # 获取搜索框文本，去除首尾空格并转为小写（用于不区分大小写的匹配）
        token = self.search_input.text().strip().casefold()
        # 获取分组下拉框当前选中的值，未选择时默认为 "none"
        group_key = str(self.combo_group.currentData() or "none")

        if not token:
            # 搜索框为空时，显示所有行数据
            rows = list(self._all_rows)
        else:
            rows = []
            # 遍历所有数据行进行过滤
            for row in self._all_rows:
                # 将多个字段拼接成一个字符串，用于全文搜索匹配
                text = " | ".join(
                    [
                        str(row.get("file_name", "")),
                        str(row.get("lyrics_title", "")),
                        str(row.get("lyrics_artist", "")),
                        str(row.get("lyrics_album", "")),
                        str(row.get("lyrics_author", "")),
                        str(row.get("lyrics_language", "")),
                        str(row.get("mapped_track", "")),
                    ]
                ).casefold()
                # 如果搜索关键词存在于拼接后的文本中，则保留该行
                if token in text:
                    rows.append(row)

        # 按照预定义的排序规则对行进行排序
        rows = self._sort_rows_by_rules(rows)
        # 如果设置了有效的分组键，按分组键进行二次排序
        if group_key and group_key != "none":
            rows.sort(key=lambda r: str(r.get(group_key, "")).casefold())
        # 将筛选排序后的数据设置到表格模型中
        self._restoring_selection = True
        try:
            self.model.set_rows(rows)
            selected_rows = [
                row_index
                for row_index, row in enumerate(rows)
                if str(row.get("lyrics_id", "") or "") in selected_ids
            ]
            self.table.set_selected_rows(selected_rows)
        finally:
            self._restoring_selection = False
        # 刷新预览面板
        self._refresh_preview()

    def _on_table_selection_changed(self, *_args) -> None:
        if not self._restoring_selection:
            self._remember_visible_selection()
        self._refresh_preview()

    def _remember_visible_selection(self) -> None:
        visible_ids = {
            str(row.get("lyrics_id", "") or "")
            for row in self.model.rows
            if str(row.get("lyrics_id", "") or "")
        }
        selected_ids = set(self.model.selected_track_ids_from_rows(self.table.selected_rows()))
        if self.selection.mode == SelectionMode.MULTI:
            self._selected_lyrics_ids_memory.difference_update(visible_ids)
            self._selected_lyrics_ids_memory.update(selected_ids)
        else:
            self._selected_lyrics_ids_memory = selected_ids

    def _selected_rows(self) -> list[dict]:
        out: list[dict] = []
        for row_index in self.table.selected_rows():
            row = self.model.row_at(row_index)
            if row:
                out.append(row)
        return out

    def _selected_lyrics_ids(self) -> list[str]:
        if self.selection.mode == SelectionMode.MULTI:
            return sorted(self._selected_lyrics_ids_memory)
        return [str(r.get("lyrics_id", "")) for r in self._selected_rows() if r.get("lyrics_id")]

    def _column_key_at(self, index: QModelIndex) -> str:
        if not index.isValid():
            return ""
        if not hasattr(self.model, "columns"):
            return ""
        if index.column() < 0 or index.column() >= len(self.model.columns):
            return ""
        return str(self.model.columns[index.column()].key)

    def _row_at_index(self, index: QModelIndex) -> dict | None:
        if not index.isValid():
            return None
        return self.model.row_at(index.row())

    def _on_toggle_preview(self, checked: bool) -> None:
        self.preview.setVisible(bool(checked))
        if checked:
            self.splitter.setSizes([750, 650])
        self._refresh_preview()

    def _selected_lyrics_row(self) -> dict | None:
        """获取当前表格中选中的歌词行数据。

        该方法检查表格是否有选中行，如果有则返回第一行选中行的数据字典，否则返回None。

        参数:
            无额外参数（除self外）

        返回:
            dict: 选中行的数据字典，当没有选中行时返回None
        """
        # 检查表格的选择模型是否已初始化，如果未初始化则直接返回None
        selected = self.table.selected_rows()
        if not selected:
            return None
        return self.model.row_at(selected[0])

    def _refresh_preview(self) -> None:
        if not self.chk_preview.isChecked():
            self.preview.clear()
            return
        row = self._selected_lyrics_row()
        if not row:
            self.preview.clear()
            return
        rel = str(row.get("storage_relpath", "") or "")
        if not rel:
            self.preview.setPlainText("")
            return
        target = Path(self.facade.library_root) / rel
        try:
            text = target.read_text(encoding="utf-8")
        except Exception as exc:
            text = f"无法读取歌词文件: {exc}"
        self.preview.setPlainText(text)

    def _map_selected_to_track(self) -> None:
        lyrics_ids = self._selected_lyrics_ids()
        if not lyrics_ids:
            return
        dlg = TrackPickerDialog(self, self.facade, allow_clear=True)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for lyrics_id in lyrics_ids:
            self.facade.set_primary_track_for_lyrics(lyrics_id, dlg.selected_track_id)
        self.reload_lyrics()
        self.library_changed.emit()

    def _map_single_row(self, row: dict) -> bool:
        lyrics_id = str((row or {}).get("lyrics_id", "") or "")
        if not lyrics_id:
            return False
        dlg = TrackPickerDialog(self, self.facade, allow_clear=True)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        self.facade.set_primary_track_for_lyrics(lyrics_id, dlg.selected_track_id)
        self.reload_lyrics()
        self.library_changed.emit()
        return True

    def _map_single_row_by_index(self, index: QModelIndex) -> bool:
        """
        功能：根据给定的索引映射单行。
        参数：
            index (QModelIndex): 要映射的行的索引。
        返回值：
            bool: 如果映射成功返回True，否则返回False。
        """
        row = self._row_at_index(index)  # 根据索引获取对应的行
        if not row:  # 如果行不存在或为空
            return False  # 返回False表示映射失败
        return self._map_single_row(row)  # 映射该行并返回结果

    def _edit_author_for_selected(self) -> None:
        """
        批量编辑选中歌词的作者。

        该方法获取当前选中的歌词ID列表，通过对话框提示用户输入新的作者名称，并更新这些歌词的作者信息。最后刷新歌词库并发射更改信号。

        参数：
        无

        返回值：
        无（None）
        """
        lyrics_ids = self._selected_lyrics_ids()  # 获取选中的歌词ID列表
        if not lyrics_ids:  # 如果没有选中的歌词，则提前返回
            return
        value, ok = QInputDialog.getText(self, "批量改作者", "歌词文件作者")  # 弹出输入对话框获取新作者名称
        if not ok:  # 如果用户取消输入，则提前返回
            return
        self.facade.update_lyrics_author(lyrics_ids, str(value))  # 调用外观层更新选中的歌词作者
        self.reload_lyrics()  # 重新加载歌词以反映更改
        self.library_changed.emit()  # 发射库更改信号，通知其他组件

    def _edit_author_for_row(self, row: dict) -> bool:
        lyrics_id = str((row or {}).get("lyrics_id", "") or "")
        if not lyrics_id:
            return False
        current_author = str((row or {}).get("lyrics_author", "") or "")
        value, ok = QInputDialog.getText(self, "修改作者", "歌词文件作者", text=current_author)
        if not ok:
            return False
        self.facade.update_lyrics_author([lyrics_id], str(value))
        self.reload_lyrics()
        self.library_changed.emit()
        return True

    def _delete_selected_lyrics(self) -> None:
        lyrics_ids = self._selected_lyrics_ids()
        if not lyrics_ids:
            return
        answer = QMessageBox.question(self, "删除歌词", f"确定删除 {len(lyrics_ids)} 条歌词吗？")
        if answer != QMessageBox.StandardButton.Yes:
            return

        total = len(lyrics_ids)

        def _task(progress, is_cancelled):
            deleted = 0
            chunk_size = 256
            processed = 0
            for start in range(0, total, chunk_size):
                if is_cancelled():
                    break
                chunk = lyrics_ids[start : start + chunk_size]
                deleted += int(self.facade.delete_lyrics(chunk) or 0)
                processed += len(chunk)
                progress(processed, total, "正在删除歌词")
            return {"deleted": deleted, "cancelled": bool(is_cancelled() and processed < total)}

        outcome = run_modal_task(self, "删除歌词", _task)
        if outcome.error is not None:
            QMessageBox.warning(self, "删除歌词", f"删除失败\n{outcome.error}")
            return
        payload = outcome.result if isinstance(outcome.result, dict) else {}
        deleted = int(payload.get("deleted", 0) or 0)
        cancelled = bool(payload.get("cancelled"))

        self.reload_lyrics()
        self.preview.clear()
        self.library_changed.emit()
        QMessageBox.information(self, "删除歌词", f"已删除 {deleted} 条歌词" + ("（已取消）" if cancelled else ""))

    def _on_double_click_cell(self, index: QModelIndex) -> None:
        """
        处理单元格的双击事件。

        功能：当用户双击表格单元格时，根据单元格所在的列执行相应操作。如果索引无效则直接返回；如果列键是"mapped_track"，则映射单行；如果编辑模式开启且列键在指定集合中，则进入编辑模式。

        参数：
            index (QModelIndex): 被双击的单元格的索引对象。

        返回值：
            None: 无返回值。
        """
        if not index.isValid():  # 如果索引无效，则直接返回
            return
        key = self._column_key_at(index)  # 获取被点击单元格的列键
        if key == "mapped_track":  # 如果列键是"mapped_track"，则映射单行
            self._map_single_row_by_index(index)
            return
        if self.chk_edit_mode.isChecked() and key in {"file_name", "lyrics_title", "lyrics_artist", "lyrics_album", "lyrics_author"}:  # 如果编辑模式开启且列键在可编辑列集合中，则进入编辑模式
            self.table.edit(index)
            return

    def _on_click_cell(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        key = self._column_key_at(index)
        if key == "mapped_track":
            if self.chk_multi.isChecked():
                return
            mods = QApplication.keyboardModifiers()
            if bool(mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)):
                return
            self._map_single_row_by_index(index)
            return
        if self.chk_edit_mode.isChecked() and key in {"file_name", "lyrics_title", "lyrics_artist", "lyrics_album", "lyrics_author"}:
            self.table.edit(index)

    def _on_ctrl_edit_requested(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        if bool(self.model.flags(index) & Qt.ItemFlag.ItemIsEditable):
            self.table.edit(index)

    def _on_lyrics_field_edited(self, lyrics_id: str, key: str, value: object) -> None:
        """当歌词字段被编辑时调用的处理函数。

        功能：接收歌词ID、字段名和字段值，更新歌词数据，并同步界面。
        参数：
            lyrics_id (str): 歌词的唯一标识符。
            key (str): 需要编辑的字段名称。
            value (object): 字段的新值。
        返回值：无。
        """
        # 如果歌词ID为空，则直接返回，不进行后续操作
        if not lyrics_id:
            return
        logger.info("[LyricsPage] _on_lyrics_field_edited: lid=%s key=%s value=%r", lyrics_id, key, value)
        print(f"[edit] LyricsPage 收到: lid={lyrics_id} key={key} value={value!r}")
        try:
            # 调用 facade 层方法，批量更新指定歌词的指定字段
            self.facade.update_lyrics_fields([lyrics_id], {key: value})
            logger.info("[LyricsPage] 编辑成功: lid=%s key=%s", lyrics_id, key)
            print(f"[edit] LyricsPage 成功: lid={lyrics_id} key={key}")
        except Exception as exc:
            # 捕获异常，记录错误日志并弹出警告框
            logger.error("[LyricsPage] 编辑失败: lid=%s key=%s exc=%s", lyrics_id, key, exc)
            print(f"[edit] LyricsPage 失败: lid={lyrics_id} key={key} exc={exc}")
            QMessageBox.warning(self, "编辑失败", f"edit: editing failed\n{exc}")
            # 使用定时器在下一个事件循环中重新加载歌词数据，确保界面刷新
            QTimer.singleShot(0, self.reload_lyrics)
            return
        # 遍历本地缓存的所有歌词行数据 (_all_rows)
        for row in self._all_rows:
            # 查找与当前编辑的歌词ID匹配的行（类型转换后进行比较）
            if str(row.get("lyrics_id", "")) != lyrics_id:
                continue
            # 找到对应行后，更新其特定字段的值
            row[key] = value
            # 更新完成后跳出循环（因为ID唯一）
            break
        # 使用定时器，在下一个事件循环中发出数据变更信号，通知其他部分刷新
        QTimer.singleShot(0, self.library_changed.emit)

    def _map_next_row_from(self, row_index: int) -> None:
        """
        从指定的行索引开始，映射下一行。

        参数：
            row_index (int): 起始行索引。

        返回值：
            None
        """
        if row_index < 0:  # 如果起始行索引为负，直接返回
            return
        row_count = self.model.rowCount()  # 获取模型当前的总行数
        if row_count <= 0:  # 如果行数小于等于0，没有行可处理，返回
            return
        mapped_col = 0  # 初始化映射列索引为0
        if hasattr(self.model, "columns"):  # 检查模型是否有columns属性
            for idx, col in enumerate(self.model.columns):  # 遍历所有列
                if str(getattr(col, "key", "")) == "mapped_track":  # 如果列的key属性为"mapped_track"
                    mapped_col = idx  # 设置映射列索引为当前列索引
                    break  # 找到后退出循环
        current_row = row_index  # 从指定行索引开始
        while current_row < row_count:  # 当当前行索引小于总行数时循环
            idx = self.model.index(current_row, mapped_col)  # 获取当前行和映射列的索引
            self.table.setCurrentIndex(idx)  # 设置表格的当前索引为该索引
            applied = self._map_single_row_by_index(idx)  # 对该行执行映射操作，返回是否应用成功
            if not applied:  # 如果映射未应用成功
                break  # 退出循环
            current_row += 1  # 移动到下一行
            row_count = self.model.rowCount()  # 更新总行数，以防映射操作导致行数变化

    def eventFilter(self, obj, event):
        if obj is self.table and event.type() == QEvent.Type.KeyPress:
            key_event = event if isinstance(event, QKeyEvent) else None
            if key_event is not None and key_event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                idx = self.table.currentIndex()
                if idx.isValid() and self._column_key_at(idx) == "mapped_track":
                    self._map_next_row_from(idx.row())
                    return True
        return super().eventFilter(obj, event)

    def _on_toggle_multi(self, checked: bool) -> None:
        """
        切换表格视图的多选模式。

        参数:
            checked (bool): 如果为True，设置选择模式为多选；如果为False，设置为单选。

        返回值:
            None
        """
        current_ids = set(self.model.selected_track_ids_from_rows(self.table.selected_rows()))
        if checked:
            self._selected_lyrics_ids_memory.update(current_ids)
        else:
            self._selected_lyrics_ids_memory = current_ids
        mode = SelectionMode.MULTI if checked else SelectionMode.NORMAL
        self.table.set_mode(mode)

    def _invert_selection(self) -> None:
        """反选当前表格视图中的行。

        逻辑说明：将已选行变为未选，将未选行变为已选。
        对于大数据量（行数 >= 10000），使用模态任务以避免界面冻结。
        无参数。
        无返回值（None）。
        """
        model = self.model
        # 获取底层数据模型，如果模型或选择模型为空，则直接返回
        if model is None or self.table.selectionModel() is None:
            return
        # 获取数据总行数
        total = model.rowCount()
        # 如果行数为0或负数，直接返回
        if total <= 0:
            return
        # 使用集合快速存储当前已选中行的行号
        selected = set(self.table.selected_rows())

        # 定义内部函数，用于计算需要反选的目标行
        def _compute_targets(progress, is_cancelled):
            out: list[int] = []  # 用于存储需要反选的行号
            # 计算进度报告的步长，最小为1，避免除零错误
            step = max(1, total // 200)
            for row in range(total):
                # 检查任务是否被外部取消（例如用户点击了取消按钮）
                if is_cancelled():
                    return {"rows": out, "cancelled": True}
                # 如果当前行号不在已选集合中，则说明需要反选（即添加到选择中）
                if row not in selected:
                    out.append(row)
                curr = row + 1
                # 按照计算的步长更新进度，或当计算完成时更新
                if curr == total or curr % step == 0:
                    progress(curr, total, "正在计算反选")
            return {"rows": out, "cancelled": False}

        # 根据数据量大小决定执行策略
        if total >= 10000:
            # 对于大数据量，在后台运行一个模态任务，防止UI卡顿
            outcome = run_modal_task(self, "反选", _compute_targets)
            # 检查任务执行是否出错
            if outcome.error is not None:
                QMessageBox.warning(self, "反选失败", f"反选失败\n{outcome.error}")
                return
            # 获取任务结果，确保其为字典类型
            payload = outcome.result if isinstance(outcome.result, dict) else {}
            # 从结果中提取目标行号，并转换为整数列表
            rows = [int(v) for v in payload.get("rows", [])]
            # 如果任务被取消且结果为空，则直接返回，不执行后续操作
            if bool(payload.get("cancelled")):
                return
        else:
            # 对于小数据量，直接同步调用计算函数
            payload = _compute_targets(lambda *_args: None, lambda: False)
            rows = [int(v) for v in payload.get("rows", [])]

        self.table.set_selected_rows(rows)
        self._remember_visible_selection()

    def _on_toggle_edit_mode(self, checked: bool) -> None:
        self.table.set_edit_mode(checked)
        if checked:
            self.table.setEditTriggers(
                QAbstractItemView.EditTrigger.SelectedClicked
                | QAbstractItemView.EditTrigger.DoubleClicked
                | QAbstractItemView.EditTrigger.EditKeyPressed
            )
        else:
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    def _show_context_menu(self, pos) -> None:
        global_pos = self.table.viewport().mapToGlobal(pos)
        lyrics_ids = self._selected_lyrics_ids()
        if not lyrics_ids:
            return
        menu = QMenu(self)
        action_map_track = menu.addAction("映射到歌曲")
        action_edit_author = menu.addAction("批量改作者")
        action_delete = menu.addAction("删除歌词")
        action_reveal = menu.addAction("使用文件管理器查看")
        action_copy = menu.addAction("复制行数据")

        chosen = menu.exec(global_pos)
        if not chosen:
            return
        if chosen == action_map_track:
            self._map_selected_to_track()
            return
        if chosen == action_edit_author:
            self._edit_author_for_selected()
            return
        if chosen == action_delete:
            self._delete_selected_lyrics()
            return
        if chosen == action_reveal:
            row = self._selected_lyrics_row() or {}
            rel = str(row.get("storage_relpath", "") or "")
            path_text = str(Path(self.facade.library_root) / rel) if rel else ""
            _reveal_in_file_manager(self, path_text)
            return
        if chosen == action_copy:
            _copy_selected_cells(self.table)

    def focus_lyrics_id(self, lyrics_id: str) -> bool:
        """根据歌词ID聚焦到对应的表格行。

        此方法通过遍历表格数据，查找匹配给定歌词ID的行，并将该行设置为当前选中项。
        如果找到匹配行，则滚动表格使其居中显示，并刷新预览面板。

        Args:
            lyrics_id (str): 需要查找的歌词ID。

        Returns:
            bool: 如果成功找到并聚焦到匹配行则返回True，否则返回False。
        """
        target = str(lyrics_id or "").strip()  # 将参数转换为字符串，处理None值，并去除首尾空白
        if not target:
            return False
        self.search_input.clear()  # 清空搜索输入框
        self.apply_filter()  # 应用过滤器确保能搜索到所有数据
        # 遍历表格模型中的每一行
        for row in range(self.model.rowCount()):
            payload = self.model.row_at(row) or {}  # 获取当前行的数据，若为空则使用空字典
            # 比较当前行的lyrics_id与目标ID，注意统一转换为字符串类型
            if str(payload.get("lyrics_id", "") or "") != target:
                continue
            idx = self.model.index(row, 0)  # 获取当前行第一列的模型索引
            if not idx.isValid():  # 检查索引是否有效
                continue
            self.table.setCurrentIndex(idx)  # 设置表格当前索引
            self.table.selectRow(row)  # 选中整行
            # 滚动表格使选中行居中显示
            self.table.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
            self._refresh_preview()  # 刷新预览面板
            return True
        return False  # 遍历所有行后仍未找到匹配项

