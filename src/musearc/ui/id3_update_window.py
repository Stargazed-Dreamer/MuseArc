from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
    QComboBox,
)

from musearc.app.facade import MuseArcFacade
from musearc.ui.table_models import ColumnDef, DictTableModel


class Id3MetadataUpdateWindow(QWidget):
    """使用 ID3 + 歌词元数据批量修复歌曲元信息。"""

    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade
        self.setWindowTitle("使用ID3和歌词更新歌曲元信息")
        self.resize(980, 680)

        root = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("全量筛选工作"))
        self.combo_work = QComboBox()
        self.btn_refresh = QPushButton("刷新工作")
        self.btn_run = QPushButton("开始更新")
        row.addWidget(self.combo_work, 1)
        row.addWidget(self.btn_refresh)
        row.addWidget(self.btn_run)

        self.model = DictTableModel(
            [
                ColumnDef("track_id", "Track ID"),
                ColumnDef("file_name", "文件名"),
                ColumnDef("status", "状态"),
                ColumnDef("applied", "更新字段"),
                ColumnDef("reason", "说明"),
            ]
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)

        self.status = QLabel("请选择一个全量筛选工作，然后开始更新。")
        self.status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        root.addLayout(row)
        root.addWidget(self.table, 1)
        root.addWidget(self.status)

        self.btn_refresh.clicked.connect(self.reload_works)
        self.btn_run.clicked.connect(self.run_update)
        self.reload_works()

    def reload_works(self) -> None:
        rows = self.facade.list_fullscan_works()
        keep = str(self.combo_work.currentData() or "")
        self.combo_work.blockSignals(True)
        self.combo_work.clear()
        for row in rows:
            work_id = str(row.get("work_id", "") or "")
            name = str(row.get("name", "") or "")
            todo = int(row.get("todo_items", 0) or 0)
            total = int(row.get("total_items", 0) or 0)
            self.combo_work.addItem(f"{name} (待处理 {todo}/{total})", work_id)
        self.combo_work.blockSignals(False)
        if keep:
            idx = self.combo_work.findData(keep)
            if idx >= 0:
                self.combo_work.setCurrentIndex(idx)

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

