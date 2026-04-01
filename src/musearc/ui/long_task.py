from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Event
from typing import Callable

from PySide6.QtCore import QObject, QEventLoop, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]
TaskCallable = Callable[[ProgressCallback, CancelCheck], object]


@dataclass(slots=True)
class TaskRunResult:
    cancelled: bool
    result: object | None = None
    error: Exception | None = None


def _format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    sec = int(round(seconds))
    mm, ss = divmod(max(0, sec), 60)
    hh, mm = divmod(mm, 60)
    if hh > 0:
        return f"{hh:02d}:{mm:02d}:{ss:02d}"
    return f"{mm:02d}:{ss:02d}"


class _TaskWorker(QObject):
    progress = Signal(int, int, str, float)
    finished = Signal(object, object, bool)

    def __init__(self, task: TaskCallable):
        super().__init__()
        self._task = task
        self._cancel = Event()
        self._start = 0.0

    def cancel(self) -> None:
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def _emit_progress(self, current: int, total: int, message: str = "") -> None:
        elapsed = max(0.0, time.monotonic() - self._start)
        total_safe = max(1, int(total or 1))
        curr_safe = max(0, min(total_safe, int(current or 0)))
        self.progress.emit(curr_safe, total_safe, str(message or ""), elapsed)

    def run(self) -> None:
        self._start = time.monotonic()
        try:
            result = self._task(self._emit_progress, self.is_cancelled)
            self.finished.emit(result, None, self._cancel.is_set())
        except Exception as exc:  # pragma: no cover
            self.finished.emit(None, exc, self._cancel.is_set())


class _TaskProgressDialog(QDialog):
    cancel_clicked = Signal()

    def __init__(self, parent: QWidget, title: str):
        super().__init__(parent)
        self.setWindowTitle(str(title or "正在处理"))
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        self.label = QLabel("准备中...")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.btn_cancel = QPushButton("取消")

        root.addWidget(self.label)
        root.addWidget(self.bar)
        root.addWidget(self.btn_cancel)

        self.btn_cancel.clicked.connect(self.cancel_clicked.emit)

    def mark_cancelling(self) -> None:
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("正在取消...")

    def update_state(self, current: int, total: int, message: str, elapsed: float) -> None:
        total_safe = max(1, int(total or 1))
        curr_safe = max(0, min(total_safe, int(current or 0)))
        pct = int(round(curr_safe * 100.0 / total_safe))
        self.bar.setValue(max(0, min(100, pct)))

        eta = None
        if curr_safe > 0:
            eta = max(0.0, elapsed * (total_safe - curr_safe) / curr_safe)
        text = f"{message or '正在处理'} | {curr_safe}/{total_safe} ({pct}%) | ETA {_format_eta(eta)}"
        self.label.setText(text)


def run_modal_task(parent: QWidget, title: str, task: TaskCallable) -> TaskRunResult:
    """Run task in worker thread with delayed modal progress and cancellation.

    - Delay modal for 3 seconds.
    - Show integer progress + ETA when task is still running.
    - Refresh UI every 250ms to keep update overhead low.
    """
    thread = QThread(parent)
    worker = _TaskWorker(task)
    worker.moveToThread(thread)

    latest = {"current": 0, "total": 100, "message": "准备中...", "elapsed": 0.0}
    done = {"value": False}
    output = TaskRunResult(cancelled=False, result=None, error=None)

    dialog = _TaskProgressDialog(parent, title)
    dialog.hide()

    show_timer = QTimer(parent)
    show_timer.setSingleShot(True)
    show_timer.setInterval(3000)

    refresh_timer = QTimer(parent)
    refresh_timer.setInterval(250)

    def _on_progress(current: int, total: int, message: str, elapsed: float) -> None:
        latest["current"] = int(current)
        latest["total"] = int(total)
        latest["message"] = str(message or "")
        latest["elapsed"] = float(elapsed or 0.0)

    def _refresh() -> None:
        if done["value"] or not dialog.isVisible():
            return
        dialog.update_state(
            int(latest["current"]),
            int(latest["total"]),
            str(latest["message"]),
            float(latest["elapsed"]),
        )

    def _show_if_needed() -> None:
        if done["value"]:
            return
        dialog.update_state(
            int(latest["current"]),
            int(latest["total"]),
            str(latest["message"]),
            float(latest["elapsed"]),
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_done(result: object, error: object, cancelled: bool) -> None:
        done["value"] = True
        output.cancelled = bool(cancelled)
        output.result = result
        output.error = error if isinstance(error, Exception) else None

    def _request_cancel() -> None:
        # IMPORTANT:
        # worker.cancel must run immediately; queued cross-thread delivery may never run
        # while worker thread is busy in long CPU loop.
        worker.cancel()
        latest["message"] = "正在取消..."
        output.cancelled = True
        if dialog.isVisible():
            dialog.mark_cancelling()

    worker.progress.connect(_on_progress)
    worker.finished.connect(_on_done)
    worker.finished.connect(thread.quit)
    thread.started.connect(worker.run)

    dialog.cancel_clicked.connect(_request_cancel)
    show_timer.timeout.connect(_show_if_needed)
    refresh_timer.timeout.connect(_refresh)

    loop = QEventLoop(parent)
    worker.finished.connect(loop.quit)

    thread.start()
    show_timer.start()
    refresh_timer.start()
    loop.exec()

    refresh_timer.stop()
    show_timer.stop()
    dialog.close()
    thread.wait(2000)
    worker.deleteLater()
    thread.deleteLater()
    return output


def make_chunked_task(
    ids: list[str],
    *,
    chunk_size: int,
    message: str,
    step: Callable[[list[str]], int | None],
) -> TaskCallable:
    seq = [str(v) for v in ids if str(v)]
    size = max(1, int(chunk_size or 1))

    def _task(progress: ProgressCallback, is_cancelled: CancelCheck) -> dict:
        total = len(seq)
        processed = 0
        affected = 0
        if total <= 0:
            progress(1, 1, message)
            return {"processed": 0, "affected": 0, "cancelled": False}
        for start in range(0, total, size):
            if is_cancelled():
                break
            chunk = seq[start : start + size]
            result = step(chunk)
            affected += int(result or 0)
            processed += len(chunk)
            progress(processed, total, message)
        cancelled = bool(is_cancelled() and processed < total)
        return {"processed": processed, "affected": affected, "cancelled": cancelled}

    return _task
