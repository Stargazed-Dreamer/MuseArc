from __future__ import annotations

from PySide6.QtCore import Qt
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
    try:
        return int(value or 0)
    except Exception:
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
        root = QVBoxLayout(self.page_filter)
        root.addWidget(QLabel("步骤 1/3：筛选将请求 LRCLIB 的歌曲"))

        self.chk_required = QCheckBox("满足 API 调用所需的信息")
        self.chk_required.setChecked(True)
        self.chk_required.setEnabled(False)
        self.chk_no_lyrics = QCheckBox("未链接歌词的歌曲")
        self.chk_no_lyrics.setChecked(True)
        self.chk_not_instrumental = QCheckBox("不是纯音乐")
        self.chk_not_instrumental.setChecked(True)
        root.addWidget(self.chk_required)
        root.addWidget(self.chk_no_lyrics)
        root.addWidget(self.chk_not_instrumental)

        self.lbl_filter_summary = QLabel("")
        root.addWidget(self.lbl_filter_summary)

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
        self.table_filter = QTableView()
        self.table_filter.setModel(self.model_filter)
        self.table_filter.setSortingEnabled(True)
        self.table_filter.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table_filter, 1)

        row = QHBoxLayout()
        self.btn_filter_refresh = QPushButton("刷新筛选")
        self.btn_filter_next = QPushButton("下一步")
        row.addWidget(self.btn_filter_refresh)
        row.addStretch(1)
        row.addWidget(self.btn_filter_next)
        root.addLayout(row)

        self.chk_no_lyrics.toggled.connect(self._apply_filter)
        self.chk_not_instrumental.toggled.connect(self._apply_filter)
        self.btn_filter_refresh.clicked.connect(self._load_tracks)
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
        self._all_tracks = self.facade.list_tracks(limit=2_000_000)
        self._apply_filter()

    def _matches_required(self, row: dict) -> bool:
        title = str(row.get("title", "") or "").strip()
        artist = str(row.get("artist", "") or "").strip()
        album = str(row.get("album", "") or "").strip()
        duration = _safe_int(row.get("duration_sec", 0), 0)
        return bool(title and artist and album and duration > 0)

    def _apply_filter(self) -> None:
        out: list[dict] = []
        only_unlinked = bool(self.chk_no_lyrics.isChecked())
        only_not_instrumental = bool(self.chk_not_instrumental.isChecked())
        for row in self._all_tracks:
            if not self._matches_required(row):
                continue
            if only_unlinked and str(row.get("lyrics_source", "") or "").strip():
                continue
            if only_not_instrumental and str(row.get("language_kind", "") or "").strip().casefold() == "instrumental":
                continue
            out.append(dict(row))
        self._filtered_tracks = out
        self.model_filter.set_rows(out)
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

