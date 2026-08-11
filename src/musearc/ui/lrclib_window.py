from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from musearc.app.facade import MuseArcFacade
from musearc.ui.table_models import ColumnDef, DictTableModel


def _safe_int(value, default: int = 0) -> int:
    """
    将给定的值安全地转换为整数。如果转换失败，则返回默认值。

    参数:
        value: 任意类型的值，将尝试转换为整数。
        default (int): 默认整数，默认为0。

    返回值:
        int: 转换后的整数或默认值。
    """
    try:
        # 尝试将value转换为整数；如果value是假值（如None、空字符串等），则使用0
        return int(value or 0)
    except Exception:
        # 如果发生任何异常（如转换失败），返回默认值
        return default


class LrcLibFetchWindow(QWidget):
    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade
        self.setWindowTitle("补全歌词（LRCLIB）")
        self.resize(1160, 760)
        self._all_tracks: list[dict] = []
        self._filtered_tracks: list[dict] = []

        root = QVBoxLayout(self)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.page_filter = QWidget()
        self.page_confirm = QWidget()
        self.page_progress = QWidget()
        self.stack.addWidget(self.page_filter)
        self.stack.addWidget(self.page_confirm)
        self.stack.addWidget(self.page_progress)

        self._build_filter_page()
        self._build_confirm_page()
        self._build_progress_page()
        self._load_tracks()

    def _build_filter_page(self) -> None:
        """
        构建筛选页面，用于选择将要请求 LRCLIB 歌词的歌曲。
        参数：无（除了self）
        返回值：无
        """
        # 创建垂直布局，并设置给self.page_filter作为根布局
        root = QVBoxLayout(self.page_filter)
        # 添加标签显示当前步骤信息
        root.addWidget(QLabel("步骤 1/3：筛选将请求 LRCLIB 的歌曲"))

        # 创建复选框：满足API调用所需的信息，设为选中且禁用（因为必须满足）
        self.chk_required = QCheckBox("满足 API 调用所需的信息")
        self.chk_required.setChecked(True)
        self.chk_required.setEnabled(False)
        # 创建复选框：未链接歌词的歌曲，默认选中
        self.chk_no_lyrics = QCheckBox("未链接歌词的歌曲")
        self.chk_no_lyrics.setChecked(True)
        # 创建复选框：不是纯音乐，默认选中
        self.chk_not_instrumental = QCheckBox("不是纯音乐")
        self.chk_not_instrumental.setChecked(True)
        # 将所有复选框添加到布局
        root.addWidget(self.chk_required)
        root.addWidget(self.chk_no_lyrics)
        root.addWidget(self.chk_not_instrumental)

        # 创建标签用于显示筛选摘要信息
        self.lbl_filter_summary = QLabel("")
        root.addWidget(self.lbl_filter_summary)

        # 创建数据模型，定义表格列以展示歌曲信息
        self.model_filter = DictTableModel(
            [
                ColumnDef("file_name", "文件名"),
                ColumnDef("title", "标题"),
                ColumnDef("artist", "艺术家"),
                ColumnDef("album", "专辑"),
                ColumnDef("duration_sec", "时长(s)"),
                ColumnDef("lyrics_source", "已有歌词"),
                ColumnDef("language_kind", "语言"),
            ]
        )
        # 创建表格视图，并绑定数据模型
        self.table_filter = QTableView()
        self.table_filter.setModel(self.model_filter)
        # 启用表格列排序功能
        self.table_filter.setSortingEnabled(True)
        # 设置水平表头最后一列自动拉伸以填充剩余空间
        self.table_filter.horizontalHeader().setStretchLastSection(True)
        # 将表格添加到布局，拉伸因子为1以占据主要空间
        root.addWidget(self.table_filter, 1)

        # 创建水平布局用于放置操作按钮
        row = QHBoxLayout()
        # 创建刷新筛选按钮，用于重新加载歌曲数据
        self.btn_filter_refresh = QPushButton("刷新筛选")
        # 创建下一步按钮，用于跳转到确认页面
        self.btn_filter_next = QPushButton("下一步")
        # 将按钮添加到水平布局
        row.addWidget(self.btn_filter_refresh)
        # 添加弹性空间，使按钮分布更美观
        row.addStretch(1)
        row.addWidget(self.btn_filter_next)
        # 将水平布局添加到主垂直布局
        root.addLayout(row)

        # 连接复选框的状态变化信号到筛选方法，实现实时更新
        self.chk_no_lyrics.toggled.connect(self._apply_filter)
        self.chk_not_instrumental.toggled.connect(self._apply_filter)
        # 连接刷新按钮的点击信号到歌曲加载方法
        self.btn_filter_refresh.clicked.connect(self._load_tracks)
        # 连接下一步按钮的点击信号到页面跳转方法
        self.btn_filter_next.clicked.connect(self._go_confirm)

    def _build_confirm_page(self) -> None:
        root = QVBoxLayout(self.page_confirm)
        root.addWidget(QLabel("步骤 2/3：确认即将请求的歌曲"))
        self.lbl_confirm = QLabel("")
        root.addWidget(self.lbl_confirm)

        self.model_confirm = DictTableModel(
            [
                ColumnDef("file_name", "文件名"),
                ColumnDef("title", "标题"),
                ColumnDef("artist", "艺术家"),
                ColumnDef("album", "专辑"),
                ColumnDef("duration_sec", "时长(s)"),
            ]
        )
        self.table_confirm = QTableView()
        self.table_confirm.setModel(self.model_confirm)
        self.table_confirm.setSortingEnabled(True)
        self.table_confirm.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table_confirm, 1)

        row = QHBoxLayout()
        self.btn_confirm_back = QPushButton("上一步")
        self.btn_confirm_start = QPushButton("开始获取")
        row.addWidget(self.btn_confirm_back)
        row.addStretch(1)
        row.addWidget(self.btn_confirm_start)
        root.addLayout(row)
        self.btn_confirm_back.clicked.connect(lambda: self.stack.setCurrentWidget(self.page_filter))
        self.btn_confirm_start.clicked.connect(self._start_fetch)

    def _build_progress_page(self) -> None:
        root = QVBoxLayout(self.page_progress)
        root.addWidget(QLabel("步骤 3/3：获取进度与结果"))
        self.lbl_progress = QLabel("准备开始")
        root.addWidget(self.lbl_progress)

        self.model_progress = DictTableModel(
            [
                ColumnDef("file_name", "文件名"),
                ColumnDef("status", "状态"),
                ColumnDef("reason", "详情"),
            ]
        )
        self.table_progress = QTableView()
        self.table_progress.setModel(self.model_progress)
        self.table_progress.setSortingEnabled(True)
        self.table_progress.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table_progress, 1)

        row = QHBoxLayout()
        self.btn_progress_back = QPushButton("返回筛选")
        row.addWidget(self.btn_progress_back)
        row.addStretch(1)
        root.addLayout(row)
        self.btn_progress_back.clicked.connect(lambda: self.stack.setCurrentWidget(self.page_filter))

    def _load_tracks(self) -> None:
        """加载所有轨道并应用过滤器。

        功能：从facade获取所有轨道数据，存储在实例变量中，然后应用过滤器。
        参数：self - 实例自身
        返回值：None
        """
        self._all_tracks = self.facade.list_tracks(limit=2_000_000)  # 从facade获取所有轨道，限制为200万条
        self._apply_filter()  # 应用过滤器

    def _matches_required(self, row: dict) -> bool:
        """
        检查给定行是否满足必需字段的匹配条件。

        参数:
            row (dict): 包含歌曲信息的字典，期望有"title"、"artist"、"album"和"duration_sec"键。

        返回值:
            bool: 如果所有必需字段都有效（非空且duration大于0），则返回True；否则返回False。
        """
        # 获取歌曲标题，如果不存在或为空则使用空字符串，然后去除首尾空格
        title = str(row.get("title", "") or "").strip()
        # 获取艺术家名称，类似处理
        artist = str(row.get("artist", "") or "").strip()
        # 获取专辑名称，类似处理
        album = str(row.get("album", "") or "").strip()
        # 安全地获取持续时间（秒），默认为0，使用_safe_int函数避免转换错误
        duration = _safe_int(row.get("duration_sec", 0), 0)
        # 返回布尔值：检查所有字段是否非空且duration大于0
        return bool(title and artist and album and duration > 0)

    def _apply_filter(self) -> None:
        """
        应用当前界面设置的过滤条件，筛选音轨列表并更新相关显示。

        该方法根据界面控件（如复选框）的状态，对存储在实例属性 `self._all_tracks` 中的
        所有音轨进行过滤。过滤结果将更新实例属性 `self._filtered_tracks`，并同步更新
        数据模型和界面标签的显示。
        """
        out: list[dict] = [] # 初始化一个空列表，用于存放通过过滤的音轨
        # 获取“仅显示无歌词”的复选框状态，并转换为布尔值
        only_unlinked = bool(self.chk_no_lyrics.isChecked())
        # 获取“仅显示非纯音乐”的复选框状态，并转换为布尔值
        only_not_instrumental = bool(self.chk_not_instrumental.isChecked())
        # 遍历所有原始音轨数据
        for row in self._all_tracks:
            # 条件1：检查当前音轨是否符合基础的“必需”匹配规则
            if not self._matches_required(row):
                continue # 不匹配则跳过此音轨
            # 条件2：如果启用了“仅显示无歌词”，则跳过有歌词来源的音轨
            if only_unlinked and str(row.get("lyrics_source", "") or "").strip():
                continue # 有歌词来源，跳过
            # 条件3：如果启用了“仅显示非纯音乐”，则跳过语言类型为“instrumental”（纯音乐）的音轨
            if only_not_instrumental and str(row.get("language_kind", "") or "").strip().casefold() == "instrumental":
                continue # 是纯音乐，跳过
            # 所有条件均通过，将当前音轨的副本添加到输出列表
            out.append(dict(row))
        # 将过滤后的音轨列表更新到实例属性中
        self._filtered_tracks = out
        # 更新数据模型中的行数据，以触发界面刷新
        self.model_filter.set_rows(out)
        # 更新过滤结果的统计摘要标签
        self.lbl_filter_summary.setText(f"当前可请求 {len(out)} / 总计 {len(self._all_tracks)} 首")

    def _go_confirm(self) -> None:
        if not self.chk_no_lyrics.isChecked():
            QMessageBox.warning(self, "提示", "您未筛除已有歌词的歌曲，将在导入成功时断开旧歌词链接。")
        self.model_confirm.set_rows(list(self._filtered_tracks))
        self.lbl_confirm.setText(f"即将请求 {len(self._filtered_tracks)} 首歌曲")
        self.stack.setCurrentWidget(self.page_confirm)

    def _start_fetch(self) -> None:
        rows = list(self._filtered_tracks)
        if not rows:
            QMessageBox.information(self, "补全歌词", "没有可请求的歌曲。")
            return
        self.stack.setCurrentWidget(self.page_progress)
        self.btn_confirm_start.setEnabled(False)
        self.btn_progress_back.setEnabled(False)
        self.model_progress.set_rows([])
        progress_rows: list[dict] = []

        def _on_progress(item: dict, done: int, total: int) -> None:
            progress_rows.append(dict(item))
            self.model_progress.set_rows(list(progress_rows))
            self.lbl_progress.setText(f"处理中 {done}/{total}")
            QApplication.processEvents()

        summary = self.facade.fetch_lrclib_lyrics_for_tracks(
            [str(r.get("track_id", "")) for r in rows if r.get("track_id")],
            replace_existing_links=not self.chk_no_lyrics.isChecked(),
            progress_callback=_on_progress,
        )
        self.lbl_progress.setText(
            f"完成：成功 {summary.get('success', 0)}，跳过 {summary.get('skipped', 0)}，失败 {summary.get('failed', 0)}"
        )
        self.btn_confirm_start.setEnabled(True)
        self.btn_progress_back.setEnabled(True)

