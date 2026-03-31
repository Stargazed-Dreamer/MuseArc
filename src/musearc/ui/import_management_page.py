from __future__ import annotations

from pathlib import Path
import re

from PySide6.QtCore import QModelIndex, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
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


def _apply_button_scale(button: QPushButton, scale: float) -> None:
    button.setMinimumHeight(max(30, int(28 * scale)))


def _copy_selected_cells(table: QTableView) -> None:
    selection_model = table.selectionModel()
    if selection_model is None:
        return
    indexes = selection_model.selectedIndexes()
    if not indexes:
        return

    cells: dict[int, dict[int, str]] = {}
    max_col = 0
    for idx in indexes:
        row = idx.row()
        col = idx.column()
        max_col = max(max_col, col)
        cells.setdefault(row, {})[col] = str(idx.data() or "")

    lines: list[str] = []
    for row in sorted(cells.keys()):
        cols = cells[row]
        lines.append("\t".join(cols.get(col, "") for col in range(max_col + 1)))

    QApplication.clipboard().setText("\n".join(lines))


def _install_copy_support(table: QTableView) -> None:
    shortcut = QShortcut(QKeySequence.StandardKey.Copy, table)
    shortcut.activated.connect(lambda: _copy_selected_cells(table))
    table._copy_shortcut = shortcut


def _errors_count(value) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, tuple):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    try:
        return int(value or 0)
    except Exception:
        return 0


def _safe_int(value, default: int = 0) -> int:
    if isinstance(value, (list, tuple, dict, set)):
        return default
    try:
        return int(value or 0)
    except Exception:
        return default


def _clean_status_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "待处理"
    # 兼容旧状态：例如“已跳过-原因xxx”
    text = re.sub(r"\s*原因\s*", "", text)
    text = text.replace("--", "-").replace("::", ":")
    text = text.strip(" -:")
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
        if role == Qt.ItemDataRole.ForegroundRole and index.isValid():
            row = self.row_at(index.row()) or {}
            code = str(row.get("status_code", ""))
            return self.STATUS_COLORS.get(code)
        return super().data(index, role)


class ImportTaskDetailDialog(QDialog):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("导入任务详情")
        self.resize(1080, 760)

        root = QVBoxLayout(self)

        top = QVBoxLayout()
        self.label_batch = QLabel("批次: -")
        self.label_source = QLabel("来源: -")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.label_stats = QLabel("-")
        top.addWidget(self.label_batch)
        top.addWidget(self.label_source)
        top.addWidget(self.progress)
        top.addWidget(self.label_stats)

        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("文件列表排序"))
        self.combo_sort = QComboBox()
        self.combo_sort.addItem("文件名", "file_name")
        self.combo_sort.addItem("状态", "status")
        sort_row.addWidget(self.combo_sort)
        sort_row.addStretch(1)

        self.file_model = ImportFileStateModel(
            [
                ColumnDef("file_name", "文件名"),
                ColumnDef("status", "状态"),
            ]
        )
        self.file_table = QTableView()
        self.file_table.setModel(self.file_model)
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_table.setAlternatingRowColors(True)
        self.file_table.horizontalHeader().setStretchLastSection(True)
        self.file_table.setSortingEnabled(False)
        _install_copy_support(self.file_table)

        root.addLayout(top)
        root.addLayout(sort_row)
        root.addWidget(self.file_table, 1)

        self.combo_sort.currentIndexChanged.connect(self._apply_sort)

    def _apply_sort(self) -> None:
        key = str(self.combo_sort.currentData() or "file_name")
        rows = list(self.file_model.rows or [])
        status_rank = {
            "processing": 0,
            "review": 1,
            "pending": 2,
            "archived": 3,
            "skipped": 4,
        }
        if key == "status":
            rows.sort(
                key=lambda r: (
                    int(status_rank.get(str(r.get("status_code", "pending")), 99)),
                    str(r.get("file_name", "")).casefold(),
                )
            )
        else:
            rows.sort(key=lambda r: str(r.get("file_name", "")).casefold())
        self.file_model.set_rows(rows)

    @staticmethod
    def _normalize_file_states(rows: list[dict]) -> list[dict]:
        out: list[dict] = []
        for item in rows or []:
            if not isinstance(item, dict):
                continue
            rel = str(item.get("relpath", "") or "")
            out.append(
                {
                    "file_name": str(item.get("file_name", "") or Path(rel).name),
                    "status": _clean_status_text(str(item.get("status", "") or "待处理")),
                    "status_code": str(item.get("status_code", "") or "pending"),
                }
            )
        return out

    def set_payload(self, payload: dict, *, running: bool) -> None:
        batch_id = str(payload.get("import_batch_id", "") or "-")
        source_path = str(payload.get("source_path", "") or "-")
        scanned = _safe_int(payload.get("scanned_files", 0), 0)
        processed = _safe_int(payload.get("processed_files", 0), 0)
        imported_tracks = _safe_int(payload.get("imported_tracks", 0), 0)
        imported_lyrics = _safe_int(payload.get("imported_lyrics", 0), 0)
        duplicate_tracks = _safe_int(payload.get("duplicate_tracks", 0), 0)
        review_items = _safe_int(payload.get("review_items", 0), 0)
        errors = _errors_count(payload.get("errors", 0))

        if not running and processed <= 0:
            processed = scanned

        percent = 0 if scanned <= 0 else int((processed / scanned) * 100)
        self.label_batch.setText(f"批次: {batch_id}")
        self.label_source.setText(f"来源: {source_path}")
        self.progress.setValue(max(0, min(100, percent)))
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
        rows = self._normalize_file_states(list(payload.get("file_states", []) or []))
        self.file_model.set_rows(rows)
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

        root = QVBoxLayout(self)

        row1 = QHBoxLayout()
        self.btn_new_import = QPushButton("导入来源")
        self.btn_resume_import = QPushButton("继续未完成导入")
        self.btn_import_stats = QPushButton("导入统计数据")
        row1.addWidget(self.btn_new_import)
        row1.addWidget(self.btn_resume_import)
        row1.addWidget(self.btn_import_stats)
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
        self.stats_table.horizontalHeader().setStretchLastSection(True)
        _install_copy_support(self.stats_table)

        root.addLayout(row1)
        root.addLayout(box)
        root.addWidget(self.queue_label)
        root.addWidget(self.queue_list)
        history_row = QHBoxLayout()
        left_hist = QVBoxLayout()
        left_hist.addWidget(QLabel("导入历史"))
        left_hist.addWidget(self.history_table, 1)
        right_hist = QVBoxLayout()
        right_hist.addWidget(QLabel("统计导入历史"))
        right_hist.addWidget(self.stats_table, 1)
        history_row.addLayout(left_hist, 3)
        history_row.addLayout(right_hist, 2)
        root.addLayout(history_row, 1)

        self.btn_new_import.clicked.connect(self.on_import)
        self.btn_resume_import.clicked.connect(self.on_resume_import)
        self.btn_import_stats.clicked.connect(self.on_import_stats)
        self.btn_pause_resume.clicked.connect(self._on_pause_resume_import)
        self.btn_cancel.clicked.connect(self._on_cancel_import)
        self.btn_detail.clicked.connect(self._open_running_detail)
        self.history_table.doubleClicked.connect(self._open_history_detail)

        self.reload_history()
        self._refresh_queue_view()

    def apply_button_scale(self, scale: float) -> None:
        for btn in [self.btn_new_import, self.btn_resume_import, self.btn_import_stats, self.btn_pause_resume, self.btn_cancel, self.btn_detail]:
            _apply_button_scale(btn, scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade
        self.reload_history()
        self._refresh_queue_view()

    def refresh_page(self) -> None:
        self.reload_history()
        self._refresh_queue_view()

    def reload_history(self) -> None:
        rows = self.facade.list_import_batches(limit=1000)
        self.history_model.set_rows(rows)
        self.reload_stats_history()

    def reload_stats_history(self) -> None:
        rows = self.facade.list_stats_import_history(limit=500)
        self.stats_model.set_rows(rows)

    def _ensure_detail_dialog(self) -> ImportTaskDetailDialog:
        if self._detail_dialog is None:
            self._detail_dialog = ImportTaskDetailDialog(self)
            self._detail_dialog.destroyed.connect(lambda *_args: setattr(self, "_detail_dialog", None))
        return self._detail_dialog

    def _open_running_detail(self) -> None:
        if not self._active_source or not self._last_progress_payload:
            QMessageBox.information(self, "导入详情", "当前没有运行中的导入任务。")
            return
        dialog = self._ensure_detail_dialog()
        dialog.set_payload(self._last_progress_payload, running=True)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _open_history_detail(self, index: QModelIndex) -> None:
        row = self.history_model.row_at(index.row()) if index.isValid() else None
        if not row:
            return
        import_batch_id = str(row.get("import_batch_id", "") or "")
        if not import_batch_id:
            return
        detail = self.facade.get_import_batch_detail(import_batch_id)
        if not detail:
            QMessageBox.warning(self, "导入详情", "未找到该批次详情。")
            return
        dialog = self._ensure_detail_dialog()
        dialog.set_payload(detail, running=False)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def on_import(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择导入来源")
        if not folder:
            return
        self._enqueue_source(folder)

    def on_resume_import(self) -> None:
        states = self.facade.list_resume_imports()
        if not states:
            QMessageBox.information(self, "继续导入", "没有未完成导入记录。")
            return

        menu = QMenu(self)
        action_map: dict[QAction, str] = {}
        for state in states:
            text = f"{state['source_path']} ({state['processed_files']}/{state['scanned_files']})"
            action_map[menu.addAction(text)] = str(state["source_path"])
        menu.addSeparator()
        action_all = menu.addAction("全部加入队列")

        chosen = menu.exec(self.btn_resume_import.mapToGlobal(self.btn_resume_import.rect().bottomLeft()))
        if not chosen:
            return

        if chosen == action_all:
            for state in states:
                self._enqueue_source(str(state["source_path"]))
            return

        source = action_map.get(chosen)
        if source:
            self._enqueue_source(source)

    def on_import_stats(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "选择统计数据文件", "", "JSON (*.json);;All Files (*)")
        if not file_path:
            return
        try:
            result = self.facade.import_playlist_stats(file_path)
        except Exception as exc:
            QMessageBox.warning(self, "导入统计数据", str(exc))
            return
        self.reload_stats_history()
        self.library_changed.emit()
        QMessageBox.information(
            self,
            "导入统计数据",
            f"歌单哈希: {result.get('playlist_hash','')}\n生效歌曲: {result.get('applied_tracks',0)}\n跳过: {result.get('skipped_rows',0)}",
        )

    def _enqueue_source(self, source_path: str) -> None:
        source = str(Path(source_path).resolve())
        if source == self._active_source or source in self._queue:
            return
        self._queue.append(source)
        self._refresh_queue_view()
        self._start_next_import()

    def _start_next_import(self) -> None:
        if self._import_thread is not None or not self._queue:
            return
        source = self._queue.pop(0)
        self._start_import(source)
        self._refresh_queue_view()

    def _start_import(self, source_path: str) -> None:
        self._active_source = source_path
        self._import_paused = False
        self._last_progress_payload = None
        self._last_report_payload = None
        self.label_active.setText(f"当前任务: {source_path}")
        self.label_stage.setText("阶段: 启动中...")
        self.label_file.setText("文件: -")
        self.progress.setValue(0)
        self.label_stats.setText("-")
        self.btn_pause_resume.setEnabled(True)
        self.btn_cancel.setEnabled(True)
        self.btn_detail.setEnabled(True)
        self.btn_pause_resume.setText("暂停")

        self._import_thread = QThread(self)
        self._import_worker = ImportWorker(str(self.facade.library_root), source_path)
        self._import_worker.moveToThread(self._import_thread)

        self._import_thread.started.connect(self._import_worker.run)
        self._import_worker.progress.connect(self._on_import_progress)
        self._import_worker.finished.connect(self._on_import_finished)
        self._import_worker.failed.connect(self._on_import_failed)
        self._import_worker.finished.connect(self._import_thread.quit)
        self._import_worker.failed.connect(self._import_thread.quit)
        self._import_thread.finished.connect(self._cleanup_import_worker)

        self._import_thread.start()

    def has_running_import(self) -> bool:
        return bool(self._import_thread is not None and self._import_thread.isRunning())

    def _close_heavy_modal(self) -> None:
        if self._heavy_modal is not None:
            self._heavy_modal.hide()
            self._heavy_modal.deleteLater()
            self._heavy_modal = None

    def shutdown_running_import(self, timeout_ms: int = 15000) -> bool:
        self._close_heavy_modal()
        if self._import_worker is not None:
            try:
                self._import_worker.request_cancel("keep")
                self._import_worker.request_resume()
            except Exception:
                pass
        if self._import_thread is None:
            return True
        if self._import_thread.isRunning():
            self._import_thread.quit()
            if not self._import_thread.wait(max(1000, int(timeout_ms))):
                return False
        self._cleanup_import_worker()
        return True

    def _on_pause_resume_import(self) -> None:
        if not self._import_worker:
            return
        if self._import_paused:
            self._import_worker.request_resume()
            self._import_paused = False
            self.btn_pause_resume.setText("暂停")
            self.label_stage.setText("阶段: 恢复中...")
        else:
            self._import_worker.request_pause()
            self._import_paused = True
            self.btn_pause_resume.setText("继续")
            self.label_stage.setText("阶段: 暂停中...")

    def _on_cancel_import(self) -> None:
        if not self._import_worker:
            return

        box = QMessageBox(self)
        box.setWindowTitle("取消导入")
        box.setText("请选择取消方式")
        keep_btn = box.addButton("保留已处理并停止", QMessageBox.ButtonRole.AcceptRole)
        rollback_btn = box.addButton("全部回退并停止", QMessageBox.ButtonRole.DestructiveRole)
        cont_btn = box.addButton("继续导入", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()

        if clicked == keep_btn:
            self._import_worker.request_cancel("keep")
            self.label_stage.setText("阶段: 取消中（保留已处理）...")
        elif clicked == rollback_btn:
            self._import_worker.request_cancel("rollback")
            self.label_stage.setText("阶段: 取消中（全部回退）...")
        elif clicked == cont_btn:
            return

    def _on_import_progress(self, payload: dict) -> None:
        self._last_progress_payload = dict(payload)
        scanned = _safe_int(payload.get("scanned_files", 0), 0)
        processed = _safe_int(payload.get("processed_files", 0), 0)
        percent = 0 if scanned <= 0 else int((processed / scanned) * 100)
        self.progress.setValue(max(0, min(100, percent)))

        paused = bool(payload.get("paused", False))
        self._import_paused = paused
        stage = str(payload.get("stage", "-") or "-")
        if paused:
            stage = f"{stage}（已暂停）"
        self.label_stage.setText(f"阶段: {stage}")
        self.label_file.setText(f"文件: {payload.get('current_file', '-')}")

        errors = _errors_count(payload.get("errors", 0))
        self.label_stats.setText(
            " | ".join(
                [
                    f"进度 {processed}/{scanned}",
                    f"曲目+{payload.get('imported_tracks', 0)}",
                    f"歌词+{payload.get('imported_lyrics', 0)}",
                    f"重复 {payload.get('duplicate_tracks', 0)}",
                    f"审查 {payload.get('review_items', 0)}",
                    f"错误 {errors}",
                ]
            )
        )
        self.btn_pause_resume.setText("继续" if paused else "暂停")

        if self._detail_dialog is not None and self._detail_dialog.isVisible():
            self._detail_dialog.set_payload(self._last_progress_payload, running=True)

    def _on_import_finished(self, report: dict) -> None:
        self._last_report_payload = dict(report)

        if report.get("cancelled"):
            if report.get("rollback_applied"):
                self.label_stage.setText("阶段: 已取消并全部回退")
            elif report.get("resume_available"):
                self.label_stage.setText("阶段: 已取消，进度已保留")
            else:
                self.label_stage.setText("阶段: 已取消")
        else:
            self.label_stage.setText("阶段: 导入完成")

        self.label_stats.setText(
            f"扫描 {report.get('scanned_files', 0)} / 曲目 {report.get('imported_tracks', 0)} / 审查 {report.get('review_items', 0)}"
        )

        self._active_source = None
        self.btn_pause_resume.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.btn_detail.setEnabled(False)
        self.reload_history()
        self.library_changed.emit()
        self._refresh_queue_view()
        self._close_heavy_modal()

        if self._detail_dialog is not None and self._detail_dialog.isVisible():
            self._detail_dialog.set_payload(self._last_report_payload, running=False)

        self._start_next_import()

    def _on_import_failed(self, message: str) -> None:
        self.label_stage.setText("阶段: 导入失败")
        self.label_stats.setText(message)
        self._active_source = None
        self._last_progress_payload = None
        self.btn_pause_resume.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.btn_detail.setEnabled(False)
        QMessageBox.critical(self, "导入失败", message)
        self._refresh_queue_view()
        self._close_heavy_modal()
        self._start_next_import()

    def _cleanup_import_worker(self) -> None:
        self._close_heavy_modal()
        if self._import_worker is not None:
            self._import_worker.deleteLater()
            self._import_worker = None
        if self._import_thread is not None:
            self._import_thread.deleteLater()
            self._import_thread = None

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
