from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from musearc.app.facade import MuseArcFacade
from musearc.ui.table_models import ColumnDef, DictTableModel


class Id3MetadataUpdateWindow(QWidget):
    """使用 ID3 + 歌词元数据批量修复歌曲元信息。"""

    def __init__(self, facade: MuseArcFacade):
        """
        初始化歌曲元信息更新界面的主窗口。

        本方法设置窗口标题、大小，创建界面布局，包含工作选择下拉框、刷新和开始更新按钮，
        以及用于显示更新结果的表格和状态栏。同时连接按钮信号到相应的槽函数。

        Args:
            facade (MuseArcFacade): 门面对象，用于提供应用层服务接口。
        """
        super().__init__()  # 调用父类构造函数
        self.facade = facade  # 保存门面对象引用
        self.setWindowTitle("使用ID3和歌词更新歌曲元信息")  # 设置窗口标题
        self.resize(980, 680)  # 设置窗口初始尺寸为980x680像素

        root = QVBoxLayout(self)  # 创建垂直布局作为主布局
        row = QHBoxLayout()  # 创建水平布局用于放置第一行控件

        row.addWidget(QLabel("全量筛选工作"))  # 添加"全量筛选工作"标签
        self.combo_work = QComboBox()  # 创建用于选择工作的下拉框
        self.btn_refresh = QPushButton("刷新工作")  # 创建"刷新工作"按钮
        self.btn_run = QPushButton("开始更新")  # 创建"开始更新"按钮

        row.addWidget(self.combo_work, 1)  # 添加下拉框，拉伸因子为1使其填充可用空间
        row.addWidget(self.btn_refresh)  # 添加刷新按钮
        row.addWidget(self.btn_run)  # 添加开始更新按钮

        # 初始化表格数据模型，定义列结构
        self.model = DictTableModel(
            [
                ColumnDef("track_id", "Track ID"),  # 音轨ID列
                ColumnDef("file_name", "文件名"),  # 文件名列
                ColumnDef("status", "状态"),  # 状态列（如成功/失败）
                ColumnDef("applied", "更新字段"),  # 实际更新的字段列
                ColumnDef("reason", "说明"),  # 更新原因或错误说明列
            ]
        )
        self.table = QTableView()  # 创建表格视图
        self.table.setModel(self.model)  # 将数据模型设置给表格视图
        self.table.setSortingEnabled(True)  # 启用表格排序功能
        self.table.setAlternatingRowColors(True)  # 启用交替行颜色以提高可读性
        self.table.horizontalHeader().setStretchLastSection(True)  # 让最后一列自动拉伸填充剩余空间

        self.status = QLabel("请选择一个全量筛选工作，然后开始更新。")  # 创建状态栏标签，显示初始提示信息
        # 设置状态栏文本左对齐且垂直居中
        self.status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        root.addLayout(row)  # 将水平布局（包含下拉框和按钮）添加到主布局
        root.addWidget(self.table, 1)  # 添加表格视图，拉伸因子为1使其填充可用垂直空间
        root.addWidget(self.status)  # 添加状态栏到主布局底部

        # 连接按钮的点击信号到对应的槽函数
        self.btn_refresh.clicked.connect(self.reload_works)  # "刷新工作"按钮点击时重新加载工作列表
        self.btn_run.clicked.connect(self.run_update)  # "开始更新"按钮点击时执行更新操作
        self.reload_works()  # 初始化时立即加载一次工作列表

    def reload_works(self) -> None:
        """重新加载工作项到组合框中。
        从门面层获取所有工作项，更新组合框内容，并恢复之前选中的项。
        参数：无。
        返回值：无。
        """
        rows = self.facade.list_fullscan_works()  # 获取所有工作项的列表
        keep = str(self.combo_work.currentData() or "")  # 保存当前选中项的标识符，确保为字符串，避免None值
        self.combo_work.blockSignals(True)  # 阻塞信号，防止在更新组合框时触发不必要的事件
        self.combo_work.clear()  # 清空组合框，为重新加载做准备
        for row in rows:  # 遍历每个工作项
            work_id = str(row.get("work_id", "") or "")  # 获取工作项ID，使用or确保空值转换为字符串
            name = str(row.get("name", "") or "")  # 获取工作项名称，同样处理空值
            todo = int(row.get("todo_items", 0) or 0)  # 获取待处理项数量，转换为整数，避免None
            total = int(row.get("total_items", 0) or 0)  # 获取总项数量，转换为整数，避免None
            self.combo_work.addItem(f"{name} (待处理 {todo}/{total})", work_id)  # 添加工作项到组合框，显示名称和待处理/总数统计
        self.combo_work.blockSignals(False)  # 恢复信号，允许组合框正常交互
        if keep:  # 如果之前有选中项
            idx = self.combo_work.findData(keep)  # 查找选中项在组合框中的索引
            if idx >= 0:  # 如果找到有效索引
                self.combo_work.setCurrentIndex(idx)  # 设置当前索引，恢复之前选中的项

    def run_update(self) -> None:
        work_id = str(self.combo_work.currentData() or "")
        if not work_id:
            QMessageBox.information(self, "更新元信息", "请先选择一个工作。")
            return
        result = self.facade.update_metadata_from_id3_and_lyrics(work_id)
        rows = list(result.get("rows", [])) if isinstance(result, dict) else []
        self.model.set_rows(rows)
        total = int(result.get("total", 0) or 0) if isinstance(result, dict) else 0
        updated = int(result.get("updated", 0) or 0) if isinstance(result, dict) else 0
        skipped = int(result.get("skipped", 0) or 0) if isinstance(result, dict) else 0
        self.status.setText(f"总计 {total} 条，已更新 {updated} 条，跳过 {skipped} 条。")

