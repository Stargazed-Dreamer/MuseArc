from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout


class ImportProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入中")
        self.setModal(True)
        self.resize(700, 240)

        root = QVBoxLayout(self)

        self.label_stage = QLabel("阶段: 准备")
        self.label_file = QLabel("文件: -")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.label_stats = QLabel("-")

        btn_line = QHBoxLayout()
        self.btn_pause_resume = QPushButton("暂停")
        self.btn_cancel = QPushButton("取消导入")
        btn_line.addStretch(1)
        btn_line.addWidget(self.btn_pause_resume)
        btn_line.addWidget(self.btn_cancel)

        root.addWidget(self.label_stage)
        root.addWidget(self.label_file)
        root.addWidget(self.bar)
        root.addWidget(self.label_stats)
        root.addLayout(btn_line)

    def update_progress(self, payload: dict) -> None:
        scanned = int(payload.get("scanned_files", 0))
        processed = int(payload.get("processed_files", 0))
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
