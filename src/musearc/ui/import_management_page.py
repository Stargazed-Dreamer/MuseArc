from __future__ import annotations

from pathlib import Path
import re

from PySide6.QtCore import QModelIndex, QObject, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMenu,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from musearc.app.facade import MuseArcFacade
from musearc.ui.import_worker import ImportWorker
from musearc.ui.table_models import ColumnDef, DictTableModel


class _HistoryLoadWorker(QObject):
    """子线程加载导入历史数据，避免阻塞主线程。"""
    finished = Signal(list, list, list)  # import_rows, stats_rows, playlist_rows

    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade

    def run(self) -> None:
        """运行导入任务，获取导入批次、统计和播放列表历史数据，并通过finished信号发送结果。

        参数：无。
        返回值：无（None），但通过finished信号发出三个列表：import_rows, stats_rows, playlist_rows。
        """
        try:
            import_rows = self.facade.list_import_batches(limit=1000)  # 调用facade获取最多1000个导入批次记录
            stats_rows = self.facade.list_stats_import_history(limit=500)  # 调用facade获取最多500个统计导入历史记录
            playlist_rows = self.facade.list_playlist_import_history(limit=500)  # 调用facade获取最多500个播放列表导入历史记录
            self.finished.emit(import_rows, stats_rows, playlist_rows)  # 通过finished信号发送获取到的数据列表
        except Exception:  # 捕获任何异常以确保程序稳定
            self.finished.emit([], [], [])  # 发生异常时，通过finished信号发送空列表作为结果


def _apply_button_scale(button: QPushButton, scale: float) -> None:
    button.setMinimumHeight(max(30, int(28 * scale)))


def _copy_selected_cells(table: QTableView) -> None:
    """将QTableView中选中的单元格复制到剪贴板。

    参数:
        table (QTableView): 要操作的表格视图对象。

    返回值:
        None
    """
    selection_model = table.selectionModel()  # 获取表格的选择模型
    if selection_model is None:  # 如果选择模型不存在，则直接返回
        return
    indexes = selection_model.selectedIndexes()  # 获取所有选中的单元格索引
    if not indexes:  # 如果没有选中的索引，则直接返回
        return

    cells: dict[int, dict[int, str]] = {}  # 初始化字典，用于存储单元格数据，结构为{行号: {列号: 单元格文本}}
    max_col = 0  # 初始化最大列号，用于后续格式化时确定列范围
    for idx in indexes:  # 遍历每个选中的索引
        row = idx.row()  # 获取当前索引的行号
        col = idx.column()  # 获取当前索引的列号
        max_col = max(max_col, col)  # 更新最大列号
        cells.setdefault(row, {})[col] = str(idx.data() or "")  # 将单元格数据存入字典，如果单元格数据为空则转换为空字符串

    lines: list[str] = []  # 初始化列表，用于存储格式化后的每一行数据
    for row in sorted(cells.keys()):  # 按行号排序遍历字典中的行
        cols = cells[row]  # 获取当前行的所有列数据
        lines.append("\t".join(cols.get(col, "") for col in range(max_col + 1)))  # 将当前行的数据按列顺序用制表符连接，缺少的列用空字符串填充

    QApplication.clipboard().setText("\n".join(lines))  # 将所有行数据用换行符连接，并设置到系统剪贴板


def _install_copy_support(table: QTableView) -> None:
    """为QTableView安装复制功能支持。

    参数:
        table (QTableView): 需要安装复制支持的表格视图对象。

    返回:
        None
    """
    # 创建复制快捷键，绑定到表格视图
    shortcut = QShortcut(QKeySequence.StandardKey.Copy, table)
    # 当快捷键被激活时，连接到复制选中单元格的函数
    shortcut.activated.connect(lambda: _copy_selected_cells(table))
    # 将快捷键引用保存在表格对象中，以防止被垃圾回收
    table._copy_shortcut = shortcut


def _errors_count(value) -> int:
    """用于统计错误或问题的数量。

    根据传入的值类型，计算并返回相应的数量。
    - 如果输入是列表、元组或字典，则返回其长度（元素/键值对的数量）。
    - 如果输入可以转换为整数，则返回该整数值（若输入为空或零，则返回0）。
    - 如果输入既不是容器类型也无法转换为整数，则返回0。

    参数:
        value: 任意类型，待统计的值。可以是列表、元组、字典，或其他可转换为整数的类型。
    
    返回:
        int: 统计出的错误或问题的数量。若无法统计则返回0。
    """
    # 检查输入是否为列表类型，若是，则返回列表的长度
    if isinstance(value, list):
        return len(value)
    # 检查输入是否为元组类型，若是，则返回元组的长度
    if isinstance(value, tuple):
        return len(value)
    # 检查输入是否为字典类型，若是，则返回字典的长度（键值对数量）
    if isinstance(value, dict):
        return len(value)
    # 如果输入不是以上容器类型，尝试将其转换为整数
    try:
        # 将 value 转换为整数；如果 value 为空或假值，则使用 0 作为默认值
        return int(value or 0)
    except Exception:
        # 如果转换失败（例如，字符串不能转换为整数），则安全地返回 0
        return 0


def _safe_int(value, default: int = 0) -> int:
    """
    安全地将输入值转换为整数。

    参数:
    value: 任意类型的值，尝试转换为整数。
    default: 整数，默认值为0，当转换失败或输入是容器类型时返回。

    返回值:
    int: 转换后的整数，如果失败则返回默认值。
    """
    if isinstance(value, (list, tuple, dict, set)):  # 如果value是容器类型（如列表、元组、字典、集合），则直接返回默认值
        return default
    try:
        return int(value or 0)  # 尝试将value转换为整数，如果value为假值（如None、False、空等），则使用0
    except Exception:  # 如果转换失败，则返回默认值
        return default


def _clean_status_text(value: str) -> str:
    """
    功能：清理状态文本字符串，去除特定字符和格式，返回标准化的文本。
    参数：value (str): 输入的状态文本。
    返回值：str: 清理后的文本，如果为空则返回“待处理”。
    """
    # 将输入转换为字符串，如果为None则用空字符串，并去除首尾空白。
    text = str(value or "").strip()
    # 如果文本为空，返回默认状态。
    if not text:
        return "待处理"
    # 兼容旧状态：例如“已跳过-原因xxx”
    # 使用正则表达式去除‘原因’及其周围的空白字符。
    text = re.sub(r"\s*原因\s*", "", text)
    # 替换双破折号为单破折号，双冒号为单冒号。
    text = text.replace("--", "-").replace("::", ":")
    # 去除字符串首尾的破折号、冒号和空格。
    text = text.strip(" -:")
    # 返回清理后的文本，如果为空则返回默认值‘待处理’。
    return text or "待处理"


class ImportFileStateModel(DictTableModel):
    STATUS_COLORS = {
        "pending": QColor(110, 110, 110),
        "processing": QColor(36, 96, 180),
        "archived": QColor(27, 132, 69),
        "review": QColor(189, 109, 0),
        "skipped": QColor(128, 128, 128),
    }

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        """为模型中的特定索引和角色提供数据。

        根据数据角色和模型索引返回相应内容。当角色为前景色且索引有效时，
        根据状态码返回对应的颜色；否则委托给父类处理。

        参数:
            index (QModelIndex): 模型中的索引，标识数据位置。
            role (int): 数据角色，决定返回的数据类型，默认为显示角色。

        返回值:
            相应角色的数据。例如，前景色角色时返回颜色，或父类返回的数据。
        """
        if role == Qt.ItemDataRole.ForegroundRole and index.isValid():  # 检查是否为前景色角色且索引有效
            row = self.row_at(index.row()) or {}  # 尝试获取行数据，失败则使用空字典以避免错误
            code = str(row.get("status_code", ""))  # 从行数据中提取状态码，确保转换为字符串
            return self.STATUS_COLORS.get(code)  # 从预定义的颜色字典中获取对应状态码的颜色
        return super().data(index, role)  # 对于其他角色，调用父类的data方法处理


class ImportTaskDetailDialog(QDialog):
    def __init__(self, parent: QWidget):
        """初始化导入任务详情对话框。

        设置窗口的基本属性，包括标题、大小，并初始化UI组件，如批次信息标签、
        来源标签、进度条、统计信息标签、排序下拉框、文件表格及其数据模型。
        最后将排序信号与排序应用方法连接。

        Args:
            parent (QWidget): 父窗口组件。
        """
        super().__init__(parent)
        self.setWindowTitle("导入任务详情")  # 设置窗口标题
        self.resize(1080, 760)  # 设置窗口初始大小
        self._cached_rows: list[dict] = []  # 用于缓存表格行数据的列表

        root = QVBoxLayout(self)  # 创建主垂直布局管理器

        # 创建顶部垂直布局，用于放置批次信息、来源、进度和统计信息
        top = QVBoxLayout()
        self.label_batch = QLabel("批次: -")  # 显示批次信息的标签
        self.label_source = QLabel("来源: -")  # 显示来源信息的标签
        self.progress = QProgressBar()  # 进度条组件
        self.progress.setRange(0, 100)  # 设置进度条范围为0到100
        self.label_stats = QLabel("-")  # 显示统计信息的标签
        top.addWidget(self.label_batch)  # 将批次标签添加到顶部布局
        top.addWidget(self.label_source)  # 将来源标签添加到顶部布局
        top.addWidget(self.progress)  # 将进度条添加到顶部布局
        top.addWidget(self.label_stats)  # 将统计标签添加到顶部布局

        # 创建排序选项行布局
        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("文件列表排序"))  # 添加排序说明标签
        self.combo_sort = QComboBox()  # 创建排序下拉框
        self.combo_sort.addItem("文件名", "file_name")  # 添加"文件名"排序选项，对应数据为"file_name"
        self.combo_sort.addItem("状态", "status")  # 添加"状态"排序选项，对应数据为"status"
        sort_row.addWidget(self.combo_sort)  # 将下拉框添加到排序行
        sort_row.addStretch(1)  # 添加弹性空间，使控件左对齐

        # 创建文件状态数据模型，定义表格列
        self.file_model = ImportFileStateModel(
            [
                ColumnDef("file_name", "文件名"),  # 定义第一列：文件名
                ColumnDef("status", "状态"),  # 定义第二列：状态
            ]
        )
        self.file_table = QTableView()  # 创建文件表格视图
        self.file_table.setModel(self.file_model)  # 将数据模型设置给表格视图
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)  # 设置表格选择行为为整行选择
        self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)  # 禁用表格编辑功能
        self.file_table.setAlternatingRowColors(True)  # 启用交替行颜色，提高可读性
        self.file_table.horizontalHeader().setStretchLastSection(True)  # 使表格最后一列自动拉伸以填充剩余空间
        self.file_table.setSortingEnabled(False)  # 初始禁用表头点击排序功能
        _install_copy_support(self.file_table)  # 为表格安装复制支持功能

        # 将布局和组件添加到根布局
        root.addLayout(top)  # 添加顶部信息区域
        root.addLayout(sort_row)  # 添加排序选项行
        root.addWidget(self.file_table, 1)  # 添加文件表格，设置拉伸因子为1使其占据剩余空间

        # 连接排序下拉框的选择改变信号到排序应用方法
        self.combo_sort.currentIndexChanged.connect(self._apply_sort)

    def _apply_sort(self) -> None:
        """应用排序功能。根据用户在下拉框中的选择，对文件列表模型中的数据进行排序。
    
        Args:
            无显式参数。方法从实例的UI组件（combo_sort）和文件数据模型（file_model）中获取数据。
    
        Returns:
            None。排序结果直接更新到文件数据模型（file_model）中。
        """
        # 从下拉框获取当前选中的排序键，若为空则默认使用“file_name”（按文件名排序）
        key = str(self.combo_sort.currentData() or "file_name")
        # 获取文件模型中的所有行数据，并转换为列表以便排序
        rows = list(self.file_model.rows or [])
        # 定义状态处理优先级的映射字典，数值越小，优先级越高
        status_rank = {
            "processing": 0,   # 处理中
            "review": 1,       # 待审核
            "pending": 2,      # 待处理
            "archived": 3,     # 已归档
            "skipped": 4,      # 已跳过
        }
        # 如果选择按“status”（状态）排序
        if key == "status":
            # 使用双重键进行排序：主要按状态优先级，次要按文件名（忽略大小写）
            rows.sort(
                key=lambda r: (
                    # 从行数据中获取状态码，若不存在则默认为“pending”，并获取其优先级数字，未知状态给予高数值（99）使其排在后面
                    int(status_rank.get(str(r.get("status_code", "pending")), 99)),
                    # 获取文件名，若不存在则默认空字符串，并转为小写进行比较
                    str(r.get("file_name", "")).casefold(),
                )
            )
        else:
            # 否则，按默认键（通常是文件名）进行排序（忽略大小写）
            rows.sort(key=lambda r: str(r.get("file_name", "")).casefold())
        # 将排序后的行数据列表设置回文件模型，以更新UI显示
        self.file_model.set_rows(rows)

    @staticmethod
    def _normalize_file_states(rows: list[dict]) -> list[dict]:
        """规范化文件状态数据。

        将输入的行列表转换为统一格式的字典列表，每个字典包含文件名、状态和状态码。

        参数:
            rows (list[dict]): 原始文件状态数据列表，每个元素应为字典格式。

        返回:
            list[dict]: 规范化后的字典列表，每个字典包含以下键:
                - file_name (str): 文件名
                - status (str): 文件状态文本
                - status_code (str): 文件状态代码
        """
        out: list[dict] = []  # 初始化空列表用于存储规范化后的结果
    
        # 遍历输入的行列表，如果rows为None则使用空列表避免报错
        for item in rows or []:
            # 跳过非字典类型的元素，确保数据格式正确
            if not isinstance(item, dict):
                continue
        
            # 获取相对路径，如果不存在或为空则使用空字符串
            rel = str(item.get("relpath", "") or "")
        
            # 构建规范化字典并添加到结果列表
            out.append(
                {
                    # 文件名：优先使用item中的file_name，若不存在则使用从relpath提取的文件名
                    "file_name": str(item.get("file_name", "") or Path(rel).name),
                    # 状态文本：清理并规范化状态文本，默认为"待处理"
                    "status": _clean_status_text(str(item.get("status", "") or "待处理")),
                    # 状态代码：优先使用item中的status_code，默认为"pending"
                    "status_code": str(item.get("status_code", "") or "pending"),
                }
            )
        return out  # 返回规范化后的字典列表

    def set_payload(self, payload: dict, *, running: bool) -> None:
        """设置导入任务的数据到UI上。

        根据传入的payload字典，更新进度条、统计信息和文件状态表格。
        支持任务运行中和完成后的不同数据处理逻辑。

        Args:
            payload (dict): 包含任务数据的字典。
            running (bool): 标识任务是否正在运行。

        Returns:
            None
        """
        # 从payload字典中安全地提取并转换各种数据字段
        batch_id = str(payload.get("import_batch_id", "") or "-")  # 获取批次ID，若缺失则设为“-”
        source_path = str(payload.get("source_path", "") or "-")  # 获取来源路径，若缺失则设为“-”
        scanned = _safe_int(payload.get("scanned_files", 0), 0)    # 已扫描文件数，确保为整数
        processed = _safe_int(payload.get("processed_files", 0), 0) # 已处理文件数
        imported_tracks = _safe_int(payload.get("imported_tracks", 0), 0) # 导入曲目数
        imported_lyrics = _safe_int(payload.get("imported_lyrics", 0), 0) # 导入歌词数
        duplicate_tracks = _safe_int(payload.get("duplicate_tracks", 0), 0) # 重复曲目数
        review_items = _safe_int(payload.get("review_items", 0), 0) # 需审查项数
        errors = _errors_count(payload.get("errors", 0))           # 错误数，使用专用函数处理

        # 逻辑：若任务已完成（非运行中）且处理数为0，则将处理数设为已扫描数，表示所有文件均已尝试处理
        if not running and processed <= 0:
            processed = scanned

        # 计算进度百分比，避免除零错误，并限制在0-100范围内
        percent = 0 if scanned <= 0 else int((processed / scanned) * 100)
        # 更新UI上的批次和来源标签
        self.label_batch.setText(f"批次: {batch_id}")
        self.label_source.setText(f"来源: {source_path}")
        # 更新进度条，并确保值在0到100之间
        self.progress.setValue(max(0, min(100, percent)))
        # 拼接统计信息字符串并更新UI
        self.label_stats.setText(
            " | ".join(
                [
                    f"进度 {processed}/{scanned}",
                    f"曲目 {imported_tracks}",
                    f"歌词 {imported_lyrics}",
                    f"重复 {duplicate_tracks}",
                    f"审查 {review_items}",
                    f"错误 {errors}",
                ]
            )
        )
        # 处理文件状态数据
        raw_states = payload.get("file_states", None)
        # 检查文件状态数据是否为非空列表
        if isinstance(raw_states, list) and raw_states:
            # 规范化并更新文件状态表格
            rows = self._normalize_file_states(list(raw_states))
            self._cached_rows = list(rows)  # 缓存处理后的行数据，用于任务完成后的显示
            self.file_model.set_rows(rows)  # 设置模型数据
            self._apply_sort()              # 应用当前排序规则
        # 如果任务已完成且存在缓存行数据，则使用缓存数据更新表格
        elif not running and self._cached_rows:
            self.file_model.set_rows(list(self._cached_rows))
            self._apply_sort()


class ImportManagementPage(QWidget):
    library_changed = Signal()

    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade
        self._queue: list[str] = []
        self._active_source: str | None = None
        self._import_thread: QThread | None = None
        self._import_worker: ImportWorker | None = None
        self._import_paused = False
        self._last_progress_payload: dict | None = None
        self._last_report_payload: dict | None = None
        self._detail_dialog: ImportTaskDetailDialog | None = None
        self._heavy_modal: QProgressDialog | None = None
        self._heavy_modal_threshold = 40
        self._history_thread: QThread | None = None
        self._history_worker: _HistoryLoadWorker | None = None

        root = QVBoxLayout(self)

        row1 = QHBoxLayout()
        self.btn_new_import = QPushButton("导入文件夹")
        self.btn_resume_import = QPushButton("继续未完成导入")
        self.btn_import_stats = QPushButton("导入统计数据")
        self.btn_import_playlist = QPushButton("导入歌单")
        row1.addWidget(self.btn_new_import)
        row1.addWidget(self.btn_resume_import)
        row1.addWidget(self.btn_import_stats)
        row1.addWidget(self.btn_import_playlist)
        row1.addStretch(1)

        box = QVBoxLayout()
        self.label_active = QLabel("当前任务: 无")
        self.label_stage = QLabel("阶段: -")
        self.label_file = QLabel("文件: -")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.label_stats = QLabel("-")
        row_ctl = QHBoxLayout()
        self.btn_pause_resume = QPushButton("暂停")
        self.btn_cancel = QPushButton("取消导入")
        self.btn_detail = QPushButton("详情")
        self.btn_pause_resume.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.btn_detail.setEnabled(False)
        row_ctl.addWidget(self.btn_pause_resume)
        row_ctl.addWidget(self.btn_cancel)
        row_ctl.addWidget(self.btn_detail)
        row_ctl.addStretch(1)

        box.addWidget(self.label_active)
        box.addWidget(self.label_stage)
        box.addWidget(self.label_file)
        box.addWidget(self.progress)
        box.addWidget(self.label_stats)
        box.addLayout(row_ctl)

        self.queue_label = QLabel("队列(0)")
        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(160)

        self.history_model = DictTableModel(
            [
                ColumnDef("source_path", "来源路径"),
                ColumnDef("started_at", "开始时间"),
                ColumnDef("finished_at", "结束时间"),
                ColumnDef("scanned_files", "扫描文件"),
                ColumnDef("imported_tracks", "曲目"),
                ColumnDef("duplicate_tracks", "重复"),
                ColumnDef("imported_lyrics", "歌词"),
                ColumnDef("review_items", "审查"),
                ColumnDef("import_batch_id", "批次ID"),
            ]
        )
        self.history_table = QTableView()
        self.history_table.setModel(self.history_model)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.horizontalHeader().setStretchLastSection(True)
        _install_copy_support(self.history_table)

        self.stats_model = DictTableModel(
            [
                ColumnDef("imported_at", "导入时间"),
                ColumnDef("playlist_hash", "歌单哈希"),
                ColumnDef("applied_tracks", "生效歌曲"),
                ColumnDef("skipped_rows", "跳过"),
                ColumnDef("source_file", "来源文件"),
            ]
        )
        self.stats_table = QTableView()
        self.stats_table.setModel(self.stats_model)
        self.stats_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.stats_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.stats_table.setAlternatingRowColors(True)
        self.stats_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.stats_table.horizontalHeader().setStretchLastSection(False)
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.stats_table.setColumnWidth(4, 520)
        _install_copy_support(self.stats_table)
        self.stats_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self.playlist_import_model = DictTableModel(
            [
                ColumnDef("imported_at", "导入时间"),
                ColumnDef("playlist_name", "歌单名"),
                ColumnDef("target_playlist_name", "目标歌单"),
                ColumnDef("playlist_hash", "歌单哈希"),
                ColumnDef("added_tracks", "成功"),
                ColumnDef("failed_tracks", "失败"),
                ColumnDef("source_file", "来源文件"),
            ]
        )
        self.playlist_import_table = QTableView()
        self.playlist_import_table.setModel(self.playlist_import_model)
        self.playlist_import_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.playlist_import_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.playlist_import_table.setAlternatingRowColors(True)
        self.playlist_import_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.playlist_import_table.horizontalHeader().setStretchLastSection(False)
        self.playlist_import_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.playlist_import_table.setColumnWidth(6, 520)
        _install_copy_support(self.playlist_import_table)

        root.addLayout(row1)
        root.addLayout(box)
        root.addWidget(self.queue_label)
        root.addWidget(self.queue_list)
        history_row = QHBoxLayout()
        left_hist = QVBoxLayout()
        left_hist.addWidget(QLabel("文件夹导入历史"))
        left_hist.addWidget(self.history_table, 1)
        right_hist = QVBoxLayout()
        right_hist.addWidget(QLabel("统计导入历史"))
        right_hist.addWidget(self.stats_table, 1)
        right_hist.addWidget(QLabel("歌单导入历史"))
        right_hist.addWidget(self.playlist_import_table, 1)
        history_row.addLayout(left_hist, 3)
        history_row.addLayout(right_hist, 2)
        root.addLayout(history_row, 1)

        self.btn_new_import.clicked.connect(self.on_import)
        self.btn_resume_import.clicked.connect(self.on_resume_import)
        self.btn_import_stats.clicked.connect(self.on_import_stats)
        self.btn_import_playlist.clicked.connect(self.on_import_playlist)
        self.btn_pause_resume.clicked.connect(self._on_pause_resume_import)
        self.btn_cancel.clicked.connect(self._on_cancel_import)
        self.btn_detail.clicked.connect(self._open_running_detail)
        self.history_table.doubleClicked.connect(self._open_history_detail)
        self.stats_table.customContextMenuRequested.connect(self._show_stats_history_menu)

        self.reload_history()
        self._refresh_queue_view()

    def apply_button_scale(self, scale: float) -> None:
        for btn in [
            self.btn_new_import,
            self.btn_resume_import,
            self.btn_import_stats,
            self.btn_import_playlist,
            self.btn_pause_resume,
            self.btn_cancel,
            self.btn_detail,
        ]:
            _apply_button_scale(btn, scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        """设置facade属性，并调用相关方法刷新历史和队列视图。

        功能：初始化或更新实例的facade属性，并触发历史记录重新加载和队列视图刷新。
        参数：
            facade (MuseArcFacade): 要设置的facade对象，用于访问或管理相关功能。
        返回值：无。
        """
        self.facade = facade  # 设置实例的facade属性
        self.reload_history()  # 重新加载历史记录
        self._refresh_queue_view()  # 刷新队列视图

    def refresh_page(self) -> None:
        """刷新当前页面。

        重新加载页面相关的历史记录，并更新页面的队列视图。

        参数:
            无

        返回:
            无
        """
        self.reload_history() # 重新加载页面的历史数据
        self._refresh_queue_view() # 刷新页面中的队列显示部分

    def reload_history(self) -> None:
        # 如果已有历史加载线程在运行，跳过
        if self._history_thread is not None and self._history_thread.isRunning():
            return
        self._history_thread = QThread(self)
        self._history_worker = _HistoryLoadWorker(self.facade)
        self._history_worker.moveToThread(self._history_thread)
        self._history_thread.started.connect(self._history_worker.run)
        self._history_worker.finished.connect(self._on_history_loaded)
        self._history_worker.finished.connect(self._history_thread.quit)
        self._history_thread.finished.connect(self._cleanup_history_worker)
        self._history_thread.start()

    def _on_history_loaded(self, import_rows: list, stats_rows: list, playlist_rows: list) -> None:
        self.history_model.set_rows(import_rows)
        self.stats_model.set_rows(stats_rows)
        self.playlist_import_model.set_rows(playlist_rows)

    def _cleanup_history_worker(self) -> None:
        """清理历史记录工作器资源。

        功能：安全地销毁历史记录工作器线程和对象，释放相关资源。
        参数：无。
        返回值：无。
        """
        if self._history_worker is not None:  # 检查历史记录工作器对象是否存在
            self._history_worker.deleteLater()  # 安排工作器对象在事件循环中稍后被销毁
            self._history_worker = None  # 将工作器对象引用设为None
        if self._history_thread is not None:  # 检查历史记录线程对象是否存在
            self._history_thread.deleteLater()  # 安排线程对象在事件循环中稍后被销毁
            self._history_thread = None  # 将线程对象引用设为None

    def _ensure_detail_dialog(self) -> ImportTaskDetailDialog:
        if self._detail_dialog is None:
            self._detail_dialog = ImportTaskDetailDialog(self)
            self._detail_dialog.destroyed.connect(lambda *_args: setattr(self, "_detail_dialog", None))
        return self._detail_dialog

    def _open_running_detail(self) -> None:
        """打开导入任务的运行详情对话框。

        检查是否有活跃的导入任务和进度载荷，如果有，则打开或更新详情对话框显示进度信息。
        如果没有，则显示提示信息。

        参数：
            self: 类实例。

        返回值：
            None
        """
        # 检查是否有活跃的导入任务和最后的进度载荷，如果缺少任一条件，则显示提示并返回
        if not self._active_source or not self._last_progress_payload:
            # 当没有运行中的导入任务时，弹出信息框通知用户
            QMessageBox.information(self, "导入详情", "当前没有运行中的导入任务。")
            return
        # 获取或创建详情对话框实例
        dialog = self._ensure_detail_dialog()
        # 设置对话框的进度载荷，并标记为运行状态以更新显示
        dialog.set_payload(self._last_progress_payload, running=True)
        # 显示对话框并将其提升到前台，确保用户可见和可交互
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _open_history_detail(self, index: QModelIndex) -> None:
        """打开历史详情对话框。

        参数：
        index (QModelIndex): 表示历史记录的模型索引。

        返回值：
        None: 该方法不返回任何值。
        """
        # 如果索引有效，获取对应行的数据，否则设为None
        row = self.history_model.row_at(index.row()) if index.isValid() else None
        # 如果没有行数据，则提前返回
        if not row:
            return
        # 从行数据中提取import_batch_id，转换为字符串，处理空值或None
        import_batch_id = str(row.get("import_batch_id", "") or "")
        # 如果import_batch_id为空，则提前返回
        if not import_batch_id:
            return
        # 通过facade获取导入批次详情
        detail = self.facade.get_import_batch_detail(import_batch_id)
        # 如果详情不存在，显示警告并返回
        if not detail:
            QMessageBox.warning(self, "导入详情", "未找到该批次详情。")
            return
        # 确保详情对话框存在
        dialog = self._ensure_detail_dialog()
        # 设置对话框内容，running=False表示不显示运行状态
        dialog.set_payload(detail, running=False)
        # 显示对话框
        dialog.show()
        # 将对话框提升到前面
        dialog.raise_()
        # 激活对话框窗口
        dialog.activateWindow()

    def on_import(self) -> None:
        """让用户选择一个文件夹作为导入源，并将其加入处理队列。

        功能：
            弹出一个系统文件夹选择对话框。如果用户成功选择了一个文件夹，
            则将该文件夹路径添加到待处理的任务队列中。
            如果用户取消了选择，则不做任何操作。

        参数：
            无。

        返回值：
            无。
        """
        # 弹出文件夹选择对话框，供用户选择要导入数据的目录
        folder = QFileDialog.getExistingDirectory(self, "选择导入文件夹")
        # 检查用户是否选择了文件夹（未选择则为空字符串）
        if not folder:
            # 用户取消了选择，直接返回
            return
        # 将用户选择的文件夹路径加入处理队列
        self._enqueue_source(folder)

    def on_resume_import(self) -> None:
        """
        恢复之前未完成的导入任务。
    
        功能：列出所有未完成的导入记录，允许用户选择其中一个或全部继续导入。
        参数：无（除了self）
        返回值：None
        """
        states = self.facade.list_resume_imports()  # 从facade获取所有未完成的导入状态
        if not states:  # 如果没有未完成的记录
            QMessageBox.information(self, "继续导入", "没有未完成导入记录。")  # 提示用户没有记录
            return  # 结束方法

        menu = QMenu(self)  # 创建一个菜单
        action_map: dict[QAction, str] = {}  # 创建动作与路径的映射字典
        for state in states:  # 遍历每个未完成的导入状态
            # 构造菜单文本，显示源路径和进度（已处理文件数/已扫描文件数）
            text = f"{state['source_path']} ({state['processed_files']}/{state['scanned_files']})"
            action_map[menu.addAction(text)] = str(state["source_path"])  # 将菜单动作与源路径关联
        menu.addSeparator()  # 添加分隔线
        action_all = menu.addAction("全部加入队列")  # 添加"全部加入队列"选项

        # 在按钮的左下方显示菜单，并获取用户选择的动作
        chosen = menu.exec(self.btn_resume_import.mapToGlobal(self.btn_resume_import.rect().bottomLeft()))
        if not chosen:  # 如果用户没有选择（例如按Esc或点击菜单外部）
            return  # 结束方法

        if chosen == action_all:  # 如果用户选择了"全部加入队列"
            for state in states:  # 遍历每个未完成的导入状态
                self._enqueue_source(str(state["source_path"]))  # 将源路径加入队列
            return  # 结束方法

        source = action_map.get(chosen)  # 从映射字典中获取选择的源路径
        if source:  # 如果找到了对应的源路径
            self._enqueue_source(source)  # 将该源路径加入队列

    def on_import_stats(self) -> None:
        """导入播放列表统计数据。
    
        功能：打开文件对话框让用户选择一个JSON格式的统计数据文件，
              将数据导入到当前播放列表中，并显示导入结果。
    
        参数：
            self: 实例自身。
    
        返回值：
            None
        """
        # 调用文件对话框，让用户选择JSON格式的统计数据文件
        file_path, _ = QFileDialog.getOpenFileName(self, "选择统计数据文件", "", "JSON (*.json);;All Files (*)")
        # 如果用户取消选择（file_path为空），则直接返回
        if not file_path:
            return
        try:
            # 调用外观层（facade）方法执行实际的导入操作
            result = self.facade.import_playlist_stats(file_path)
        except Exception as exc:
            # 如果导入过程中发生异常，显示警告消息并返回
            QMessageBox.warning(self, "导入统计数据", str(exc))
            return
        # 导入成功后，重新加载播放历史列表
        self.reload_history()
        # 发出信号，通知相关组件库数据已变更
        self.library_changed.emit()
        # 显示导入结果的详细信息框
        QMessageBox.information(
            self,
            "导入统计数据",
            f"歌单哈希: {result.get('playlist_hash','')}\n生效歌曲: {result.get('applied_tracks',0)}\n跳过: {result.get('skipped_rows',0)}",
        )

    def on_import_playlist(self) -> None:
        """从文件导入歌单。检查数据库位置一致性，处理重名歌单，然后导入并显示结果。"""
        file_path, _ = QFileDialog.getOpenFileName(self, "选择歌单文件", "", "JSON (*.json);;All Files (*)")  # 打开文件选择对话框
        if not file_path:  # 如果没有选择文件
            return
        try:
            inspect = self.facade.inspect_playlist_package(file_path)  # 检查歌单包信息
        except Exception as exc:  # 捕获异常
            QMessageBox.warning(self, "导入歌单", str(exc))  # 显示警告信息
            return

        if not bool(inspect.get("database_location_match", False)):  # 检查数据库位置是否匹配当前库
            src = str(inspect.get("database_location", "") or "")
            cur = str(self.facade.library_root)
            QMessageBox.warning(self, "导入歌单", f"数据库位置不一致，无法导入。\n文件内: {src}\n当前: {cur}")  # 显示不匹配警告
            return

        duplicate_mode = "rename"  # 默认重命名模式
        existing_name = str(inspect.get("existing_playlist_name", "") or "")
        if existing_name:  # 如果存在同名歌单，询问用户处理方式
            box = QMessageBox(self)  # 创建消息框
            box.setWindowTitle("导入歌单")
            box.setText(f"已存在同名歌单：{existing_name}")
            overwrite_btn = box.addButton("覆盖原来的记录和歌单", QMessageBox.ButtonRole.AcceptRole)  # 添加覆盖按钮
            rename_btn = box.addButton("自动以(x)重命名再导入", QMessageBox.ButtonRole.ActionRole)  # 添加重命名按钮
            cancel_btn = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)  # 添加取消按钮
            box.exec()  # 显示消息框
            clicked = box.clickedButton()  # 获取点击的按钮
            if clicked == cancel_btn:  # 如果点击取消
                return
            duplicate_mode = "overwrite" if clicked == overwrite_btn else "rename"  # 根据用户选择设置处理模式

        try:
            result = self.facade.import_playlist_package(file_path, duplicate_mode=duplicate_mode)  # 导入歌单包
        except Exception as exc:  # 捕获异常
            QMessageBox.warning(self, "导入歌单", str(exc))  # 显示警告
            return

        self.reload_history()  # 重载历史记录
        self.library_changed.emit()  # 发射库变化信号
        QMessageBox.information(  # 显示导入结果信息
            self,
            "导入歌单",
            "\n".join(
                [
                    f"目标歌单: {result.get('target_playlist_name', '')}",
                    f"成功: {result.get('added_tracks', 0)}",
                    f"失败: {result.get('failed_tracks', 0)}",
                ]
            ),
        )

    def _show_stats_history_menu(self, pos) -> None:
        """显示统计历史上下文菜单。根据点击位置或选中行，提供取消导入或复制行数据的选项。
        参数：
            pos (QPoint): 点击的位置坐标。
        返回值：
            None
        """
        # 根据点击位置获取表格项索引
        idx = self.stats_table.indexAt(pos)
        # 如果索引有效，获取对应行数据；否则为None
        row = self.stats_model.row_at(idx.row()) if idx.isValid() else None
        # 如果行数据为空，尝试从选中的行中获取
        if row is None:
            # 获取当前选中的行，如果选择模型存在
            selected = self.stats_table.selectionModel().selectedRows() if self.stats_table.selectionModel() is not None else []
            if selected:
                # 取第一个选中行的数据
                row = self.stats_model.row_at(selected[0].row())
        # 如果仍然没有行数据，直接返回
        if not row:
            return

        # 从行数据中提取播放列表哈希和源文件路径，并清理空白字符
        playlist_hash = str(row.get("playlist_hash", "") or "").strip()
        source_file = str(row.get("source_file", "") or "").strip()
        # 检查是否可以撤销导入：需要播放列表哈希、源文件存在且路径有效
        can_revert = bool(playlist_hash) and bool(source_file) and Path(source_file).exists()

        # 创建上下文菜单
        menu = QMenu(self)
        # 添加“取消导入”动作，并根据条件设置是否启用
        action_revert = menu.addAction("取消导入")
        action_revert.setEnabled(can_revert)
        # 添加“复制行数据”动作
        action_copy = menu.addAction("复制行数据")
        # 执行菜单，将点击位置转换为全局坐标显示菜单，并获取用户选择
        chosen = menu.exec(self.stats_table.viewport().mapToGlobal(pos))
        # 如果没有选择，直接返回
        if not chosen:
            return
        # 如果选择了复制行数据，执行复制操作并返回
        if chosen == action_copy:
            _copy_selected_cells(self.stats_table)
            return
        # 如果选择的不是取消导入，直接返回（例如，菜单关闭或其他）
        if chosen != action_revert:
            return
        # 显示确认对话框，询问用户是否确定取消导入
        answer = QMessageBox.question(self, "取消导入", "确定取消该统计导入并回退其贡献数据吗？")
        # 如果用户未确认，直接返回
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            # 调用facade方法撤销播放列表统计导入
            result = self.facade.revert_playlist_stats_import(playlist_hash)
        except Exception as exc:
            # 如果发生异常，显示警告消息并返回
            QMessageBox.warning(self, "取消导入", str(exc))
            return
        # 重新加载历史数据
        self.reload_history()
        # 发出库已更改的信号
        self.library_changed.emit()
        # 显示成功信息，包括回退的哈希和影响的歌曲数
        QMessageBox.information(
            self,
            "取消导入",
            f"已回退歌单哈希: {result.get('playlist_hash','')}\n影响歌曲: {result.get('affected_tracks',0)}",
        )

    def _enqueue_source(self, source_path: str) -> None:
        """将新的源路径加入导入队列，并尝试启动后续导入。
    
        该方法用于管理待导入文件的队列。首先将输入的路径解析为绝对路径，
        然后检查该路径是否与当前正在处理的路径相同或已存在于队列中。
        若不重复，则将其添加到队列末尾，更新队列视图，并尝试启动下一个导入任务。
    
        Args:
            source_path (str): 需要加入队列的源文件路径，可以是相对或绝对路径。
    
        Returns:
            None: 该方法没有返回值。
        """
        # 将源路径解析为绝对路径，确保路径格式统一
        source = str(Path(source_path).resolve())
        # 检查路径是否与当前活跃的源文件重复，或是否已在导入队列中
        if source == self._active_source or source in self._queue:
            return
        # 将不重复的源路径追加到导入队列末尾
        self._queue.append(source)
        # 刷新队列的显示视图（如UI列表）
        self._refresh_queue_view()
        # 尝试启动队列中下一个文件的导入过程
        self._start_next_import()

    def _start_next_import(self) -> None:
        """启动队列中的下一个导入操作。
    
        检查是否有活动的导入线程或队列为空，如果没有，则从队列中取出源并启动导入，然后刷新队列视图。
    
        参数：
            self: 当前实例。
    
        返回值：
            None
        """
        if self._import_thread is not None or not self._queue:  # 如果导入线程已存在或队列为空，则不启动新导入
            return
        source = self._queue.pop(0)  # 从队列中取出第一个源
        self._start_import(source)  # 启动导入操作
        self._refresh_queue_view()  # 刷新队列视图

    def _start_import(self, source_path: str) -> None:
        """启动一个后台导入任务。

        该方法会初始化一个QThread和一个ImportWorker，并将其信号与相应的槽函数连接，
        用于在后台异步执行从 `source_path` 导入数据的任务，同时更新UI状态。

        Args:
            source_path (str): 要导入的数据源路径。
        """
        self._active_source = source_path  # 记录当前活动的源路径
        self._import_paused = False  # 重置导入暂停状态为否
        self._last_progress_payload = None  # 清空上次的进度数据
        self._last_report_payload = None  # 清空上次的报告数据

        # 更新界面控件，显示任务启动状态
        self.label_active.setText(f"当前任务: {source_path}")
        self.label_stage.setText("阶段: 启动中...")
        self.label_file.setText("文件: -")
        self.progress.setValue(0)  # 进度条重置为0
        self.label_stats.setText("-")  # 统计信息重置
        # 启用操作按钮
        self.btn_pause_resume.setEnabled(True)
        self.btn_cancel.setEnabled(True)
        self.btn_detail.setEnabled(True)
        self.btn_pause_resume.setText("暂停")  # 将暂停/恢复按钮的文本设置为“暂停”

        # 创建用于运行导入任务的工作线程
        self._import_thread = QThread(self)
        # 创建实际的工作对象，并传入库的根路径和数据源路径
        self._import_worker = ImportWorker(str(self.facade.library_root), source_path)
        # 将工作对象移动到工作线程中，确保其槽函数在该线程执行
        self._import_worker.moveToThread(self._import_thread)

        # 连接信号与槽函数，建立线程间通信
        self._import_thread.started.connect(self._import_worker.run)  # 线程启动后，执行工作对象的run方法
        self._import_worker.progress.connect(self._on_import_progress)  # 工作对象发出进度信号时，更新进度UI
        self._import_worker.finished.connect(self._on_import_finished)  # 工作对象完成后，处理完成逻辑
        self._import_worker.failed.connect(self._on_import_failed)  # 工作对象失败时，处理失败逻辑
        # 工作线程无论成功或失败完成后，都请求工作线程退出
        self._import_worker.finished.connect(self._import_thread.quit)
        self._import_worker.failed.connect(self._import_thread.quit)
        self._import_thread.finished.connect(self._cleanup_import_worker)  # 工作线程退出后，清理资源

        self._import_thread.start()  # 启动工作线程，开始导入任务

    def has_running_import(self) -> bool:
        return bool(self._import_thread is not None and self._import_thread.isRunning())

    def _close_heavy_modal(self) -> None:
        if self._heavy_modal is not None:
            self._heavy_modal.hide()
            self._heavy_modal.deleteLater()
            self._heavy_modal = None

    def shutdown_running_import(self, timeout_ms: int = 15000) -> bool:
        """
        关闭正在运行的导入操作。

        参数:
            timeout_ms (int): 等待导入线程停止的超时时间（毫秒），默认为15000。

        返回:
            bool: 如果成功关闭导入操作，返回True；如果超时或关闭失败，返回False。
        """
        self._close_heavy_modal()  # 关闭可能存在的重模态框
        if self._import_worker is not None:  # 检查导入工作者是否存在
            try:
                self._import_worker.request_cancel("keep")  # 请求取消导入，参数"keep"可能表示保留状态
                self._import_worker.request_resume()  # 请求恢复导入，确保取消操作生效
            except Exception:  # 捕获任何可能的异常
                pass  # 忽略异常，继续执行后续逻辑
        if self._import_thread is None:  # 如果没有导入线程
            return True  # 直接返回成功
        if self._import_thread.isRunning():  # 如果导入线程正在运行
            self._import_thread.quit()  # 请求线程退出
            if not self._import_thread.wait(max(1000, int(timeout_ms))):  # 等待线程停止，超时时间至少1000毫秒
                return False  # 如果等待超时，返回失败
        self._cleanup_import_worker()  # 清理导入工作者相关资源
        return True  # 返回成功

    def _on_pause_resume_import(self) -> None:
        """
        切换导入任务的暂停与恢复状态。
        此方法用于响应暂停/恢复按钮的点击事件，根据当前导入状态，
        向后台工作线程发送暂停或恢复请求，并同步更新界面显示。

        参数:
            self: 类实例对象，包含导入任务状态及界面控件。

        返回:
            None: 此方法不返回任何值。
        """
        # 检查是否存在正在运行的导入工作线程
        if not self._import_worker:
            return
        # 根据当前暂停状态决定执行恢复还是暂停操作
        if self._import_paused:
            # 当前已暂停，发送恢复请求并更新状态和界面
            self._import_worker.request_resume()
            self._import_paused = False
            self.btn_pause_resume.setText("暂停")
            self.label_stage.setText("阶段: 恢复中...")
        else:
            # 当前正在运行，发送暂停请求并更新状态和界面
            self._import_worker.request_pause()
            self._import_paused = True
            self.btn_pause_resume.setText("继续")
            self.label_stage.setText("阶段: 暂停中...")

    def _on_cancel_import(self) -> None:
        """显示取消导入对话框，让用户选择取消方式（保留已处理并停止、全部回退并停止或继续导入），并根据选择执行相应操作。无参数，无返回值。"""
        if not self._import_worker:  # 检查导入工人是否存在，如果不存在则提前返回
            return

        box = QMessageBox(self)  # 创建消息框对象，父组件为当前实例
        box.setWindowTitle("取消导入")  # 设置消息框标题
        box.setText("请选择取消方式")  # 设置消息框显示文本
        keep_btn = box.addButton("保留已处理并停止", QMessageBox.ButtonRole.AcceptRole)  # 添加接受角色按钮
        rollback_btn = box.addButton("全部回退并停止", QMessageBox.ButtonRole.DestructiveRole)  # 添加破坏性角色按钮
        cont_btn = box.addButton("继续导入", QMessageBox.ButtonRole.RejectRole)  # 添加拒绝角色按钮
        box.exec()  # 显示消息框并等待用户响应
        clicked = box.clickedButton()  # 获取用户点击的按钮

        if clicked == keep_btn:  # 用户选择保留已处理并停止
            self._import_worker.request_cancel("keep")  # 向导入工人发送保留取消请求
            self.label_stage.setText("阶段: 取消中（保留已处理）...")  # 更新阶段标签显示取消状态
        elif clicked == rollback_btn:  # 用户选择全部回退并停止
            self._import_worker.request_cancel("rollback")  # 向导入工人发送回退取消请求
            self.label_stage.setText("阶段: 取消中（全部回退）...")  # 更新阶段标签显示取消状态
        elif clicked == cont_btn:  # 用户选择继续导入
            return  # 直接返回，不执行取消操作

    def _on_import_progress(self, payload: dict) -> None:
        """处理导入进度更新的回调方法，用于合并状态、计算进度并刷新UI显示。

        本方法负责合并当前进度数据与上一次的全量数据（主要用于处理增量更新），
        计算总体进度百分比，并更新窗口中的进度条、阶段提示、文件名和统计信息标签。

        参数:
            payload (dict): 从底层传入的当前进度字典，包含已扫描文件数、已处理文件数、
                            文件状态列表、阶段、当前文件名、导入数量统计、错误信息等。

        返回值:
            None: 无返回值。
        """
        # 创建 payload 的副本进行操作，避免直接修改原始参数
        merged_payload = dict(payload)
        # 获取上一次保存的完整进度数据，用于合并
        prev_payload = self._last_progress_payload or {}
        # 从当前数据中获取文件状态列表
        raw_states = merged_payload.get("file_states", None)
        # 判断是否为无效或空的文件状态列表
        if not isinstance(raw_states, list) or not raw_states:
            # 增量模式下空列表表示"无变化"，保留上次的全量数据
            if isinstance(prev_payload.get("file_states"), list):
                merged_payload["file_states"] = prev_payload.get("file_states") or []
        else:
            # 有数据：检查是否为增量更新（行数远小于上次全量）
            prev_states = prev_payload.get("file_states") or []
            # 判断是否满足增量合并的条件：上次数据存在且当前数据量远小于上次
            if isinstance(prev_states, list) and len(raw_states) < len(prev_states) and len(prev_states) > 10:
                # 增量合并：用 relpath 为主键合并
                # 构建上一次数据的字典，键为文件相对路径
                prev_by_rel = {str(s.get("relpath", "")): s for s in prev_states if isinstance(s, dict)}
                # 遍历当前的新增/更新数据，合并到上次的字典中
                for row in raw_states:
                    if isinstance(row, dict):
                        rel = str(row.get("relpath", ""))
                        if rel:
                            # 用当前行的数据覆盖或新增对应的 relpath 条目
                            prev_by_rel[rel] = row
                # 将合并后的数据转为列表，作为本次的完整文件状态
                merged_payload["file_states"] = list(prev_by_rel.values())
        # 更新保存的完整进度数据，供下次调用时合并使用
        self._last_progress_payload = merged_payload
        # 安全地获取并转换已扫描文件数和已处理文件数
        scanned = _safe_int(payload.get("scanned_files", 0), 0)
        processed = _safe_int(payload.get("processed_files", 0), 0)
        # 计算进度百分比，避免除零错误，并限制在0-100之间
        percent = 0 if scanned <= 0 else int((processed / scanned) * 100)
        # 更新进度条的值
        self.progress.setValue(max(0, min(100, percent)))

        # 获取并保存暂停状态
        paused = bool(payload.get("paused", False))
        self._import_paused = paused
        # 获取当前阶段字符串，如果为空或None则默认为“-”
        stage = str(payload.get("stage", "-") or "-")
        # 如果处于暂停状态，在阶段名称后追加提示
        if paused:
            stage = f"{stage}（已暂停）"
        # 更新阶段显示标签
        self.label_stage.setText(f"阶段: {stage}")
        # 更新当前正在处理的文件名显示标签
        self.label_file.setText(f"文件: {payload.get('current_file', '-')}")

        # 安全地获取错误数量
        errors = _errors_count(payload.get("errors", 0))
        # 更新统计数据标签，拼接多种统计信息
        self.label_stats.setText(
            " | ".join(
                [
                    f"进度 {processed}/{scanned}", # 已处理/总扫描文件数
                    f"曲目+{payload.get('imported_tracks', 0)}", # 新导入的曲目数
                    f"歌词+{payload.get('imported_lyrics', 0)}", # 新导入的歌词数
                    f"重复 {payload.get('duplicate_tracks', 0)}", # 发现的重复曲目数
                    f"审查 {payload.get('review_items', 0)}", # 需要审查的项目数
                    f"错误 {errors}", # 发生的错误数
                ]
            )
        )
        # 根据暂停状态，更新暂停/继续按钮的文本
        self.btn_pause_resume.setText("继续" if paused else "暂停")

        # 如果详情对话框存在且正在显示，则更新其内容
        if self._detail_dialog is not None and self._detail_dialog.isVisible():
            self._detail_dialog.set_payload(self._last_progress_payload, running=True)

    def _on_import_finished(self, report: dict) -> None:
        """处理导入任务完成后的UI更新和后续操作。

        Args:
            report (dict): 包含导入任务结果的报告字典，包含状态和数据统计信息。

        Returns:
            None: 此方法不返回任何值。
        """
        # 保存报告副本，用于后续可能的操作（如重新显示）
        self._last_report_payload = dict(report)

        # 根据导入是否被取消，更新状态标签显示
        if report.get("cancelled"):
            # 检查是否执行了回滚操作
            if report.get("rollback_applied"):
                self.label_stage.setText("阶段: 已取消并全部回退")
            # 检查是否保存了进度以便恢复
            elif report.get("resume_available"):
                self.label_stage.setText("阶段: 已取消，进度已保留")
            else:
                self.label_stage.setText("阶段: 已取消")
        else:
            # 导入成功完成
            self.label_stage.setText("阶段: 导入完成")

        # 更新统计信息标签，使用格式化字符串组合多个数据字段
        self.label_stats.setText(
            f"扫描 {report.get('scanned_files', 0)} / 曲目 {report.get('imported_tracks', 0)} / 审查 {report.get('review_items', 0)}"
        )

        # 重置活动导入源状态
        self._active_source = None
        # 禁用操作按钮，防止任务完成后的误操作
        self.btn_pause_resume.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.btn_detail.setEnabled(False)
        # 刷新历史记录列表
        self.reload_history()
        # 发出库内容变更信号，通知其他组件更新
        self.library_changed.emit()
        # 刷新导入队列视图
        self._refresh_queue_view()
        # 关闭可能存在的重量级模态对话框
        self._close_heavy_modal()

        # 如果详情对话框存在且正在显示，则更新其内容为最终报告
        if self._detail_dialog is not None and self._detail_dialog.isVisible():
            self._detail_dialog.set_payload(self._last_report_payload, running=False)

        # 启动队列中的下一个导入任务（如果存在）
        self._start_next_import()

    def _on_import_failed(self, message: str) -> None:
        """处理导入失败事件。

        当导入过程中发生错误时，由系统调用此方法。
        它会更新UI以显示失败状态和错误消息，重置相关状态，
        禁用用户操作，并启动队列中的下一个导入任务。

        Args:
            message (str): 描述导入失败原因的错误消息字符串。
        """
        # 更新阶段标签，显示为“导入失败”
        self.label_stage.setText("阶段: 导入失败")
        # 将错误消息显示在统计标签上
        self.label_stats.setText(message)
        # 清空当前活动的导入源
        self._active_source = None
        # 清空最后收到的进度数据
        self._last_progress_payload = None
        # 禁用“暂停/恢复”、“取消”和“详情”按钮，因为当前导入已失败
        self.btn_pause_resume.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.btn_detail.setEnabled(False)
        # 弹出一个临界错误对话框，向用户显示失败消息
        QMessageBox.critical(self, "导入失败", message)
        # 刷新导入队列视图，以反映当前状态
        self._refresh_queue_view()
        # 关闭可能存在的、占用资源较大的模态对话框
        self._close_heavy_modal()
        # 启动队列中的下一个待导入任务
        self._start_next_import()

    def _cleanup_import_worker(self) -> None:
        """清理导入工作线程资源。

        此方法用于安全地释放与导入任务相关的工作线程和对象，防止内存泄漏。
        它会首先关闭可能处于打开状态的对话框，然后删除相关的工作线程对象。

        参数:
            无。

        返回:
            无。
        """
        # 先关闭可能处于打开状态的“重量级”模态对话框
        self._close_heavy_modal()
        # 检查并清理导入工作器对象
        if self._import_worker is not None:
            # 使用 deleteLater() 确保在事件循环中安全删除对象
            self._import_worker.deleteLater()
            self._import_worker = None  # 将引用置空，避免悬垂指针
        # 检查并清理导入线程对象
        if self._import_thread is not None:
            # 使用 deleteLater() 确保在事件循环中安全删除对象
            self._import_thread.deleteLater()
            self._import_thread = None  # 将引用置空，避免悬垂指针

    def _refresh_queue_view(self) -> None:
        self.queue_list.clear()
        if self._active_source:
            self.queue_list.addItem(f"[运行中] {self._active_source}")
        for source in self._queue:
            self.queue_list.addItem(f"[排队] {source}")
        self.queue_label.setText(f"队列({len(self._queue) + (1 if self._active_source else 0)})")
        if not self._active_source:
            self.label_active.setText("当前任务: 无")
            self.btn_detail.setEnabled(False)
