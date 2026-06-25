from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout


class ImportProgressDialog(QDialog):
    def __init__(self, parent=None):
        """初始化导入对话框，用于显示导入过程的界面元素。

        功能：创建并配置导入对话框，包含阶段标签、文件标签、进度条和控制按钮。
        参数：
            parent (QWidget, 可选): 父组件，默认为None，用于指定父窗口。
        返回值：无，因为这是构造函数。
        """
        super().__init__(parent)  # 调用父类QWidget的构造函数
        self.setWindowTitle("导入中")  # 设置窗口标题
        self.setModal(True)  # 设置为模态对话框，阻止用户与其他窗口交互
        self.resize(700, 240)  # 调整窗口大小为700x240像素

        root = QVBoxLayout(self)  # 创建垂直布局管理器并应用到当前对话框

        self.label_stage = QLabel("阶段: 准备")  # 创建标签显示当前导入阶段
        self.label_file = QLabel("文件: -")  # 创建标签显示当前处理文件
        self.bar = QProgressBar()  # 创建进度条
        self.bar.setRange(0, 100)  # 设置进度条范围为0到100
        self.label_stats = QLabel("-")  # 创建标签显示统计信息，如速度或剩余时间

        btn_line = QHBoxLayout()  # 创建水平布局用于放置控制按钮
        self.btn_pause_resume = QPushButton("暂停")  # 创建暂停/恢复按钮
        self.btn_cancel = QPushButton("取消导入")  # 创建取消导入按钮
        btn_line.addStretch(1)  # 添加弹性空间，使按钮靠右对齐
        btn_line.addWidget(self.btn_pause_resume)  # 将暂停按钮添加到水平布局
        btn_line.addWidget(self.btn_cancel)  # 将取消按钮添加到水平布局

        root.addWidget(self.label_stage)  # 将阶段标签添加到主布局
        root.addWidget(self.label_file)  # 将文件标签添加到主布局
        root.addWidget(self.bar)  # 将进度条添加到主布局
        root.addWidget(self.label_stats)  # 将统计标签添加到主布局
        root.addLayout(btn_line)  # 将按钮水平布局添加到主布局

    def update_progress(self, payload: dict) -> None:
        try:
            scanned = int(payload.get("scanned_files", 0) or 0)
        except Exception:
            scanned = 0
        try:
            processed = int(payload.get("processed_files", 0) or 0)
        except Exception:
            processed = 0
        percent = 0 if scanned <= 0 else int((processed / scanned) * 100)
        self.bar.setValue(max(0, min(100, percent)))

        paused = bool(payload.get("paused", False))
        stage = payload.get("stage", "-")
        if paused:
            stage = f"{stage}（已暂停）"
        self.label_stage.setText(f"阶段: {stage}")
        self.label_file.setText(f"文件: {payload.get('current_file', '-')}")
        self.label_stats.setText(
            " | ".join(
                [
                    f"进度 {processed}/{scanned}",
                    f"曲目+{payload.get('imported_tracks', 0)}",
                    f"歌词+{payload.get('imported_lyrics', 0)}",
                    f"重复 {payload.get('duplicate_tracks', 0)}",
                    f"审查 {payload.get('review_items', 0)}",
                    f"错误 {payload.get('errors', 0)}",
                ]
            )
        )
        self.btn_pause_resume.setText("继续" if paused else "暂停")
