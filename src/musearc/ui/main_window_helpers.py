from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableView,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from musearc.app.facade import MuseArcFacade
from musearc.config.store import save_runtime_config
from musearc.ui.table_models import ColumnDef, DictTableModel


def _apply_button_scale(button: QPushButton, scale: float) -> None:
    """
    应用按钮缩放功能，根据缩放因子计算并设置按钮的最小高度。

    参数:
        button (QPushButton): 要应用缩放的按钮对象。
        scale (float): 缩放因子，用于计算高度。

    返回:
        None
    """
    # 计算高度：使用缩放因子调整基础高度28，然后确保至少为30
    h = max(30, int(28 * scale))
    # 设置按钮的最小高度
    button.setMinimumHeight(h)


def _copy_selected_cells(table: QTableView) -> None:
    """
    复制选中的单元格内容到剪贴板。

    参数：
        table (QTableView): 包含选中单元格的QTableView控件。

    返回值：
        None: 无返回值，但会将复制内容写入系统剪贴板。
    """
    # 获取表格的选择模型，用于管理选中状态
    selection_model = table.selectionModel()
    # 如果选择模型不存在，则直接返回，避免后续操作出错
    if selection_model is None:
        return
    # 获取所有选中单元格的索引列表
    indexes = selection_model.selectedIndexes()
    # 如果没有选中索引，但表格有controller和model属性，则尝试从controller获取选中行
    if not indexes and hasattr(table, "controller") and table.model() is not None:
        # 安全获取controller属性，避免属性错误
        controller = getattr(table, "controller", None)
        # 从controller中获取选中行，并排序；如果controller不存在则设为空列表
        selected_rows = sorted(getattr(controller, "selected_rows", set())) if controller is not None else []
        # 如果有选中行，则遍历这些行和所有列，构建索引
        if selected_rows:
            model = table.model()
            for row in selected_rows:
                for col in range(model.columnCount()):
                    idx = model.index(row, col)
                    # 仅当索引有效时，才添加到索引列表
                    if idx.isValid():
                        indexes.append(idx)
    # 如果最终没有索引可复制，则返回
    if not indexes:
        return

    # 初始化一个嵌套字典，用于按行和列存储单元格数据
    cells: dict[int, dict[int, str]] = {}
    # 记录最大列索引，用于后续填充缺失列
    max_col = 0
    for idx in indexes:
        row = idx.row()
        col = idx.column()
        # 更新最大列索引，确保覆盖所有列
        max_col = max(max_col, col)
        # 使用setdefault处理新行，存储单元格数据；空值用空字符串表示
        cells.setdefault(row, {})[col] = str(idx.data() or "")

    # 初始化行列表，用于存储格式化后的文本行
    lines: list[str] = []
    # 按行号排序遍历字典，确保输出顺序正确
    for row in sorted(cells.keys()):
        cols = cells[row]
        # 生成该行的所有列数据，缺失列用空字符串填充
        line = [cols.get(col, "") for col in range(max_col + 1)]
        # 用制表符连接列数据，并添加到行列表
        lines.append("\t".join(line))

    # 将所有行用换行符连接，并复制到系统剪贴板
    QApplication.clipboard().setText("\n".join(lines))


def _install_copy_support(table: QTableView) -> None:
    """为指定的 QTableView 安装复制支持，通过快捷键复制选中的单元格。

    参数:
        table (QTableView): 要安装复制支持的表视图对象。

    返回值:
        None
    """
    shortcut = QShortcut(QKeySequence.StandardKey.Copy, table)  # 创建复制快捷键，绑定到表视图
    shortcut.activated.connect(lambda: _copy_selected_cells(table))  # 当快捷键激活时触发复制选中单元格的函数
    table._copy_shortcut = shortcut  # 将快捷键存储到表视图属性中，防止被垃圾回收


class _ButtonHotkeyMarker(QObject):
    def __init__(self, button: QPushButton, label: QLabel):
        """初始化方法，用于设置按钮和标签的实例。
        参数:
            button (QPushButton): 按钮控件
            label (QLabel): 标签控件
        返回值:
            无
        """
        super().__init__(button)  # 调用父类的初始化方法
        self.button = button  # 保存按钮实例
        self.label = label  # 保存标签实例
        self.button.installEventFilter(self)  # 为按钮安装事件过滤器，让当前对象处理按钮事件
        self._relayout()  # 调用重新布局方法

    def eventFilter(self, obj, event) -> bool:
        """
        事件过滤器方法，用于拦截和处理特定对象的事件。

        参数：
            obj: 事件发生的目标对象。
            event: 事件对象，包含事件类型等信息。

        返回值：
            bool: 表示事件是否被传递给父类的事件过滤器进行处理。
        """
        # 检查是否是目标按钮，并且事件类型是调整大小、显示或移动
        if obj is self.button and event.type() in {
            QEvent.Type.Resize,  # 调整大小事件
            QEvent.Type.Show,    # 显示事件
            QEvent.Type.Move,    # 移动事件
        }:
            # 调用重新布局方法以响应事件
            self._relayout()
        # 调用父类的事件过滤器并返回结果
        return super().eventFilter(obj, event)

    def _relayout(self) -> None:
        """重新布局内部标签在按钮中的位置。

        调整标签大小，然后将其移动到按钮内水平靠右、垂直靠上的位置，并确保标签在按钮内部且不被遮挡。

        Args:
            self: 实例对象自身，包含 button 和 label 属性。

        Returns:
            None: 该方法不返回任何值。
        """
        self.label.adjustSize()  # 调整标签大小以适应其文本内容
        x = max(2, self.button.width() - self.label.width() - 4)  # 计算x坐标，确保标签左边缘至少距按钮左边缘2像素，且右边缘距按钮右边缘4像素
        y = 1  # 设置垂直起始位置，距按钮顶部1像素
        self.label.move(x, y)  # 将标签移动到计算得到的位置
        self.label.raise_()  # 将标签置于按钮所有子部件的最上层，防止被其他部件遮挡


def _install_row_function_shortcuts(parent: QWidget, buttons: list[QPushButton], *, start_f: int = 3) -> None:
    """功能：为指定的按钮列表安装功能键（F3到F12）快捷键，并在按钮上显示快捷键标记。
参数：
    parent (QWidget): 父控件，用于快捷键的上下文。
    buttons (list[QPushButton]): 需要安装快捷键的按钮列表。
    start_f (int, 可选): 起始的功能键编号，默认为3。
返回值：None，函数会修改parent控件，添加 _function_key_shortcuts 和 _function_key_markers 属性。
    """
    hotkeys: list[QShortcut] = []  # 存储所有快捷键对象
    markers: list[_ButtonHotkeyMarker] = []  # 存储所有按钮标记对象
    for idx, button in enumerate(buttons):  # 遍历按钮列表，idx为索引，button为按钮对象
        fn = start_f + idx  # 计算当前功能键编号，从start_f开始递增
        if fn > 12:  # 功能键编号最大为12（F12），超出则停止
            break
        key_text = f"F{fn}"  # 生成快捷键文本，如"F3"
        shortcut = QShortcut(QKeySequence(key_text), parent)  # 创建快捷键对象，绑定到父控件
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)  # 设置快捷键上下文，包含子控件
        # 连接快捷键激活信号到lambda函数，该函数在按钮启用且可见时执行点击
        shortcut.activated.connect(lambda btn=button: btn.click() if btn.isEnabled() and btn.isVisible() else None)
        hotkeys.append(shortcut)  # 将快捷键添加到列表

        # 创建标签显示快捷键文本，附加到按钮上
        marker = QLabel(key_text, button)
        marker.setStyleSheet("font-size:10px;color:#496383;background:transparent;padding:0;")  # 设置标签样式
        marker.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)  # 设置标签对鼠标事件透明，不干扰按钮点击
        markers.append(_ButtonHotkeyMarker(button, marker))  # 创建标记对象并添加到列表

    # 将快捷键和标记存储到父控件的属性中，以便外部访问
    parent._function_key_shortcuts = hotkeys
    parent._function_key_markers = markers


class _InlineClearButtonLayout(QObject):
    def __init__(self, line_edit: QLineEdit, button: QToolButton):
        """
        初始化 QSearchEdit 对象。

        参数:
            line_edit (QLineEdit): 关联的文本编辑控件实例。
            button (QToolButton): 关联的工具按钮控件实例。

        返回值:
            无返回值。
        """
        super().__init__(line_edit)  # 调用父类的初始化方法，并传入 line_edit 作为参数
        self.line_edit = line_edit  # 将传入的 QLineEdit 实例保存为对象的属性
        self.button = button  # 将传入的 QToolButton 实例保存为对象的属性
        self.line_edit.installEventFilter(self)  # 为 line_edit 安装事件过滤器，使得当前对象能拦截并处理其事件
        self._relayout()  # 执行布局相关的初始化操作

    def eventFilter(self, obj, event) -> bool:
        """
        事件过滤器方法，用于监听和处理特定UI事件，当事件发生在行编辑控件上且类型为调整大小、显示、移动或布局请求时，触发布局重新计算。

        参数:
            self (object): 实例对象。
            obj (QObject): 接收事件的对象。
            event (QEvent): 发生的事件对象。

        返回:
            bool: 事件是否被过滤（始终调用父类方法，返回其结果）。
        """
        # 检查事件对象是否为行编辑控件且事件类型为调整大小、显示、移动或布局请求
        if obj is self.line_edit and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Move,
            QEvent.Type.LayoutRequest,
        }:
            self._relayout()  # 触发布局重新计算
        return super().eventFilter(obj, event)

    def _relayout(self) -> None:
        """
        重新布局当前行编辑控件内部的按钮位置。

        此方法用于根据行编辑控件的当前尺寸，动态调整其内部按钮的位置，
        以确保按钮始终位于合适的位置（例如靠近行编辑控件的右侧并垂直居中）。

        参数：
            无（除了 self）。

        返回值：
            无（None）。
        """
        # 调整按钮大小以适合其内容
        self.button.adjustSize()

        # 计算按钮的 x 坐标：确保按钮不超出左边界（最小为2像素），并紧贴行编辑控件右侧（减去按钮宽度和4像素的边距）
        x = max(2, self.line_edit.width() - self.button.width() - 4)

        # 计算按钮的 y 坐标：使按钮在行编辑控件内垂直居中
        y = max(0, (self.line_edit.height() - self.button.height()) // 2)

        # 将按钮移动到计算出的 (x, y) 位置
        self.button.move(x, y)

        # 将按钮提升至顶层，确保其不被其他控件遮挡
        self.button.raise_()


def _clear_line_edit_with_undo(line_edit: QLineEdit) -> None:
    """清除 QLineEdit 控件的文本内容，并支持撤销操作。

    参数:
        line_edit (QLineEdit): 需要清空的 QLineEdit 控件实例。

    返回:
        None
    """
    # 获取输入框的文本内容，并转换为字符串；如果文本为空则使用空字符串，以避免 None 值导致的潜在错误
    text = str(line_edit.text() or "")
    # 如果文本内容已经为空，则无需执行后续操作，直接返回
    if not text:
        return
    # 将焦点设置到该输入框，以便后续操作可以针对此控件进行
    line_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
    # 全选输入框中的所有文本
    line_edit.selectAll()
    # 删除当前选中的文本（即全选的所有文本），此操作会保留在撤销栈中，允许用户撤销
    line_edit.backspace()


def _install_inline_clear_button(line_edit: QLineEdit, *, on_cleared=None) -> None:
    """为 QLineEdit 控件安装内联清除按钮。

    Args:
        line_edit: 需要添加清除按钮的 QLineEdit 控件。
        on_cleared: 可选的回调函数，在清除操作完成后调用，接受无参数。

    Returns:
        None: 该函数不返回任何值。
    """
    # 如果 line_edit 已经安装过清除按钮，则直接返回，避免重复安装
    if hasattr(line_edit, "_inline_clear_btn"):
        return

    # 创建工具按钮，将其设置为 line_edit 的子控件
    button = QToolButton(line_edit)
    # 设置按钮显示的文本为乘号（×）
    button.setText("×")
    # 设置鼠标悬停时的光标样式为箭头
    button.setCursor(Qt.CursorShape.ArrowCursor)
    # 设置按钮不接收键盘焦点，避免干扰输入框的焦点
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    # 设置按钮的样式表，包括基础样式和悬停时的样式
    button.setStyleSheet(
        "QToolButton{border:none;background:transparent;color:#c62828;font-weight:700;font-size:14px;padding:0 2px;}"
        "QToolButton:hover{color:#8e1d1d;}"
    )
    # 设置按钮的工具提示为“清空”
    button.setToolTip("清空")
    # 初始时隐藏按钮，直到输入框有内容时才显示
    button.hide()
    # 获取 line_edit 原始的样式表，如果为空则使用空字符串
    original_style = str(line_edit.styleSheet() or "")
    # 向 line_edit 的样式表追加样式，确保右侧为按钮留出空间
    line_edit.setStyleSheet(original_style + " QLineEdit{padding-right:24px;}")

    # 定义内部函数用于执行清除操作
    def _clear() -> None:
        # 调用 _clear_line_edit_with_undo 函数清除输入框内容，并支持撤销
        _clear_line_edit_with_undo(line_edit)
        # 如果 on_cleared 是可调用的函数，则调用它
        if callable(on_cleared):
            on_cleared()

    # 将按钮的点击信号连接到 _clear 函数
    button.clicked.connect(_clear)
    # 将输入框的文本变化信号连接到 lambda 表达式，控制按钮的可见性
    line_edit.textChanged.connect(lambda text: button.setVisible(bool(str(text))))

    # 将创建的清除按钮实例附加到 line_edit 对象上，便于后续访问
    line_edit._inline_clear_btn = button
    # 创建并附加自定义的布局管理器，用于在 line_edit 内定位清除按钮
    line_edit._inline_clear_layout = _InlineClearButtonLayout(line_edit, button)


def _ask_export_format(parent: QWidget, anchor: QWidget) -> tuple[str, bool]:
    """显示导出格式选择菜单，并返回用户选择的格式和操作标志。

    参数:
        parent (QWidget): 菜单的父窗口部件，用于菜单的生命周期管理。
        anchor (QWidget): 菜单的锚点部件，菜单将显示在其下方。

    返回:
        tuple[str, bool]: 一个元组，包含两个元素：
            - 第一个元素是格式字符串（如"mp3", "flac", "opus", "original", "__plan__"）。
              如果用户取消选择，返回空字符串""。
            - 第二个元素是布尔值，表示用户是否做出了有效选择（True为有效选择，False为取消）。
    """
    menu = QMenu(parent) # 创建弹出式菜单，parent为父窗口部件
    action_original = menu.addAction("原格式") # 添加“保持原格式”的菜单项
    action_plan = menu.addAction("逐首配置...") # 添加“为每首歌单独配置”的菜单项
    menu.addSeparator() # 在菜单中添加分隔线
    action_mp3 = menu.addAction("mp3") # 添加MP3格式选项
    action_flac = menu.addAction("flac") # 添加FLAC格式选项
    action_opus = menu.addAction("opus") # 添加OPUS格式选项
    # 在anchor部件下方显示菜单，并等待用户选择，获取用户选择的菜单项
    chosen = menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
    if chosen == action_original: # 如果用户选择了“原格式”
        return "original", True
    if chosen == action_plan: # 如果用户选择了“逐首配置...”
        return "__plan__", True
    if chosen == action_mp3: # 如果用户选择了“mp3”
        return "mp3", True
    if chosen == action_flac: # 如果用户选择了“flac”
        return "flac", True
    if chosen == action_opus: # 如果用户选择了“opus”
        return "opus", True
    return "", False # 用户未选择或取消了菜单，返回空字符串和False


def _next_sort_state(state: str) -> str:
    """根据当前排序状态返回下一个排序状态。

    参数：
        state (str): 当前的排序状态，可以是"asc"、"desc"或"off"。

    返回：
        str: 下一个排序状态。
    """
    if state == "asc":  # 如果当前状态是升序，则下一个状态为降序
        return "desc"
    if state == "desc":  # 如果当前状态是降序，则下一个状态为关闭
        return "off"
    return "asc"  # 对于其他情况（如无效状态或初始状态），默认返回升序


def _safe_int(value, default: int = 0) -> int:
    """安全地将值转换为整数，若转换失败则返回指定的默认值。

    参数:
        value: 待转换的值，可以是任意类型。
        default (int): 转换失败时返回的默认值，默认为 0。

    返回:
        int: 转换成功后的整数；若 value 为不可转换的类型或转换失败，则返回 default。
    """
    # 如果 value 是列表、元组、集合或字典，则无法转换为整数，直接返回默认值
    if isinstance(value, (list, tuple, set, dict)):
        return default
    try:
        # 尝试将 value 转换为整数，若 value 为 None 或空值，则使用 0 进行转换
        return int(value or 0)
    except Exception:
        # 若转换过程中发生任何异常（如类型不匹配、格式错误等），返回默认值
        return default


def _show_track_details(parent: QWidget, track: dict) -> None:
    """在父窗口中弹出一个对话框，显示给定轨道的详细信息。

    Args:
        parent (QWidget): 用于指定消息框父窗口的部件。
        track (dict): 包含轨道信息的字典，键包括'track_id', 'file_name', 'title'等。

    Returns:
        None: 此函数无返回值，仅用于显示UI消息框。
    """
    # 构建显示信息的行列表，使用.get()方法安全地获取字典值，避免KeyError
    lines = [
        f"Track ID: {track.get('track_id', '')}",
        f"文件名: {track.get('file_name', '')}",
        f"标题: {track.get('title', '')}",
        f"艺术家: {track.get('artist', '')}",
        f"专辑: {track.get('album', '')}",
        f"语言: {track.get('language_kind', '')}",
        f"喜好: {track.get('preference_level', '')}",
        f"Source: {track.get('source_fullpath', '')}",
        f"Storage: {track.get('storage_relpath', '')}",
    ]
    # 调用标准信息框显示所有拼接好的信息行
    QMessageBox.information(parent, "详情（待设计）", "\n".join(lines))


def _storage_path_for_track_row(facade: MuseArcFacade, row: dict) -> str:
    """
    返回轨迹行的存储路径。

    参数:
    facade (MuseArcFacade): 提供library_root属性的对象，表示库的根路径。
    row (dict): 包含存储路径和源路径的字典。

    返回:
    str: 存储路径的字符串表示。
    """
    # 从字典中获取存储相对路径，并转换为字符串，去除空白字符；如果不存在或为空，则使用空字符串
    rel = str(row.get("storage_relpath", "") or "").strip()
    if rel:  # 检查是否有有效的相对路径
        # 基于库根路径和相对路径构建完整存储路径
        return str(Path(facade.library_root) / rel)
    # 如果没有相对路径，则从字典中获取源完整路径并返回
    return str(row.get("source_fullpath", "") or "").strip()


def _reveal_in_file_manager(parent: QWidget, path_text: str) -> None:
    """
    功能：在文件管理器（如Windows资源管理器）中定位指定路径的文件或目录，若路径无效则显示提示或错误。
    参数：
        parent (QWidget): 父窗口部件，用于作为消息框的父组件。
        path_text (str): 要定位的路径文本，可能是文件或目录路径。
    返回值：None（无返回值）。
    """
    # 将输入路径文本转换为字符串，如果为空则使用空字符串，并去除首尾空格
    text = str(path_text or "").strip()
    # 如果路径文本为空，显示提示信息并直接返回
    if not text:
        QMessageBox.information(parent, "文件管理器", "当前项没有可定位的文件路径。")
        return
    # 将文本转换为Path对象，便于路径操作
    path = Path(text)
    # 初始化目标路径为原始路径
    target = path
    # 如果目标路径不存在，尝试定位到其父目录
    if not target.exists():
        parent_dir = target.parent  # 获取父目录路径
        # 如果父目录存在，则将目标路径更新为父目录
        if parent_dir.exists():
            target = parent_dir
    # 尝试执行文件管理器操作，处理可能的异常
    try:
        # 如果目标路径是文件，使用explorer打开并选中该文件
        if target.is_file():
            subprocess.Popen(["explorer", "/select,", str(target)])
        # 否则（目标路径是目录），使用explorer打开该目录
        else:
            subprocess.Popen(["explorer", str(target)])
    # 捕获所有异常，并显示错误信息
    except Exception as exc:
        QMessageBox.critical(parent, "文件管理器", str(exc))


def _ask_delete_tracks_with_lyrics(parent: QWidget, count: int, default_mode: str) -> tuple[str, bool]:
    """
    弹出对话框询问用户如何删除带歌词的轨道。

    参数:
        parent (QWidget): 父窗口部件。
        count (int): 要删除的轨道数量。
        default_mode (str): 默认删除模式，可以是"unlink_only"或其他。

    返回:
        tuple[str, bool]: 第一个元素是用户选择的操作模式（如"move_linked_lyrics"、"unlink_only"或"cancel"），第二个元素是布尔值，表示是否记住选择。
    """
    default_is_move = default_mode != "unlink_only"  # 判断默认模式是否为移动（即不是仅删除）
    box = QMessageBox(parent)
    box.setWindowTitle("从音乐库中删除")
    box.setText(f"确定将 {count} 条移到回收站吗？")
    move_btn = box.addButton("绑定歌词一起移动到回收站", QMessageBox.ButtonRole.AcceptRole)
    unlink_btn = box.addButton("仅删除歌曲并解开映射关系", QMessageBox.ButtonRole.DestructiveRole)
    cancel_btn = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    remember = QCheckBox("设为默认")
    box.setCheckBox(remember)
    box.setDefaultButton(move_btn if default_is_move else unlink_btn)  # 根据默认模式设置默认按钮
    box.exec()
    clicked = box.clickedButton()
    if clicked == move_btn:
        return "move_linked_lyrics", bool(remember.isChecked())  # 用户选择移动，返回相应模式和记住设置
    if clicked == unlink_btn:
        return "unlink_only", bool(remember.isChecked())  # 用户选择仅删除
    if clicked == cancel_btn:
        return "cancel", False  # 用户取消
    return "cancel", False  # 默认返回取消


def _resolve_delete_mode_and_maybe_save_default(
    parent: QWidget,
    facade: MuseArcFacade,
    count: int,
    track_ids: list[str] | None = None,
) -> str:
    """
    解析删除模式并可能保存默认设置。

    参数:
        parent (QWidget): 父组件窗口。
        facade (MuseArcFacade): 外观对象，用于获取配置和检查链接歌词。
        count (int): 要删除的曲目数量。
        track_ids (list[str] | None, optional): 曲目ID列表，默认为None。

    返回:
        str: 确定的删除模式，可能是 "move_linked_lyrics" 或 "unlink_only"。
    """
    cfg = facade.get_runtime_config()
    # 获取配置中的默认删除模式，如果为空则使用 "move_linked_lyrics" 作为默认值
    default_mode = str(cfg.ui.delete_tracks_mode_default or "move_linked_lyrics")
    # 验证默认模式是否在有效集合 {"move_linked_lyrics", "unlink_only"} 中，否则回退到 "move_linked_lyrics"
    valid_default = default_mode if default_mode in {"move_linked_lyrics", "unlink_only"} else "move_linked_lyrics"

    # 将 track_ids 转换为字符串列表，并过滤掉空或仅含空白字符的值
    ids = [str(v) for v in (track_ids or []) if str(v).strip()]
    if ids:
        try:
            # 尝试检查这些曲目是否有链接歌词，将结果转换为布尔值
            has_linked = bool(facade.has_linked_lyrics_for_tracks(ids))
        except Exception:
            # 如果检查过程中发生异常，则假设有链接歌词以确保安全
            has_linked = True
        if not has_linked:
            # 没有绑定歌词时不弹“歌词处理”确认框，直接沿用默认行为。
            return valid_default

    # 调用内部函数询问用户删除模式及是否记住选择
    mode, remember = _ask_delete_tracks_with_lyrics(parent, count, valid_default)
    # 如果用户选择记住且模式有效，则保存到运行时配置中
    if remember and mode in {"move_linked_lyrics", "unlink_only"}:
        cfg.ui.delete_tracks_mode_default = mode
        save_runtime_config(cfg)
    return mode


def _history_action_label(action_type: str) -> str:
    """
    功能：根据传入的动作类型字符串，返回对应的中文标签。
    参数：action_type (str) - 动作类型的字符串标识符。
    返回值：str - 映射后的中文标签，若未找到则返回原始字符串。
    """
    # 定义动作类型到中文标签的映射字典
    mapping = {
        "soft_delete_tracks": "移到回收站",
        "restore_tracks": "恢复歌曲",
        "update_tracks_fields": "编辑字段",
        "update_lyrics_fields": "编辑歌词字段",
        "set_primary_lyrics_for_track": "修改歌曲歌词映射",
        "set_primary_track_for_lyrics": "修改歌词歌曲映射",
        "merge_lyrics_for_review": "合并歌词审查项",
        "resolve_reviews": "处理审查项",
        "delete_lyrics": "删除歌词",
        "restore_lyrics": "恢复歌词",
        "create_playlist": "新建歌单",
        "delete_playlist": "删除歌单",
        "add_tracks_to_playlist": "加到歌单",
        "remove_tracks_from_playlist": "从歌单移除",
        "clear_playlist": "清空歌单",
        "reorder_playlist": "重排歌单",
        "update_playlist_entries": "修改自定义排序",
        "create_fullscan_work": "新建全量筛选工作",
    }
    # 使用字典的get方法查找键，若未找到则返回原始字符串作为默认值
    return mapping.get(action_type, action_type)


def _choose_or_create_playlist(
    parent: QWidget,
    facade: MuseArcFacade,
    anchor: QWidget,
    *,
    exclude_ids: set[str] | None = None,
    allow_create: bool = True,
) -> str | None:
    """显示播放列表选择菜单，允许用户选择现有播放列表或新建播放列表。

    参数:
        parent (QWidget): 父窗口部件，用于设置菜单的父子关系
        facade (MuseArcFacade): 数据门面对象，用于获取播放列表数据
        anchor (QWidget): 菜单的锚点部件，菜单将出现在该部件的左下角
        exclude_ids (set[str] | None): 需要排除的播放列表ID集合，默认为None
        allow_create (bool): 是否允许创建新播放列表，默认为True

    返回:
        str | None: 选择的播放列表ID，如果取消选择或没有有效选择则返回None
    """
    exclude = exclude_ids or set()
    playlists = [p for p in facade.list_playlists() if str(p.get("playlist_id", "")) not in exclude]
    menu = QMenu(parent)
    action_map: dict[QAction, str] = {}  # 存储菜单动作与播放列表ID的映射关系
    for row in playlists:
        playlist_id = str(row.get("playlist_id", ""))
        title = str(row.get("name", ""))
        action_map[menu.addAction(title)] = playlist_id  # 将动作与ID关联

    action_new = None
    if allow_create:
        if playlists:
            menu.addSeparator()  # 在已有播放列表和新建选项之间添加分隔线
        action_new = menu.addAction("新建歌单...")  # 添加新建播放列表的选项

    chosen = menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))  # 在锚点左下角显示菜单
    if not chosen:
        return None  # 用户取消选择
    if action_new is not None and chosen == action_new:
        return _prompt_new_playlist(parent, facade)  # 用户选择新建播放列表
    return action_map.get(chosen)  # 返回选择的播放列表ID


def _prompt_new_playlist(parent: QWidget, facade: MuseArcFacade, *, title: str = "新建歌单") -> str | None:
    """提示用户输入新歌单名称并创建歌单。

    Args:
        parent (QWidget): 父窗口部件，用于显示输入对话框。
        facade (MuseArcFacade): 提供歌单创建功能的门面对象。
        title (str, optional): 输入对话框的标题。默认为"新建歌单"。

    Returns:
        str | None: 成功创建时返回新歌单名称，否则返回None。
    """
    name, ok = QInputDialog.getText(parent, title, "歌单名称")  # 显示输入对话框获取歌单名称
    if not ok or not name.strip():  # 用户点击取消或输入内容为空
        return None  # 返回None表示未创建歌单
    return facade.create_playlist(name.strip())  # 创建并返回歌单名称


class TrackPickerDialog(QDialog):
    def __init__(self, parent: QWidget, facade: MuseArcFacade, *, allow_clear: bool = True):
        """初始化歌曲选择对话框。

        参数:
            parent: QWidget, 父部件。
            facade: MuseArcFacade, 门面对象。
            allow_clear: bool, 是否允许清空映射，默认为True。

        返回值:
            None
        """
        super().__init__(parent)  # 调用父类QWidget的构造函数
        self.facade = facade  # 存储门面对象引用
        self.setWindowTitle("选择歌曲")  # 设置对话框标题
        self.resize(980, 620)  # 设置对话框初始大小
        self.selected_track_id: str | None = None  # 初始化选中的歌曲ID为None

        root = QVBoxLayout(self)  # 创建垂直主布局
        top = QHBoxLayout()  # 创建水平顶部布局用于搜索部件
        self.search_input = QLineEdit()  # 创建搜索输入框
        self.search_input.setPlaceholderText("搜索 标题/艺术家/专辑/文件名")  # 设置输入框提示文本
        self.btn_search = QPushButton("搜索")  # 创建搜索按钮
        top.addWidget(self.search_input, 1)  # 将搜索输入框添加到顶部布局，拉伸因子为1
        top.addWidget(self.btn_search)  # 将搜索按钮添加到顶部布局

        self.model = DictTableModel(  # 创建表格数据模型，定义列结构
            [
                ColumnDef("file_name", "文件名"),  # 文件名列
                ColumnDef("title", "标题"),  # 标题列
                ColumnDef("artist", "艺术家"),  # 艺术家列
                ColumnDef("album", "专辑"),  # 专辑列
                ColumnDef("track_id", "数据库ID"),  # 数据库ID列
            ]
        )
        self.table = QTableView()  # 创建表格视图
        self.table.setModel(self.model)  # 将模型设置给表格视图
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)  # 设置选择行为为整行选择
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)  # 设置选择模式为单选
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)  # 禁用表格编辑功能
        self.table.setAlternatingRowColors(True)  # 启用行交替颜色
        self.table.setSortingEnabled(True)  # 启用列排序功能
        self.table.horizontalHeader().setStretchLastSection(True)  # 设置最后一列自动拉伸以填充空间

        self.buttons = QDialogButtonBox()  # 创建按钮框
        self.btn_ok = self.buttons.addButton("确定", QDialogButtonBox.ButtonRole.AcceptRole)  # 添加确定按钮
        self.btn_cancel = self.buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)  # 添加取消按钮
        self.btn_clear = self.buttons.addButton("清空映射", QDialogButtonBox.ButtonRole.DestructiveRole) if allow_clear else None  # 根据allow_clear参数条件添加清空映射按钮

        root.addLayout(top)  # 将顶部布局添加到主布局
        root.addWidget(self.table, 1)  # 将表格视图添加到主布局，拉伸因子为1
        root.addWidget(self.buttons)  # 将按钮框添加到主布局

        self._all_rows = self.facade.list_tracks(limit=200_000)  # 从门面对象加载所有歌曲数据，最多200,000条
        self._apply_filter()  # 初始应用过滤条件以显示数据

        self.btn_search.clicked.connect(self._apply_filter)  # 连接搜索按钮点击信号到过滤方法
        self.search_input.returnPressed.connect(self._apply_filter)  # 连接输入框回车信号到过滤方法
        self.table.doubleClicked.connect(lambda _idx: self._accept_current())  # 连接表格双击信号到接受当前选择方法
        self.btn_ok.clicked.connect(self._accept_current)  # 连接确定按钮点击信号到接受当前选择方法
        self.btn_cancel.clicked.connect(self.reject)  # 连接取消按钮点击信号到对话框拒绝方法
        if self.btn_clear is not None:  # 如果清空映射按钮存在
            self.btn_clear.clicked.connect(self._accept_clear)  # 连接清空映射按钮点击信号到清空映射方法

    def _apply_filter(self) -> None:
        token = self.search_input.text().strip().casefold()
        if not token:
            rows = list(self._all_rows)
        else:
            rows = []
            for row in self._all_rows:
                text = " | ".join(
                    [
                        str(row.get("file_name", "")),
                        str(row.get("title", "")),
                        str(row.get("artist", "")),
                        str(row.get("album", "")),
                    ]
                ).casefold()
                if token in text:
                    rows.append(row)
        self.model.set_rows(rows)

    def _accept_current(self) -> None:
        indexes = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not indexes:
            QMessageBox.warning(self, "选择歌曲", "请先选择一首歌曲。")
            return
        row = self.model.row_at(indexes[0].row())
        self.selected_track_id = str(row.get("track_id", "")) if row else None
        if not self.selected_track_id:
            QMessageBox.warning(self, "选择歌曲", "当前行没有有效 track_id。")
            return
        self.accept()

    def _accept_clear(self) -> None:
        """清除选定的轨道ID并调用accept方法，用于重置接受状态。"""
        # 将selected_track_id重置为None，表示无选定轨道
        self.selected_track_id = None
        # 调用accept方法以完成清除操作
        self.accept()


class LyricsPickerDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        facade: MuseArcFacade,
        *,
        initial_query: str = "",
        allow_clear: bool = True,
    ):
        super().__init__(parent)
        self.facade = facade
        self.selected_lyrics_id: str | None = None
        self.setWindowTitle("选择歌词映射")
        self.resize(1180, 700)

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索 歌词文件名/标题/艺术家/专辑/语言")
        self.btn_search = QPushButton("搜索")
        top.addWidget(self.search_input, 1)
        top.addWidget(self.btn_search)

        self.model = DictTableModel(
            [
                ColumnDef("file_name", "歌词文件名"),
                ColumnDef("lyrics_title", "歌曲标题"),
                ColumnDef("lyrics_artist", "艺术家"),
                ColumnDef("lyrics_album", "专辑"),
                ColumnDef("lyrics_language", "语言"),
                ColumnDef("line_count", "行数"),
                ColumnDef("mapped_track", "对应歌曲"),
                ColumnDef("lyrics_id", "歌词ID"),
            ]
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)

        self.preview = QTreeWidget()
        self.preview.setHeaderLabels(["歌词预览"])
        self.preview.setRootIsDecorated(False)
        self.preview.setAlternatingRowColors(True)

        split = QHBoxLayout()
        split.addWidget(self.table, 3)
        split.addWidget(self.preview, 2)

        self.buttons = QDialogButtonBox()
        self.btn_ok = self.buttons.addButton("确定", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_cancel = self.buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_clear = self.buttons.addButton("清空映射", QDialogButtonBox.ButtonRole.DestructiveRole) if allow_clear else None

        root.addLayout(top)
        root.addLayout(split, 1)
        root.addWidget(self.buttons)

        rows = self.facade.list_lyrics(limit=300_000)
        self._all_rows: list[dict] = []
        for row in rows:
            item = dict(row)
            item["lyrics_language"] = str(item.get("lyrics_language", "") or "unknown")
            self._all_rows.append(item)
        if str(initial_query).strip():
            self.search_input.setText(str(initial_query).strip())
        self._apply_filter()

        self.btn_search.clicked.connect(self._apply_filter)
        self.search_input.returnPressed.connect(self._apply_filter)
        self.table.doubleClicked.connect(lambda _idx: self._accept_current())
        if self.table.selectionModel() is not None:
            self.table.selectionModel().selectionChanged.connect(lambda *_args: self._refresh_preview())
        self.btn_ok.clicked.connect(self._accept_current)
        self.btn_cancel.clicked.connect(self.reject)
        if self.btn_clear is not None:
            self.btn_clear.clicked.connect(self._accept_clear)

    def _apply_filter(self) -> None:
        token = self.search_input.text().strip().casefold()
        if not token:
            rows = list(self._all_rows)
        else:
            rows = []
            for row in self._all_rows:
                text = " | ".join(
                    [
                        str(row.get("file_name", "")),
                        str(row.get("lyrics_title", "")),
                        str(row.get("lyrics_artist", "")),
                        str(row.get("lyrics_album", "")),
                        str(row.get("lyrics_language", "")),
                        str(row.get("mapped_track", "")),
                    ]
                ).casefold()
                if token in text:
                    rows.append(row)
        self.model.set_rows(rows)
        self._refresh_preview()

    def _current_row(self) -> dict | None:
        """获取表格中当前选中行的数据。

        此私有方法通过视图的选择模型获取用户选中的行，并返回该行对应的模型数据。
        如果没有任何行被选中，则返回 None。

        Args:
            self: 实例自身。

        Returns:
            dict | None: 一个包含选中行数据的字典。如果没有行被选中，则返回 None。
        """
        sm = self.table.selectionModel()  # 获取表格视图的选择模型
        selected = sm.selectedRows() if sm is not None else []  # 如果选择模型存在，则获取所有被选中的行索引；否则设为空列表
        if not selected:  # 如果没有选中任何行
            return None  # 返回 None
        return self.model.row_at(selected[0].row())  # 通过数据模型获取第一个（或唯一一个）选中行的数据并返回

    def _refresh_preview(self) -> None:
        self.preview.clear()
        row = self._current_row()
        if not row:
            return
        rel = str(row.get("storage_relpath", "") or "").strip()
        if not rel:
            self.preview.addTopLevelItem(QTreeWidgetItem(["（无可预览内容）"]))
            return
        target = Path(self.facade.library_root) / rel
        try:
            text = target.read_text(encoding="utf-8")
        except Exception as exc:
            text = f"无法读取歌词: {exc}"
        lines = text.splitlines()[:200]
        if not lines:
            lines = ["（空）"]
        for line in lines:
            self.preview.addTopLevelItem(QTreeWidgetItem([str(line)]))

    def _accept_current(self) -> None:
        """接受当前选择的歌词。

        功能：处理用户选择歌词的操作，验证选择的有效性后完成对话框。
        参数：无（除了self）
        返回值：None（但会通过accept()关闭对话框）
        """
        # 获取当前选中的歌词行数据
        row = self._current_row()
        # 如果没有选中任何行，显示警告并退出
        if not row:
            QMessageBox.warning(self, "选择歌词", "请先选择一条歌词。")
            return
        # 从行数据中提取lyrics_id，转换为字符串并确保非空
        lyrics_id = str(row.get("lyrics_id", "") or "")
        # 如果lyrics_id无效，显示警告并退出
        if not lyrics_id:
            QMessageBox.warning(self, "选择歌词", "当前行没有有效 lyrics_id。")
            return
        # 将有效的lyrics_id保存到实例属性中
        self.selected_lyrics_id = lyrics_id
        # 关闭对话框并返回接受状态
        self.accept()

    def _accept_clear(self) -> None:
        self.selected_lyrics_id = None
        self.accept()


def _resolve_lyrics_cell_default_action(facade: MuseArcFacade) -> str:
    """获取并解析UI配置中‘歌词单元格’的默认交互动作。

    该函数从运行时配置中读取 UI 相关的设置，特别是歌词单元格的默认行为。
    它确保返回一个有效的动作字符串，如果配置值无效或未设置，则回退到默认值。

    Args:
        facade (MuseArcFacade): 用于访问应用程序配置和状态的门面对象。

    Returns:
        str: 有效的默认动作字符串。预期值为 "change_mapping" 或 "jump_to_lyrics"。
    """
    # 从门面对象获取当前的运行时配置。
    cfg = facade.get_runtime_config()
    # 安全地获取配置属性 `cfg.ui.lyrics_cell_action_default`。
    # 如果该属性不存在或为 None/False，则使用字符串 "change_mapping" 作为备选值。
    # `or` 运算符处理了 None 或空字符串等情况，确保后续 `str()` 总是得到一个有效字符串。
    value = str(getattr(cfg.ui, "lyrics_cell_action_default", "change_mapping") or "change_mapping")
    # 验证获取到的值是否是预定义的有效选项集合中的成员。
    if value not in {"change_mapping", "jump_to_lyrics"}:
        # 如果值无效，则强制返回默认的 "change_mapping" 动作，保证程序行为的确定性。
        return "change_mapping"
    # 返回经过验证的有效配置值。
    return value


def _jump_to_lyrics_page(parent: QWidget, lyrics_id: str) -> bool:
    """
    跳转到歌词页面，并将焦点设置到指定的歌词ID。

    参数:
        parent (QWidget): 父QWidget对象，用于获取顶层窗口。
        lyrics_id (str): 歌词ID字符串。

    返回值:
        bool: 如果成功跳转并聚焦，返回True；否则返回False。
    """
    target_id = str(lyrics_id or "").strip()  # 将歌词ID转换为字符串，并去除首尾空白字符
    if not target_id:  # 如果歌词ID为空，则返回失败
        return False
    top = parent.window()  # 获取父窗口的顶层窗口对象
    page = getattr(top, "page_lyrics", None)  # 获取歌词页面对象，如果不存在则设为None
    sidebar = getattr(top, "sidebar", None)  # 获取侧边栏对象，如果不存在则设为None
    if page is None or sidebar is None:  # 如果歌词页面或侧边栏不存在，则返回失败
        return False
    try:
        sidebar.setCurrentRow(6)  # 尝试将侧边栏的当前行设置为6（可能是歌词页面的索引）
    except Exception:  # 如果设置过程中出现异常，则返回失败
        return False
    focus_fn = getattr(page, "focus_lyrics_id", None)  # 获取聚焦歌词ID的函数，如果不存在则为None
    if not callable(focus_fn):  # 如果聚焦函数不存在或不可调用，则返回失败
        return False
    return bool(focus_fn(target_id))  # 调用聚焦函数并返回其布尔结果


def _change_track_lyrics_mapping(parent: QWidget, facade: MuseArcFacade, tracks: list[dict]) -> bool:
    """批量修改音轨与歌词的映射关系。

    参数:
        parent (QWidget): 父窗口组件，用于弹出对话框。
        facade (MuseArcFacade): 应用门面对象，提供数据访问接口。
        tracks (list[dict]): 包含音轨信息的字典列表，每个字典至少需要有 'track_id' 键。

    返回:
        bool: 操作是否成功完成。如果用户取消选择或没有有效音轨则返回 False，否则返回 True。
    """
    # 筛选有效音轨：只保留 track_id 不为空的记录，并创建字典副本避免修改原始数据
    valid_rows = [dict(r) for r in tracks if str((r or {}).get("track_id", "")).strip()]
    if not valid_rows:  # 如果没有有效音轨，直接返回失败
        return False

    # 获取第一个音轨的信息，用于对话框的初始搜索词
    first = valid_rows[0]
    query = str(first.get("title", "") or first.get("file_name", "") or "").strip()  # 优先使用标题，其次文件名

    # 打开歌词选择对话框，设置初始搜索词并允许清除选项
    dialog = LyricsPickerDialog(parent, facade, initial_query=query, allow_clear=True)
    if dialog.exec() != QDialog.DialogCode.Accepted:  # 用户取消选择则返回失败
        return False

    selected_lyrics_id = dialog.selected_lyrics_id  # 获取用户选择的歌词ID

    # 遍历所有有效音轨，设置其主歌词为选中的歌词
    for row in valid_rows:
        track_id = str(row.get("track_id", "") or "").strip()
        if not track_id:  # 再次检查音轨ID，防止意外
            continue
        facade.set_primary_lyrics_for_track(track_id, selected_lyrics_id)  # 调用门面方法更新映射

    return True  # 全部操作成功完成


def _handle_track_lyrics_cell_action(
    parent: QWidget,
    facade: MuseArcFacade,
    tracks: list[dict],
    *,
    action: str | None = None,
) -> bool:
    """处理歌词单元格（通常是歌曲列表中的某一行）的点击或其他交互操作。

    根据指定的 `action` 或默认逻辑，决定是跳转到对应的歌词详情页，
    还是修改所选歌曲的歌词映射关系。

    Args:
        parent (QWidget): 发起此操作的父级窗口或部件，用于创建模态对话框。
        facade (MuseArcFacade): 应用程序的外观层门面对象，用于访问业务逻辑。
        tracks (list[dict]): 与当前操作相关的歌曲数据列表，每个元素是一个包含歌曲信息的字典。
        action (str | None, optional): 指定要执行的操作类型。如果为 None，则使用默认解析逻辑。

    Returns:
        bool: 操作是否成功执行。如果过滤后无有效歌曲，或跳转歌词时歌词不存在，则返回 False。
    """
    # 使用列表推导式，过滤出 `track_id` 字段存在且不为空的歌曲行，并创建副本。
    valid_rows = [dict(r) for r in tracks if str((r or {}).get("track_id", "")).strip()]
    # 如果没有有效的歌曲数据，则无法进行任何操作。
    if not valid_rows:
        return False
    # 确定最终要执行的操作：如果调用时指定了 `action`，则使用它；否则，调用默认解析函数。
    resolved = str(action or _resolve_lyrics_cell_default_action(facade))
    # 如果解析出的操作是“跳转到歌词页面”。
    if resolved == "jump_to_lyrics":
        # 获取第一首有效歌曲的信息。
        first = valid_rows[0]
        # 尝试获取其绑定的歌词ID，并做字符串清理。
        lyrics_id = str(first.get("lyrics_id", "") or "").strip()
        # 如果没有找到有效的歌词ID，提示用户并结束。
        if not lyrics_id:
            QMessageBox.information(parent, "歌词映射", "该歌曲当前没有绑定歌词。")
            return False
        # 调用跳转函数，并传递结果。
        return _jump_to_lyrics_page(parent, lyrics_id)
    # 如果操作不是跳转（或未匹配到上述情况），则执行默认逻辑：修改歌曲的歌词映射。
    return _change_track_lyrics_mapping(parent, facade, valid_rows)


class ExportPlanDialog(QDialog):
    def __init__(self, parent: QWidget, tracks: list[dict]):
        """初始化一个对话框，用于逐首导出格式。

        参数：
            parent (QWidget): 父窗口。
            tracks (list[dict]): 歌曲列表，每个字典应包含 'track_id', 'artist', 'title', 'file_name' 等键。

        返回值：
            无。
        """
        super().__init__(parent)
        self.setWindowTitle("逐首导出格式")
        self.resize(860, 560)
        self._combo_by_track_id: dict[str, QComboBox] = {}  # 存储track_id与下拉框的映射关系

        root = QVBoxLayout(self)  # 创建垂直布局作为根布局
        row_set = QHBoxLayout()  # 创建水平布局用于放置批量设置按钮
        self.btn_all_original = QPushButton("全部原格式")  # 创建按钮：设置全部为原格式
        self.btn_all_mp3 = QPushButton("全部 mp3")  # 创建按钮：设置全部为MP3格式
        self.btn_all_flac = QPushButton("全部 flac")  # 创建按钮：设置全部为FLAC格式
        self.btn_all_opus = QPushButton("全部 opus")  # 创建按钮：设置全部为Opus格式
        row_set.addWidget(self.btn_all_original)
        row_set.addWidget(self.btn_all_mp3)
        row_set.addWidget(self.btn_all_flac)
        row_set.addWidget(self.btn_all_opus)
        row_set.addStretch(1)  # 添加弹性空间，使按钮左对齐

        self.tree = QTreeWidget()  # 创建树形控件用于显示歌曲列表
        self.tree.setHeaderLabels(["歌曲", "导出格式", "track_id"])  # 设置表头标签
        self.tree.setAlternatingRowColors(True)  # 启用交替行颜色
        self.tree.setRootIsDecorated(False)  # 隐藏根节点装饰

        # 遍历歌曲列表，为每首歌创建树形控件项和下拉框
        for row in tracks:
            track_id = str(row.get("track_id", ""))  # 获取歌曲ID，若不存在则默认空字符串
            label = f"{row.get('artist', '')} - {row.get('title', '')} ({row.get('file_name', '')})"  # 格式化显示标签
            item = QTreeWidgetItem([label, "", track_id])  # 创建树形控件项，包含歌曲信息
            self.tree.addTopLevelItem(item)  # 添加为顶级项
            combo = QComboBox()  # 创建下拉框用于选择导出格式
            combo.addItems(["original", "mp3", "flac", "opus"])  # 添加格式选项
            combo.setCurrentText("original")  # 设置默认选择为原格式
            self.tree.setItemWidget(item, 1, combo)  # 将下拉框设置到第二列
            if track_id:  # 如果track_id有效，则保存映射
                self._combo_by_track_id[track_id] = combo

        # 设置树形控件列的调整模式
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)  # 第一列拉伸填充
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # 第二列根据内容调整
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # 第三列根据内容调整

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)  # 创建对话框按钮框，包含确认和取消按钮

        root.addLayout(row_set)  # 将批量设置按钮布局添加到根布局
        root.addWidget(self.tree, 1)  # 将树形控件添加到根布局，并设置拉伸因子为1
        root.addWidget(self.buttons)  # 将按钮框添加到根布局

        # 连接批量设置按钮的点击信号到槽函数
        self.btn_all_original.clicked.connect(lambda: self._apply_all("original"))  # 点击后设置所有格式为原格式
        self.btn_all_mp3.clicked.connect(lambda: self._apply_all("mp3"))  # 点击后设置所有格式为MP3
        self.btn_all_flac.clicked.connect(lambda: self._apply_all("flac"))  # 点击后设置所有格式为FLAC
        self.btn_all_opus.clicked.connect(lambda: self._apply_all("opus"))  # 点击后设置所有格式为Opus
        self.buttons.accepted.connect(self.accept)  # 连接确认按钮信号到接受槽
        self.buttons.rejected.connect(self.reject)  # 连接取消按钮信号到拒绝槽

    def _apply_all(self, fmt: str) -> None:
        """功能：将指定的格式应用到所有相关的组合框。

        参数：
            fmt (str): 要设置的格式文本。

        返回值：
            无。
        """
        # 遍历所有组合框并设置文本格式
        for combo in self._combo_by_track_id.values():
            combo.setCurrentText(fmt)  # 设置组合框的文本为指定格式

    def export_plan(self) -> dict[str, str]:
        """导出计划，返回每个音轨ID对应的组合框文本字典。

        功能：从实例的_combo_by_track_id字典中提取每个音轨的当前选择文本，形成新的字典。
        参数：无额外参数，self表示当前实例。
        返回值：字典，键为音轨ID（字符串），值为组合框当前文本（字符串），默认为"original"。
        """
        out: dict[str, str] = {}  # 初始化一个空字典用于存储结果
        for track_id, combo in self._combo_by_track_id.items():  # 遍历音轨ID和对应的组合框
            # 获取组合框的当前文本，如果为空或None则使用"original"，并确保为字符串类型
            out[track_id] = str(combo.currentText() or "original")
        return out  # 返回包含所有音轨选择的字典


class ExportConfigDialog(QDialog):
    def __init__(self, parent: QWidget, tracks: list[dict], *, default_name: str = "playlist"):
        super().__init__(parent)
        self.setWindowTitle("导出配置")
        self.resize(980, 700)
        self._combo_by_track_id: dict[str, QComboBox] = {}
        self._tracks = list(tracks)
        self._default_name = default_name.strip() or "playlist"

        root = QVBoxLayout(self)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("导出路径"))
        self.path_input = QLineEdit()
        self.btn_browse = QPushButton("浏览...")
        path_row.addWidget(self.path_input, 1)
        path_row.addWidget(self.btn_browse)

        mode_row = QHBoxLayout()
        self.chk_files = QCheckBox("导出为多个音频文件")
        self.chk_playlist = QCheckBox("导出为歌单清单(JSON)")
        self.chk_export_lyrics = QCheckBox("同步导出绑定歌词（同名 .lrc）")
        self.chk_files.setChecked(True)
        self.chk_playlist.setChecked(True)
        self.chk_export_lyrics.setChecked(True)
        mode_row.addWidget(self.chk_files)
        mode_row.addWidget(self.chk_playlist)
        mode_row.addWidget(self.chk_export_lyrics)
        mode_row.addStretch(1)

        self.playlist_hint = QLabel("歌单清单将包含数据库路径、歌词路径、统计占位字段与歌单唯一哈希。")
        self.playlist_hint.setStyleSheet("color:#5d6f86;")
        self.playlist_hint.setVisible(False)

        row_set = QHBoxLayout()
        self.btn_all_original = QPushButton("整列设为源格式")
        self.btn_all_mp3 = QPushButton("整列设为mp3")
        self.btn_all_opus = QPushButton("整列设为opus")
        self.btn_all_flac = QPushButton("整列设为flac")
        self.btn_all_wav = QPushButton("整列设为wav")
        self.btn_all_ogg = QPushButton("整列设为ogg")
        for btn in [
            self.btn_all_original,
            self.btn_all_mp3,
            self.btn_all_opus,
            self.btn_all_flac,
            self.btn_all_wav,
            self.btn_all_ogg,
        ]:
            row_set.addWidget(btn)
        row_set.addStretch(1)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["歌曲", "导出格式", "track_id"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(False)
        for row in self._tracks:
            track_id = str(row.get("track_id", "") or "")
            label = f"{row.get('artist', '')} - {row.get('title', '')} ({row.get('file_name', '')})"
            item = QTreeWidgetItem([label, "", track_id])
            self.tree.addTopLevelItem(item)
            combo = QComboBox()
            combo.addItems(["源格式", "mp3", "opus", "flac", "wav", "ogg"])
            combo.setCurrentText("源格式")
            self.tree.setItemWidget(item, 1, combo)
            if track_id:
                self._combo_by_track_id[track_id] = combo
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)

        root.addLayout(path_row)
        root.addLayout(mode_row)
        root.addWidget(self.playlist_hint)
        root.addLayout(row_set)
        root.addWidget(self.tree, 1)
        root.addWidget(self.buttons)

        self.btn_browse.clicked.connect(self._choose_folder)
        self.chk_files.toggled.connect(self._apply_mode_visibility)
        self.chk_playlist.toggled.connect(self._apply_mode_visibility)
        self.btn_all_original.clicked.connect(lambda: self._apply_all("源格式"))
        self.btn_all_mp3.clicked.connect(lambda: self._apply_all("mp3"))
        self.btn_all_opus.clicked.connect(lambda: self._apply_all("opus"))
        self.btn_all_flac.clicked.connect(lambda: self._apply_all("flac"))
        self.btn_all_wav.clicked.connect(lambda: self._apply_all("wav"))
        self.btn_all_ogg.clicked.connect(lambda: self._apply_all("ogg"))
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)

        self.path_input.setText(str(Path.cwd()))
        self._apply_mode_visibility()

    def _choose_folder(self) -> None:
        """打开文件对话框让用户选择一个文件夹，并将选择的路径显示在路径输入框中。

        功能：
            弹出系统文件对话框，让用户选择一个文件夹路径。
            如果用户选择了一个有效的文件夹路径，则将该路径显示在界面上的路径输入框中。
            如果用户取消选择或未选择任何文件夹，则不进行任何操作。

        参数：
            self (QWidget): 当前窗口实例，用于作为对话框的父窗口，并访问路径输入框控件。

        返回值：
            None: 此方法不返回任何值，仅通过副作用更新UI控件。
        """
        # 打开文件夹选择对话框，提示用户选择导出目录
        # 参数1：父窗口对象(self)
        # 参数2：对话框标题
        # 参数3：对话框打开时默认显示的目录路径，若为空则使用当前工作目录
        folder = QFileDialog.getExistingDirectory(self, "选择导出目录", self.path_input.text().strip() or str(Path.cwd()))

        # 检查用户是否选择了有效的文件夹路径（非空字符串表示用户做出了选择）
        if folder:
            # 将用户选择的文件夹路径更新到路径输入框中
            self.path_input.setText(folder)

    def _apply_mode_visibility(self) -> None:
        """根据当前选中的模式，更新界面元素的可见性。

        功能：该方法根据用户选择的"文件模式"或"播放列表模式"，控制相关界面控件的显示与隐藏。
        参数：self - 实例对象本身，用于访问界面控件和状态。
        返回值：无（None）。
        """
        # 获取"文件模式"复选框的选中状态，并转换为布尔值
        files_mode = bool(self.chk_files.isChecked())
        # 获取"播放列表模式"复选框的选中状态，并转换为布尔值
        playlist_mode = bool(self.chk_playlist.isChecked())

        # 根据文件模式状态，控制树形视图和导出歌词复选框的可见性
        self.tree.setVisible(files_mode)
        self.chk_export_lyrics.setVisible(files_mode)

        # 根据播放列表模式状态，控制播放列表提示标签的可见性
        self.playlist_hint.setVisible(playlist_mode)

        # 遍历一组批量操作按钮，统一根据文件模式状态设置其可见性
        for btn in [
            self.btn_all_original,
            self.btn_all_mp3,
            self.btn_all_opus,
            self.btn_all_flac,
            self.btn_all_wav,
            self.btn_all_ogg,
        ]:
            btn.setVisible(files_mode)

    def _apply_all(self, text: str) -> None:
        for combo in self._combo_by_track_id.values():
            combo.setCurrentText(text)

    def _on_accept(self) -> None:
        """功能：处理导出配置的接受逻辑，验证导出目录和导出方式，并执行接受操作。
        参数：无（除了self）
        返回值：无
        """
        out_dir = self.output_dir()  # 获取输出目录
        if not out_dir:  # 检查输出目录是否为空
            QMessageBox.warning(self, "导出配置", "请选择导出目录。")  # 显示警告信息
            return  # 提前返回，不执行后续操作
        if not self.export_files_enabled() and not self.export_playlist_enabled():  # 检查是否至少启用一种导出方式
            QMessageBox.warning(self, "导出配置", "请至少勾选一种导出方式。")  # 显示警告信息
            return  # 提前返回，不执行后续操作
        self.accept()  # 验证通过，执行接受操作

    def output_dir(self) -> str:
        return str(self.path_input.text().strip())

    def export_files_enabled(self) -> bool:
        return bool(self.chk_files.isChecked())

    def export_playlist_enabled(self) -> bool:
        return bool(self.chk_playlist.isChecked())

    def export_lyrics_enabled(self) -> bool:
        return bool(self.chk_export_lyrics.isChecked() and self.chk_files.isChecked())

    def export_plan(self) -> dict[str, str]:
        """获取当前界面所选择的音轨转换计划。

        功能：
            遍历每个音轨对应的格式选择下拉框，根据用户选择，将其中文格式名映射为内部统一的英文格式标识符，并组装成转换计划字典。

        参数：
            self: 实例自身，隐式传入。

        返回值：
            dict[str, str]: 一个字典，键为音轨ID (track_id)，值为对应的文件格式（如"original", "mp3", "flac"等）。
        """
        # 构建一个映射字典，用于将UI界面上的中文格式名称转换为程序内部使用的格式标识符。
        mapping = {
            "源格式": "original",  # “源格式”代表不进行转换，保持原始格式
            "mp3": "mp3",
            "opus": "opus",
            "flac": "flac",
            "wav": "wav",
            "ogg": "ogg",
        }
        out: dict[str, str] = {}
        # 遍历每个音轨ID及其对应的格式选择组合框（combo）
        for track_id, combo in self._combo_by_track_id.items():
            # 获取组合框当前选中的文本，如果为空或None，则默认使用“源格式”
            text = str(combo.currentText() or "源格式")
            # 根据映射字典查找对应的内部格式，如果找不到则默认使用“original”
            out[track_id] = mapping.get(text, "original")
        return out


"""运行导出对话框，处理歌曲导出设置和执行。

参数：
    parent (QWidget): 父窗口组件。
    facade (MuseArcFacade): 门面类实例，用于导出操作。
    tracks (list[dict]): 歌曲列表，每个字典包含歌曲信息。
    playlist_name (str, optional): 播放列表名称，默认为空字符串。

返回：
    tuple[bool, str]: 第一个元素表示是否成功导出，第二个元素是导出文件的路径（多个路径用分号分隔），或空字符串表示失败。
"""
def _run_export_dialog(parent: QWidget, facade: MuseArcFacade, tracks: list[dict], *, playlist_name: str = "") -> tuple[bool, str]:
    """
    运行导出对话框，处理歌曲导出过程。

    参数：
        parent (QWidget): 父窗口对象，用于显示对话框。
        facade (MuseArcFacade): 门面对象，提供导出功能接口。
        tracks (list[dict]): 歌曲列表，每个字典应包含 'track_id' 等字段。
        playlist_name (str, 可选): 播放列表名称，默认为空字符串。

    返回：
        tuple[bool, str]: 第一个元素为布尔值，表示是否成功导出；第二个元素为字符串，表示输出路径（多个路径用分号分隔）。
    """
    # 从歌曲列表中提取有效的track_id，并转换为字符串
    track_ids = [str(t.get("track_id", "")) for t in tracks if t.get("track_id")]
    # 如果没有有效的track_id，显示警告并返回失败
    if not track_ids:
        QMessageBox.warning(parent, "导出", "请先选择歌曲")
        return False, ""
    # 创建导出配置对话框，使用播放列表名称或默认名称
    dlg = ExportConfigDialog(parent, tracks, default_name=playlist_name or "playlist")
    # 如果用户取消对话框，返回失败
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False, ""
    # 获取输出目录
    out_dir = dlg.output_dir()
    # 初始化导出输出路径列表
    outputs: list[str] = []
    # 如果导出播放列表功能启用，执行导出播放列表包操作
    if dlg.export_playlist_enabled():
        file_path = facade.export_playlist_package(track_ids, out_dir, playlist_name=playlist_name or "playlist")
        outputs.append(file_path)
    # 如果导出文件功能启用，执行导出文件操作
    if dlg.export_files_enabled():
        facade.export_with_plan(
            track_ids,
            out_dir,
            dlg.export_plan(),
            bitrate="320k",
            copy_bound_lyrics=dlg.export_lyrics_enabled(),
        )
        outputs.append(out_dir)
    # 返回成功状态和输出路径，多个路径用分号分隔；如果没有输出路径，使用输出目录
    return True, " ; ".join(outputs) if outputs else out_dir


